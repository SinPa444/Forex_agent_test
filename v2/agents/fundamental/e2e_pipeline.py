#!/usr/bin/env python3
"""
e2e_pipeline.py
===============

End-to-end validation pipeline for the forex AI event analysis platform.

Modes:
  - live : فقط از Forex Factory
  - db   : فقط از economic_events_history
  - auto : اول live، اگر چیزی نبود fallback به DB

Pipeline:
  source fetch/load
    -> convert to EventAnalysisInput
    -> sanity filter + routing
    -> fetch MarketContext
    -> analyze via nlp_event
    -> persist signals
    -> summary report
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import json
import traceback
from dataclasses import dataclass, field, is_dataclass, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel
from pathlib import Path

from core.database import EventHistoryDB, SessionLocal, init_db
from agents.fundamental.data_fetcher import fetch_market_context
from ingestion.forex_factory_crawler import (
    EventPersistenceResult,
    ForexFactoryEvent,
    ForexFactoryFetchResult,
    fetch_forex_factory_events_debug,
    save_events,
)
from core.models import EventAnalysisInput, EventSignal, MarketContext
from agents.fundamental.nlp_event import (
    analyze_economic_event,
    event_input_from_db_record,
    event_input_from_forex_factory,
)
from core.routing import(
    AssetRoute,
    RouteAlignment,
    CURRENCY_ROUTE_MAP,
    resolve_asset_route,
    translate_instrument_direction,
    direction_label,
    alignment_label,
)

logger = logging.getLogger("e2e_pipeline")


# ============================================================================
# Logging
# ============================================================================

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


# ============================================================================
# Enums / constants
# ============================================================================

class LLMProvider(str, Enum):
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    ARVAN = "arvan"


class SourceMode(str, Enum):
    LIVE = "live"
    DB = "db"
    AUTO = "auto"


IMPACT_RANK: dict[str, int] = {
    "High": 3,
    "Medium": 2,
    "Low": 1,
    "Unknown": 0,
    "Holiday": 0,
    "Non-Economic": 0,
}

# ============================================================================
# Config / result data classes
# ============================================================================

@dataclass
class PipelineConfig:
    # source mode
    source_mode: SourceMode = SourceMode.AUTO

    # filtering
    currencies: list[str] = field(default_factory=lambda: ["USD", "EUR", "GBP"])
    min_impact: str = "High"
    released_only: bool = True
    require_forecast: bool = False
    max_events: int = 10

    # db source options
    db_lookback_days: Optional[int] = 365
    db_candidate_limit: int = 50

    # persistence
    save_raw_events: bool = True
    persist_signals: bool = True

    # execution
    dry_run: bool = False
    continue_on_error: bool = True
    verbose: bool = False

    # llm
    llm_provider: LLMProvider = LLMProvider.OPENROUTER
    llm_model: str = ""
    temperature: float = 0.1

    # providers
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    ollama_base_url: str = "http://localhost:11434"

    # network
    http_timeout: int = 15
    
    # audit
    audit_dir: str = "runs"

    def __post_init__(self) -> None:
        self.currencies = [c.strip().upper() for c in self.currencies if c.strip()]
        self.min_impact = self.min_impact.strip().title()

        if self.max_events < 1:
            raise ValueError("max_events باید حداقل 1 باشد")

        if self.db_candidate_limit < self.max_events:
            self.db_candidate_limit = max(self.max_events * 3, self.max_events)

        if self.dry_run:
            self.persist_signals = False


@dataclass
class SkipRecord:
    stage: str
    event_title: str
    currency: str
    reason: str


@dataclass
class FailureRecord:
    stage: str
    event_title: str
    currency: str
    error: str
    traceback_text: str


@dataclass
class PreparedEvent:
    origin: str                  # live | db
    source_record_id: Optional[int]
    route: AssetRoute
    event_input: EventAnalysisInput


@dataclass
class EnrichedEvent:
    origin: str
    source_record_id: Optional[int]
    route: AssetRoute
    event_input: EventAnalysisInput
    market_context: MarketContext


@dataclass
class AnalyzedEvent:
    origin: str
    source_record_id: Optional[int]
    route: AssetRoute
    event_input: EventAnalysisInput
    signal: EventSignal
    elapsed_seconds: float


@dataclass
class PipelineRunResult:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None

    requested_source_mode: str = "auto"
    effective_source_mode: Optional[str] = None

    # live source metadata
    live_fetch_result: Optional[ForexFactoryFetchResult] = None
    raw_event_persistence: Optional[EventPersistenceResult] = None

    # db source metadata
    db_loaded_count: int = 0
    db_query_filters: dict[str, Any] = field(default_factory=dict)

    prepared_events: list[PreparedEvent] = field(default_factory=list)
    enriched_events: list[EnrichedEvent] = field(default_factory=list)
    analyzed_events: list[AnalyzedEvent] = field(default_factory=list)

    skipped: list[SkipRecord] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)

    fatal_error: Optional[str] = None

    def finalize(self) -> None:
        self.finished_at = datetime.now(timezone.utc)

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    @property
    def prepared_events_count(self) -> int:
        return len(self.prepared_events)

    @property
    def enriched_events_count(self) -> int:
        return len(self.enriched_events)

    @property
    def analyzed_events_count(self) -> int:
        return len(self.analyzed_events)

    @property
    def persisted_signals_count(self) -> int:
        return len(self.analyzed_events)

    @property
    def tradable_signals_count(self) -> int:
        return sum(1 for item in self.analyzed_events if item.signal.is_tradable)

    def add_skip(self, stage: str, title: str, currency: str, reason: str) -> None:
        self.skipped.append(
            SkipRecord(
                stage=stage,
                event_title=title,
                currency=currency,
                reason=reason,
            )
        )

    def add_failure(self, stage: str, event_title: str, currency: str, exc: Exception) -> None:
        self.failures.append(
            FailureRecord(
                stage=stage,
                event_title=event_title,
                currency=currency,
                error=str(exc),
                traceback_text=traceback.format_exc(),
            )
        )


# ============================================================================
# Helpers
# ============================================================================

def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
    )
    
    
def _audit_default(obj):
    """تبدیل آبجکت‌های پیچیده به فرمت JSON برای Audit Artifact."""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

def save_audit_artifact(config: PipelineConfig, result: PipelineRunResult) -> str:
    """ذخیره خروجی کامل اجرای پایپ‌لاین در یک فایل JSON."""
    audit_dir = Path(config.audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = result.started_at.strftime("%Y%m%d_%H%M%S")
    filename = f"event_pipeline_{timestamp}.json"
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
    logger.info("═" * 76)
    logger.info(title)
    logger.info("═" * 76)


def expand_impact_filter(min_impact: str) -> list[str]:
    """
    crawler و DB هر دو با impact equality کار می‌کنند.
    این helper رفتار threshold می‌دهد.
    """
    normalized = min_impact.title()
    if normalized == "High":
        return ["High"]
    if normalized == "Medium":
        return ["High", "Medium"]
    if normalized == "Low":
        return ["High", "Medium", "Low"]
    return ["High"]


def meets_impact_threshold(event_impact: str, min_impact: str) -> bool:
    return IMPACT_RANK.get(event_impact, 0) >= IMPACT_RANK.get(min_impact, 0)


def build_llm(config: PipelineConfig):
    if config.llm_provider == LLMProvider.OLLAMA:
        from langchain_ollama import ChatOllama

        model_name = config.llm_model or "llama3.1:8b"
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

    if config.llm_provider == LLMProvider.ARVAN:
        from langchain_openai import ChatOpenAI

        model_name = config.llm_model or "GLM-5.2"
        base_url = os.getenv("ARVAN_BASE_URL")
        api_key = os.getenv("ARVAN_API_KEY", "not-needed")

        if not base_url:
            raise ValueError(
                "Arvan base URL not found. Set ARVAN_BASE_URL "
                "environment variable to the full endpoint URL."
            )

        logger.info(
            "LLM provider: Arvan | model=%s | base_url=%s",
            model_name,
            base_url,
        )
        return ChatOpenAI(
            model=model_name,
            temperature=config.temperature,
            api_key=api_key,
            base_url=base_url,
        )

    # Default: OpenRouter
    from langchain_openai import ChatOpenAI

    model_name = config.llm_model or "openai/gpt-4o-mini"
    api_key = os.getenv(config.api_key_env) or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            f"کلید API پیدا نشد. متغیر محیطی {config.api_key_env} "
            f"یا OPENAI_API_KEY را تنظیم کنید."
        )

    logger.info(
        "LLM provider: OpenRouter | model=%s | base_url=%s | api_env=%s",
        model_name,
        config.openrouter_base_url,
        config.api_key_env,
    )

    return ChatOpenAI(
        model=model_name,
        temperature=config.temperature,
        api_key=api_key,
        base_url=config.openrouter_base_url,
    )


def validate_and_route_event_input(
    event_input: EventAnalysisInput,
    config: PipelineConfig,
) -> tuple[bool, Optional[str], Optional[AssetRoute]]:
    """
    sanity filter مستقل از منبع داده.
    """
    if config.released_only and event_input.actual is None:
        return False, "missing_actual", None

    if config.require_forecast and event_input.forecast is None:
        return False, "missing_forecast", None

    if not meets_impact_threshold(event_input.impact, config.min_impact):
        return False, f"impact_below_threshold({event_input.impact}<{config.min_impact})", None

    if event_input.currency not in config.currencies:
        return False, "currency_not_whitelisted", None

    route = resolve_asset_route(event_input.currency)
    if route is None:
        return False, "no_route_configured_for_currency", None

    return True, None, route


# ============================================================================
# Source loaders
# ============================================================================

def prepare_events_from_live_source(
    config: PipelineConfig,
    result: PipelineRunResult,
) -> list[PreparedEvent]:
    log_section("مرحله ۱A: دریافت رویدادها از Forex Factory")

    impact_filter = expand_impact_filter(config.min_impact)

    fetch_result = fetch_forex_factory_events_debug(
        impact_filter=impact_filter,
        currency_filter=config.currencies,
        released_only=True if config.released_only else None,
        require_forecast=config.require_forecast,
        timeout=config.http_timeout,
    )
    result.live_fetch_result = fetch_result

    summary = fetch_result.summary
    logger.info(
        "Live fetch summary | raw=%d parsed=%d accepted=%d filtered=%d invalid=%d",
        summary.raw_count,
        summary.parsed_count,
        summary.accepted_count,
        summary.filtered_out_count,
        summary.invalid_count,
    )
    logger.info("Live filters applied: %s", summary.filters_applied)
    if summary.rejection_reasons:
        logger.info("Live rejection reasons: %s", summary.rejection_reasons)

    events = fetch_result.events
    logger.info("تعداد eventهای live پس از فیلتر crawler: %d", len(events))

    if config.save_raw_events and events:
        try:
            persistence_result = save_events(events)
            result.raw_event_persistence = persistence_result
            logger.info(
                "Raw event persistence | total=%d inserted=%d updated=%d skipped=%d failed=%d",
                persistence_result.total,
                persistence_result.inserted,
                persistence_result.updated,
                persistence_result.skipped,
                persistence_result.failed,
            )
        except Exception as exc:
            logger.warning("ذخیره raw events با خطا مواجه شد ولی pipeline ادامه می‌دهد: %s", exc)
            result.add_failure("persist_raw_events", "batch", "MULTI", exc)
            if not config.continue_on_error:
                raise

    prepared: list[PreparedEvent] = []

    for event in events:
        if len(prepared) >= config.max_events:
            result.add_skip("capacity_live", event.title, event.currency, f"max_events_reached({config.max_events})")
            continue

        try:
            event_input = event_input_from_forex_factory(event)
        except Exception as exc:
            logger.warning("تبدیل live event ناموفق بود: %s | %s", event.title, exc)
            result.add_failure("convert_live", event.title, event.currency, exc)
            if not config.continue_on_error:
                raise
            continue

        ok, reason, route = validate_and_route_event_input(event_input, config)
        if not ok or route is None:
            result.add_skip("sanity_live", event_input.title, event_input.currency, reason or "unknown")
            continue

        prepared.append(
            PreparedEvent(
                origin="live",
                source_record_id=None,
                route=route,
                event_input=event_input,
            )
        )

    logger.info("تعداد prepared event از live source: %d", len(prepared))
    return prepared


def load_db_records(
    config: PipelineConfig,
    result: PipelineRunResult,
) -> list[EventHistoryDB]:
    log_section("مرحله ۱B: دریافت رویدادها از دیتابیس")

    session = SessionLocal()
    try:
        query = session.query(EventHistoryDB)

        if config.released_only:
            query = query.filter(EventHistoryDB.actual.isnot(None))

        impact_values = expand_impact_filter(config.min_impact)
        query = query.filter(EventHistoryDB.impact.in_(impact_values))

        if config.currencies:
            query = query.filter(EventHistoryDB.currency.in_(config.currencies))

        if config.require_forecast:
            query = query.filter(EventHistoryDB.forecast.isnot(None))

        cutoff_date = None
        if config.db_lookback_days is not None and config.db_lookback_days > 0:
            cutoff_date = datetime.utcnow() - timedelta(days=config.db_lookback_days)
            query = query.filter(EventHistoryDB.date.isnot(None))
            query = query.filter(EventHistoryDB.date >= cutoff_date)

        query = query.order_by(
            EventHistoryDB.date.desc(),
            EventHistoryDB.id.desc(),
        )

        records = query.limit(config.db_candidate_limit).all()

        result.db_loaded_count = len(records)
        result.db_query_filters = {
            "impact_filter": impact_values,
            "currency_filter": config.currencies,
            "released_only": config.released_only,
            "require_forecast": config.require_forecast,
            "lookback_days": config.db_lookback_days,
            "cutoff_date": cutoff_date.isoformat() if cutoff_date else None,
            "candidate_limit": config.db_candidate_limit,
        }

        logger.info("DB query filters: %s", result.db_query_filters)
        logger.info("تعداد رکوردهای بارگذاری‌شده از DB: %d", len(records))
        return records
    finally:
        session.close()


def prepare_events_from_db_source(
    config: PipelineConfig,
    result: PipelineRunResult,
) -> list[PreparedEvent]:
    records = load_db_records(config, result)

    prepared: list[PreparedEvent] = []

    for record in records:
        if len(prepared) >= config.max_events:
            result.add_skip(
                "capacity_db",
                record.title,
                record.currency or "UNK",
                f"max_events_reached({config.max_events})",
            )
            continue

        try:
            event_input = event_input_from_db_record(record)
        except Exception as exc:
            logger.warning("تبدیل DB record ناموفق بود: id=%s | %s | %s", record.id, record.title, exc)
            result.add_failure("convert_db", record.title, record.currency or "UNK", exc)
            if not config.continue_on_error:
                raise
            continue

        ok, reason, route = validate_and_route_event_input(event_input, config)
        if not ok or route is None:
            result.add_skip("sanity_db", event_input.title, event_input.currency, reason or "unknown")
            continue

        prepared.append(
            PreparedEvent(
                origin="db",
                source_record_id=record.id,
                route=route,
                event_input=event_input,
            )
        )

    logger.info("تعداد prepared event از DB source: %d", len(prepared))
    return prepared


# ============================================================================
# Common stages
# ============================================================================

def stage_build_market_contexts(
    prepared_events: list[PreparedEvent],
    config: PipelineConfig,
    result: PipelineRunResult,
) -> list[EnrichedEvent]:
    log_section("مرحله ۲: ساخت MarketContext")

    enriched: list[EnrichedEvent] = []

    for idx, item in enumerate(prepared_events, 1):
        event_input = item.event_input
        route = item.route

        logger.info(
            "[%d/%d] Build context | origin=%s | %s/%s | ticker=%s",
            idx,
            len(prepared_events),
            item.origin,
            event_input.currency,
            event_input.title,
            route.ticker,
        )

        try:
            context = fetch_market_context(
                ticker=route.ticker,
                currency=route.market_context_currency,
                event_title=event_input.title,
                actual=event_input.actual,
                forecast=event_input.forecast,
                previous=event_input.previous,
                event_currency=event_input.currency,
            )

            context = context.model_copy(update={
                "event_category": event_input.category
            })

            enriched.append(
                EnrichedEvent(
                    origin=item.origin,
                    source_record_id=item.source_record_id,
                    route=route,
                    event_input=event_input,
                    market_context=context,
                )
            )

            logger.info(
                "Context ok | surprise=%.3f | volatility=%.3f | hv=%s | iv=%s",
                context.calculated_surprise,
                context.calculated_volatility,
                f"{context.historical_volatility:.4f}" if context.historical_volatility is not None else "N/A",
                f"{context.implied_volatility:.2f}" if context.implied_volatility is not None else "N/A",
            )
        except Exception as exc:
            logger.warning("ساخت MarketContext ناموفق بود: %s | %s", event_input.title, exc)
            result.add_failure("enrich", event_input.title, event_input.currency, exc)
            if not config.continue_on_error:
                raise

    result.enriched_events = enriched
    logger.info("تعداد MarketContext موفق: %d", len(enriched))
    return enriched


def stage_analyze_events(
    enriched_events: list[EnrichedEvent],
    config: PipelineConfig,
    result: PipelineRunResult,
) -> list[AnalyzedEvent]:
    log_section("مرحله ۳ تا ۵: تحلیل، تولید سیگنال و persistence")

    if config.dry_run:
        logger.info("Dry-run فعال است؛ مرحله تحلیل/ذخیره‌سازی رد شد.")
        return []

    llm = build_llm(config)
    analyzed: list[AnalyzedEvent] = []

    for idx, item in enumerate(enriched_events, 1):
        event_input = item.event_input
        route = item.route

        logger.info(
            "[%d/%d] Analyze | origin=%s | %s/%s | ticker=%s",
            idx,
            len(enriched_events),
            item.origin,
            event_input.currency,
            event_input.title,
            route.ticker,
        )

        t0 = time.perf_counter()
        try:
            interpretation, signal = analyze_economic_event(
                event_input=event_input,
                market_context=item.market_context,
                ticker=route.ticker,
                llm=llm,
                persist=config.persist_signals,
            )
            elapsed = time.perf_counter() - t0

            analyzed_item = AnalyzedEvent(
                origin=item.origin,
                source_record_id=item.source_record_id,
                route=route,
                event_input=event_input,
                signal=signal,
                elapsed_seconds=elapsed,
            )
            analyzed.append(analyzed_item)

            instrument_direction = translate_instrument_direction(
                signal.direction, route.alignment
            )

            logger.info(
                "Signal ok | native=%s | instrument=%s | score=%+.4f | conf=%.4f | tradable=%s | half_life=%dm | %.2fs",
                direction_label(signal.direction),
                direction_label(instrument_direction),
                signal.final_score,
                signal.confidence,
                signal.is_tradable,
                signal.signal_half_life_mins,
                elapsed,
            )

            _ = interpretation  # فقط برای تکمیل E2E invoke

        except Exception as exc:
            logger.error("تحلیل event ناموفق بود: %s | %s", event_input.title, exc)
            result.add_failure("analyze", event_input.title, event_input.currency, exc)
            if not config.continue_on_error:
                raise

    result.analyzed_events = analyzed
    logger.info("تعداد سیگنال‌های تولیدشده: %d", len(analyzed))
    return analyzed


# ============================================================================
# Reporting
# ============================================================================

def print_report(config: PipelineConfig, result: PipelineRunResult) -> None:
    result.finalize()

    print()
    print("=" * 104)
    print("E2E FOREX EVENT PIPELINE REPORT")
    print("=" * 104)
    print(f"Started At           : {result.started_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Finished At          : {result.finished_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Duration Seconds     : {result.duration_seconds:.2f}")
    print(f"Requested Source     : {result.requested_source_mode}")
    print(f"Effective Source     : {result.effective_source_mode or 'N/A'}")
    print("-" * 104)

    if result.live_fetch_result is not None:
        s = result.live_fetch_result.summary
        print("Live Fetch Summary")
        print(f"  Raw Count          : {s.raw_count}")
        print(f"  Parsed Count       : {s.parsed_count}")
        print(f"  Accepted Count     : {s.accepted_count}")
        print(f"  Filtered Out       : {s.filtered_out_count}")
        print(f"  Invalid Count      : {s.invalid_count}")
        print(f"  Filters Applied    : {s.filters_applied}")
        if s.rejection_reasons:
            print(f"  Rejection Reasons  : {s.rejection_reasons}")
        print("-" * 104)

    if result.raw_event_persistence is not None:
        p = result.raw_event_persistence
        print("Raw Event Persistence")
        print(f"  Total              : {p.total}")
        print(f"  Inserted           : {p.inserted}")
        print(f"  Updated            : {p.updated}")
        print(f"  Skipped            : {p.skipped}")
        print(f"  Failed             : {p.failed}")
        if p.errors:
            print(f"  Errors             : {p.errors[:5]}")
        print("-" * 104)

    if result.db_query_filters:
        print("DB Load Summary")
        print(f"  Loaded Count       : {result.db_loaded_count}")
        print(f"  Query Filters      : {result.db_query_filters}")
        print("-" * 104)

    print("Pipeline Summary")
    print(f"  Prepared Inputs    : {result.prepared_events_count}")
    print(f"  Enriched Contexts  : {result.enriched_events_count}")
    print(f"  Signals Produced   : {result.analyzed_events_count}")
    print(f"  Signals Persisted  : {result.persisted_signals_count if config.persist_signals and not config.dry_run else 0}")
    print(f"  Tradable Signals   : {result.tradable_signals_count}")
    print(f"  Skipped Events     : {len(result.skipped)}")
    print(f"  Failures           : {len(result.failures)}")
    print("-" * 104)

    if result.analyzed_events:
        print("Generated Signals")
        print(
            f"{'Origin':<8} {'ID':<6} {'CCY':<5} {'Impact':<8} {'Ticker':<12} "
            f"{'Align':<8} {'Native':<8} {'Instr.':<8} {'Score':>8} {'Conf':>8} "
            f"{'Trade':>7} {'Half':>6}  Event"
        )
        print("-" * 104)

        for item in result.analyzed_events:
            signal = item.signal
            route = item.route
            native_dir = direction_label(signal.direction)
            instrument_dir = direction_label(
                translate_instrument_direction(signal.direction, route.alignment)
            )
            print(
                f"{item.origin:<8} "
                f"{str(item.source_record_id or '-'): <6} "
                f"{signal.event_currency:<5} "
                f"{signal.event_impact:<8} "
                f"{route.ticker:<12} "
                f"{alignment_label(route.alignment):<8} "
                f"{native_dir:<8} "
                f"{instrument_dir:<8} "
                f"{signal.final_score:>+8.3f} "
                f"{signal.confidence:>8.3f} "
                f"{str(signal.is_tradable):>7} "
                f"{signal.signal_half_life_mins:>6}  "
                f"{signal.event_title}"
            )

        print("-" * 104)
        print("* Origin = منبع ورودی (live یا db)")
        print("* Native = جهت بنیادی خود ارز/رویداد")
        print("* Instr. = ترجمه همان جهت به ticker انتخاب‌شده با direct/inverse/index routing")
        print("* direction ذخیره‌شده در DB فعلاً currency-native است.")
        print("-" * 104)

    if result.skipped:
        print("Skipped Events (first 12)")
        for item in result.skipped[:12]:
            print(f"  - [{item.stage}] {item.currency}/{item.event_title}: {item.reason}")
        if len(result.skipped) > 12:
            print(f"  ... and {len(result.skipped) - 12} more")
        print("-" * 104)

    if result.failures:
        print("Failures")
        for item in result.failures[:12]:
            print(f"  - [{item.stage}] {item.currency}/{item.event_title}: {item.error}")
            if config.verbose:
                print(item.traceback_text)
        if len(result.failures) > 12:
            print(f"  ... and {len(result.failures) - 12} more")
        print("-" * 104)

    print("Run Status")
    if result.fatal_error:
        print("  FATAL_FAILURE")
    elif result.analyzed_events:
        if result.failures:
            print("  PARTIAL_SUCCESS")
        else:
            print("  SUCCESS")
    elif result.prepared_events:
        print("  COMPLETED_NO_SIGNALS")
    else:
        print("  NO_ELIGIBLE_EVENTS")
    print("=" * 104)
    print()


# ============================================================================
# Orchestration
# ============================================================================

def choose_and_prepare_source(
    config: PipelineConfig,
    result: PipelineRunResult,
) -> list[PreparedEvent]:
    """
    source=auto:
      1) live
      2) اگر صفر شد -> db
    """
    result.requested_source_mode = config.source_mode.value

    if config.source_mode == SourceMode.LIVE:
        prepared = prepare_events_from_live_source(config, result)
        result.effective_source_mode = "live"
        return prepared

    if config.source_mode == SourceMode.DB:
        prepared = prepare_events_from_db_source(config, result)
        result.effective_source_mode = "db"
        return prepared

    # AUTO mode
    logger.info("Source mode = auto | ابتدا live و سپس در صورت نیاز fallback به DB")

    try:
        prepared_live = prepare_events_from_live_source(config, result)
    except Exception as exc:
        logger.warning("Live source در حالت auto خطا داد؛ fallback به DB انجام می‌شود: %s", exc)
        result.add_failure("source_live_auto", "live_source", "MULTI", exc)
        prepared_live = []

    if prepared_live:
        logger.info("Auto mode: live source موفق بود و fallback لازم نشد.")
        result.effective_source_mode = "live"
        return prepared_live

    logger.warning("Auto mode: رویداد واجد شرایط live پیدا نشد؛ fallback به DB شروع می‌شود.")
    prepared_db = prepare_events_from_db_source(config, result)
    result.effective_source_mode = "db"
    return prepared_db


def run_pipeline(config: PipelineConfig) -> PipelineRunResult:
    result = PipelineRunResult(requested_source_mode=config.source_mode.value)

    logger.info("شروع e2e pipeline")
    logger.info(
        "config | source=%s currencies=%s min_impact=%s released_only=%s "
        "require_forecast=%s max_events=%d db_lookback_days=%s db_candidate_limit=%d "
        "provider=%s persist_signals=%s save_raw_events=%s dry_run=%s",
        config.source_mode.value,
        config.currencies,
        config.min_impact,
        config.released_only,
        config.require_forecast,
        config.max_events,
        config.db_lookback_days,
        config.db_candidate_limit,
        config.llm_provider.value,
        config.persist_signals,
        config.save_raw_events,
        config.dry_run,
    )

    try:
        init_db()
        logger.info("Database initialized successfully.")

        prepared = choose_and_prepare_source(config, result)
        result.prepared_events = prepared

        if not prepared:
            logger.warning("هیچ event واجد شرایطی برای ادامه pipeline پیدا نشد.")
            return result

        enriched = stage_build_market_contexts(prepared, config, result)
        if not enriched:
            logger.warning("هیچ MarketContext موفقی ساخته نشد.")
            return result

        stage_analyze_events(enriched, config, result)
        return result

    except KeyboardInterrupt:
        result.fatal_error = "Pipeline interrupted by user."
        logger.warning("Execution interrupted by user.")
        return result
    except Exception as exc:
        result.fatal_error = str(exc)
        logger.critical("Fatal pipeline error: %s", exc, exc_info=True)
        return result


# ============================================================================
# CLI
# ============================================================================

def parse_args(argv: Optional[list[str]] = None) -> PipelineConfig:
    parser = argparse.ArgumentParser(
        description="End-to-end forex event analysis pipeline",
    )

    parser.add_argument(
        "--source",
        choices=[m.value for m in SourceMode],
        default=SourceMode.AUTO.value,
        help="منبع داده: live | db | auto (پیش‌فرض: auto)",
    )
    parser.add_argument(
        "--currencies",
        nargs="+",
        default=["USD", "EUR", "GBP"],
        help="لیست ارزهای مورد پردازش. پیش‌فرض: USD EUR GBP",
    )
    parser.add_argument(
        "--min-impact",
        choices=["High", "Medium", "Low"],
        default="High",
        help="حداقل impact. پیش‌فرض: High",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=10,
        help="حداکثر event برای هر اجرا. پیش‌فرض: 10",
    )
    parser.add_argument(
        "--require-forecast",
        action="store_true",
        help="فقط eventهایی که forecast دارند پردازش شوند",
    )
    parser.add_argument(
        "--db-lookback-days",
        type=int,
        default=365,
        help="در حالت DB/auto فقط رویدادهای این بازه زمانی اخیر را لود کن. 0 یا منفی = بدون محدودیت",
    )
    parser.add_argument(
        "--db-candidate-limit",
        type=int,
        default=50,
        help="حداکثر تعداد رکورد اولیه‌ای که از DB خوانده می‌شود. پیش‌فرض: 50",
    )
    parser.add_argument(
        "--no-save-raw-events",
        action="store_true",
        help="raw events live را در economic_events_history ذخیره نکن",
    )
    parser.add_argument(
        "--no-persist-signals",
        action="store_true",
        help="سیگنال‌ها را در event_signals ذخیره نکن",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="فقط source/prepare/enrich؛ بدون LLM و بدون persistence سیگنال",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="در اولین خطا pipeline متوقف شود",
    )
    parser.add_argument(
        "--llm-provider",
        choices=[p.value for p in LLMProvider],
        default=LLMProvider.OPENROUTER.value,
        help="LLM provider: openrouter | ollama | arvan",
    )
    parser.add_argument(
        "--llm-model",
        default="",
        help="نام مدل LLM. اگر خالی باشد از پیش‌فرض provider استفاده می‌شود",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="temperature مدل. پیش‌فرض: 0.1",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENROUTER_API_KEY",
        help="نام env variable برای API key در حالت OpenRouter",
    )
    parser.add_argument(
        "--openrouter-base-url",
        default="https://openrouter.ai/api/v1",
        help="Base URL برای OpenRouter",
    )
    parser.add_argument(
        "--ollama-base-url",
        default="http://localhost:11434",
        help="Base URL برای Ollama",
    )
    parser.add_argument(
        "--http-timeout",
        type=int,
        default=15,
        help="HTTP timeout برای live fetch. پیش‌فرض: 15",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="logging مفصل‌تر",
    )

    args, _ = parser.parse_known_args(argv)

    lookback_days: Optional[int]
    if args.db_lookback_days is None or args.db_lookback_days <= 0:
        lookback_days = None
    else:
        lookback_days = args.db_lookback_days

    return PipelineConfig(
        source_mode=SourceMode(args.source),
        currencies=args.currencies,
        min_impact=args.min_impact,
        released_only=True,
        require_forecast=args.require_forecast,
        max_events=args.max_events,
        db_lookback_days=lookback_days,
        db_candidate_limit=args.db_candidate_limit,
        save_raw_events=not args.no_save_raw_events,
        persist_signals=not args.no_persist_signals,
        dry_run=args.dry_run,
        continue_on_error=not args.stop_on_error,
        verbose=args.verbose,
        llm_provider=LLMProvider(args.llm_provider),
        llm_model=args.llm_model,
        temperature=args.temperature,
        api_key_env=args.api_key_env,
        openrouter_base_url=args.openrouter_base_url,
        ollama_base_url=args.ollama_base_url,
        http_timeout=args.http_timeout,
    )


def main(argv: Optional[list[str]] = None) -> int:
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