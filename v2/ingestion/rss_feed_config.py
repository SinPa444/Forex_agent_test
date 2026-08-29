#!/usr/bin/env python3
"""
rss_feed_config.py
==================

Registry تمام RSS feedهای پروژه به همراه metadata کامل برای
هر feed: URL, source_reliability, currency hints, category hints.

این فایل فقط داده است؛ هیچ منطقی ندارد.

برای اضافه کردن feed جدید:
  - یک FeedConfig جدید به ALL_FEEDS اضافه کن
  - tier را درست انتخاب کن (1=core, 2=official, 3=commodities, 4=aggregator)
  - source_reliability طبق راهنمای پروژه (1.00=رسمی, 0.85=editorial سریع, ...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ============================================================================
# Enums
# ============================================================================


class FeedTier(str, Enum):
    """دسته‌بندی feed برای کنترل scope بارگذاری."""

    TIER_1_EDITORIAL = "tier_1_editorial"  # ForexLive, DailyFX, Investing
    TIER_2_OFFICIAL = "tier_2_official"  # Central banks
    TIER_3_COMMODITIES = "tier_3_commodities"  # Kitco, OilPrice — بعداً
    TIER_4_AGGREGATOR = "tier_4_aggregator"  # Google News, MarketWatch — بعداً


class SummaryQuality(str, Enum):
    """کیفیت summary در feed — برای تصمیم enrichment."""

    FULL = "full"  # متن کامل/طولانی
    PARTIAL = "partial"  # خلاصه کوتاه
    HEADLINE_ONLY = "headline_only"  # فقط عنوان


# ============================================================================
# Config dataclass
# ============================================================================


@dataclass(frozen=True)
class FeedConfig:
    """
    پیکربندی یک RSS feed.

    name: نام منبع که در NewsItem.source ذخیره می‌شود.
    url:  آدرس RSS feed.
    tier: دسته‌بندی feed.
    source_reliability: امتیاز اعتبار (0.0-1.0) که به NewsItem.source_reliability می‌رود.
    default_currency: اگر feed مخصوص یک ارز خاص است (مثل ECB → EUR).
                      None یعنی multi-currency و نیاز به detection از title دارد.
    default_category: اگر feed مخصوص یک category خاص است (مثل central bank → Central Bank Commentary).
                      None یعنی نیاز به detection.
    currency_hints: کلمات کلیدی برای حدس currency از title (وقتی default_currency=None).
                    مثلاً {'USD': ['fed', 'fomc', 'powell'], 'EUR': ['ecb', 'lagarde']}.
    summary_quality: کیفیت summary در این feed.
    requires_enrichment: اگر True، body باید از URL scrape شود.
    enabled: True/False برای فعال/غیرفعال کردن بدون حذف.
    notes: توضیحات اختیاری.
    """

    name: str
    url: str
    tier: FeedTier
    source_reliability: float
    default_currency: Optional[str] = None
    default_category: Optional[str] = None
    currency_hints: dict[str, list[str]] = field(default_factory=dict)
    summary_quality: SummaryQuality = SummaryQuality.PARTIAL
    requires_enrichment: bool = False
    enabled: bool = True
    notes: str = ""
    custom_timeout: Optional[int] = None
    # اگر True، آیتم فقط در صورت داشتن حداقل یک کلمه ماکرو در title/summary پذیرفته می‌شود
    # (برای فیدهای aggregator مثل Google News که false positive تولید می‌کنند)
    require_macro_keyword: bool = False


# ============================================================================
# Macro content keywords (فیلتر نویز برای فیدهای aggregator مثل Google News)
# ============================================================================
# اگر فید require_macro_keyword=True داشته باشد و هیچ‌کدام از این کلمات در
# title/summary آیتم نباشد، آیتم حذف می‌شود. دلیل: کوئری‌های گوگل false positive
# تولید می‌کنند (مثلاً ECB = External Commercial Borrowing یا England Cricket Board).
MACRO_CONTENT_KEYWORDS: list[str] = [
    # سیاست پولی و داده‌های ماکرو
    "interest rate", "rate cut", "rate hike", "rate decision", "monetary policy",
    "central bank", "inflation", "deflation", "cpi", "ppi", "pce", "gdp",
    "unemployment", "nonfarm", "jobs report", "payrolls", "retail sales",
    "fomc", "quantitative easing", "taper", "dovish", "hawkish",
    "governing council", "policy meeting", "bond yield", "yields", "treasury",
    "recession", "pmi", "consumer confidence",
    # بازار ارز
    "forex", "exchange rate", "eurozone", "euro area", "dollar index",
    "greenback", "safe haven",
    # مقامات کلیدی بانک‌های مرکزی
    "lagarde", "powell", "ueda", "bailey", "macklem", "bullock",
    # فلزات و انرژی
    "bullion", "ounce", "xau", "precious metal", "spot gold", "gold futures",
    "gold price", "crude", "brent", "wti", "opec", "oil price", "barrel",
]


# ============================================================================
# Currency hint shortcuts (برای multi-currency feeds)
# ============================================================================

DEFAULT_FX_CURRENCY_HINTS: dict[str, list[str]] = {
    # USD — کلمات قوی + Fed/officials
    "USD": [
        "fed ",
        "fomc",
        "powell",
        "yellen",
        "treasury",
        "dollar",
        "dxy",
        "greenback",
        "fed's",
        "federal reserve",
    ],
    # EUR — ECB + officials + countries
    "EUR": [
        "ecb",
        "lagarde",
        "euro ",
        "eurozone",
        "euro area",
        "german",
        "germany",
        "france ",
        "french",
        "italy ",
        "italian",
        "spain ",
        "spanish",
        "bundesbank",
        "nagel",
        "schnabel",
        "wunsch",
    ],
    # GBP
    "GBP": [
        "boe ",
        "bailey",
        "sterling",
        "pound",
        "gilt",
        "uk ",
        "britain",
        "british",
        "bank of england",
    ],
    # JPY
    "JPY": [
        "boj",
        "ueda",
        "yen ",
        "japan",
        "japanese",
        "tokyo",
        "bank of japan",
    ],
    # AUD
    "AUD": [
        "rba",
        "aussie",
        "australia",
        "australian",
        "reserve bank of australia",
    ],
    # NZD
    "NZD": [
        "rbnz",
        "kiwi",
        "new zealand",
    ],
    # CAD
    "CAD": [
        "boc ",
        "macklem",
        "loonie",
        "canada",
        "canadian",
        "bank of canada",
    ],
    # CHF
    "CHF": [
        "snb",
        "franc",
        "switzerland",
        "swiss",
        "swiss national bank",
    ],
    # CNY
    "CNY": [
        "pboc",
        "yuan",
        "china",
        "chinese",
        "renminbi",
        "people's bank of china",
    ],
    # XAU
    "XAU": [
        "gold ",
        "bullion",
        "xau",
        "precious metal",
        "ounce",
    ],
    # OIL
    "OIL": [
        "oil ",
        "crude",
        "wti",
        "brent",
        "opec",
        "barrel",
    ],
}


# ============================================================================
# Tier 1 — Editorial (هسته اصلی)
# ============================================================================

TIER_1_FEEDS: list[FeedConfig] = [
    FeedConfig(
        name="ForexLive",
        url="https://www.forexlive.com/feed/",
        tier=FeedTier.TIER_1_EDITORIAL,
        source_reliability=0.85,
        default_currency=None,
        default_category=None,
        currency_hints=DEFAULT_FX_CURRENCY_HINTS,
        summary_quality=SummaryQuality.FULL,
        requires_enrichment=False,
        notes="Real-time FX/macro editorial. Multi-currency.",
    ),
    FeedConfig(
        name="DailyFX",
        url="https://www.dailyfx.com/feeds/all",
        tier=FeedTier.TIER_1_EDITORIAL,
        source_reliability=0.78,
        default_currency=None,
        default_category=None,
        currency_hints=DEFAULT_FX_CURRENCY_HINTS,
        summary_quality=SummaryQuality.PARTIAL,
        enabled=False,
        requires_enrichment=False,
        notes="FX technical + fundamental analysis.",
    ),
    FeedConfig(
        name="Investing.com Forex",
        url="https://www.investing.com/rss/news_1.rss",
        tier=FeedTier.TIER_1_EDITORIAL,
        source_reliability=0.72,
        default_currency=None,
        default_category=None,
        currency_hints=DEFAULT_FX_CURRENCY_HINTS,
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Broad FX coverage; mixed editorial quality.",
    ),
    FeedConfig(
        name="Investing.com Economy",
        url="https://www.investing.com/rss/news_14.rss",
        tier=FeedTier.TIER_1_EDITORIAL,
        source_reliability=0.72,
        default_currency=None,
        default_category=None,
        currency_hints=DEFAULT_FX_CURRENCY_HINTS,
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Macro/economy news.",
    ),
    FeedConfig(
        name="FXStreet",
        url="https://www.fxstreet.com/rss/news",
        tier=FeedTier.TIER_1_EDITORIAL,
        source_reliability=0.80,
        default_currency=None,
        default_category=None,
        currency_hints=DEFAULT_FX_CURRENCY_HINTS,
        summary_quality=SummaryQuality.FULL,
        requires_enrichment=False,
        notes="Real-time FX news, analysis and calendar.",
    ),
    FeedConfig(
        name="MarketWatch",
        url="https://feeds.marketwatch.com/marketwatch/topstories/",
        tier=FeedTier.TIER_1_EDITORIAL,
        source_reliability=0.78,
        default_currency=None,
        default_category=None,
        currency_hints=DEFAULT_FX_CURRENCY_HINTS,
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Broad macroeconomic and financial market news.",
    ),
]


# ============================================================================
# Tier 2 — Central Banks (رسمی، 1.00 reliability)
# ============================================================================

TIER_2_FEEDS: list[FeedConfig] = [
    FeedConfig(
        name="Federal Reserve",
        url="https://www.federalreserve.gov/feeds/press_all.xml",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=1.00,
        default_currency="USD",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Official Fed press releases.",
    ),
    FeedConfig(
        name="ECB",
        url="https://www.ecb.europa.eu/rss/press.html",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=1.00,
        default_currency="EUR",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        custom_timeout=20,
        enabled=False,
        notes="Official ECB press.",
    ),
    FeedConfig(
        name="Bank of England",
        url="https://www.bankofengland.co.uk/rss/news",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=1.00,
        default_currency="GBP",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Official BoE news.",
    ),
    FeedConfig(
        name="Bank of Japan",
        url="https://www.boj.or.jp/en/rss/whatsnew.xml",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=1.00,
        default_currency="JPY",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.HEADLINE_ONLY,
        requires_enrichment=True,
        custom_timeout=20,  # BoJ feed often headline-only
        notes="Official BoJ; headlines need scraping for body.",
    ),
    FeedConfig(
        name="Bank of Canada",
        url="https://www.bankofcanada.ca/feed/",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=1.00,
        default_currency="CAD",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Official BoC news.",
    ),
    FeedConfig(
        name="RBA",
        url="https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=1.00,
        default_currency="AUD",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        enabled=False,
        notes="Official RBA media releases.",
    ),
    FeedConfig(
        name="Reserve Bank of New Zealand",
        url="https://www.rbnz.govt.nz/feeds/news",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=1.00,
        default_currency="NZD",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Official RBNZ news.",
    )
]

# ============================================================================
# Tier 2 alternatives — Google News topic feeds
# Used as fallback for central bank feeds that are unreachable from our IP.
# ============================================================================

TIER_2_GOOGLE_FALLBACKS: list[FeedConfig] = [
    FeedConfig(
        name="Google News ECB",
        url="https://news.google.com/rss/search?q=ECB+OR+Lagarde+OR+Eurozone+when:1d&hl=en-US&gl=US&ceid=US:en",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=0.75,  # کمتر از ECB رسمی چون aggregated است
        default_currency="EUR",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        enabled=True,
        notes="Fallback for ECB feed via Google News.",
        require_macro_keyword=True,
    ),
    FeedConfig(
        name="Google News RBA",
        url="https://news.google.com/rss/search?q=RBA+OR+%22Reserve+Bank+of+Australia%22+when:1d&hl=en-US&gl=US&ceid=US:en",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=0.75,
        default_currency="AUD",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        enabled=True,
        notes="Fallback for RBA feed via Google News.",
        require_macro_keyword=True,
    ),
    FeedConfig(
        name="Google News PBoC",
        url="https://news.google.com/rss/search?q=PBoC+OR+%22People%27s+Bank+of+China%22+when:1d&hl=en-US&gl=US&ceid=US:en",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=0.75,
        default_currency="CNY",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        enabled=True,
        notes="Fallback for PBoC feed via Google News(official site blocked).",
        require_macro_keyword=True,
    ),
    FeedConfig(
        name="Google News SNB",
        url="https://news.google.com/rss/search?q=SNB+OR+%22Swiss+National+Bank%22+when:1d&hl=en-US&gl=US&ceid=US:en",
        tier=FeedTier.TIER_2_OFFICIAL,
        source_reliability=0.75,
        default_currency="CHF",
        default_category="Central Bank Commentary",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        enabled=True,
        notes="Fallback for SNB feed via Google News(official site broken).",
        require_macro_keyword=True,
    )
]


# ============================================================================
# Tier 3 — Commodities (Gold, Oil and...)
# ============================================================================


TIER_3_FEEDS: list[FeedConfig] = [
    FeedConfig(
        name="Google News Gold",
        url="https://news.google.com/rss/search?q=Gold+prices+OR+%22Gold+market%22+OR+Bullion+when:1d&hl=en-US&gl=US&ceid=US:en",
        tier=FeedTier.TIER_3_COMMODITIES,
        source_reliability=0.85,
        default_currency="XAU",
        default_category="Commodities",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Gold news via Google News (Kitco blocks scrapers).",
        require_macro_keyword=True,
    ),
    FeedConfig(
        name="Google News Oil",
        url="https://news.google.com/rss/search?q=Oil+prices+OR+Crude+OR+WTI+OR+Brent+when:1d&hl=en-US&gl=US&ceid=US:en",
        tier=FeedTier.TIER_3_COMMODITIES,
        source_reliability=0.80,
        default_currency="OIL",
        default_category="Commodities",
        currency_hints={},
        summary_quality=SummaryQuality.PARTIAL,
        requires_enrichment=False,
        notes="Oil market news via Google News (OilPrice feed broken).",
        require_macro_keyword=True,
    )
]


# ============================================================================
# Aggregate registry
# ============================================================================

ALL_FEEDS: list[FeedConfig] = TIER_1_FEEDS + TIER_2_FEEDS + TIER_2_GOOGLE_FALLBACKS + TIER_3_FEEDS


# ============================================================================
# Helpers
# ============================================================================


def get_feeds_by_tier(tier: FeedTier) -> list[FeedConfig]:
    """تمام feedهای یک tier را برمی‌گرداند (فقط enabledها)."""
    return [f for f in ALL_FEEDS if f.tier == tier and f.enabled]


def get_enabled_feeds() -> list[FeedConfig]:
    """تمام feedهای فعال."""
    return [f for f in ALL_FEEDS if f.enabled]


def get_feed_by_name(name: str) -> Optional[FeedConfig]:
    """یک feed را بر اساس name پیدا می‌کند."""
    for feed in ALL_FEEDS:
        if feed.name.lower() == name.lower():
            return feed
    return None


def list_feed_names() -> list[str]:
    """لیست نام تمام feedهای فعال."""
    return [f.name for f in get_enabled_feeds()]


if __name__ == "__main__":
    print(f"Total feeds registered: {len(ALL_FEEDS)}")
    print(f"Enabled feeds         : {len(get_enabled_feeds())}")
    print()
    for tier in FeedTier:
        feeds = get_feeds_by_tier(tier)
        if feeds:
            print(f"  {tier.value}: {len(feeds)} feeds")
            for f in feeds:
                print(
                    f"    - {f.name:<30} reliability={f.source_reliability}  url={f.url}"
                )
