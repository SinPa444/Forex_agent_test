#!/usr/bin/env python3
"""
e2e_news_pipeline.py
====================

End-to-end validation pipeline for RSS news analysis.

Mirrors e2e_pipeline.py architecture but for the news analysis path:
  RSS feeds → NewsItems → filter+route → MarketContext → LLM → NewsSignal → DB

Reuses:
  - rss_feed_loader.FeedLoader   : loads and normalizes RSS feeds
  - rss_feed_config              : feed registry with metadata
  - data_fetcher.fetch_market_context : quantitative enrichment
  - nlp_news.analyze_news_item   : LLM analysis + persistence
  - routing                      : shared currency→ticker mapping
  - database.NewsSignalDB        : dedup query source

Designed to be trivially convertible into a LangGraph pipeline in Phase 2.

Usage:
    # Basic run with defaults (OpenRouter, USD/EUR/GBP/JPY, 5 items)
    python e2e_news_pipeline.py

    # Dry-run: load and filter but skip LLM
    python e2e_news_pipeline.py --dry-run -v

    # Use Arvan LLM (requires ARVAN_BASE_URL + ARVAN_API_KEY env vars)
    python e2e_news_pipeline.py --llm-provider arvan --llm-model GLM-5.2

    # Only tier-1 editorial feeds
    python e2e_news_pipeline.py --feed-tier tier_1_editorial

    # Skip persistence (for testing)
    python e2e_news_pipeline.py --no-persist
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
import json
import traceback
from dataclasses import dataclass, field, is_dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

# Load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed; fall back to shell environment only
    pass

from core.database import NewsSignalDB, SessionLocal, session_scope, RawNewsItemDB, init_db, insert_raw_news_item_if_new
from agents.fundamental.data_fetcher import fetch_market_context
from core.models import MarketContext, NewsItem, NewsSignal
from agents.fundamental.nlp_news import analyze_news_digest
from core.routing import (
    AssetRoute,
    RouteAlignment,
    alignment_label,
    direction_label,
    is_currency_supported,
    resolve_asset_route,
    translate_instrument_direction,
)
from ingestion.rss_feed_config import (
    FeedTier,
    get_enabled_feeds,
    get_feeds_by_tier,
)
from ingestion.rss_feed_loader import FeedLoader, FeedLoadResult


logger = logging.getLogger("e2e_news_pipeline")


# Hash creator for deduplication
def compute_dedup_hash(title: str, source: str, link: Optional[str]) -> str:
    basis = (link or "") + "|" + title + "|" + source
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


# ============================================================================
# Logging setup
# ============================================================================

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


# ============================================================================
# Enums
# ============================================================================

class LLMProvider(str, Enum):
    """LLM provider options for news analysis."""
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    ARVAN = "arvan"


class FeedTierChoice(str, Enum):
    """Feed tier selection for CLI."""
    ALL = "all"
    TIER_1 = "tier_1_editorial"
    TIER_2 = "tier_2_official"


# ============================================================================
# Config
# ============================================================================

@dataclass
class NewsPipelineConfig:
    """
    Immutable configuration for a single pipeline run.

    All CLI flags are captured here. Passed through the pipeline for
    consistency and auditability.
    """

    # ── Filtering ──
    currencies: list[str] = field(
        default_factory=lambda: ["USD", "EUR", "GBP", "JPY", "XAU", "OIL"]
    )
    min_reliability: float = 0.70
    min_summary_length: int = 50
    max_items: int = 5  # سقف per-currency است (نه سراسری) — هر ارز سهمیه مستقل دارد
    feed_tier: FeedTierChoice = FeedTierChoice.ALL

    # ── Deduplication ──
    enable_db_dedup: bool = True

    # ── Persistence ──
    persist_signals: bool = True

    # ── Execution ──
    dry_run: bool = False
    continue_on_error: bool = True
    verbose: bool = False

    # ── LLM ──
    llm_provider: LLMProvider = LLMProvider.ARVAN
    llm_model: str = ""
    temperature: float = 0.1

    # ── Provider-specific ──
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key_env: str = "OPENROUTER_API_KEY"
    ollama_base_url: str = "http://localhost:11434"
    arvan_base_url_env: str = "ARVAN_BASE_URL"
    arvan_api_key_env: str = "ARVAN_API_KEY"

    # ── RSS enrichment ──
    enable_feed_enrichment: bool = False
    
    # ── Audit ──
    audit_dir: str = "runs"

    def __post_init__(self) -> None:
        self.currencies = [c.strip().upper() for c in self.currencies if c.strip()]

        if not (0.0 <= self.min_reliability <= 1.0):
            raise ValueError(
                f"min_reliability must be in [0.0, 1.0]; got {self.min_reliability}"
            )
        if self.min_summary_length < 0:
            raise ValueError("min_summary_length cannot be negative")
        if self.max_items < 1:
            raise ValueError("max_items must be at least 1")

        if self.dry_run:
            self.persist_signals = False


# ============================================================================
# Result dataclasses
# ============================================================================

@dataclass
class SkipRecord:
    """A single skip event with stage, item, and reason."""
    stage: str
    source: str
    title: str
    currency: Optional[str]
    reason: str


@dataclass
class FailureRecord:
    """A single failure event with stage, item, error, and traceback."""
    stage: str
    source: str
    title: str
    currency: Optional[str]
    error: str
    traceback_text: str


@dataclass
class RoutedNewsItem:
    """A NewsItem that has passed filtering and been routed to a ticker."""
    news_item: NewsItem
    route: AssetRoute


@dataclass
class NewsPipelineRunResult:
    """
    Complete state of one pipeline run.

    Contains all inputs, intermediate results, and outputs for auditability
    and reporting.
    """

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None

    # ── Stage 1: Load ──
    feed_load_result: Optional[FeedLoadResult] = None

    # ── Stage 2: Filter + Route ──
    routed_items: list[RoutedNewsItem] = field(default_factory=list)

    # ── Stage 3: Enrich ──
    # گروه‌های ارزی غنی‌شده با MarketContext (تعریف EnrichedCurrencyGroup در بخش Stage 3 است)
    enriched_items: list[EnrichedCurrencyGroup] = field(default_factory=list)

    # ── Stage 4: Analyze ──
    # تاپل‌های (سیگنال تجمیعی، گروه ارزی)
    analyzed_items: list[tuple[NewsSignal, EnrichedCurrencyGroup]] = field(default_factory=list)

    # ── Tracking ──
    skipped: list[SkipRecord] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)

    fatal_error: Optional[str] = None

    def finalize(self) -> None:
        """Mark the run as finished."""
        self.finished_at = datetime.now(timezone.utc)

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    @property
    def loaded_items_count(self) -> int:
        if self.feed_load_result is None:
            return 0
        return len(self.feed_load_result.items)

    @property
    def routed_items_count(self) -> int:
        return len(self.routed_items)

    @property
    def enriched_items_count(self) -> int:
        return len(self.enriched_items)

    @property
    def analyzed_items_count(self) -> int:
        return len(self.analyzed_items)

    @property
    def persisted_signals_count(self) -> int:
        return len(self.analyzed_items)

    @property
    def tradable_signals_count(self) -> int:
        return sum(1 for signal, _group in self.analyzed_items if signal.is_tradable)

    def add_skip(
        self,
        stage: str,
        source: str,
        title: str,
        currency: Optional[str],
        reason: str,
    ) -> None:
        self.skipped.append(
            SkipRecord(
                stage=stage,
                source=source,
                title=title[:120],  # truncate long titles
                currency=currency,
                reason=reason,
            )
        )

    def add_failure(
        self,
        stage: str,
        source: str,
        title: str,
        currency: Optional[str],
        exc: Exception,
    ) -> None:
        self.failures.append(
            FailureRecord(
                stage=stage,
                source=source,
                title=title[:120],
                currency=currency,
                error=str(exc),
                traceback_text=traceback.format_exc(),
            )
        )


# ============================================================================
# Helpers
# ============================================================================

def configure_logging(verbose: bool) -> None:
    """Configure root logger and quiet noisy third-party loggers."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
    )
    # Quiet noisy libraries even in verbose mode
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("yfinance").setLevel(logging.INFO)
    logging.getLogger("peewee").setLevel(logging.WARNING)
    

def _audit_default(obj):
    """تبدیل آبجکت‌های پیچیده به فرمت JSON برای Audit Artifact."""
    if is_dataclass(obj):
        # استفاده از asdict برای دیتاکلاس‌ها (خودش بازگشتی کار می‌کند)
        return asdict(obj)
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, tuple):
        return list(obj)
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

def save_audit_artifact(config: NewsPipelineConfig, result: NewsPipelineRunResult) -> str:
    """ذخیره خروجی کامل اجرای پایپ‌لاین در یک فایل JSON."""
    audit_dir = Path(config.audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = result.started_at.strftime("%Y%m%d_%H%M%S")
    filename = f"news_pipeline_{timestamp}.json"
    filepath = audit_dir / filename
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, default=_audit_default, indent=2, ensure_ascii=False)
        logger.info(f"Audit artifact saved to {filepath}")
        return str(filepath)
    except Exception as exc:
        logger.error(f"Failed to save audit artifact: {exc}")
        return ""


def log_section(title: str) -> None:
    """Print a highlighted section header in logs."""
    logger.info("═" * 76)
    logger.info(title)
    logger.info("═" * 76)


def build_llm(config: NewsPipelineConfig):
    """
    Construct a configured LLM based on provider selection.

    Returns a LangChain BaseChatModel instance ready for structured output.
    """
    if config.llm_provider == LLMProvider.OLLAMA:
        from langchain_ollama import ChatOllama

        model_name = config.llm_model or "0xroyce/plutus"
        logger.info(
            "LLM provider: Ollama | model=%s | base_url=%s",
            model_name,
            config.ollama_base_url,
        )
        return ChatOllama(
            model=model_name,
            temperature=config.temperature,
            base_url=config.ollama_base_url,
        )

    if config.llm_provider == LLMProvider.OPENROUTER:
        from langchain_openai import ChatOpenAI

        model_name = config.llm_model or "openai/gpt-4o-mini"
        api_key = os.getenv(config.openrouter_api_key_env) or os.getenv("OPENAI_API_KEY")
        
        # Allow base URL override from env
        base_url = os.getenv("OPENROUTER_BASE_URL", config.openrouter_base_url)
        if not api_key:
            raise ValueError(
                f"OpenRouter API key not found. Set {config.openrouter_api_key_env} "
                f"or OPENAI_API_KEY environment variable."
            )
        logger.info(
            "LLM provider: OpenRouter | model=%s | base_url=%s | api_env=%s",
            model_name,
            config.openrouter_base_url,
            config.openrouter_api_key_env,
        )
        return ChatOpenAI(
            model=model_name,
            temperature=config.temperature,
            api_key=api_key,
            base_url=base_url,
        )

    if config.llm_provider == LLMProvider.ARVAN:
        from langchain_openai import ChatOpenAI

        model_name = config.llm_model or "GLM-5.2"
        base_url = os.getenv(config.arvan_base_url_env)
        api_key = os.getenv(config.arvan_api_key_env, "not-needed")

        if not base_url:
            raise ValueError(
                f"Arvan base URL not found. Set {config.arvan_base_url_env} "
                f"environment variable to the full endpoint URL."
            )

        logger.info(
            "LLM provider: Arvan | model=%s | base_url_env=%s | api_key_env=%s",
            model_name,
            config.arvan_base_url_env,
            config.arvan_api_key_env,
        )
        return ChatOpenAI(
            model=model_name,
            temperature=config.temperature,
            api_key=api_key,
            base_url=base_url,
        )

    raise ValueError(f"Unknown LLM provider: {config.llm_provider}")


def compute_dedup_key(news_item: NewsItem) -> str:
    """
    Compute a stable dedup key for a NewsItem.

    Priority: link → title+source hash.
    This must match the query logic in `is_already_persisted()`.
    """
    if news_item.link:
        return news_item.link.strip().lower()
    # Fallback: hash of title+source (case-insensitive)
    basis = f"{news_item.title.strip().lower()}|{news_item.source.strip().lower()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _news_recency_key(item: NewsItem) -> tuple[float, float]:
    """
    کلید مرتب‌سازی اخبار: (زمان انتشار، reliability منبع).
    برای اینکه سقف max_items به نفع تازه‌ترین/معتبرترین اخبار اعمال شود.
    """
    ts = 0.0
    if item.published is not None:
        try:
            ts = item.published.timestamp()
        except Exception:
            ts = 0.0
    reliability = item.source_reliability if item.source_reliability is not None else 0.0
    return (ts, reliability)


def is_already_persisted(news_item: NewsItem) -> bool:
    """
    Check if a NewsItem is already saved in news_signals table.

    Query strategy:
      - If link exists: match by link
      - Otherwise: match by (title + source) exact combination

    Returns True if duplicate found, False otherwise.
    On DB error, returns False (fail-open — better to re-analyze than to skip).
    """
    session = SessionLocal()
    try:
        query = session.query(NewsSignalDB)

        if news_item.link:
            query = query.filter(NewsSignalDB.link == news_item.link)
        else:
            query = query.filter(
                NewsSignalDB.title == news_item.title,
                NewsSignalDB.source == news_item.source,
            )

        exists = session.query(query.exists()).scalar()
        return bool(exists)

    except Exception as exc:
        logger.warning(
            "DB dedup check failed for %r: %s (assuming not-duplicate)",
            news_item.title[:60],
            exc,
        )
        return False
    finally:
        session.close()


# ============================================================================
# Stage 1: Load feeds
# ============================================================================

def stage_load_feeds(
    config: NewsPipelineConfig,
    result: NewsPipelineRunResult,
) -> list[NewsItem]:
    """
    بارگذاری RSS feeds و ذخیره اخبار جدید در دیتابیس خام.
    این مرحله مستقل از LLM است و می‌تواند با VPN روشن اجرا شود.
    """
    log_section("مرحله ۱: بارگذاری RSS feeds و ذخیره در DB")

    loader = FeedLoader(enable_enrichment=config.enable_feed_enrichment)

    if config.feed_tier == FeedTierChoice.ALL:
        logger.info("Loading all enabled feeds")
        feed_result = loader.load_all_collect()
    else:
        tier_enum = FeedTier(config.feed_tier.value)
        tier_feeds = get_feeds_by_tier(tier_enum)
        logger.info("Loading tier=%s (%d feeds)", config.feed_tier.value, len(tier_feeds))
        loader._collected_items = []
        stats_result = loader.load_feeds(tier_feeds)
        stats_result.items = loader._collected_items
        feed_result = stats_result

    result.feed_load_result = feed_result

    logger.info(
        "Feed load complete: %d items from %d feeds (errors=%d)",
        len(feed_result.items), len(feed_result.stats), feed_result.total_errors,
    )

    # ذخیره در دیتابیس خام با استفاده از ابزارهای جدید database.py
    new_items_count = 0
    dup_items_count = 0
    
    with session_scope() as session:
        for item in feed_result.items:
            h = compute_dedup_hash(item.title, item.source, item.link)
            
            fields = {
                "title": item.title,
                "summary": item.summary,
                "link": item.link,
                "source": item.source,
                "published_at": item.published,
                "currency": item.currency,
                "source_reliability": item.source_reliability,
                "category": item.category,
                "impact": item.impact
            }
            
            # استفاده از تابع یکپارچه Idempotent
            _, created = insert_raw_news_item_if_new(session, dedup_hash=h, **fields)
            if created:
                new_items_count += 1
            else:
                dup_items_count += 1

    logger.info("Raw news DB save: %d new items, %d duplicates skipped", new_items_count, dup_items_count)

    return feed_result.items


# ============================================================================
# Stage 2: Dedup + Filter + Route
# ============================================================================

def stage_dedup_filter_route(
    news_items: list[NewsItem],
    config: NewsPipelineConfig,
    result: NewsPipelineRunResult,
) -> list[RoutedNewsItem]:
    """
    Apply DB dedup, filters, and routing.

    Pre-step: sort by recency (reliability tie-breaker) so the capacity cap
    keeps the freshest/most reliable items.

    Filter order (fail-fast):
      1. DB dedup (if enabled)
      2. Currency detected?
      3. Currency in whitelist?
      4. Reliability >= threshold?
      5. Summary length >= min?
      6. Per-currency capacity reached? (max_items per currency, not global)
      7. Route configured?
    """
    log_section("Stage 2: Dedup + Filter + Route")

    # مرتب‌سازی بر اساس تازگی (tie-breaker: reliability) قبل از حلقه فیلتر،
    # تا سقف ظرفیت max_items تازه‌ترین/معتبرترین اخبار را نگه دارد نه صرفاً اولین‌های لودشده
    news_items = sorted(news_items, key=_news_recency_key, reverse=True)

    routed: list[RoutedNewsItem] = []
    per_currency_count: dict[str, int] = {}

    for item in news_items:
        # DB dedup
        if config.enable_db_dedup and is_already_persisted(item):
            result.add_skip(
                "dedup_db",
                item.source,
                item.title,
                item.currency,
                "already_in_news_signals",
            )
            continue

        # Currency present
        if not item.currency:
            result.add_skip(
                "filter",
                item.source,
                item.title,
                None,
                "no_currency_detected",
            )
            continue

        # Currency in whitelist
        if item.currency.upper() not in config.currencies:
            result.add_skip(
                "filter",
                item.source,
                item.title,
                item.currency,
                f"currency_not_whitelisted({item.currency})",
            )
            continue

        # Reliability threshold
        reliability = item.source_reliability if item.source_reliability is not None else 0.65
        if reliability < config.min_reliability:
            result.add_skip(
                "filter",
                item.source,
                item.title,
                item.currency,
                f"reliability_below_threshold({reliability:.2f}<{config.min_reliability:.2f})",
            )
            continue

        # Summary length
        summary_len = len(item.summary or "")
        if summary_len < config.min_summary_length:
            result.add_skip(
                "filter",
                item.source,
                item.title,
                item.currency,
                f"summary_too_short({summary_len}<{config.min_summary_length})",
            )
            continue

        # Per-currency capacity cap (به‌جای سقف سراسری — هر ارز سهمیه مستقل دارد
        # تا فیدهای پرتکرار مثل Google News کل ظرفیت را نبلعند)
        ccy = item.currency.upper()
        if per_currency_count.get(ccy, 0) >= config.max_items:
            result.add_skip(
                "capacity",
                item.source,
                item.title,
                item.currency,
                f"per_currency_cap_reached({ccy}>={config.max_items})",
            )
            continue

        # Route
        route = resolve_asset_route(item.currency)
        if route is None:
            result.add_skip(
                "routing",
                item.source,
                item.title,
                item.currency,
                f"no_route_for_currency({item.currency})",
            )
            continue

        routed.append(RoutedNewsItem(news_item=item, route=route))
        per_currency_count[ccy] = per_currency_count.get(ccy, 0) + 1

    result.routed_items = routed

    logger.info(
        "After dedup+filter+route: %d items (from %d loaded)",
        len(routed),
        len(news_items),
    )
    if routed:
        for idx, r in enumerate(routed, 1):
            logger.info(
                "[%d/%d] %s | %s | reliability=%.2f | %s",
                idx,
                len(routed),
                r.news_item.currency,
                r.route.ticker,
                r.news_item.source_reliability or 0.0,
                r.news_item.title[:80],
            )

    return routed


# ============================================================================
# Stage 3: Enrich (MarketContext)
# ============================================================================


@dataclass
class EnrichedCurrencyGroup:
    """گروهی از اخبار یک ارز به همراه Context آن ارز."""
    currency: str
    route: AssetRoute
    news_items: list[NewsItem]
    market_context: MarketContext


def stage_group_and_enrich(
    routed_items: list[RoutedNewsItem],
    config: NewsPipelineConfig,
    result: NewsPipelineRunResult,
) -> list[EnrichedCurrencyGroup]:
    """
    گروه‌بندی اخبار بر اساس ارز و ساخت یک MarketContext برای هر ارز.
    """
    log_section("مرحله ۳: گروه‌بندی ارزی و ساخت MarketContext")

    # 1. گروه‌بندی بر اساس currency
    groups_map: dict[str, list[RoutedNewsItem]] = {}
    for item in routed_items:
        ccy = item.news_item.currency
        if ccy not in groups_map:
            groups_map[ccy] = []
        groups_map[ccy].append(item)

    enriched_groups: list[EnrichedCurrencyGroup] = []

    for ccy, items in groups_map.items():
        # استفاده از route اولین آیتم (چون همه یک ارز هستند route یکسانی دارند)
        route = items[0].route
        
        logger.info(
            "Grouping %s: %d items | ticker=%s",
            ccy, len(items), route.ticker
        )

        try:
            # ساخت یک Context برای کل گروه (ارز)
            # از عنوان اولین خبر به عنوان event_title استفاده می‌کنیم صرفا برای رفرنس
            context = fetch_market_context(
                ticker=route.ticker,
                currency=route.market_context_currency,
                event_title=f"Macro News Digest for {ccy}",
                actual=None,
                forecast=None,
                previous=None,
                event_currency=ccy,
            )

            enriched_groups.append(
                EnrichedCurrencyGroup(
                    currency=ccy,
                    route=route,
                    news_items=[i.news_item for i in items],
                    market_context=context,
                )
            )

            logger.info(
                "  context ok for %s | vol=%.3f | items=%d",
                ccy, context.calculated_volatility, len(items)
            )

        except Exception as exc:
            logger.warning("MarketContext build failed for group %s: %s", ccy, exc)
            result.add_failure("enrich_group", "Aggregated", ccy, ccy, exc)
            if not config.continue_on_error:
                raise

    result.enriched_items = enriched_groups  # ذخیره در همان فیلد برای گزارش
    logger.info("MarketContext success for %d currency groups", len(enriched_groups))
    return enriched_groups


# ============================================================================
# Stage 4: Analyze + Persist
# ============================================================================
def stage_analyze(
    enriched_groups: list[EnrichedCurrencyGroup],
    config: NewsPipelineConfig,
    result: NewsPipelineRunResult,
) -> list[tuple[NewsSignal, EnrichedCurrencyGroup]]:
    """
    فراخوانی Digest Analysis به ازای هر گروه ارزی.
    """
    log_section("مرحله ۴: تحلیل LLM تجمیعی + persistence")

    if config.dry_run:
        logger.info("Dry-run mode — skipping LLM and persistence.")
        return []

    llm = build_llm(config)
    analyzed_groups: list[tuple[NewsSignal, EnrichedCurrencyGroup]] = []

    for idx, group in enumerate(enriched_groups, 1):
        logger.info(
            "[%d/%d] Analyze Digest | %s | items=%d | ticker=%s",
            idx, len(enriched_groups), group.currency, len(group.news_items), group.route.ticker
        )

        t0 = time.perf_counter()
        try:
            interpretation, signal = analyze_news_digest(
                currency=group.currency,
                news_items=group.news_items,
                market_context=group.market_context,
                ticker=group.route.ticker,
                llm=llm,
                persist=config.persist_signals,
            )
            elapsed = time.perf_counter() - t0

            analyzed_groups.append((signal, group))

            translated_dir = translate_instrument_direction(
                signal.direction, group.route.alignment
            )

            logger.info(
                "  digest ok | native=%s | instrument=%s | score=%+.4f | conf=%.4f | "
                "tradable=%s | half_life=%dm | %.2fs",
                direction_label(signal.direction),
                direction_label(translated_dir),
                signal.final_score,
                signal.confidence,
                signal.is_tradable,
                signal.signal_half_life_mins,
                elapsed,
            )

        except Exception as exc:
            logger.error("Digest analysis failed for %s: %s", group.currency, exc)
            result.add_failure("analyze_digest", "Aggregated", group.currency, group.currency, exc)
            if not config.continue_on_error:
                raise

    # ذخیره تاپل‌های (signal, group) برای گزارش نهایی
    result.analyzed_items = analyzed_groups
    logger.info("Aggregated signals produced: %d", len(analyzed_groups))
    return analyzed_groups


# ============================================================================
# Stage 5: Report
# ============================================================================

def print_report(config: NewsPipelineConfig, result: NewsPipelineRunResult) -> None:
    """
    Print a structured summary report of the pipeline run.
    """
    result.finalize()

    print()
    print("=" * 108)
    print("E2E NEWS PIPELINE REPORT")
    print("=" * 108)
    print(f"Started At           : {result.started_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Finished At          : {result.finished_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Duration Seconds     : {result.duration_seconds:.2f}")
    print(f"Feed Tier            : {config.feed_tier.value}")
    print(f"LLM Provider         : {config.llm_provider.value}")
    print(f"LLM Model            : {config.llm_model or '(default)'}")
    print("-" * 108)

    # Feed load summary
    if result.feed_load_result is not None:
        fr = result.feed_load_result
        print("Feed Load Summary")
        print(f"  Total feeds        : {len(fr.stats)}")
        print(f"  Total items        : {fr.total_normalized}")
        print(f"  Duplicates (loader): {fr.total_duplicates}")
        print(f"  Load errors        : {fr.total_errors}")
        for s in fr.stats:
            err_part = f"  errors={len(s.errors)}" if s.errors else ""
            print(
                f"  {s.feed_name:<32}  fetched={s.fetched_entries:>4}  "
                f"items={s.normalized_items:>4}  dup={s.duplicates_skipped:>3}{err_part}"
            )
        print("-" * 108)

    # Pipeline summary
    print("Pipeline Summary")
    print(f"  Loaded Items       : {result.loaded_items_count}")
    print(f"  Routed Items       : {result.routed_items_count}")
    print(f"  Enriched Contexts  : {result.enriched_items_count}")
    print(f"  Signals Produced   : {result.analyzed_items_count}")
    print(
        f"  Signals Persisted  : "
        f"{result.persisted_signals_count if config.persist_signals and not config.dry_run else 0}"
    )
    print(f"  Tradable Signals   : {result.tradable_signals_count}")
    print(f"  Skipped Items      : {len(result.skipped)}")
    print(f"  Failures           : {len(result.failures)}")
    print("-" * 108)

        # Signals table
    if result.analyzed_items:
        print("Generated Signals")
        print(
            f"{'CCY':<5} {'Ticker':<12} {'Align':<8} {'Native':<8} {'Instr.':<8} "
            f"{'Score':>8} {'Conf':>8} {'Trade':>7} {'Half':>6}  {'Type':<15}  Context"
        )
        print("-" * 108)
        
        # analyzed_items شامل تاپل‌های (signal, group) است
        for signal, group in result.analyzed_items:
            route = group.route
            ccy = group.currency
            type_str = f"DIGEST({len(group.news_items)})"
            context_str = signal.reasoning[:60]

            native = direction_label(signal.direction)
            instr = direction_label(
                translate_instrument_direction(signal.direction, route.alignment)
            )
            print(
                f"{ccy:<5} "
                f"{route.ticker:<12} "
                f"{alignment_label(route.alignment):<8} "
                f"{native:<8} "
                f"{instr:<8} "
                f"{signal.final_score:>+8.3f} "
                f"{signal.confidence:>8.3f} "
                f"{str(signal.is_tradable):>7} "
                f"{signal.signal_half_life_mins:>6}  "
                f"{type_str:<15}  "
                f"{context_str}"
            )
        print("-" * 108)
        print("* Native  = currency-native direction (as persisted in DB)")
        print("* Instr.  = translated to instrument view (for display only)")
        print("* DIGEST(N) shows the number of news items aggregated into this signal")
        print("-" * 108)

    # Skipped
    if result.skipped:
        print(f"Skipped Items (first 15 of {len(result.skipped)})")
        for s in result.skipped[:15]:
            currency_str = s.currency or "?"
            print(
                f"  - [{s.stage}] {s.source} / {currency_str} / "
                f"{s.title[:60]}: {s.reason}"
            )
        if len(result.skipped) > 15:
            print(f"  ... and {len(result.skipped) - 15} more")
        print("-" * 108)

    # Failures
    if result.failures:
        print(f"Failures ({len(result.failures)})")
        for f in result.failures[:10]:
            currency_str = f.currency or "?"
            print(
                f"  - [{f.stage}] {f.source} / {currency_str} / "
                f"{f.title[:60]}: {f.error}"
            )
            if config.verbose:
                print(f.traceback_text)
        if len(result.failures) > 10:
            print(f"  ... and {len(result.failures) - 10} more")
        print("-" * 108)

    if result.fatal_error:
        print(f"Fatal Error: {result.fatal_error}")
        print("-" * 108)

    # Status
    print("Run Status")
    if result.fatal_error:
        print("  FATAL_FAILURE")
    elif result.analyzed_items:
        if result.failures:
            print("  PARTIAL_SUCCESS")
        else:
            print("  SUCCESS")
    elif result.routed_items:
        print("  COMPLETED_NO_SIGNALS")
    elif result.feed_load_result and result.feed_load_result.items:
        print("  NO_ELIGIBLE_ITEMS")
    else:
        print("  NO_ITEMS_LOADED")
    print("=" * 108)
    print()


# ============================================================================
# Orchestrator
# ============================================================================

def run_pipeline(config: NewsPipelineConfig) -> NewsPipelineRunResult:
    result = NewsPipelineRunResult()
    logger.info("شروع e2e news pipeline")
    
    try:
        init_db()
        logger.info("Database initialized successfully.")

        # Stage 1: Load feeds (and save to DB)
        news_items = stage_load_feeds(config, result)
        
        if not news_items:
            logger.warning("No news items loaded from live feeds. Falling back to raw_news_items DB.")
            with session_scope() as session:
                db_records = session.query(RawNewsItemDB).order_by(
                    RawNewsItemDB.fetched_at.desc()
                ).limit(config.max_items * 5).all()
                
                news_items = [
                    NewsItem(
                        title=r.title,
                        summary=r.summary or "",
                        published=r.published_at,
                        link=r.link,
                        source=r.source,
                        source_reliability=r.source_reliability,
                        category=r.category,
                        currency=r.currency,
                        impact=r.impact
                    ) for r in db_records
                ]
                logger.info(f"Loaded {len(news_items)} items from raw_news_items DB fallback.")

        if not news_items:
            logger.error("No news items available even from DB fallback. Exiting.")
            return result

        # Stage 2: Dedup + filter + route
        routed = stage_dedup_filter_route(news_items, config, result)
        if not routed:
            logger.warning("No items passed dedup+filter+routing.")
            return result

        # Stage 3: Enrich with MarketContext
        enriched_groups = stage_group_and_enrich(routed, config, result)
        if not enriched_groups:
            logger.warning("No MarketContext successfully built for any currency group.")
            return result

        # Stage 4: Analyze + persist
        stage_analyze(enriched_groups, config, result)

        return result

    except KeyboardInterrupt:
        result.fatal_error = "Pipeline interrupted by user."
        return result
    except Exception as exc:
        result.fatal_error = str(exc)
        logger.critical("Fatal pipeline error: %s", exc, exc_info=True)
        return result


# ============================================================================
# CLI
# ============================================================================

def parse_args(argv: Optional[list[str]] = None) -> NewsPipelineConfig:
    """Parse CLI arguments into a NewsPipelineConfig."""
    parser = argparse.ArgumentParser(
        description="End-to-end RSS news analysis pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Filtering ──
    parser.add_argument(
        "--currencies",
        nargs="+",
        default=["USD", "EUR", "GBP", "JPY"],
        help="Whitelist of currencies to analyze",
    )
    parser.add_argument(
        "--min-reliability",
        type=float,
        default=0.70,
        help="Minimum source_reliability threshold (0.0-1.0)",
    )
    parser.add_argument(
        "--min-summary-length",
        type=int,
        default=50,
        help="Minimum summary length in characters",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=10,
        help="Maximum items per currency per run (per-currency cap, not global)",
    )
    parser.add_argument(
        "--feed-tier",
        choices=[t.value for t in FeedTierChoice],
        default=FeedTierChoice.ALL.value,
        help="Which feed tier to load",
    )

    # ── Dedup ──
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable DB-level duplicate detection",
    )

    # ── Persistence ──
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not persist signals to DB",
    )

    # ── Execution ──
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load/filter/enrich only; skip LLM and persistence",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop pipeline on first per-item error",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )

    # ── LLM ──
    parser.add_argument(
        "--llm-provider",
        choices=[p.value for p in LLMProvider],
        default=LLMProvider.ARVAN.value,
        help="LLM provider",
    )
    parser.add_argument(
        "--llm-model",
        default="",
        help="LLM model name (empty = provider default)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="LLM temperature",
    )

    # ── Provider config (rare overrides) ──
    parser.add_argument(
        "--openrouter-base-url",
        default="https://openrouter.ai/api/v1",
        help="OpenRouter base URL",
    )
    parser.add_argument(
        "--openrouter-api-key-env",
        default="OPENROUTER_API_KEY",
        help="Env var name for OpenRouter API key",
    )
    parser.add_argument(
        "--ollama-base-url",
        default="http://localhost:11434",
        help="Ollama base URL",
    )
    parser.add_argument(
        "--arvan-base-url-env",
        default="ARVAN_BASE_URL",
        help="Env var name for Arvan base URL",
    )
    parser.add_argument(
        "--arvan-api-key-env",
        default="ARVAN_API_KEY",
        help="Env var name for Arvan API key",
    )

    # ── RSS enrichment ──
    parser.add_argument(
        "--enable-feed-enrichment",
        action="store_true",
        help="Enable body scraping for headline-only feeds (slower)",
    )

    args, _ = parser.parse_known_args(argv)

    return NewsPipelineConfig(
        currencies=args.currencies,
        min_reliability=args.min_reliability,
        min_summary_length=args.min_summary_length,
        max_items=args.max_items,
        feed_tier=FeedTierChoice(args.feed_tier),
        enable_db_dedup=not args.no_dedup,
        persist_signals=not args.no_persist,
        dry_run=args.dry_run,
        continue_on_error=not args.stop_on_error,
        verbose=args.verbose,
        llm_provider=LLMProvider(args.llm_provider),
        llm_model=args.llm_model,
        temperature=args.temperature,
        openrouter_base_url=args.openrouter_base_url,
        openrouter_api_key_env=args.openrouter_api_key_env,
        ollama_base_url=args.ollama_base_url,
        arvan_base_url_env=args.arvan_base_url_env,
        arvan_api_key_env=args.arvan_api_key_env,
        enable_feed_enrichment=args.enable_feed_enrichment,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point."""
    config = parse_args(argv)
    configure_logging(config.verbose)

    result = run_pipeline(config)
    save_audit_artifact(config, result)
    print_report(config, result)

    if result.fatal_error:
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())