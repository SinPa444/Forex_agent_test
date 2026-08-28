"""
phase2_graph.py
===============
Phase 2 Live Multi-Agent Orchestration using LangGraph.

This graph executes Phase 1 pipelines in-memory as nodes, combines their
outputs deterministically, and generates a final LLM-based executive summary.

Graph Flow:
START -> fetch_and_analyze_event -> fetch_and_analyze_news -> aggregate_signals -> generate_final_summary -> END
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional, TypedDict, List
from datetime import datetime, timezone, timedelta

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

# Phase 1 Pipelines & Components
import datetime as dt

from core.database import SessionLocal, EventHistoryDB, TradingSignalDB, RawNewsItemDB, NewsSignalDB
from agents.fundamental.data_fetcher import fetch_market_context
import ingestion.ff_bridge as ff_bridge
from agents.fundamental.nlp_event import analyze_economic_event, event_input_from_db_record
from agents.fundamental.nlp_news import analyze_news_digest
from core.routing import resolve_asset_route

from core.models import EventSignal, NewsSignal, NewsItem

logger = logging.getLogger(__name__)



# ============================================================================
# Temporal Context Engine
# ============================================================================


def get_temporal_context() -> dict:
    """
    Compute current temporal and market session context in UTC.
    Used for risk management (e.g., blocking weekends trades) and LLM awareness.
    """
    now = datetime.now(timezone.utc)
    dow = now.strftime("%A")  # Day of week
    hour = now.hour
    
    # Market session logic (approximate UTC boundaries)
    if 0 <= hour < 7:
        session = "Asian Session"
    elif 7 <= hour < 12:
        session = "London Session"
    elif 12 <= hour < 16:
        session = "London/NY Overlap (High Volatility)"
    elif 16 <= hour < 21:
        session = "New York Session"
    else:
        session = "Market After Hours / Closing"
        
    
    # weekends check (FX market closes ~21:00 UTC Friday, opens ~21:00 UTC Sunday)
    is_weekend = (dow == "Saturday") or (dow == "Friday" and hour >= 21) or (dow == "Sunday" and hour <= 21)
    
    # simple major holiday check (can be expanded)
    is_holiday = (now.month == 12 and now.day in [25, 26]) or (now.month == 1 and now.day == 1)
    
    return {
        "current_time_utc": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "day_of_week": dow,
        "market_session": session,
        "is_weekend": is_weekend,
        "is_holiday": is_holiday
    }


# ============================================================================
# State Definition
# ============================================================================

class PipelineState(TypedDict, total=False):
    """
    The central state object passed between nodes in the LangGraph.
    """
    currency: str
    llm: BaseChatModel
    temporal_context: dict
    
    # pipeline toggles
    enable_events: bool
    enable_news: bool
    enable_speakers: bool
    use_digest_cache: bool  # Phase 5: پیش‌فرض روشن — اگر مجموعه اخبار عوض نشده، کال LLM دایجست رد می‌شود
    
    
    event_signals: List[EventSignal]  # Changed to list to support multiple events
    news_signal: Optional[NewsSignal]
    speaker_signal: Optional[dict]
    
    composite_signal: Optional[Any]
    final_report: Optional[str]
    
    
# ============================================================================
# Composite Signal Schema
# ============================================================================

class CompositeSignal(BaseModel):
    currency: str
    direction: int = Field(description="1=Bullish, -1=Bearish, 0=Neutral")
    final_score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    is_tradable: bool
    confluence_status: str
    components_used: list[str]
    reasoning: str


# ============================================================================
# Node 1: Live Events Pipeline (High & Medium Impact)
# ============================================================================

def fetch_and_analyze_event_node(state: PipelineState) -> dict:
    """
    Scrapes recent events via ff_bridge, reads ALL High/Medium events from DB,
    analyzes each one, and updates state with a list of signals.
    """
    if not state.get("enable_events", True):
        logger.info("[Node 1] Event pipeline disabled by configuration.")
        return {"event_signals": []}
    
    currency = state["currency"]
    llm = state["llm"]
    logger.info(f"[Node 1] Scraping & analyzing live events for {currency}...")

    event_inputs = []

    try:
        # Query DB for ALL High and Medium events in last 48h
        session = SessionLocal()
        try:
            cutoff = dt.datetime.utcnow() - timedelta(hours=48)
            records = session.query(EventHistoryDB).filter(
                EventHistoryDB.currency == currency,
                EventHistoryDB.actual.isnot(None),
                EventHistoryDB.impact.in_(["High", "Medium"]),
                EventHistoryDB.date >= cutoff
            ).order_by(EventHistoryDB.date.desc()).all()
            
            if records:
                # Deduplicate by title to avoid re-analyzing the same event scraped in different weeks
                seen_titles = set()
                unique_records = []
                for record in records:
                    if record.title not in seen_titles:
                        unique_records.append(record)
                        seen_titles.add(record.title)
                
                logger.info(f"[Node 1] Found {len(records)} High/Medium released events in DB ({len(unique_records)} unique).")
                for record in unique_records:
                    event_inputs.append(event_input_from_db_record(record))
        finally:
            session.close()

    except Exception as exc:
        logger.error(f"[Node 1] Scraper or DB phase failed: {exc}", exc_info=True)

    if not event_inputs:
        logger.info(f"[Node 1] No released events found for {currency} after scraping.")
        return {"event_signals": []}

    # 3. Analyze all events
    event_signals = []
    try:
        route = resolve_asset_route(currency)
        for event_input in event_inputs:
            try:
                context = fetch_market_context(
                    ticker=route.ticker,
                    currency=route.market_context_currency,
                    event_title=event_input.title,
                    actual=event_input.actual,
                    forecast=event_input.forecast,
                    previous=event_input.previous,
                    event_currency=event_input.currency
                )

                _, signal = analyze_economic_event(
                    event_input=event_input,
                    market_context=context,
                    ticker=route.ticker,
                    llm=llm,
                    persist=True
                )
                event_signals.append(signal)
                logger.info(f"[Node 1] Event analyzed: {event_input.title} | Score: {signal.final_score:.2f}")
            except Exception as exc_inner:
                logger.error(f"[Node 1] Failed to analyze event {event_input.title}: {exc_inner}")

        return {"event_signals": event_signals}

    except Exception as exc:
        logger.error(f"[Node 1] Event pipeline failed: {exc}", exc_info=True)
        return {"event_signals": []}


# ============================================================================
# Node 2: Live News Pipeline
# ============================================================================

# Phase 5: کش دایجست اخبار — اگر مجموعه لینک‌های top-10 خبر تغییری نکرده باشد،
# همان تحلیل قبلی از DB بازسازی می‌شود و کال LLM رد می‌شود.
# حداکثر سن کش: بعد از این مدت حتی با لینک‌های یکسان، تحلیل تازه می‌گیریم
# (چون market context مثل نوسان/ATR ممکن است عوض شده باشد).
DIGEST_CACHE_TTL_HOURS: float = 6.0


def _digest_links_hash(links: list[str]) -> str:
    """هش پایدار از مجموعه لینک‌ها (مرتب‌شده تا به ترتیب حساس نباشد)."""
    return hashlib.sha256(json.dumps(sorted(links)).encode("utf-8")).hexdigest()


def _try_cached_digest_signal(
    currency: str,
    ticker: str,
    news_items: list,
) -> Optional[NewsSignal]:
    """
    تلاش برای بازاستفاده از آخرین دایجست تحلیل‌شده اگر مجموعه اخبار تغییر نکرده باشد.

    منطق:
      1) هش sha256 لینک‌های مرتب‌شده آیتم‌های فعلی را حساب می‌کنیم.
      2) آخرین رکورد NewsSignalDB با source='Aggregated' و is_aggregated=True
         برای همین ticker را می‌خوانیم.
      3) اگر رکورد تازه‌تر از DIGEST_CACHE_TTL_HOURS باشد و هش لینک‌های
         ذخیره‌شده (source_links_json) با هش فعلی برابر باشد → NewsSignal از
         فیلدهای رکورد بازسازی می‌شود و کال LLM کلاً حذف می‌شود.

    Returns:
        NewsSignal بازسازی‌شده از کش، یا None (یعنی باید LLM صدا زده شود).
    """
    current_links = [item.link for item in news_items if getattr(item, "link", None)]
    if not current_links:
        return None
    current_hash = _digest_links_hash(current_links)

    session = SessionLocal()
    try:
        row = (
            session.query(NewsSignalDB)
            .filter(
                NewsSignalDB.source == "Aggregated",
                NewsSignalDB.is_aggregated == True,  # noqa: E712
                NewsSignalDB.ticker == ticker,
            )
            .order_by(NewsSignalDB.created_at.desc())
            .first()
        )

        if row is None or not row.source_links_json:
            return None

        # چک تازگی کش
        created_at = row.created_at
        if created_at is not None:
            try:
                age_hours = (dt.datetime.utcnow() - created_at).total_seconds() / 3600.0
            except Exception:
                age_hours = float("inf")
            if age_hours > DIGEST_CACHE_TTL_HOURS:
                logger.info(
                    f"[Node 2] Digest cache for {currency} expired "
                    f"({age_hours:.1f}h > {DIGEST_CACHE_TTL_HOURS}h). Re-analyzing."
                )
                return None

        # چک تطابق مجموعه لینک‌ها
        try:
            stored_links = json.loads(row.source_links_json)
        except Exception:
            return None
        if not isinstance(stored_links, list) or not stored_links:
            return None
        if _digest_links_hash([l for l in stored_links if l]) != current_hash:
            return None

        # بازسازی NewsSignal از فیلدهای رکورد — بدون هیچ کال LLM
        cached = NewsSignal(
            reasoning=row.reasoning or "",
            asset_class=row.asset_class or "forex",
            direction=row.direction if row.direction is not None else 0,
            final_score=float(row.final_score) if row.final_score is not None else 0.0,
            confidence=float(row.confidence) if row.confidence is not None else 0.0,
            is_tradable=bool(row.is_tradable),
            expected_volatility_level=row.expected_volatility_level or "Normal",
            signal_half_life_mins=int(row.signal_half_life_mins) if row.signal_half_life_mins else 60,
            cross_asset_signals={},  # cross_asset در DB ذخیره نمی‌شود — تأثیری در aggregation ندارد
            source=row.source or "Aggregated",
            source_reliability=float(row.source_reliability) if row.source_reliability is not None else 0.0,
            event_category_weight=float(row.event_category_weight) if row.event_category_weight is not None else 0.5,
            data_completeness_score=float(row.data_completeness_score) if row.data_completeness_score is not None else 0.5,
            data_quality_factor=float(row.data_quality_factor) if row.data_quality_factor is not None else 0.8,
            headline_body_alignment=float(row.headline_body_alignment) if row.headline_body_alignment is not None else 0.5,
            quantitative_alignment=float(row.quantitative_alignment) if row.quantitative_alignment is not None else 0.5,
            ticker=row.ticker or ticker,
        )
        logger.info(
            f"[Node 2] Digest cache HIT for {currency} — reusing analysis from "
            f"{created_at} ({len(current_links)} links unchanged). LLM call skipped."
        )
        return cached

    except Exception as exc:
        logger.warning(f"[Node 2] Digest cache lookup failed for {currency}: {exc} — falling back to LLM.")
        return None
    finally:
        session.close()


def _news_recency_key(item) -> tuple[float, float]:
    """
    کلید مرتب‌سازی اخبار: (زمان انتشار، reliability منبع).
    برای انتخاب تازه‌ترین اخبار؛ در تساوی زمانی، منبع معتبرتر جلوتر می‌ایستد.
    """
    ts = 0.0
    published = getattr(item, "published", None)
    if published is not None:
        try:
            ts = published.timestamp()
        except Exception:
            ts = 0.0
    reliability = item.source_reliability if item.source_reliability is not None else 0.0
    return (ts, reliability)


def fetch_and_analyze_news_node(state: PipelineState) -> dict:
    """
    Reads recent news for currency from raw_news_items DB (populated by the
    ingestion phase at run start — no re-crawl here), analyzes digest, and
    updates state.
    """
    if not state.get("enable_news", True):
        logger.info("[Node 2] News pipeline disabled by configuration.")
        return {"news_signal": None}

    currency = state["currency"]
    llm = state["llm"]
    logger.info(f"[Node 2] Loading news for {currency} from DB (no re-crawl)...")

    try:
        # اخبار تازه از DB — Phase 0 در run_phase3 قبلاً RSSها را کرال و ذخیره کرده است
        cutoff = dt.datetime.utcnow() - timedelta(hours=48)
        session = SessionLocal()
        try:
            records = (
                session.query(RawNewsItemDB)
                .filter(
                    RawNewsItemDB.currency == currency,
                    RawNewsItemDB.published_at.isnot(None),
                    RawNewsItemDB.published_at >= cutoff,
                )
                .all()
            )
        finally:
            session.close()

        # بازسازی NewsItem از رکوردهای DB برای تحلیل
        routed_items = [
            NewsItem(
                title=r.title,
                summary=r.summary or "",
                published=r.published_at,
                link=r.link,
                source=r.source,
                source_reliability=r.source_reliability,
                category=r.category,
                currency=r.currency,
                impact=r.impact,
            )
            for r in records
        ]

        # مرتب‌سازی: تازه‌ترین اول (tie-breaker: منبع معتبرتر) تا برش top-N واقعاً «most recent» باشد
        routed_items.sort(key=_news_recency_key, reverse=True)

        if not routed_items:
            logger.info(f"[Node 2] No recent news found for {currency}.")
            return {"news_signal": None}

        route = resolve_asset_route(currency)
        
        # Build a generic context for news digest
        context = fetch_market_context(
            ticker=route.ticker,
            currency=route.market_context_currency,
            event_title=f"Macro News Digest for {currency}",
            actual=None,
            forecast=None,
            previous=None,
            event_currency=currency
        )

        # Limit to 10 most recent items to save LLM context
        news_items = routed_items[:20]

        # Phase 5: اگر مجموعه اخبار نسبت به آخرین تحلیل عوض نشده، از کش استفاده کن
        if state.get("use_digest_cache", True):
            cached_signal = _try_cached_digest_signal(currency, route.ticker, news_items)
            if cached_signal is not None:
                return {"news_signal": cached_signal}

        _, signal = analyze_news_digest(
            currency=currency,
            news_items=news_items,
            market_context=context,
            ticker=route.ticker,
            llm=llm,
            persist=True
        )
        logger.info(f"[Node 2] News digest analyzed ({len(news_items)} items). Score: {signal.final_score:.2f}")
        return {"news_signal": signal}

    except Exception as exc:
        logger.error(f"[Node 2] News pipeline failed: {exc}", exc_info=True)
        return {"news_signal": None}
    
    
# ============================================================================
# Node 3: Latest Speaker Signal (DB Lookup)
# ============================================================================

def fetch_latest_speaker_signal_node(state: PipelineState) -> dict:
    """Fetches the latest speaker signal from DB for this currency."""
    if not state.get("enable_speakers", True):
        logger.info("[Node 3] Speakers pipeline disabled. Skipping...")
        return {"speaker_signal": None}
    
    currency = state["currency"]
    logger.info(f"[Node 3] Fetching latest speaker signal for {currency}...")
    
    session = SessionLocal()
    try:
        # look back 24 hours for a relevant speaker signal
        cutoff = dt.datetime.utcnow() - timedelta(hours=24)
        record = session.query(TradingSignalDB).filter(
            TradingSignalDB.asset_class == currency,
            TradingSignalDB.timestamp >= cutoff,
        ).order_by(TradingSignalDB.timestamp.desc()).first()
        
        if not record:
            logger.info(f"[Node 3] No recent speaker signal found for {currency}.")
            return {"speaker_signal": None}
        
        # extract only needed fields for aggregation
        signal_data = {
            "direction": record.direction,
            "final_score": record.final_score or 0.0,
            "confidence": record.confidence or 0.0,
            "is_tradable": record.is_tradable or False,
            "reasoning": record.reasoning or "No reasoning provided.",
        }
        
        logger.info(f"[Node 3] Latest speaker signal found | Score: {signal_data['final_score']:.2f}, Dir: {signal_data['direction']}")
        return {"speaker_signal": signal_data}
    finally:
        session.close()


# ============================================================================
# Node 4: Deterministic Aggregator
# ============================================================================

def aggregate_signals_node(state: PipelineState) -> dict:
    """Combines Event, News, and Speaker signals deterministically."""
    currency = state.get("currency", "Unknown")
    event_sigs: List[EventSignal] = state.get("event_signals", [])
    news_sig: Optional[NewsSignal] = state.get("news_signal")
    speaker_sig: Optional[dict] = state.get("speaker_signal")
    
    logger.info(f"[Aggregator] Aggregating for {currency} | Events: {len(event_sigs)} | News: {bool(news_sig)} | Speaker: {bool(speaker_sig)}")

    # 1. Aggregate Event Signals (Impact-Weighted Average)
    event_net_score = 0.0
    event_net_conf = 0.0
    event_direction = 0
    has_events = bool(event_sigs)

    if has_events:
        total_weight = sum(s.event_impact_weight for s in event_sigs)
        if total_weight > 0:
            event_net_score = sum(s.final_score * s.event_impact_weight for s in event_sigs) / total_weight
            event_net_conf = sum(s.confidence * s.event_impact_weight for s in event_sigs) / total_weight
            
            if event_net_score > 0.05: event_direction = 1
            elif event_net_score < -0.05: event_direction = -1
        else:
            has_events = False

    # Helper to apply confluence rules iteratively
    def apply_confluence(base_comp: CompositeSignal, new_sig_dir: int, new_sig_conf: float, component_name: str) -> CompositeSignal:
        if new_sig_dir == 0 or base_comp.direction == 0:
            return base_comp # No actionable confluence if either is neutral
            
        if base_comp.direction == new_sig_dir:
            boosted_score = min(abs(base_comp.final_score) * 1.1, 1.0) * base_comp.direction
            boosted_conf = min(max(base_comp.confidence, new_sig_conf) + 0.10, 1.0)
            return base_comp.model_copy(update={
                "final_score": boosted_score,
                "confidence": boosted_conf,
                "is_tradable": abs(boosted_score) >= 0.40 and boosted_conf >= 0.60,
                "confluence_status": "aligned",
                "components_used": base_comp.components_used + [component_name],
                "reasoning": base_comp.reasoning + f" {component_name.capitalize()} aligned (Dir: {new_sig_dir})."
            })
        else:
            penalized_score = base_comp.final_score * 0.7
            penalized_conf = max(base_comp.confidence - 0.20, 0.0)
            return base_comp.model_copy(update={
                "final_score": penalized_score,
                "confidence": penalized_conf,
                "is_tradable": False,
                "confluence_status": "conflicting",
                "components_used": base_comp.components_used + [component_name],
                "reasoning": base_comp.reasoning + f" {component_name.capitalize()} conflicts (Dir: {new_sig_dir})."
            })

    # 2. Establish Base Composite hierarchically
    components = []
    
    if has_events and event_direction != 0:
        components.append("event")
        composite = CompositeSignal(
            currency=currency, direction=event_direction, final_score=event_net_score,
            confidence=event_net_conf, is_tradable=(abs(event_net_score) >= 0.40 and event_net_conf >= 0.55),
            confluence_status="event_only", components_used=components, reasoning="Event directional base."
        )
        # Apply News and Speaker confluence
        if news_sig:
            composite = apply_confluence(composite, news_sig.direction, news_sig.confidence, "news")
        if speaker_sig:
            composite = apply_confluence(composite, speaker_sig["direction"], speaker_sig["confidence"], "speaker")
            
    elif news_sig and news_sig.direction != 0:
        components.append("news")
        composite = CompositeSignal(
            currency=currency, direction=news_sig.direction, final_score=news_sig.final_score,
            confidence=news_sig.confidence, is_tradable=False, # News alone is low conviction
            confluence_status="news_only", components_used=components, reasoning="News directional base."
        )
        # Apply Speaker confluence
        if speaker_sig:
            composite = apply_confluence(composite, speaker_sig["direction"], speaker_sig["confidence"], "speaker")
            
    elif speaker_sig and speaker_sig["direction"] != 0:
        components.append("speaker")
        composite = CompositeSignal(
            currency=currency, direction=speaker_sig["direction"], final_score=speaker_sig["final_score"],
            confidence=speaker_sig["confidence"], is_tradable=False, # Speaker alone is low conviction
            confluence_status="speaker_only", components_used=components, reasoning="Speaker directional base."
        )
    else:
        composite = CompositeSignal(
            currency=currency, direction=0, final_score=0.0, confidence=0.0,
            is_tradable=False, confluence_status="no_data", components_used=[], reasoning="No directional data."
        )

    # 3. Temporal Risk Filter
    temporal_ctx = state.get("temporal_context", {})
    is_weekend = temporal_ctx.get("is_weekend", False)
    is_holiday = temporal_ctx.get("is_holiday", False)
    
    if (is_weekend or is_holiday) and composite.is_tradable:
        composite = composite.model_copy(update={
            "is_tradable": False,
            "reasoning": composite.reasoning + " Tradability blocked due to market closure (weekend/holiday)."
        })
        logger.info(f"[Aggregator] Tradability blocked due to weekend/holiday.")

    return {"composite_signal": composite}


# ============================================================================
# Node 6: LLM Executive Summary
# ============================================================================

_SUMMARY_SYSTEM_PROMPT = """\
You are a senior macro strategist. Synthesize the provided fundamental signals into a brief, actionable executive summary.
Highlight the confluence (alignment/conflict) between quantitative data and macro news.
If the signal is tradable, state the directional bias clearly.
Keep it under 5 sentences. Do not invent numbers; use only what is provided.
IMPORTANT TEMPORAL RULE: If the Temporal Context indicates it is a weekend, holiday, or market after-hours, explicitly mention the timing risks (e.g., weekend gap risk, low liquidity) and suggest avoiding new positions or scalping only, even if the signal is fundamentally strong.
"""

def generate_final_summary_node(state: PipelineState) -> dict:
    """Uses LLM to generate a final human-readable report."""
    llm = state["llm"]
    composite: CompositeSignal = state["composite_signal"]
    event_sigs: List[EventSignal] = state.get("event_signals", [])
    news_sig: Optional[NewsSignal] = state.get("news_signal")
    temporal_ctx = state.get("temporal_context", {})

    if composite.confluence_status == "no_data":
        return {"final_report": "No fundamental data available to generate a report."}

    # Gather all event reasonings for LLM context
    event_reasonings = " | ".join([f"{s.event_title} (Score: {s.final_score:.2f}, Impact: {s.event_impact}): {s.reasoning}" for s in event_sigs])
    if not event_reasonings:
        event_reasonings = "None"

    temporal_text = (
        f"Day: {temporal_ctx.get('day_of_week', 'Unknown')}, "
        f"Session: {temporal_ctx.get('market_session', 'Unknown')}, "
        f"Weekend/Holiday: {temporal_ctx.get('is_weekend', False) or temporal_ctx.get('is_major_holiday', False)}"
    )

    human_text = f"""
    Target Currency: {composite.currency}
    Composite Status: {composite.confluence_status} (Score: {composite.final_score:.2f}, Conf: {composite.confidence:.2f}, Tradable: {composite.is_tradable})
    
    Note: The event score is an impact-weighted aggregate of {len(event_sigs)} processed events.
    
    Processed Event Signals ({len(event_sigs)} total):
    {event_reasonings}
    
    News Digest Reasoning: {news_sig.reasoning if news_sig else "None"}
    
    Temporal Context: {temporal_text}
    
    Write the executive summary:
    """

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SUMMARY_SYSTEM_PROMPT),
        ("human", human_text)
    ])
    
    chain = prompt | llm
    try:
        response = chain.invoke({})
        report = response.content if hasattr(response, 'content') else str(response)
        logger.info(f"[Node 4] Final report generated.")
        return {"final_report": report.strip()}
    except Exception as exc:
        logger.error(f"[Node 4] LLM Summary failed: {exc}")
        return {"final_report": composite.reasoning}

# ============================================================================
# Graph Construction
# ============================================================================

def build_phase2_graph():
    workflow = StateGraph(PipelineState)
    
    workflow.add_node("fetch_event", fetch_and_analyze_event_node)
    workflow.add_node("fetch_news", fetch_and_analyze_news_node)
    workflow.add_node("fetch_speaker", fetch_latest_speaker_signal_node)
    workflow.add_node("aggregate", aggregate_signals_node)
    
    workflow.add_edge(START, "fetch_event")
    workflow.add_edge("fetch_event", "fetch_news")
    workflow.add_edge("fetch_news", "fetch_speaker")
    workflow.add_edge("fetch_speaker", "aggregate")
    workflow.add_edge("aggregate", END)
    
    return workflow.compile()




# ============================================================================
# Phase 2.5: Cross-Asset Consistency Graph
# ============================================================================

class GlobalPipelineState(TypedDict, total=False):
    """State for the cross-asset validation graph."""
    llm: BaseChatModel
    temporal_context: dict
    composite_signals: dict[str, CompositeSignal]  # Dictionary of currency -> CompositeSignal
    detailed_reports: dict[str, str]
    global_report: Optional[str]
    enable_reports: bool  # Phase 5: پیش‌فرض خاموش — گزارش‌های تفصیلی ~30% هزینه توکن بودند


def cross_asset_consistency_node(state: GlobalPipelineState) -> dict:
    """
    Validates signals across currencies to detect macro anomalies.
    Penalizes inverse correlations that move in the same direction (e.g., USD and CAD both bullish).
    """
    signals: dict[str, CompositeSignal] = state.get("composite_signals", {})
    if not signals:
        return {"composite_signals": {}}

    # Make a deep copy to modify
    updated_signals = {k: v.model_copy(deep=True) for k, v in signals.items()}
    
    logger.info("[Cross-Asset] Validating macro consistency...")
    
    # Define inverse correlation pairs
    # If both have the same direction, it's a macro anomaly (e.g., USD up and CAD up)
    inverse_pairs = [("USD", "CAD"), ("USD", "XAU"), ("USD", "OIL")]
    
    for a, b in inverse_pairs:
        if a in updated_signals and b in updated_signals:
            sig_a = updated_signals[a]
            sig_b = updated_signals[b]
            
            # Only check if both have a clear directional bias
            if sig_a.direction != 0 and sig_a.direction == sig_b.direction:
                logger.warning(f"[Cross-Asset] Anomaly detected: {a} and {b} are both {sig_a.direction}. Penalizing.")
                
                # Penalize signal A
                sig_a.final_score = round(sig_a.final_score * 0.8, 4)
                sig_a.confidence = max(0.0, round(sig_a.confidence - 0.15, 4))
                sig_a.is_tradable = sig_a.is_tradable and sig_a.confidence >= 0.55 and abs(sig_a.final_score) >= 0.40
                sig_a.reasoning += f" Cross-asset anomaly with {b} penalized."
                
                # Penalize signal B
                sig_b.final_score = round(sig_b.final_score * 0.8, 4)
                sig_b.confidence = max(0.0, round(sig_b.confidence - 0.15, 4))
                sig_b.is_tradable = sig_b.is_tradable and sig_b.confidence >= 0.55 and abs(sig_b.final_score) >= 0.40
                sig_b.reasoning += f" Cross-asset anomaly with {a} penalized."

    return {"composite_signals": updated_signals}




_DETAILED_CURRENCY_PROMPT = """\
You are a senior macro analyst. Write a detailed, multi-paragraph analysis for the target currency based on its fundamental signals.
CRITICAL INSTRUCTION: You MUST explicitly reference how this currency's signal relates to the other currencies/commodities in the provided Global Macro Context (e.g., if USD is bearish and CAD is bullish, explain the USDCAD dynamic, or if USD is weak and XAU is strong, explain the gold dynamic).
Highlight specific event shocks, news themes, and confluence status. Factor in the temporal context for timing risks.
Do not invent numbers; use only what is provided.
"""

def generate_detailed_currency_reports_node(state: GlobalPipelineState) -> dict:
    """Generates detailed macro-aware analysis for each currency individually.

    Phase 5: این نود به‌صورت پیش‌فرض غیرفعال است (enable_reports=False) چون
    یک کال LLM با خروجی طولانی markdown per ارز داشت (~30% هزینه کل پایپ‌لاین).
    با enable_reports=True در state دوباره فعال می‌شود.
    """
    # Gate: بدون enable_reports هیچ کال LLM زده نمی‌شود
    if not state.get("enable_reports", False):
        logger.info("[Detailed Reports] Skipped (enable_reports=False — default for token optimization).")
        return {"detailed_reports": {}}

    llm = state["llm"]
    signals: dict[str, CompositeSignal] = state.get("composite_signals", {})
    temporal_ctx = state.get("temporal_context", {})

    if not signals:
        return {"detailed_reports": {}}

    # Build a snapshot of the global macro environment to pass to each currency's prompt
    macro_context_lines = []
    for c, sig in signals.items():
        macro_context_lines.append(f"{c} (Dir: {sig.direction:+d}, Score: {sig.final_score:.2f})")
    macro_context_str = ", ".join(macro_context_lines)

    temporal_text = (
        f"Day: {temporal_ctx.get('day_of_week', 'Unknown')}, "
        f"Session: {temporal_ctx.get('market_session', 'Unknown')}, "
        f"Weekend/Holiday: {temporal_ctx.get('is_weekend', False) or temporal_ctx.get('is_major_holiday', False)}"
    )

    detailed_reports = {}
    
    for ccy, sig in signals.items():
        human_text = f"""
        Global Macro Context (All Analyzed Assets): {macro_context_str}
        Temporal Context: {temporal_text}
        
        Target Currency for Detailed Analysis: {ccy}
        Target Signal Data: Dir {sig.direction}, Score {sig.final_score:.2f}, Conf {sig.confidence:.2f}, Tradable {sig.is_tradable}
        Target Reasoning Context: {sig.reasoning}
        
        Write the detailed analysis for {ccy}, explicitly referencing its relationship with the other assets in the Global Macro Context:
        """
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", _DETAILED_CURRENCY_PROMPT),
            ("human", human_text)
        ])
        
        chain = prompt | llm
        try:
            response = chain.invoke({})
            report = response.content if hasattr(response, 'content') else str(response)
            detailed_reports[ccy] = report.strip()
        except Exception as exc:
            logger.error(f"[Detailed Report] Failed for {ccy}: {exc}")
            detailed_reports[ccy] = f"Analysis failed for {ccy}."
            
    logger.info("[Detailed Reports] Generated for all currencies.")
    return {"detailed_reports": detailed_reports}
    


_GLOBAL_SUMMARY_SYSTEM_PROMPT = """\
You are a senior macro strategist overseeing the entire forex board. 
You are given the final adjusted composite signals for multiple currencies after cross-asset consistency checks.
Synthesize these into a brief, unified global macro executive summary.
Highlight the dominant theme (e.g., Risk-Off, USD strength, commodity rally).
Explicitly mention if any cross-asset anomalies were detected and penalized.
Keep it under 6 sentences. Do not invent numbers.
IMPORTANT TEMPORAL RULE: Factor in the Temporal Context for execution timing (e.g., weekend gap risk).
"""

def generate_global_summary_node(state: GlobalPipelineState) -> dict:
    """Generates a final global report across all currencies."""
    llm = state["llm"]
    signals: dict[str, CompositeSignal] = state.get("composite_signals", {})
    temporal_ctx = state.get("temporal_context", {})

    if not signals:
        return {"global_report": "No signals available for global summary."}

    lines = []
    for ccy, sig in signals.items():
        # Phase 5: کوتاه‌سازی reasoning برای کاهش توکن ورودی (قبلاً کامل چسبانده می‌شد)
        reasoning_short = (sig.reasoning or "")[:250]
        lines.append(
            f"- {ccy}: Dir {sig.direction}, Score {sig.final_score:.2f}, "
            f"Conf {sig.confidence:.2f}, Tradable {sig.is_tradable}. "
            f"Reason: {reasoning_short}"
        )
    signals_text = "\n".join(lines)

    temporal_text = (
        f"Day: {temporal_ctx.get('day_of_week', 'Unknown')}, "
        f"Session: {temporal_ctx.get('market_session', 'Unknown')}, "
        f"Weekend/Holiday: {temporal_ctx.get('is_weekend', False) or temporal_ctx.get('is_major_holiday', False)}"
    )

    human_text = f"""
    Adjusted Composite Signals:
    {signals_text}
    
    Temporal Context: {temporal_text}
    
    Write the global macro executive summary:
    """

    prompt = ChatPromptTemplate.from_messages([
        ("system", _GLOBAL_SUMMARY_SYSTEM_PROMPT),
        ("human", human_text)
    ])
    
    chain = prompt | llm
    try:
        response = chain.invoke({})
        report = response.content if hasattr(response, 'content') else str(response)
        logger.info("[Global Summary] Report generated.")
        return {"global_report": report.strip()}
    except Exception as exc:
        logger.error(f"[Global Summary] LLM failed: {exc}")
        return {"global_report": "Failed to generate global summary."}


def build_cross_asset_graph():
    """Constructs the graph that runs after all currencies are processed."""
    workflow = StateGraph(GlobalPipelineState)
    
    workflow.add_node("cross_check", cross_asset_consistency_node)
    workflow.add_node("detailed_reports", generate_detailed_currency_reports_node)
    workflow.add_node("global_summary", generate_global_summary_node)
    
    workflow.add_edge(START, "cross_check")
    workflow.add_edge("cross_check", "detailed_reports")
    workflow.add_edge("detailed_reports", "global_summary")
    workflow.add_edge("global_summary", END)
    
    return workflow.compile()