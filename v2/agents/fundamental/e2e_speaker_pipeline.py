#!/usr/bin/env python3
"""
e2e_speaker_pipeline.py
=======================

End-to-end validation pipeline for speaker statement analysis.

Pipeline:
  source load (Mock JSON/API)
    -> save to raw_speaker_items (dedup)
    -> convert to SpeakerTextItem
    -> filter (currency, weight, source_type) + route
    -> fetch MarketContext
    -> analyze via nlp_x
    -> persist signals to trading_signals
    -> summary report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, is_dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.database import (
    SessionLocal, 
    init_db, 
    session_scope, 
    insert_raw_speaker_item_if_new,
    TradingSignalDB
)
from agents.fundamental.data_fetcher import fetch_market_context
from core.models import SpeakerTextItem, Speaker, MarketContext, SpeakerSignal
from agents.fundamental.nlp_x import analyze_speaker_text
from core.routing import (
    AssetRoute,
    resolve_asset_route,
    translate_instrument_direction,
    direction_label,
    alignment_label,
)

logger = logging.getLogger("e2e_speaker_pipeline")

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


# ============================================================================
# Enums
# ============================================================================

class LLMProvider(str, Enum):
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    ARVAN = "arvan"


# ============================================================================
# Config
# ============================================================================

@dataclass
class SpeakerPipelineConfig:
    """Immutable configuration for a single speaker pipeline run."""

    # ── Source ──
    mock_json_path: str = "data/mock_speaker_statements.json"

    # ── Filtering ──
    currencies: list[str] = field(default_factory=lambda: ["USD", "EUR", "GBP", "JPY"])
    min_speaker_weight: float = 0.70
    source_types: list[str] = field(default_factory=list)  # Empty = all allowed types
    max_items: int = 10

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

    # ── Audit ──
    audit_dir: str = "runs"

    def __post_init__(self) -> None:
        self.currencies = [c.strip().upper() for c in self.currencies if c.strip()]
        self.source_types = [s.strip().lower() for s in self.source_types if s.strip()]

        if not (0.0 <= self.min_speaker_weight <= 1.0):
            raise ValueError("min_speaker_weight must be in [0.0, 1.0]")
        if self.max_items < 1:
            raise ValueError("max_items must be at least 1")

        if self.dry_run:
            self.persist_signals = False


# ============================================================================
# Result dataclasses
# ============================================================================

@dataclass
class SkipRecord:
    stage: str
    speaker_name: str
    reason: str


@dataclass
class FailureRecord:
    stage: str
    speaker_name: str
    error: str
    traceback_text: str


@dataclass
class PreparedSpeakerItem:
    text_item: SpeakerTextItem
    speaker: Speaker
    route: AssetRoute


@dataclass
class EnrichedSpeakerItem:
    text_item: SpeakerTextItem
    speaker: Speaker
    route: AssetRoute
    market_context: MarketContext


@dataclass
class AnalyzedSpeakerItem:
    text_item: SpeakerTextItem
    speaker: Speaker
    route: AssetRoute
    signal: SpeakerSignal
    elapsed_seconds: float


@dataclass
class SpeakerPipelineRunResult:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None

    # ── Stage 1: Load ──
    loaded_items_count: int = 0
    raw_db_inserted: int = 0
    raw_db_skipped: int = 0

    # ── Stage 2: Filter + Route ──
    prepared_items: list[PreparedSpeakerItem] = field(default_factory=list)

    # ── Stage 3: Enrich ──
    enriched_items: list[EnrichedSpeakerItem] = field(default_factory=list)

    # ── Stage 4: Analyze ──
    analyzed_items: list[AnalyzedSpeakerItem] = field(default_factory=list)

    # ── Tracking ──
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
    def prepared_items_count(self) -> int:
        return len(self.prepared_items)

    @property
    def enriched_items_count(self) -> int:
        return len(self.enriched_items)

    @property
    def analyzed_items_count(self) -> int:
        return len(self.analyzed_items)

    @property
    def tradable_signals_count(self) -> int:
        return sum(1 for item in self.analyzed_items if item.signal.is_tradable)

    def add_skip(self, stage: str, speaker_name: str, reason: str) -> None:
        self.skipped.append(SkipRecord(stage=stage, speaker_name=speaker_name, reason=reason))

    def add_failure(self, stage: str, speaker_name: str, exc: Exception) -> None:
        self.failures.append(
            FailureRecord(
                stage=stage,
                speaker_name=speaker_name,
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
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("yfinance").setLevel(logging.INFO)


def _audit_default(obj: Any) -> Any:
    """JSON serializer for complex objects."""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def save_audit_artifact(config: SpeakerPipelineConfig, result: SpeakerPipelineRunResult) -> str:
    audit_dir = Path(config.audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = result.started_at.strftime("%Y%m%d_%H%M%S")
    filename = f"speaker_pipeline_{timestamp}.json"
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


def build_llm(config: SpeakerPipelineConfig):
    if config.llm_provider == LLMProvider.OLLAMA:
        from langchain_ollama import ChatOllama
        model_name = config.llm_model or "0xroyce/plutus"
        logger.info(f"LLM provider: Ollama | model={model_name} | base_url={config.ollama_base_url}")
        return ChatOllama(model=model_name, temperature=config.temperature, base_url=config.ollama_base_url)

    if config.llm_provider == LLMProvider.OPENROUTER:
        from langchain_openai import ChatOpenAI
        model_name = config.llm_model or "openai/gpt-4o-mini"
        api_key = os.getenv(config.openrouter_api_key_env) or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(f"OpenRouter API key not found. Set {config.openrouter_api_key_env}")
        base_url = os.getenv("OPENROUTER_BASE_URL", config.openrouter_base_url)
        logger.info(f"LLM provider: OpenRouter | model={model_name} | base_url={base_url}")
        return ChatOpenAI(model=model_name, temperature=config.temperature, api_key=api_key, base_url=base_url)

    if config.llm_provider == LLMProvider.ARVAN:
        from langchain_openai import ChatOpenAI
        model_name = config.llm_model or "GLM-5.2"
        base_url = os.getenv(config.arvan_base_url_env)
        api_key = os.getenv(config.arvan_api_key_env, "not-needed")
        if not base_url:
            raise ValueError(f"Arvan base URL not found. Set {config.arvan_base_url_env}")
        logger.info(f"LLM provider: Arvan | model={model_name} | base_url={base_url}")
        return ChatOpenAI(model=model_name, temperature=config.temperature, api_key=api_key, base_url=base_url)

    raise ValueError(f"Unknown LLM provider: {config.llm_provider}")


def compute_dedup_hash(speaker_name: str, text: str, link: Optional[str]) -> str:
    basis = (link or "") + "|" + speaker_name + "|" + text[:200]
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def is_already_persisted(text_item: SpeakerTextItem) -> bool:
    """Check if signal already exists in trading_signals to avoid re-analysis."""
    if not text_item.external_id:
        return False
        
    session = SessionLocal()
    try:
        exists = session.query(TradingSignalDB).filter(
            TradingSignalDB.external_id == text_item.external_id
        ).first()
        return exists is not None
    except Exception as exc:
        logger.warning(f"DB dedup check failed for {text_item.speaker_name}: {exc}")
        return False
    finally:
        session.close()


# ============================================================================
# Stage 1: Load Mock Source & Save Raw
# ============================================================================

def stage_load_and_save_raw(
    config: SpeakerPipelineConfig,
    result: SpeakerPipelineRunResult,
) -> list[SpeakerTextItem]:
    log_section("Stage 1: Load Mock Statements & Save to Raw DB")

    mock_path = Path(config.mock_json_path)
    if not mock_path.exists():
        raise FileNotFoundError(f"Mock JSON file not found: {mock_path}")

    with open(mock_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    logger.info(f"Loaded {len(raw_data)} mock statements from {mock_path}")

    text_items: list[SpeakerTextItem] = []
    
    with session_scope() as session:
        for row in raw_data:
            speaker_name = str(row.get("speaker_name", "")).strip()
            text = str(row.get("text", "")).strip()
            
            if not speaker_name or not text:
                continue

            link = row.get("link")
            dedup_hash = compute_dedup_hash(speaker_name, text, link)
            
            # Build SpeakerTextItem
            try:
                text_item = SpeakerTextItem(
                    text=text,
                    speaker_name=speaker_name,
                    published=datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")) if row.get("published_at") else None,
                    source=row.get("source", "Unknown"),
                    source_type=row.get("source_type", "unknown"),
                    link=link,
                    external_id=dedup_hash,  # Use hash as external_id for signal dedup
                    currency_hint=row.get("currency_hint"),
                )
                text_items.append(text_item)
            except Exception as exc:
                logger.warning(f"Failed to parse mock row for {speaker_name}: {exc}")
                continue

            # Save to Raw DB
            fields = {
                "speaker_name": speaker_name,
                "text": text,
                "source": text_item.source,
                "source_type": text_item.source_type,
                "link": link,
                "published_at": text_item.published,
                "currency": text_item.currency_hint,
            }
            
            _, created = insert_raw_speaker_item_if_new(session, dedup_hash=dedup_hash, **fields)
            if created:
                result.raw_db_inserted += 1
            else:
                result.raw_db_skipped += 1

    result.loaded_items_count = len(text_items)
    logger.info(f"Raw DB Save: {result.raw_db_inserted} new, {result.raw_db_skipped} duplicates skipped")
    return text_items


# ============================================================================
# Stage 2: Filter & Route
# ============================================================================

def stage_filter_and_route(
    text_items: list[SpeakerTextItem],
    config: SpeakerPipelineConfig,
    result: SpeakerPipelineRunResult,
) -> list[PreparedSpeakerItem]:
    log_section("Stage 2: Filter & Route")

    prepared: list[PreparedSpeakerItem] = []

    for item in text_items:
        if len(prepared) >= config.max_items:
            result.add_skip("capacity", item.speaker_name, f"max_items_reached({config.max_items})")
            continue

        # Signal DB Dedup
        if config.enable_db_dedup and is_already_persisted(item):
            result.add_skip("dedup_db", item.speaker_name, "already_in_trading_signals")
            continue

        # Source Type Filter
        if config.source_types and item.source_type.lower() not in config.source_types:
            result.add_skip("filter", item.speaker_name, f"source_type_not_allowed({item.source_type})")
            continue

        # Resolve Speaker to get weight and asset
        speaker = Speaker.from_name(item.speaker_name)

        # Weight Filter
        if speaker.weight < config.min_speaker_weight:
            result.add_skip("filter", item.speaker_name, f"weight_below_threshold({speaker.weight:.2f}<{config.min_speaker_weight:.2f})")
            continue

        # Currency Filter & Routing
        target_currency = item.currency_hint or speaker.primarily_impacts
        if not target_currency or target_currency.upper() not in config.currencies:
            result.add_skip("filter", item.speaker_name, f"currency_not_whitelisted({target_currency})")
            continue

        route = resolve_asset_route(target_currency.upper())
        if route is None:
            result.add_skip("routing", item.speaker_name, f"no_route_for_currency({target_currency})")
            continue

        prepared.append(PreparedSpeakerItem(text_item=item, speaker=speaker, route=route))

    result.prepared_items = prepared
    logger.info(f"After filter+route: {len(prepared)} items (from {len(text_items)} loaded)")
    return prepared


# ============================================================================
# Stage 3: Enrich (MarketContext)
# ============================================================================

def stage_build_market_contexts(
    prepared_items: list[PreparedSpeakerItem],
    config: SpeakerPipelineConfig,
    result: SpeakerPipelineRunResult,
) -> list[EnrichedSpeakerItem]:
    log_section("Stage 3: Build MarketContext")

    enriched: list[EnrichedSpeakerItem] = []

    for idx, p_item in enumerate(prepared_items, 1):
        speaker = p_item.speaker
        route = p_item.route
        text_item = p_item.text_item

        logger.info(f"[{idx}/{len(prepared_items)}] Build context | {speaker.name} | ticker={route.ticker}")

        try:
            context = fetch_market_context(
                ticker=route.ticker,
                currency=route.market_context_currency,
                event_title=f"Statement by {speaker.name}",
                actual=None,
                forecast=None,
                previous=None,
                event_currency=route.event_currency,
            )

            enriched.append(EnrichedSpeakerItem(
                text_item=text_item,
                speaker=speaker,
                route=route,
                market_context=context
            ))
            logger.info(f"Context ok | vol={context.calculated_volatility:.3f}")
        except Exception as exc:
            logger.warning(f"MarketContext failed for {speaker.name}: {exc}")
            result.add_failure("enrich", speaker.name, exc)
            if not config.continue_on_error:
                raise

    result.enriched_items = enriched
    logger.info(f"MarketContext success for {len(enriched)} items")
    return enriched


# ============================================================================
# Stage 4: Analyze & Persist
# ============================================================================

def stage_analyze(
    enriched_items: list[EnrichedSpeakerItem],
    config: SpeakerPipelineConfig,
    result: SpeakerPipelineRunResult,
) -> list[AnalyzedSpeakerItem]:
    log_section("Stage 4: Analyze & Persist Signals")

    if config.dry_run:
        logger.info("Dry-run active; skipping LLM and persistence.")
        return []

    llm = build_llm(config)
    analyzed: list[AnalyzedSpeakerItem] = []

    for idx, e_item in enumerate(enriched_items, 1):
        speaker = e_item.speaker
        route = e_item.route
        text_item = e_item.text_item
        context = e_item.market_context

        logger.info(f"[{idx}/{len(enriched_items)}] Analyze | {speaker.name} | ticker={route.ticker}")

        t0 = time.perf_counter()
        try:
            _, signal = analyze_speaker_text(
                item=text_item,
                market_context=context,
                ticker=route.ticker,
                llm=llm,
                persist=config.persist_signals,
            )
            elapsed = time.perf_counter() - t0

            analyzed.append(AnalyzedSpeakerItem(
                text_item=text_item,
                speaker=speaker,
                route=route,
                signal=signal,
                elapsed_seconds=elapsed
            ))

            instrument_dir = translate_instrument_direction(signal.direction, route.alignment)
            logger.info(
                f"Signal ok | native={direction_label(signal.direction)} | "
                f"instrument={direction_label(instrument_dir)} | score={signal.final_score:+.4f} | "
                f"conf={signal.confidence:.4f} | tradable={signal.is_tradable} | {elapsed:.2f}s"
            )

        except Exception as exc:
            logger.error(f"Analysis failed for {speaker.name}: {exc}")
            result.add_failure("analyze", speaker.name, exc)
            if not config.continue_on_error:
                raise

    result.analyzed_items = analyzed
    logger.info(f"Signals produced: {len(analyzed)}")
    return analyzed


# ============================================================================
# Reporting
# ============================================================================

def print_report(config: SpeakerPipelineConfig, result: SpeakerPipelineRunResult) -> None:
    result.finalize()

    print()
    print("=" * 104)
    print("E2E SPEAKER PIPELINE REPORT")
    print("=" * 104)
    print(f"Started At           : {result.started_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Finished At          : {result.finished_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Duration Seconds     : {result.duration_seconds:.2f}")
    print(f"LLM Provider         : {config.llm_provider.value}")
    print(f"LLM Model            : {config.llm_model or '(default)'}")
    print("-" * 104)

    print("Raw DB Summary")
    print(f"  Loaded Items       : {result.loaded_items_count}")
    print(f"  Raw Inserted       : {result.raw_db_inserted}")
    print(f"  Raw Skipped (Dup)  : {result.raw_db_skipped}")
    print("-" * 104)

    print("Pipeline Summary")
    print(f"  Prepared Items     : {result.prepared_items_count}")
    print(f"  Enriched Contexts  : {result.enriched_items_count}")
    print(f"  Signals Produced   : {result.analyzed_items_count}")
    print(f"  Tradable Signals   : {result.tradable_signals_count}")
    print(f"  Skipped Items      : {len(result.skipped)}")
    print(f"  Failures           : {len(result.failures)}")
    print("-" * 104)

    if result.analyzed_items:
        print("Generated Signals")
        print(
            f"{'Speaker':<20} {'Ticker':<12} {'Align':<8} {'Native':<8} {'Instr.':<8} "
            f"{'Score':>8} {'Conf':>8} {'Trade':>7} {'Half':>6}  Excerpt"
        )
        print("-" * 104)

        for item in result.analyzed_items:
            signal = item.signal
            route = item.route
            native = direction_label(signal.direction)
            instr = direction_label(translate_instrument_direction(signal.direction, route.alignment))
            excerpt = item.text_item.text[:40].replace("\n", " ") + "..."
            
            print(
                f"{item.speaker.name:<20} "
                f"{route.ticker:<12} "
                f"{alignment_label(route.alignment):<8} "
                f"{native:<8} "
                f"{instr:<8} "
                f"{signal.final_score:>+8.3f} "
                f"{signal.confidence:>8.3f} "
                f"{str(signal.is_tradable):>7} "
                f"{signal.signal_half_life_mins:>6}  "
                f"{excerpt}"
            )
        print("-" * 104)

    if result.skipped:
        print(f"Skipped Items (first 10 of {len(result.skipped)})")
        for s in result.skipped[:10]:
            print(f"  - [{s.stage}] {s.speaker_name}: {s.reason}")
        if len(result.skipped) > 10:
            print(f"  ... and {len(result.skipped) - 10} more")
        print("-" * 104)

    if result.failures:
        print(f"Failures ({len(result.failures)})")
        for f in result.failures[:10]:
            print(f"  - [{f.stage}] {f.speaker_name}: {f.error}")
            if config.verbose:
                print(f.traceback_text)
        if len(result.failures) > 10:
            print(f"  ... and {len(result.failures) - 10} more")
        print("-" * 104)

    print("Run Status")
    if result.fatal_error:
        print("  FATAL_FAILURE")
    elif result.analyzed_items:
        if result.failures:
            print("  PARTIAL_SUCCESS")
        else:
            print("  SUCCESS")
    elif result.prepared_items:
        print("  COMPLETED_NO_SIGNALS")
    else:
        print("  NO_ELIGIBLE_ITEMS")
    print("=" * 104)
    print()


# ============================================================================
# Orchestrator
# ============================================================================

def run_pipeline(config: SpeakerPipelineConfig) -> SpeakerPipelineRunResult:
    result = SpeakerPipelineRunResult()
    logger.info("Starting e2e speaker pipeline")

    try:
        init_db()
        logger.info("Database initialized successfully.")

        text_items = stage_load_and_save_raw(config, result)
        if not text_items:
            logger.warning("No speaker items loaded. Exiting.")
            return result

        prepared = stage_filter_and_route(text_items, config, result)
        if not prepared:
            logger.warning("No items passed filter+routing.")
            return result

        enriched = stage_build_market_contexts(prepared, config, result)
        if not enriched:
            logger.warning("No MarketContext successfully built.")
            return result

        stage_analyze(enriched, config, result)
        return result

    except KeyboardInterrupt:
        result.fatal_error = "Pipeline interrupted by user."
        return result
    except Exception as exc:
        result.fatal_error = str(exc)
        logger.critical(f"Fatal pipeline error: {exc}", exc_info=True)
        return result


# ============================================================================
# CLI
# ============================================================================

def parse_args(argv: Optional[list[str]] = None) -> SpeakerPipelineConfig:
    parser = argparse.ArgumentParser(
        description="End-to-end speaker statement analysis pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--mock-path",
        default="data/mock_speaker_statements.json",
        help="Path to mock JSON data file"
    )
    parser.add_argument(
        "--currencies",
        nargs="+",
        default=["USD", "EUR", "GBP", "JPY"],
        help="Whitelist of currencies to analyze"
    )
    parser.add_argument(
        "--min-speaker-weight",
        type=float,
        default=0.70,
        help="Minimum speaker weight threshold (0.0-1.0)"
    )
    parser.add_argument(
        "--source-types",
        nargs="+",
        default=[],
        help="Allowed source types (e.g., speech interview). Empty = all"
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=10,
        help="Maximum items to analyze per run"
    )
    parser.add_argument("--no-dedup", action="store_true", help="Disable DB duplicate detection")
    parser.add_argument("--no-persist", action="store_true", help="Do not persist signals to DB")
    parser.add_argument("--dry-run", action="store_true", help="Load/filter/enrich only; skip LLM")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop on first error")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    parser.add_argument(
        "--llm-provider",
        choices=[p.value for p in LLMProvider],
        default=LLMProvider.ARVAN.value,
        help="LLM provider"
    )
    parser.add_argument("--llm-model", default="", help="LLM model name")
    parser.add_argument("--temperature", type=float, default=0.1, help="LLM temperature")

    args = parser.parse_args(argv)

    return SpeakerPipelineConfig(
        mock_json_path=args.mock_path,
        currencies=args.currencies,
        min_speaker_weight=args.min_speaker_weight,
        source_types=args.source_types,
        max_items=args.max_items,
        enable_db_dedup=not args.no_dedup,
        persist_signals=not args.no_persist,
        dry_run=args.dry_run,
        continue_on_error=not args.stop_on_error,
        verbose=args.verbose,
        llm_provider=LLMProvider(args.llm_provider),
        llm_model=args.llm_model,
        temperature=args.temperature,
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