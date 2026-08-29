#!/usr/bin/env python3
"""
rss_feed_loader.py
==================

بارگذاری، نرمالایز، deduplicate و enrich کردن RSS feedهای پروژه.

خروجی نهایی: list[NewsItem] که مستقیماً به nlp_news.py می‌رود.

Architecture:
  FeedFetcher        : raw feed parsing با feedparser
  FeedNormalizer     : raw entry → NewsItem
  FeedEnricher       : optional body scraping برای headline-only feeds
  DuplicateDetector  : in-memory dedupe بر اساس link/hash
  FeedLoader         : orchestrator

Usage (basic):
  loader = FeedLoader()
  result = loader.load_all()
  for item in result.items:
      print(item.title, item.currency, item.source_reliability)

Usage (with enrichment):
  loader = FeedLoader(enable_enrichment=True)
  result = loader.load_all()

Usage (specific tier):
  from rss_feed_config import FeedTier, get_feeds_by_tier
  loader = FeedLoader()
  result = loader.load_feeds(get_feeds_by_tier(FeedTier.TIER_1_EDITORIAL))
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import mktime
from typing import Any, Optional

import feedparser
from core.models import NewsItem
from ingestion.rss_feed_config import (
    ALL_FEEDS,
    MACRO_CONTENT_KEYWORDS,
    FeedConfig,
    SummaryQuality,
    get_enabled_feeds,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

DEFAULT_HTTP_TIMEOUT = 10
DEFAULT_RETRY_TIMEOUT = 25  # ← جدید
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# HTML tag stripping
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# Minimum summary length to consider "good enough"
MIN_SUMMARY_LEN = 80


# ============================================================================
# Result models
# ============================================================================


@dataclass
class FeedLoadStats:
    """آمار بارگذاری یک feed."""

    feed_name: str
    fetched_entries: int = 0
    normalized_items: int = 0
    duplicates_skipped: int = 0
    enrichment_attempted: int = 0
    enrichment_succeeded: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class FeedLoadResult:
    """نتیجه نهایی بارگذاری همه feedها."""

    items: list[NewsItem] = field(default_factory=list)
    stats: list[FeedLoadStats] = field(default_factory=list)
    total_fetched: int = 0
    total_normalized: int = 0
    total_duplicates: int = 0
    total_errors: int = 0


# ============================================================================
# Utilities
# ============================================================================


def _strip_html(text: Optional[str]) -> str:
    """حذف HTML tags و normalize whitespace."""
    if not text:
        return ""
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _parse_entry_datetime(entry: Any) -> Optional[datetime]:
    """
    تبدیل published_parsed (struct_time) → datetime UTC.
    """
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None) or (
            entry.get(attr) if isinstance(entry, dict) else None
        )
        if parsed:
            try:
                ts = mktime(parsed)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _hash_for_dedup(title: str, source: str, link: Optional[str]) -> str:
    """تولید یک hash برای duplicate detection."""
    basis = (link or "") + "|" + title + "|" + source
    return hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()


# الگوی ازپیش‌کامپایل‌شده برای فیلتر محتوای ماکرو (فیدهای aggregator)
_MACRO_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in MACRO_CONTENT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# الگوی اسپم ویدیویی: ID تصادفی داخل پرانتز در انتهای عنوان
# (مزارع محتوای یوتیوبی مثل mshale.com — مثلاً «... Christine Lagarde (tJjV0aXKPT)»)
_VIDEO_ID_TITLE_RE = re.compile(r"\([A-Za-z0-9_-]{10,12}\)")


def _contains_macro_keyword(text: str) -> bool:
    """True اگر متن حداقل یک کلمه کلیدی ماکرو/بازار داشته باشد (word-boundary، بدون حساسیت به حروف)."""
    return bool(_MACRO_PATTERN.search(text))


def _detect_currency(
    title: str, summary: str, hints: dict[str, list[str]]
) -> Optional[str]:
    """
    حدس currency با scoring-based matching.

    استراتژی:
      - برای هر keyword که match شود، به آن currency 1 score می‌دهیم
      - title دو برابر summary وزن دارد (sentinel بیشتر)
      - currency با بالاترین score برنده است
      - اگر tie یا score = 0 → None
      - اگر title فقط 1 currency keyword داشت، آن قطعی است (override scoring)
    """
    if not hints:
        return None

    title_lower = (title or "").lower()
    summary_lower = (summary or "").lower()

    # title-priority strategy: ابتدا چک کن فقط در title چه چیزهایی هستند
    title_matches: dict[str, int] = {}
    for currency, keywords in hints.items():
        for kw in keywords:
            kw_lower = kw.lower()
            if _keyword_in_text(kw_lower, title_lower):
                title_matches[currency] = title_matches.get(currency, 0) + 1

    # اگر فقط یک currency در title match داشت، قطعی است
    if len(title_matches) == 1:
        return next(iter(title_matches))

    # اگر چند currency در title بودند، یا هیچی نبود، scoring کن
    scores: dict[str, int] = {}
    for currency, keywords in hints.items():
        for kw in keywords:
            kw_lower = kw.lower()
            # title weight = 2, summary weight = 1
            if _keyword_in_text(kw_lower, title_lower):
                scores[currency] = scores.get(currency, 0) + 2
            if _keyword_in_text(kw_lower, summary_lower):
                scores[currency] = scores.get(currency, 0) + 1

    if not scores:
        return None

    # پیدا کردن بالاترین score
    max_score = max(scores.values())
    winners = [c for c, s in scores.items() if s == max_score]

    # tie → None (نمی‌توانیم با اطمینان تصمیم بگیریم)
    if len(winners) > 1:
        return None

    # حداقل score باید 2 باشد تا قابل اعتماد باشد (یعنی حداقل یک‌بار در title)
    if max_score < 2:
        # اگر فقط در summary بود، باز هم برمی‌گردانیم اما با احتیاط کمتر
        # برای اطمینان بالاتر می‌توانیم اینجا None برگردانیم
        return winners[0] if max_score >= 1 else None

    return winners[0]


def _keyword_in_text(keyword: str, text: str) -> bool:
    """
    چک می‌کند keyword در text وجود دارد، با احترام به word boundary.

    این جلوی false positiveهایی مثل "us" در "discuss" را می‌گیرد.
    """
    if not keyword or not text:
        return False

    # اگر keyword با space ختم می‌شود، فقط prefix word match چک کن
    if keyword.endswith(" "):
        # "us " → match فقط اگر "us " به‌عنوان کلمه مستقل باشد
        # یعنی قبلش space یا شروع متن، بعدش space (که در keyword هست)
        kw_stripped = keyword.strip()
        # اضافه کردن whitespace boundary در دو طرف
        padded_text = " " + text + " "
        return f" {kw_stripped} " in padded_text

    # برای keywordهای چندحرفی بدون space، substring match کافی است
    # ولی برای جلوگیری از false positive، اگر keyword کوتاه است (≤3 char)،
    # word boundary را اجباری کن
    if len(keyword) <= 3:
        padded_text = " " + text + " "
        return (
            f" {keyword} " in padded_text
            or f" {keyword}." in padded_text
            or f" {keyword}," in padded_text
        )

    # برای keywordهای طولانی‌تر، substring match
    return keyword in text


# ============================================================================
# Fetcher
# ============================================================================


class FeedFetcher:
    """
    Wrapper روی feedparser با timeout واقعی + retry.

    استراتژی:
      - تلاش اول با timeout کوتاه (10s)
      - اگر timeout/connection error بود، یک retry با timeout بلند (25s)
      - سایر errorها (403, 404, ...) بدون retry skip می‌شوند
    """

    def __init__(
        self,
        timeout: int = DEFAULT_HTTP_TIMEOUT,
        retry_timeout: int = DEFAULT_RETRY_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout = timeout
        self._retry_timeout = retry_timeout
        self._user_agent = user_agent

    def fetch(self, url: str) -> Any:
        """
        یک feed را fetch می‌کند و parsed object برمی‌گرداند.
        در خطا یا timeout (پس از retry)، یک parsed خالی برمی‌گرداند.
        """
        # تلاش اول
        content = self._download(url, self._timeout, attempt=1)

        # اگر None برگشت (timeout/connection error)، retry با timeout بلندتر
        if content is None:
            logger.info(
                "Retrying %s with extended timeout=%ds...",
                url,
                self._retry_timeout,
            )
            content = self._download(url, self._retry_timeout, attempt=2)

        if content is None:
            # هر دو attempt fail شدند
            return feedparser.FeedParserDict(entries=[], bozo=1)

        # حالا feedparser را روی محتوای دانلود‌شده اجرا کن
        try:
            parsed = feedparser.parse(content)
            if parsed.bozo:
                bozo_exc = getattr(parsed, "bozo_exception", None)
                logger.warning("Feed parse warning for %s: %s", url, bozo_exc)
            return parsed
        except Exception as exc:
            logger.error("FeedFetcher.fetch parse failed for %s: %s", url, exc)
            return feedparser.FeedParserDict(entries=[], bozo=1, bozo_exception=exc)

    def _download(self, url: str, timeout: int, attempt: int) -> Optional[bytes]:
        """
        تلاش برای دانلود یک URL.
        برمی‌گرداند:
          - bytes در صورت موفقیت
          - None در صورت timeout / connection error (قابل retry)
          - None در صورت HTTP error (بدون retry)
        """
        try:
            import requests
        except ImportError:
            logger.error("requests is required. Install with: pip install requests")
            return None

        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

        try:
            response = requests.get(
                url,
                timeout=timeout,
                headers=headers,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response.content

        except requests.exceptions.Timeout:
            logger.warning(
                "Feed fetch timeout (attempt %d) after %ds: %s",
                attempt,
                timeout,
                url,
            )
            return None  # retry-able

        except requests.exceptions.ConnectionError as exc:
            logger.warning(
                "Feed fetch connection error (attempt %d) for %s: %s",
                attempt,
                url,
                exc,
            )
            return None  # retry-able

        except requests.exceptions.HTTPError as exc:
            # 429, 503, 504 می‌توانند با retry حل شوند
            status_code = exc.response.status_code if exc.response else 0
            if status_code in (429, 503, 504):
                logger.warning(
                    "Feed fetch HTTP %d (attempt %d) for %s — retryable",
                    status_code,
                    attempt,
                    url,
                )
                return None  # retry-able
            else:
                logger.error("Feed fetch HTTP error for %s: %s", url, exc)
                return None  # not retry-able (403, 404, ...)

        except Exception as exc:
            logger.error("Feed fetch unexpected error for %s: %s", url, exc)
            return None


# ============================================================================
# Normalizer
# ============================================================================


class FeedNormalizer:
    """
    تبدیل raw feed entry به NewsItem طبق FeedConfig.
    """

    def normalize(
        self,
        entry: Any,
        feed_cfg: FeedConfig,
    ) -> Optional[NewsItem]:
        """
        یک entry را به NewsItem تبدیل می‌کند.
        اگر title معتبر نداشته باشد، None برمی‌گرداند.
        """
        title = self._extract_title(entry)
        if not title:
            return None

        summary = self._extract_summary(entry)
        link = self._extract_link(entry)
        published = _parse_entry_datetime(entry)

        # فیلتر اسپم ویدیویی برای فیدهای aggregator: عناوین با ID ویدیو در انتها
        # (YouTube content farms که کلمات ترند مثل «Christine Lagarde» را به تیتر می‌چسبانند)
        if feed_cfg.require_macro_keyword and _VIDEO_ID_TITLE_RE.search(title[-40:]):
            logger.debug(
                "Video-spam filter dropped item: feed=%s | title=%r",
                feed_cfg.name,
                title[:80],
            )
            return None

        # فیلتر نویز برای فیدهای aggregator: تأیید محتوای ماکرو اجباری است
        # (مثلاً رد «ECB = External Commercial Borrowing» یا خبر کریکت)
        if feed_cfg.require_macro_keyword and not _contains_macro_keyword(f"{title} {summary}"):
            logger.debug(
                "Macro keyword filter dropped item: feed=%s | title=%r",
                feed_cfg.name,
                title[:80],
            )
            return None

        currency = feed_cfg.default_currency
        if currency is None:
            currency = _detect_currency(title, summary, feed_cfg.currency_hints)

        try:
            return NewsItem(
                title=title,
                summary=summary,
                published=published,
                link=link,
                source=feed_cfg.name,
                source_reliability=feed_cfg.source_reliability,
                category=feed_cfg.default_category,
                currency=currency,
                impact=None,  # RSS این را نمی‌دهد
            )
        except Exception as exc:
            logger.warning(
                "Normalize failed for entry from %s: %s | title=%r",
                feed_cfg.name,
                exc,
                title[:80],
            )
            return None

    @staticmethod
    def _extract_title(entry: Any) -> str:
        raw = getattr(entry, "title", None) or (
            entry.get("title") if isinstance(entry, dict) else None
        )
        return _strip_html(raw) if raw else ""

    @staticmethod
    def _extract_summary(entry: Any) -> str:
        # ترتیب: content > summary > description
        for attr in ("content", "summary", "description"):
            raw = getattr(entry, attr, None) or (
                entry.get(attr) if isinstance(entry, dict) else None
            )
            if not raw:
                continue
            # content معمولاً list of dict است
            if isinstance(raw, list) and raw:
                value = raw[0].get("value") if isinstance(raw[0], dict) else str(raw[0])
            else:
                value = str(raw)
            cleaned = _strip_html(value)
            if cleaned:
                return cleaned
        return ""

    @staticmethod
    def _extract_link(entry: Any) -> Optional[str]:
        raw = getattr(entry, "link", None) or (
            entry.get("link") if isinstance(entry, dict) else None
        )
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None


# ============================================================================
# Enricher (optional)
# ============================================================================


class FeedEnricher:
    """
    اگر summary یک NewsItem ضعیف باشد و feed نیاز به enrichment داشته باشد،
    body را از link استخراج می‌کند.

    این کلاس فقط در صورت enable_enrichment=True در FeedLoader فعال است.
    """

    def __init__(
        self,
        timeout: int = DEFAULT_HTTP_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        min_summary_len: int = MIN_SUMMARY_LEN,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent
        self._min_summary_len = min_summary_len

    def needs_enrichment(self, item: NewsItem, feed_cfg: FeedConfig) -> bool:
        """تشخیص اینکه آیا این item نیاز به enrichment دارد."""
        if not item.link:
            return False
        if feed_cfg.requires_enrichment:
            return True
        if feed_cfg.summary_quality == SummaryQuality.HEADLINE_ONLY:
            return True
        if len(item.summary or "") < self._min_summary_len:
            return True
        return False

    def enrich(self, item: NewsItem) -> NewsItem:
        """
        body را از link استخراج می‌کند.
        اگر موفق نشد، همان item اصلی را برمی‌گرداند.
        """
        if not item.link:
            return item

        try:
            # import lazy تا اگر کاربر enrichment نخواست، نیاز به نصب نباشد
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning(
                "Enrichment requires `requests` and `beautifulsoup4`. "
                "Install with: pip install requests beautifulsoup4"
            )
            return item

        try:
            response = requests.get(
                item.link,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            )
            response.raise_for_status()
        except Exception as exc:
            logger.debug("Enrichment fetch failed for %s: %s", item.link, exc)
            return item

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            body_text = self._extract_main_body(soup)
            if body_text and len(body_text) > len(item.summary or ""):
                return item.model_copy(update={"summary": body_text})
        except Exception as exc:
            logger.debug("Enrichment parse failed for %s: %s", item.link, exc)

        return item

    @staticmethod
    def _extract_main_body(soup: Any) -> str:
        """
        تلاش برای استخراج main article body با heuristic ساده.
        """
        # ترتیب: <article> > <main> > biggest <div>
        article = soup.find("article")
        if article:
            text = article.get_text(separator=" ", strip=True)
            if len(text) > MIN_SUMMARY_LEN:
                return _WHITESPACE_RE.sub(" ", text)

        main = soup.find("main")
        if main:
            text = main.get_text(separator=" ", strip=True)
            if len(text) > MIN_SUMMARY_LEN:
                return _WHITESPACE_RE.sub(" ", text)

        # fallback: همه paragraphها
        paragraphs = soup.find_all("p")
        if paragraphs:
            text = " ".join(p.get_text(strip=True) for p in paragraphs)
            text = _WHITESPACE_RE.sub(" ", text).strip()
            if len(text) > MIN_SUMMARY_LEN:
                return text

        return ""


# ============================================================================
# Duplicate Detector
# ============================================================================


class DuplicateDetector:
    """
    تشخیص duplicate در حافظه بر اساس hash از link/title/source.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_duplicate(self, item: NewsItem) -> bool:
        key = _hash_for_dedup(item.title, item.source, item.link)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def reset(self) -> None:
        self._seen.clear()

    @property
    def size(self) -> int:
        return len(self._seen)


# ============================================================================
# Loader (orchestrator)
# ============================================================================


class FeedLoader:
    """
    Orchestrator نهایی: feedها را بارگذاری، نرمالایز و dedupe می‌کند.
    """

    def __init__(
        self,
        fetcher: Optional[FeedFetcher] = None,
        normalizer: Optional[FeedNormalizer] = None,
        enricher: Optional[FeedEnricher] = None,
        deduper: Optional[DuplicateDetector] = None,
        enable_enrichment: bool = False,
    ) -> None:
        self._fetcher = fetcher or FeedFetcher()
        self._normalizer = normalizer or FeedNormalizer()
        self._enricher = enricher or FeedEnricher()
        self._deduper = deduper or DuplicateDetector()
        self._enable_enrichment = enable_enrichment

    def load_all(self) -> FeedLoadResult:
        """تمام feedهای فعال را بارگذاری می‌کند."""
        return self.load_feeds(get_enabled_feeds())

    def load_feeds(self, feeds: list[FeedConfig]) -> FeedLoadResult:
        """یک لیست feed مشخص را بارگذاری می‌کند."""
        result = FeedLoadResult()

        for feed_cfg in feeds:
            stats = self._load_single_feed(feed_cfg)
            result.stats.append(stats)
            result.total_fetched += stats.fetched_entries
            result.total_normalized += stats.normalized_items
            result.total_duplicates += stats.duplicates_skipped
            result.total_errors += len(stats.errors)

        # جمع‌آوری تمام items — این‌ها در حین _load_single_feed به result.items اضافه می‌شوند
        return result

    def _load_single_feed(self, feed_cfg: FeedConfig) -> FeedLoadStats:
        """یک feed را load می‌کند و stats برمی‌گرداند."""
        stats = FeedLoadStats(feed_name=feed_cfg.name)

        if feed_cfg.custom_timeout:
            fetcher = FeedFetcher(
                timeout=feed_cfg.custom_timeout,
                user_agent=self._fetcher._user_agent,
            )
        else:
            fetcher = self._fetcher

        logger.info(
            "Loading feed: %s (tier=%s, reliability=%.2f)",
            feed_cfg.name,
            feed_cfg.tier.value,
            feed_cfg.source_reliability,
        )

        parsed = self._fetcher.fetch(feed_cfg.url)
        entries = getattr(parsed, "entries", []) or []
        stats.fetched_entries = len(entries)

        if not entries:
            msg = f"No entries returned from {feed_cfg.url}"
            logger.warning(msg)
            stats.errors.append(msg)
            return stats

        for entry in entries:
            try:
                item = self._normalizer.normalize(entry, feed_cfg)
                if item is None:
                    continue

                if self._deduper.is_duplicate(item):
                    stats.duplicates_skipped += 1
                    continue

                if self._enable_enrichment and self._enricher.needs_enrichment(
                    item, feed_cfg
                ):
                    stats.enrichment_attempted += 1
                    enriched = self._enricher.enrich(item)
                    if enriched is not item and enriched.summary != item.summary:
                        stats.enrichment_succeeded += 1
                    item = enriched

                self._collected_items.append(item)
                stats.normalized_items += 1

            except Exception as exc:
                msg = f"Entry processing failed: {exc}"
                logger.warning("%s | feed=%s", msg, feed_cfg.name)
                stats.errors.append(msg)

        return stats

    # ---------- internal accumulator ----------

    def load_all_collect(self) -> FeedLoadResult:
        """
        نسخه‌ای از load_all که items را هم در result جمع می‌کند.
        (تابع load_all خود این را صدا می‌زند.)
        """
        self._collected_items: list[NewsItem] = []
        result = self.load_all()
        result.items = self._collected_items
        return result


# ============================================================================
# Convenience
# ============================================================================


def load_all_news() -> FeedLoadResult:
    """
    یک shortcut برای استفاده مستقیم بدون نیاز به ساخت loader.
    """
    loader = FeedLoader()
    return loader.load_all_collect()


# ============================================================================
# CLI smoke test
# ============================================================================


def _print_summary(result: FeedLoadResult) -> None:
    print()
    print("=" * 80)
    print("RSS FEED LOAD SUMMARY")
    print("=" * 80)
    print(f"Total fetched entries  : {result.total_fetched}")
    print(f"Total normalized items : {result.total_normalized}")
    print(f"Total duplicates       : {result.total_duplicates}")
    print(f"Total errors           : {result.total_errors}")
    print("-" * 80)
    for s in result.stats:
        err_part = f"  errors={len(s.errors)}" if s.errors else ""
        enrich_part = (
            f"  enrich={s.enrichment_succeeded}/{s.enrichment_attempted}"
            if s.enrichment_attempted
            else ""
        )
        print(
            f"  {s.feed_name:<28}  fetched={s.fetched_entries:>4}  "
            f"items={s.normalized_items:>4}  dup={s.duplicates_skipped:>3}"
            f"{enrich_part}{err_part}"
        )
    print("-" * 80)

    if result.items:
        print(f"\nSample items (first 5 of {len(result.items)}):")
        for item in result.items[:5]:
            currency = item.currency or "?"
            pub = item.published.strftime("%Y-%m-%d %H:%M") if item.published else "?"
            print(f"  [{item.source:<22}] {currency:<4} {pub}  {item.title[:80]}")
    print("=" * 80)
    print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RSS feed loader smoke test")
    parser.add_argument("--enrich", action="store_true", help="Enable body enrichment")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    loader = FeedLoader(enable_enrichment=args.enrich)
    result = loader.load_all_collect()
    _print_summary(result)
