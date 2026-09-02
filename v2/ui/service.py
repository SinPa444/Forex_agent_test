"""
ui/service.py
=============

Application/service layer between Streamlit and the existing backend.

This module RE-ORCHESTRATES the same steps as ``orchestration/run_phase3.py::main()``
using the same imported building blocks, but returns structured results instead
of printing to a terminal. It does NOT modify or re-implement any scoring,
routing, or decision logic — the CLI entry point remains the reference
implementation and must stay untouched.

IMPORTANT: keep this orchestration in sync with ``run_phase3.py::main()``
(see .ai/decisions.md — "UI Service Layer").
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from core.database import (
    CompositeSignalDB,
    TradeOutcomeDB,
    get_latest_composite_signal,
    get_trade_memory_stats,
    init_db,
    query_by_time_range,
    session_scope,
)
from core.routing import (
    direction_label,
    list_supported_currencies,
    resolve_asset_route,
    translate_instrument_direction,
)

logger = logging.getLogger("ui.service")

LogSink = Optional[Callable[[str], None]]

_db_initialized = False


def _ensure_db() -> None:
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


def _emit(sink: LogSink, msg: str) -> None:
    logger.info(msg)
    if sink:
        try:
            sink(msg)
        except Exception:
            pass


class UILogHandler(logging.Handler):
    """Temporary logging handler that forwards backend log records to the UI.

    Attached to the root logger for the duration of a live run so the user
    sees the same messages the CLI would print. Always detached in `finally`.
    """

    def __init__(self, sink: Callable[[str], None]):
        super().__init__(level=logging.INFO)
        self._sink = sink
        self.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink(self.format(record))
        except Exception:
            pass


# ============================================================================
# Result models (structured, UI-facing)
# ============================================================================

@dataclass
class CurrencyResult:
    """Structured result of the per-currency multi-agent loop."""

    currency: str
    ticker: str = ""
    alignment: str = ""
    plan: object = None          # ExecutionPlan
    composite: object = None     # CompositeSignal (post risk/cross-asset update)
    tech_metrics: object = None  # TechnicalMetrics
    tech_report: object = None   # TechnicalReport
    risk: object = None          # RiskDecision
    memory: dict = field(default_factory=dict)
    trade_registered: bool = False
    errors: list = field(default_factory=list)


@dataclass
class AnalysisResult:
    """Full result of one live Phase 3 run."""

    started_at: datetime
    finished_at: Optional[datetime] = None
    currencies: dict = field(default_factory=dict)        # ccy -> CurrencyResult
    adjusted_signals: dict = field(default_factory=dict)  # ccy -> CompositeSignal
    detailed_reports: dict = field(default_factory=dict)  # ccy -> str
    global_report: str = ""
    errors: list = field(default_factory=list)


@dataclass
class CurrencySnapshot:
    """DB-only view of one currency (no LLM)."""

    currency: str
    ticker: str = ""
    latest_composite: Optional[dict] = None
    memory: dict = field(default_factory=dict)
    open_trades: list = field(default_factory=list)


@dataclass
class DbSnapshot:
    fetched_at: datetime
    currencies: dict = field(default_factory=dict)  # ccy -> CurrencySnapshot


# ============================================================================
# Helpers
# ============================================================================

def _row_to_dict(row, columns) -> dict:
    return {c: getattr(row, c) for c in columns}


_COMPOSITE_COLS = [
    "id", "created_at", "currency", "direction", "final_score", "confidence",
    "is_tradable", "confluence_status", "components_used", "reasoning",
]

_TRADE_COLS = [
    "id", "created_at", "currency", "direction", "entry_zone_low",
    "entry_zone_high", "stop_loss", "take_profit", "fusion_state",
    "fundamental_score", "technical_score", "temporal_session",
    "status", "closed_at", "evaluated_price",
]


def _register_approved_trade(ccy: str, final_dir: int, fund_sig, fund_status: str,
                             fund_score_val: float, tech_report, risk_decision,
                             temporal_ctx: dict) -> bool:
    """Register an APPROVED trade in TradeOutcomeDB.

    Mirrors the registration block of run_phase3.py::main() exactly,
    including the entry-zone string parsing ("low-high").
    """
    try:
        entry_str = risk_decision.trade_plan.entry_zone
        entry_parts = entry_str.split("-")
        entry_low = float(entry_parts[0].strip()) if len(entry_parts) > 0 else None
        entry_high = float(entry_parts[-1].strip()) if len(entry_parts) > 1 else entry_low

        session_name = temporal_ctx.get("market_session", "Unknown")

        with session_scope() as session_db:
            new_trade = TradeOutcomeDB(
                currency=ccy,
                direction=final_dir,
                entry_zone_low=entry_low,
                entry_zone_high=entry_high,
                stop_loss=risk_decision.trade_plan.stop_loss,
                take_profit=risk_decision.trade_plan.take_profit,
                fusion_state="tech_driven" if not fund_sig else "aligned",
                fundamental_score=fund_score_val if fund_status == "evaluated" else None,
                technical_score=tech_report.score if tech_report else None,
                temporal_session=session_name,
                status="PENDING",
            )
            session_db.add(new_trade)
        return True
    except Exception:
        logger.exception("[%s] Failed to register trade in Memory DB", ccy)
        return False


# ============================================================================
# Live analysis (re-orchestration of run_phase3.main)
# ============================================================================

def run_live_analysis(
    currencies: list,
    *,
    run_ingestion: bool = False,
    timeframe_override: Optional[str] = None,
    provider: str = "arvan",
    model: str = "",
    log_sink: LogSink = None,
) -> AnalysisResult:
    """Run the Phase 3 multi-agent pipeline for the given currencies.

    Same step order as orchestration/run_phase3.py::main(); returns an
    AnalysisResult instead of printing. Synchronous and LLM-heavy — the
    caller (Streamlit) is expected to gate this behind a cache TTL.
    """
    # Imported here so that merely importing ui.service stays cheap and the
    # backend modules are only loaded when a live run is actually requested.
    from orchestration.run_phase3 import build_llm, run_ingestion_phase
    from agents.fundamental.phase2_graph import (
        CompositeSignal,
        build_cross_asset_graph,
        build_phase2_graph,
        get_temporal_context,
    )
    from agents.technical.technical_analyzer import TechnicalAgent
    from agents.risk.risk_manager import RiskManagerAgent
    from agents.supervisor.head_agent import HeadAgent

    _ensure_db()
    result = AnalysisResult(started_at=datetime.utcnow())

    handler = None
    if log_sink:
        handler = UILogHandler(log_sink)
        logging.getLogger().addHandler(handler)

    try:
        if run_ingestion:
            _emit(log_sink, "PHASE 0: Running data ingestion (FF calendar + RSS)...")
            try:
                run_ingestion_phase()
            except Exception as exc:
                msg = f"Phase 0 ingestion failed ({exc}); continuing with existing DB data."
                result.errors.append(msg)
                _emit(log_sink, f"WARNING | {msg}")

        _emit(log_sink, f"Building LLM (provider={provider})...")
        llm = build_llm(provider, model)

        fund_app = build_phase2_graph()
        cross_asset_app = build_cross_asset_graph()
        tech_agent = TechnicalAgent(llm)
        risk_agent = RiskManagerAgent(llm)
        head_agent = HeadAgent(llm)

        temporal_ctx = get_temporal_context()
        _emit(log_sink, f"Temporal context: {temporal_ctx.get('day_of_week')}, "
                        f"{temporal_ctx.get('market_session')} | "
                        f"Weekend: {temporal_ctx.get('is_weekend')}")

        composite_signals = {}

        for ccy in currencies:
            res = CurrencyResult(currency=ccy)
            result.currencies[ccy] = res

            route = resolve_asset_route(ccy)
            if not route:
                res.errors.append(f"No asset route found for {ccy}; skipped.")
                _emit(log_sink, f"[{ccy}] No route found. Skipping.")
                continue
            res.ticker = route.ticker
            res.alignment = route.alignment.value

            try:
                # 1. Head Agent plan (speakers stay force-disabled, as in CLI)
                _emit(log_sink, f"[{ccy}] Consulting Head Agent...")
                plan = head_agent.create_plan(ccy, route.ticker)
                plan.activate_speakers = False  # invariant: speakers offline
                if timeframe_override:
                    plan.technical_timeframe = timeframe_override
                    _emit(log_sink, f"[{ccy}] Timeframe override: {timeframe_override}")
                res.plan = plan
                _emit(log_sink, f"[{ccy}] Plan: Events={plan.activate_events}, "
                                f"News={plan.activate_news}, Tech={plan.activate_technical} "
                                f"({plan.technical_timeframe}) | Regime={plan.market_regime}")

                # 2. Fundamental Agent (Phase 2 graph)
                fund_sig = None
                run_fund = plan.activate_events or plan.activate_news or plan.activate_speakers
                if run_fund:
                    _emit(log_sink, f"[{ccy}] Running Fundamental Agent...")
                    state_input = {
                        "currency": ccy,
                        "llm": llm,
                        "temporal_context": temporal_ctx,
                        "enable_events": plan.activate_events,
                        "enable_news": plan.activate_news,
                        "enable_speakers": plan.activate_speakers,
                    }
                    try:
                        fund_result = fund_app.invoke(state_input, {"recursion_limit": 15})
                        fund_sig = fund_result.get("composite_signal")
                        if fund_sig:
                            _emit(log_sink, f"[{ccy}] Fundamental: {fund_sig.confluence_status} "
                                            f"| Dir: {fund_sig.direction} | Score: {fund_sig.final_score:+.2f}")
                    except Exception as exc:
                        res.errors.append(f"Fundamental pipeline failed: {exc}")
                        _emit(log_sink, f"[{ccy}] ERROR | Fundamental pipeline failed: {exc}")
                else:
                    _emit(log_sink, f"[{ccy}] Fundamental Agent skipped by Head Agent.")

                # 3. Technical Agent
                tech_metrics, tech_report = None, None
                if plan.activate_technical:
                    _emit(log_sink, f"[{ccy}] Running Technical Agent on {plan.technical_timeframe}...")
                    try:
                        # Phase 1: پارامتر مرده temporal_context حذف شد (W6)
                        tech_metrics, tech_report = tech_agent.analyze(
                            route.ticker,
                            timeframe=plan.technical_timeframe,
                        )
                        res.tech_metrics = tech_metrics
                        res.tech_report = tech_report
                        _emit(log_sink, f"[{ccy}] Technical Dir: {tech_report.direction} "
                                        f"| Score: {tech_report.score:+.2f}")
                    except Exception as exc:
                        res.errors.append(f"Technical pipeline failed: {exc}")
                        _emit(log_sink, f"[{ccy}] ERROR | Technical pipeline failed: {exc}")

                # 4. Risk Manager
                if fund_sig or tech_report:
                    _emit(log_sink, f"[{ccy}] Running Risk Manager...")

                    fund_status = "not_evaluated"
                    fund_dir = 0
                    fund_score_val = 0.0
                    fund_conf = 0.0
                    fund_tradable = False
                    fund_reason = "Fundamental analysis not run."

                    if fund_sig:
                        fund_status = "evaluated"
                        fund_dir = fund_sig.direction
                        fund_score_val = fund_sig.final_score
                        fund_conf = fund_sig.confidence
                        fund_tradable = fund_sig.is_tradable
                        fund_reason = fund_sig.reasoning

                    mem_stats = get_trade_memory_stats(ccy)
                    res.memory = mem_stats

                    try:
                        risk_decision = risk_agent.evaluate(
                            fund_direction=fund_dir,
                            fund_score=fund_score_val,
                            fund_confidence=fund_conf,
                            fund_tradable=fund_tradable,
                            fund_reasoning=fund_reason,
                            tech_direction=tech_report.direction if tech_report else 0,
                            tech_confidence=tech_report.confidence if tech_report else 0.0,
                            tech_strategy=tech_report.strategy if tech_report else "No tech data",
                            tech_reasoning=tech_report.reasoning if tech_report else "Technical analysis not run.",
                            current_price=tech_metrics.current_price if tech_metrics else None,
                            nearest_support=tech_metrics.active_bullish_ob if tech_metrics else None,
                            nearest_resistance=tech_metrics.active_bearish_ob if tech_metrics else None,
                            mem_total=mem_stats["total_trades"],
                            mem_wins=mem_stats["wins"],
                            mem_losses=mem_stats["losses"],
                            mem_win_rate=mem_stats["win_rate"],
                        )
                    except Exception as exc:
                        res.errors.append(f"Risk Manager failed: {exc}")
                        _emit(log_sink, f"[{ccy}] ERROR | Risk Manager failed: {exc}")
                        continue
                    res.risk = risk_decision
                    _emit(log_sink, f"[{ccy}] Risk Decision: {risk_decision.decision}")

                    # Final direction/score — identical semantics to run_phase3
                    final_dir = tech_report.direction if tech_report else (fund_sig.direction if fund_sig else 0)
                    final_score = fund_score_val if fund_status == "evaluated" else (
                        tech_report.score if tech_report else 0.0)
                    is_tradable = risk_decision.decision == "APPROVED"

                    # Reasoning merge (same shape as CLI)
                    reasoning_parts = []
                    if fund_sig:
                        reasoning_parts.append(f"[Fundamental]: {fund_sig.reasoning}")
                    if tech_report:
                        reasoning_parts.append(f"[Technical]: {tech_report.reasoning}")
                    reasoning_parts.append(f"[Risk Manager ({risk_decision.decision})]: {risk_decision.reasoning}")
                    if risk_decision.trade_plan:
                        tp = risk_decision.trade_plan
                        reasoning_parts.append(
                            f"[Trade Plan]: Entry={tp.entry_zone}, SL={tp.stop_loss}, TP={tp.take_profit}")

                    # 5. Register APPROVED trades in memory DB
                    if is_tradable and risk_decision.trade_plan:
                        res.trade_registered = _register_approved_trade(
                            ccy, final_dir, fund_sig, fund_status, fund_score_val,
                            tech_report, risk_decision, temporal_ctx)
                        if res.trade_registered:
                            _emit(log_sink, f"[{ccy}] Trade registered in Memory DB (PENDING).")

                    # 6. Final composite (same None-vs-0.0 semantics as CLI)
                    if not fund_sig:
                        fund_sig = CompositeSignal(
                            currency=ccy, direction=final_dir, final_score=final_score,
                            confidence=fund_conf, is_tradable=is_tradable,
                            confluence_status="tech_driven", components_used=["technical"],
                            reasoning=" | ".join(reasoning_parts),
                        )
                    else:
                        fund_sig = fund_sig.model_copy(update={
                            "is_tradable": is_tradable,
                            "reasoning": " | ".join(reasoning_parts),
                        })

                    res.composite = fund_sig
                    composite_signals[ccy] = fund_sig
                else:
                    _emit(log_sink, f"[{ccy}] Nothing to evaluate (no fundamental, no technical).")
            except Exception as exc:
                res.errors.append(f"Unexpected failure: {exc}")
                _emit(log_sink, f"[{ccy}] ERROR | Unexpected failure: {exc}")
                logger.exception("[%s] Unexpected failure", ccy)

        # Stage 2: Cross-Asset consistency + global summary
        if composite_signals:
            _emit(log_sink, "Running Cross-Asset Consistency & Global Summary...")
            try:
                final_result = cross_asset_app.invoke({
                    "llm": llm,
                    "temporal_context": temporal_ctx,
                    "composite_signals": composite_signals,
                })
                result.adjusted_signals = final_result.get("composite_signals", composite_signals)
                result.detailed_reports = final_result.get("detailed_reports", {})
                result.global_report = final_result.get("global_report", "No report.")
                # Reflect cross-asset adjustments back into per-currency cards
                for ccy, sig in result.adjusted_signals.items():
                    if ccy in result.currencies:
                        result.currencies[ccy].composite = sig
            except Exception as exc:
                result.errors.append(f"Cross-asset graph failed: {exc}")
                _emit(log_sink, f"ERROR | Cross-asset graph failed: {exc}")
        else:
            _emit(log_sink, "No signals generated; cross-asset stage skipped.")

        _emit(log_sink, "Run finished.")
        return result
    finally:
        result.finished_at = datetime.utcnow()
        if handler:
            logging.getLogger().removeHandler(handler)


# ============================================================================
# DB-only reads (no LLM) — Dashboard DB View & History tab
# ============================================================================

def load_db_snapshot(currencies: Optional[list] = None) -> DbSnapshot:
    """Load latest composites, memory stats and open trades from the DB.

    Never cached: trade_evaluator updates trade_outcomes in the background
    and the UI must reflect WIN/LOSS changes immediately.
    """
    _ensure_db()
    ccys = currencies or list_supported_currencies()
    snapshot = DbSnapshot(fetched_at=datetime.utcnow())

    with session_scope() as session:
        for ccy in ccys:
            snap = CurrencySnapshot(currency=ccy)
            route = resolve_asset_route(ccy)
            snap.ticker = route.ticker if route else ""

            row = get_latest_composite_signal(session, currency=ccy)
            if row is not None:
                data = _row_to_dict(row, _COMPOSITE_COLS)
                session.expunge(row)
                snap.latest_composite = data

            snap.memory = get_trade_memory_stats(ccy)

            open_rows = (
                session.query(TradeOutcomeDB)
                .filter(TradeOutcomeDB.currency == ccy, TradeOutcomeDB.status == "PENDING")
                .order_by(TradeOutcomeDB.created_at.desc())
                .all()
            )
            snap.open_trades = [_row_to_dict(r, _TRADE_COLS) for r in open_rows]
            for r in open_rows:
                session.expunge(r)

            snapshot.currencies[ccy] = snap

    return snapshot


def load_trade_history(currency: Optional[str] = None,
                       range_type: str = "last_30_days") -> list:
    """Trade outcomes history for the History tab (optionally per currency)."""
    _ensure_db()
    with session_scope() as session:
        extra = [TradeOutcomeDB.currency == currency] if currency else None
        rows = query_by_time_range(
            session, TradeOutcomeDB, "created_at", range_type,
            extra_filters=extra,
        ).all()
        out = [_row_to_dict(r, _TRADE_COLS) for r in rows]
        for r in rows:
            session.expunge(r)
        return out


def load_composite_history(currency: Optional[str] = None,
                           range_type: str = "last_30_days",
                           limit: int = 200) -> list:
    """Recent composite signals for the History tab."""
    _ensure_db()
    with session_scope() as session:
        extra = [CompositeSignalDB.currency == currency] if currency else None
        rows = query_by_time_range(
            session, CompositeSignalDB, "created_at", range_type,
            extra_filters=extra, limit=limit,
        ).all()
        out = [_row_to_dict(r, _COMPOSITE_COLS) for r in rows]
        for r in rows:
            session.expunge(r)
        return out


# ============================================================================
# Display helpers (pure, no I/O beyond routing data)
# ============================================================================

def instrument_view_label(currency: str, direction: int) -> str:
    """Translate a currency-native direction to the instrument-view label."""
    route = resolve_asset_route(currency)
    if not route:
        return direction_label(direction)
    inst_dir = translate_instrument_direction(direction, route.alignment)
    return f"{direction_label(inst_dir)} ({route.ticker})"


def format_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
