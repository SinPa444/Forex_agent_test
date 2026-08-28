# nlp_news.py

"""
nlp_news.py
===========
Production-grade news analysis module for the forex trading assistant.

Analyzes structured RSS news items combined with quantitative MarketContext
to produce explainable, auditable NewsSignal objects and persist them to the
news_signals database table.

Architecture layers (top to bottom):
  1. Input schemas      : NewsItem, NewsInterpretation, NewsSignal
  2. Prompt builder     : build_news_prompt()
  3. Analysis service   : NewsAnalysisService
  4. Scoring engine     : NewsSignalScorer
  5. Repository         : NewsSignalRepository
  6. Orchestration      : analyze_news_item()  ← future LangGraph node

Design principles:
  - No speaker weight, no Speaker lookup (news-specific scoring only)
  - Source reliability replaces speaker weight
  - Event category weight replaces speaker importance
  - Fully auditable: raw payloads stored as JSON in the DB
  - LangGraph-ready: analyze_news_item() wraps cleanly into a state node
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError
from core.llm_utils import invoke_with_retry

# ---- پروژه داخلی ----
# NewsSignalDB از database.py می‌آید (نه اینجا تعریف نمی‌شود)
from core.database import SessionLocal, NewsSignalDB
# schema ها از models.py می‌آیند (نه اینجا تعریف نمی‌شوند)
from core.models import MarketContext, NewsItem, NewsInterpretation, NewsSignal
logger = logging.getLogger(__name__)

# ===========================================================================
# CONSTANTS
# ===========================================================================

SOURCE_RELIABILITY: dict[str, float] = {
    "ForexLive": 0.82,
    "DailyFX": 0.80,
    "Forex Factory": 0.85,
    "Unknown": 0.65,
}

EVENT_IMPORTANCE: dict[str, float] = {
    "Interest Rate Decision": 1.00,
    "Non-Farm Payrolls": 1.00,
    "CPI": 0.95,
    "Inflation": 0.95,
    "GDP": 0.85,
    "Employment": 0.85,
    "Retail Sales": 0.75,
    "PMI": 0.70,
    "Manufacturing": 0.70,
    "Trade Balance": 0.60,
    "Housing": 0.55,
    "Consumer Confidence": 0.50,
    "Geopolitical": 0.65,
    "Central Bank Commentary": 0.80,
    "Unknown": 0.40,
}

HALF_LIFE_CATEGORY_MULTIPLIER: dict[str, float] = {
    "Interest Rate Decision": 2.5,
    "Non-Farm Payrolls": 2.0,
    "CPI": 1.8,
    "Inflation": 1.8,
    "GDP": 1.5,
    "Employment": 1.5,
    "Central Bank Commentary": 1.6,
    "Geopolitical": 1.7,
    "Unknown": 1.0,
}

HALF_LIFE_BASE_MINS: int = 90

TRADABILITY_MIN_SCORE: float = 0.35
TRADABILITY_MIN_CONFIDENCE: float = 0.55
TRADABILITY_MIN_COMPLETENESS: float = 0.45

# ===========================================================================
# UTILITY FUNCTIONS
# ===========================================================================


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp a float value between low and high (inclusive)."""
    return max(low, min(high, value))


def _strip_json_markdown(text: Any) -> str:
    """
    Remove markdown code blocks around JSON if present.
    Some LLMs wrap JSON output in ```json ... ``` which breaks Pydantic parsing.
    """
    if isinstance(text, BaseMessage):
        text = text.content
    if isinstance(text, str):
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if match:
            return match.group(1)
        if text.strip().startswith("```") and text.strip().endswith("```"):
            text = text.strip()[3:-3].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
            return text
    return text


def _parse_published(raw: Any) -> Optional[datetime]:
    """
    Safely parse a published timestamp from various formats.

    Accepts: datetime, ISO-format str, RFC-2822 str (from feedparser), or None.
    Returns None on any parsing failure rather than raising.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        # Try ISO format first
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        # Fallback: let email.utils handle RFC-2822 (feedparser format)
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(raw)
        except Exception:  # noqa: BLE001 — intentional broad catch for date parsing
            logger.warning("Could not parse published date: %r", raw)
            return None
    logger.warning("Unexpected type for published field: %s", type(raw))
    return None


def _resolve_source_reliability(
    source: str,
    explicit_reliability: Optional[float],
) -> float:
    """
    Return source reliability score.

    Priority:
      1. Explicit value provided by caller (if not None)
      2. SOURCE_RELIABILITY lookup by source name
      3. Default 'Unknown' fallback
    """
    if explicit_reliability is not None:
        return _clamp(explicit_reliability, 0.0, 1.0)
    return SOURCE_RELIABILITY.get(source, SOURCE_RELIABILITY["Unknown"])


def _resolve_event_category_weight(category: Optional[str]) -> tuple[str, float]:
    """
    Resolve event category and its importance weight.

    Returns (resolved_category_name, weight).
    Performs case-insensitive partial matching against EVENT_IMPORTANCE keys.
    """
    if not category:
        return "Unknown", EVENT_IMPORTANCE["Unknown"]

    # Exact match first
    if category in EVENT_IMPORTANCE:
        return category, EVENT_IMPORTANCE[category]

    # Case-insensitive partial match
    category_lower = category.lower()
    for key, weight in EVENT_IMPORTANCE.items():
        if key.lower() in category_lower or category_lower in key.lower():
            return key, weight

    return "Unknown", EVENT_IMPORTANCE["Unknown"]


# ===========================================================================
# PROMPT BUILDER
# ===========================================================================

_NEWS_SYSTEM_PROMPT = """\
You are an expert macroeconomic analyst and forex market interpreter.
Your role is to analyze financial news items in the context of quantitative
market data and produce structured, economically rigorous interpretations.

## Your responsibilities

1. Read the news headline and body/summary carefully.
2. Identify the primary asset class and currency affected.
3. Classify the economic event category (e.g., CPI, Interest Rate Decision, GDP, PMI,
   Geopolitical, Central Bank Commentary).
4. Determine whether the news is: hawkish, dovish, inflationary, deflationary,
   recessionary, risk-on, or risk-off — as applicable to the asset.
5. Compare the text's claims against the numeric market context provided
   (actual vs. forecast vs. previous values).
6. Use the pre-calculated `calculated_surprise` and `calculated_volatility` values
   EXACTLY as provided — do not recalculate or modify them.
7. Reason about cross-asset impacts (e.g., strong USD NFP → negative for XAU and SPX).
8. Score headline-body alignment: does the article body support what the headline claims?
9. Score quantitative alignment: does the textual interpretation match the numeric data?

## Strict output rules

- `direction` MUST match the sign of `nlp_sentiment_score`:
    positive sentiment → direction = +1
    negative sentiment → direction = -1
    neutral sentiment  → direction = 0
- `surprise_factor` MUST equal the provided `calculated_surprise` value exactly.
- `expected_volatility` MUST equal the provided `calculated_volatility` value exactly.
- `headline_body_alignment` and `quantitative_alignment` MUST be between 0.0 and 1.0.
- `cross_assets` values MUST be signed integers: -1, 0, or +1.
- If the text is vague and the numeric data is weak, keep sentiment magnitude LOW.
- If actual equals forecast (surprise near zero), do NOT produce exaggerated signals.
- Never invent numeric data that is not provided.
- Never use speaker identity or credibility — this is pure news analysis.
- impact_horizon must be one of: "immediate", "intraday", "multi-session"

## Alignment scoring guidance

- headline_body_alignment = 1.0: headline and body tell the same story consistently.
- headline_body_alignment = 0.5: body partially supports headline but with caveats.
- headline_body_alignment = 0.0: body contradicts or severely undermines the headline.
- quantitative_alignment = 1.0: text sentiment is perfectly consistent with the numbers.
- quantitative_alignment = 0.5: text is partially consistent; some numeric contradiction.
- quantitative_alignment = 0.0: text says "beats" but actual < forecast, or vice versa.

## Illustrative examples

### Example A — Strong upside surprise (CPI beats expectations)

Input:
  title: "US CPI Comes In Hot at 3.8%, Above 3.5% Forecast"
  summary: "Consumer prices rose more than expected in January, driven by shelter and
            energy costs. Core CPI also exceeded forecasts. Markets are pricing in
            fewer Fed rate cuts as a result."
  actual: 3.8, forecast: 3.5, previous: 3.6
  calculated_surprise: 0.82, calculated_volatility: 1.65

Expected output direction:
  - USD is strengthened by hotter inflation → direction = +1 for USD
  - nlp_sentiment_score: ~+0.78 (strong bullish USD)
  - headline_body_alignment: 0.92 (body fully supports headline)
  - quantitative_alignment: 0.95 (text correctly reflects actual > forecast)
  - cross_assets: {{"XAU": -1, "SPX": -1}}  (hot inflation → hawkish → risk-off)
  - impact_horizon: "multi-session"

### Example B — In-line data (NFP meets expectations)

Input:
  title: "US NFP Prints 185K, In Line With Forecasts"
  summary: "Nonfarm payrolls came in at 185,000, matching the median forecast.
            The unemployment rate was unchanged at 4.1%. Wage growth was modest."
  actual: 185000, forecast: 185000, previous: 190000
  calculated_surprise: 0.04, calculated_volatility: 0.85

Expected output direction:
  - In-line data → minimal surprise → direction = 0 or mild
  - nlp_sentiment_score: ~0.05 (nearly neutral)
  - headline_body_alignment: 0.90 (body matches headline perfectly)
  - quantitative_alignment: 0.90 (text correctly reflects in-line result)
  - cross_assets: {{{{}}}}  (no strong cross-asset signal)
  - impact_horizon: "immediate"

Keep your reasoning concise (3–5 sentences) but economically meaningful.
Always explain the transmission mechanism (e.g., "hotter CPI reduces rate-cut
expectations, strengthening the dollar via yield differentials").
"""

_NEWS_HUMAN_TEMPLATE = """\
## News Item

**Source**: {source}
**Title**: {title}
**Published**: {published}
**Category (from feed)**: {category}
**Primary Currency**: {currency}
**Impact Level**: {impact}

**Summary/Body**:
{summary}

---

## Quantitative Market Context

**Target Asset**: {target_asset}
**Event Title (economic calendar)**: {event_title}

**Economic Data**:
- Actual Value: {actual_value}
- Forecast Value: {forecast_value}
- Previous Value: {previous_value}
- Historical Std Dev of Surprises: {historical_std}

**Volatility Context**:
- Historical Volatility (annualized): {hv}
- ATR (current 3-day): {atr_current}
- ATR (baseline 14-day): {atr_baseline}
- Implied Volatility (index): {iv}

**Macro Context**:
- Yield Spread (10Y - 2Y): {yield_spread}

**Pre-Calculated Metrics** (use these EXACTLY — do not recalculate):
- Calculated Surprise Factor: {calculated_surprise}
- Calculated Expected Volatility Multiplier: {calculated_volatility}

---

Analyze this news item against the quantitative context above.
Produce a structured NewsInterpretation following all output rules in your instructions.
"""


def build_news_prompt() -> ChatPromptTemplate:
    """
    Build and return the ChatPromptTemplate for news analysis.

    Returns a LangChain ChatPromptTemplate with a system message
    (containing full analytical rules and examples) and a human message
    (containing the news item + market context template).

    The returned template is ready to be chained with an LLM via:
        prompt | llm.with_structured_output(NewsInterpretation)
    """
    return ChatPromptTemplate.from_messages(
        [
            ("system", _NEWS_SYSTEM_PROMPT),
            ("human", _NEWS_HUMAN_TEMPLATE),
        ]
    )


def _format_optional(value: Any, precision: int = 4) -> str:
    """Format an optional numeric value for prompt injection, returning 'N/A' if None."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{precision}f}"
    return str(value)


def _build_prompt_inputs(
    news_item: NewsItem,
    market_context: MarketContext,
) -> dict[str, str]:
    """
    Build the flat string dict for prompt template variable substitution.

    Converts both NewsItem and MarketContext into prompt-safe strings,
    handling None values gracefully with 'N/A' placeholders.
    """
    return {
        # News item fields
        "source": news_item.source,
        "title": news_item.title,
        "published": (
            news_item.published.isoformat() if news_item.published else "Unknown"
        ),
        "category": news_item.category or "Not specified",
        "currency": news_item.currency or "Not specified",
        "impact": news_item.impact or "Not specified",
        "summary": news_item.summary or "(no summary provided)",
        # Market context fields
        "target_asset": market_context.target_asset,
        "event_title": market_context.event_title or "N/A",
        "actual_value": _format_optional(market_context.actual_value),
        "forecast_value": _format_optional(market_context.forecast_value),
        "previous_value": _format_optional(market_context.previous_value),
        "historical_std": _format_optional(market_context.historical_std),
        "hv": _format_optional(market_context.historical_volatility),
        "atr_current": _format_optional(market_context.atr_current),
        "atr_baseline": _format_optional(market_context.atr_baseline),
        "iv": _format_optional(market_context.implied_volatility),
        "yield_spread": _format_optional(market_context.yield_spread),
        "calculated_surprise": _format_optional(market_context.calculated_surprise),
        "calculated_volatility": _format_optional(market_context.calculated_volatility),
    }


# ===========================================================================
# DATA COMPLETENESS
# ===========================================================================


def compute_data_completeness(market_context: MarketContext) -> float:
    """
    Calculate data completeness score from MarketContext.

    Checks the nine key quantitative fields defined in the specification.
    Returns a float in [0.0, 1.0] representing the proportion of non-null fields.

    This score is used in:
      - data_quality_factor calculation
      - confidence formula
      - tradability rule
    """
    key_fields = [
        market_context.actual_value,
        market_context.forecast_value,
        market_context.previous_value,
        market_context.historical_std,
        market_context.historical_volatility,
        market_context.atr_current,
        market_context.atr_baseline,
        market_context.implied_volatility,
        market_context.yield_spread,
    ]
    non_null_count = sum(1 for f in key_fields if f is not None)
    return non_null_count / len(key_fields)


def compute_data_quality_factor(data_completeness_score: float) -> float:
    """
    Calculate data quality factor from completeness score.

    Formula: 0.5 + (0.5 * data_completeness_score)
    Valid range: [0.5, 1.0]
    """
    return _clamp(0.5 + (0.5 * data_completeness_score), 0.5, 1.0)


# ===========================================================================
# NEWS ANALYSIS SERVICE
# ===========================================================================


class NewsAnalysisService:
    """
    Calls the configured LLM to produce a NewsInterpretation.

    Accepts a pre-configured LangChain chat model (BaseChatModel) and the
    shared prompt template. The LLM is called with structured output binding
    to ensure type-safe, validated NewsInterpretation objects.

    Designed for dependency injection — the LLM and prompt are passed in,
    not constructed internally. This makes the service testable and
    LangGraph-compatible.

    Usage:
        service = NewsAnalysisService(llm=my_openrouter_llm)
        interpretation = service.analyze(news_item, market_context)
    """

    def __init__(
        self,
        llm: BaseChatModel,
        prompt: Optional[ChatPromptTemplate] = None,
    ) -> None:
        self._llm = llm
        self._prompt = prompt or build_news_prompt()
        # Build the chain once at construction time
        self._chain = self._prompt | self._llm.with_structured_output(
            NewsInterpretation
        )

    def analyze(
        self,
        news_item: NewsItem,
        market_context: MarketContext,
    ) -> NewsInterpretation:
        """
        Call the LLM chain and return a validated NewsInterpretation.

        Raises:
            ValueError: If LLM returns None or an unexpected type.
            Exception: Re-raises LLM API or network exceptions after logging.
        """
        prompt_inputs = _build_prompt_inputs(news_item, market_context)

        logger.info(
            "Calling analysis LLM for news item: %r (source=%s)",
            news_item.title[:80],
            news_item.source,
        )

        try:
            result = invoke_with_retry(self._chain, prompt_inputs)
        except Exception as exc:
            logger.error(
                "LLM call failed for news item %r: %s",
                news_item.title[:80],
                exc,
                exc_info=True,
            )
            raise

        if result is None:
            raise ValueError(
                f"LLM returned None for news item: {news_item.title!r}"
            )

        if not isinstance(result, NewsInterpretation):
            raise ValueError(
                f"LLM returned unexpected type {type(result).__name__!r}; "
                f"expected NewsInterpretation"
            )

        # Post-hoc enforcement: ensure surprise/volatility match market context
        # The LLM is instructed to echo these, but we enforce in code as well
        result = self._enforce_market_context_values(result, market_context)

        logger.info(
            "LLM interpretation complete: direction=%d sentiment=%.3f "
            "category=%r horizon=%s",
            result.direction,
            result.nlp_sentiment_score,
            result.detected_event_category,
            result.impact_horizon,
        )
        return result

    @staticmethod
    def _enforce_market_context_values(
        interpretation: NewsInterpretation,
        market_context: MarketContext,
    ) -> NewsInterpretation:
        """
        Enforce that surprise_factor and expected_volatility match MarketContext.

        The LLM is instructed to echo these values, but we validate and correct
        in code to prevent downstream scoring errors caused by LLM drift.
        """
        needs_correction = False

        surprise = market_context.calculated_surprise or 0.0
        volatility = market_context.calculated_volatility or 1.0

        if abs(interpretation.surprise_factor - surprise) > 0.001:
            logger.warning(
                "LLM surprise_factor drift: LLM=%.4f, MarketContext=%.4f — correcting",
                interpretation.surprise_factor,
                surprise,
            )
            needs_correction = True

        if abs(interpretation.expected_volatility - volatility) > 0.001:
            logger.warning(
                "LLM expected_volatility drift: LLM=%.4f, MarketContext=%.4f — correcting",
                interpretation.expected_volatility,
                volatility,
            )
            needs_correction = True

        if needs_correction:
            # Use model_copy to produce a corrected immutable copy
            interpretation = interpretation.model_copy(
                update={
                    "surprise_factor": _clamp(surprise, 0.0, 1.0),
                    "expected_volatility": max(volatility, 0.0),
                }
            )

        return interpretation


# ===========================================================================
# SCORING ENGINE
# ===========================================================================


class NewsSignalScorer:
    """
    Pure deterministic scoring engine for news-based signals.

    Converts a (NewsItem, NewsInterpretation, MarketContext, ticker) tuple
    into a fully computed NewsSignal. All math is implemented here with no
    LLM involvement.

    Formula summary:
      - final_score   = clamp(sentiment * category_weight * (1 + surprise) * quality, -1, 1)
      - confidence    = weighted sum of completeness, surprise, reliability, alignments
      - is_tradable   = score >= 0.35 AND confidence >= 0.55 AND completeness >= 0.45
      - half_life     = int((90 * category_multiplier) / max(volatility, 0.25))
      - cross_assets  = {asset: round(|score| * 0.7 * direction, 3)}

    No speaker weight is used anywhere in this class.
    """

    def score(
        self,
        news_item: NewsItem,
        interpretation: NewsInterpretation,
        market_context: MarketContext,
        ticker: str,
    ) -> NewsSignal:
        """
        Compute and return a NewsSignal from interpretation + context.

        All intermediate values are logged at DEBUG level for auditability.
        """
        # --- Resolve source metadata ---
        source_reliability = _resolve_source_reliability(
            news_item.source,
            news_item.source_reliability,
        )

        # --- Resolve event category ---
        resolved_category, event_category_weight = _resolve_event_category_weight(
            interpretation.detected_event_category
            or news_item.category
        )

        # --- Data completeness ---
        data_completeness_score = compute_data_completeness(market_context)
        data_quality_factor = compute_data_quality_factor(data_completeness_score)

        # --- Core signal values from interpretation ---
        nlp_sentiment_score = interpretation.nlp_sentiment_score
        surprise_factor = interpretation.surprise_factor
        expected_volatility = interpretation.expected_volatility
        headline_body_alignment = interpretation.headline_body_alignment
        quantitative_alignment = interpretation.quantitative_alignment
        direction = interpretation.direction

        # --- Final score ---
        raw_score = (
            nlp_sentiment_score
            * event_category_weight
            * (1.0 + surprise_factor)
            * data_quality_factor
        )
        final_score = _clamp(raw_score, -1.0, 1.0)

        # --- Confidence ---
        confidence_raw = (
            (data_completeness_score * 0.30)
            + (min(abs(surprise_factor), 1.0) * 0.25)
            + (source_reliability * 0.20)
            + (headline_body_alignment * 0.15)
            + (quantitative_alignment * 0.10)
        )
        confidence = _clamp(confidence_raw, 0.0, 1.0)

        # --- Tradability ---
        is_tradable = (
            abs(final_score) >= TRADABILITY_MIN_SCORE
            and confidence >= TRADABILITY_MIN_CONFIDENCE
            and data_completeness_score >= TRADABILITY_MIN_COMPLETENESS
        )

        # --- Expected volatility level ---
        if expected_volatility >= 1.5:
            vol_level = "High"
        elif expected_volatility <= 0.5:
            vol_level = "Low"
        else:
            vol_level = "Normal"

        # --- Half-life ---
        category_multiplier = HALF_LIFE_CATEGORY_MULTIPLIER.get(
            resolved_category,
            HALF_LIFE_CATEGORY_MULTIPLIER["Unknown"],
        )
        safe_volatility = max(expected_volatility, 0.25)  # guard division by zero
        signal_half_life_mins = int(
            (HALF_LIFE_BASE_MINS * category_multiplier) / safe_volatility
        )
        signal_half_life_mins = max(signal_half_life_mins, 1)  # floor at 1 minute

        # --- Cross-asset signals ---
        cross_asset_signals: dict[str, float] = {}
        for asset, asset_direction in interpretation.cross_assets.items():
            cross_asset_signals[asset] = round(
                abs(final_score) * 0.7 * float(asset_direction), 3
            )

        logger.debug(
            "NewsSignalScorer results: ticker=%s score=%.4f confidence=%.4f "
            "tradable=%s vol_level=%s half_life=%dmin completeness=%.3f",
            ticker,
            final_score,
            confidence,
            is_tradable,
            vol_level,
            signal_half_life_mins,
            data_completeness_score,
        )

        return NewsSignal(
            reasoning=interpretation.reasoning,
            asset_class=interpretation.asset_class,
            direction=direction,
            final_score=final_score,
            confidence=confidence,
            is_tradable=is_tradable,
            expected_volatility_level=vol_level,
            signal_half_life_mins=signal_half_life_mins,
            cross_asset_signals=cross_asset_signals,
            source=news_item.source,
            source_reliability=source_reliability,
            event_category_weight=event_category_weight,
            data_completeness_score=data_completeness_score,
            data_quality_factor=data_quality_factor,
            headline_body_alignment=headline_body_alignment,
            quantitative_alignment=quantitative_alignment,
            ticker=ticker,
        )


# ===========================================================================
# REPOSITORY / PERSISTENCE LAYER
# ===========================================================================


class NewsSignalRepository:
    """
    Handles persistence of NewsSignal objects to the news_signals SQLite table.

    Follows the existing project pattern of using SQLAlchemy SessionLocal.
    Each save operation is wrapped in a try/except with explicit rollback
    to prevent transaction leaks.

    Also provides a safe JSON serialization method for raw payload archiving.
    """

    @staticmethod
    def _serialize_news_item(news_item: NewsItem) -> str:
        """Serialize NewsItem to JSON string for raw payload storage."""
        try:
            data = news_item.model_dump()
            # datetime is not JSON serializable by default
            if data.get("published") and isinstance(data["published"], datetime):
                data["published"] = data["published"].isoformat()
            return json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("Could not serialize news_item to JSON: %s", exc)
            return json.dumps({"error": "serialization_failed"})

    @staticmethod
    def _serialize_market_context(market_context: MarketContext) -> str:
        """Serialize MarketContext to JSON string for raw payload storage."""
        try:
            return market_context.model_dump_json()
        except (TypeError, ValueError) as exc:
            logger.warning("Could not serialize market_context to JSON: %s", exc)
            return json.dumps({"error": "serialization_failed"})

    def save(
        self,
        news_item: NewsItem,
        interpretation: NewsInterpretation,
        signal: NewsSignal,
        market_context: MarketContext,
    ) -> int:
        """
        Persist a NewsSignal to the database.

        Args:
            news_item: The original news input.
            interpretation: The LLM-produced interpretation.
            signal: The scored signal.
            market_context: The quantitative context used.

        Returns:
            The auto-generated primary key (id) of the inserted row.

        Raises:
            SQLAlchemyError: On database commit failure (after rollback).
        """
        db_record = NewsSignalDB(
            created_at=datetime.utcnow(),
            # News provenance
            source=news_item.source,
            title=news_item.title,
            summary=news_item.summary or None,
            link=news_item.link,
            published_at=news_item.published,
            # Market context reference
            event_title=market_context.event_title,
            asset_class=signal.asset_class,
            ticker=signal.ticker,
            # Signal core
            direction=signal.direction,
            final_score=signal.final_score,
            confidence=signal.confidence,
            is_tradable=signal.is_tradable,
            expected_volatility_level=signal.expected_volatility_level,
            signal_half_life_mins=signal.signal_half_life_mins,
            # Quality metadata
            source_reliability=signal.source_reliability,
            event_category_weight=signal.event_category_weight,
            data_completeness_score=signal.data_completeness_score,
            data_quality_factor=signal.data_quality_factor,
            headline_body_alignment=signal.headline_body_alignment,
            quantitative_alignment=signal.quantitative_alignment,
            # Explainability
            reasoning=signal.reasoning,
            # Raw payloads
            raw_news_payload_json=self._serialize_news_item(news_item),
            raw_market_context_json=self._serialize_market_context(market_context),
        )

        session = SessionLocal()
        try:
            session.add(db_record)
            session.commit()
            session.refresh(db_record)
            record_id = db_record.id
            logger.info(
                "NewsSignal saved: id=%d ticker=%s direction=%d score=%.4f tradable=%s",
                record_id,
                signal.ticker,
                signal.direction,
                signal.final_score,
                signal.is_tradable,
            )
            return record_id
        except SQLAlchemyError as exc:
            session.rollback()
            logger.error(
                "Failed to save NewsSignal to DB: %s",
                exc,
                exc_info=True,
            )
            raise
        finally:
            session.close()


# ===========================================================================
# FEEDPARSER HELPER
# ===========================================================================


def news_item_from_feedparser(
    entry: dict[str, Any],
    source: str,
    source_reliability: Optional[float] = None,
    category: Optional[str] = None,
    currency: Optional[str] = None,
    impact: Optional[str] = None,
) -> NewsItem:
    """
    Convert a raw feedparser entry dict into a validated NewsItem.

    feedparser entries are dict-like objects with inconsistent field presence.
    This function handles missing fields, malformed dates, and empty content
    gracefully.

    Args:
        entry: A feedparser entry (feedparser.FeedParserDict or plain dict).
        source: Source name string, e.g. "ForexLive" or "DailyFX".
        source_reliability: Optional override for reliability score.
        category: Optional event category override.
        currency: Optional currency hint.
        impact: Optional impact level string.

    Returns:
        A validated NewsItem instance.

    Raises:
        ValueError: If title is missing or empty (non-recoverable for routing).
    """
    # feedparser may use 'title', 'summary', or 'description'
    title = (
        entry.get("title")
        or entry.get("headline")
        or ""
    ).strip()

    if not title:
        raise ValueError(
            f"feedparser entry from source {source!r} has no usable title field. "
            f"Available keys: {list(entry.keys())}"
        )

    summary = (
        entry.get("summary")
        or entry.get("description")
        or entry.get("content", [{}])[0].get("value", "")
        if isinstance(entry.get("content"), list)
        else entry.get("content", "")
    ).strip()

    # feedparser provides 'published' as a string
    raw_published = entry.get("published") or entry.get("updated") or None
    published = _parse_published(raw_published)

    link = entry.get("link") or entry.get("url") or None

    # feedparser tags may contain category hints
    if category is None:
        tags = entry.get("tags", [])
        if tags and isinstance(tags, list):
            tag_terms = [t.get("term", "") for t in tags if isinstance(t, dict)]
            if tag_terms:
                category = tag_terms[0]

    return NewsItem(
        title=title,
        summary=summary,
        published=published,
        link=link,
        source=source,
        source_reliability=source_reliability,
        category=category,
        currency=currency,
        impact=impact,
    )
    
    
class NewsDigestInterpretation(BaseModel):
    """
    خروجی تحلیل LLM برای یک Digest (تجمیع چند خبر یک ارز).
    """
    nlp_sentiment_score: float = Field(
        ..., ge=-1.0, le=1.0,
        description="Overall sentiment score for the currency based on aggregated news."
    )
    direction: int = Field(
        ..., description="1 for bullish, -1 for bearish, 0 for neutral"
    )
    reasoning: str = Field(
        ...,
        description="Concise reasoning (3-5 sentences) synthesizing the macro view from the provided news items."
    )
    detected_event_category: Optional[str] = Field(
        default=None,
        description="Primary economic theme (e.g., Inflation, Interest Rate Decision, Geopolitical)."
    )
    impact_horizon: str = Field(
        ...,
        description="immediate, intraday, or multi-session"
    )
    cross_assets: dict[str, int] = Field(
        default_factory=dict,
        description="Cross-asset implications (e.g., {'XAU': -1, 'SPX': -1}). Values must be -1, 0, or +1."
    )
    
    # مقادیر کپی‌شده از Context (توسط سرویس enforce خواهند شد)
    surprise_factor: float = Field(..., ge=0.0, le=1.0)
    expected_volatility: float = Field(..., ge=0.0)
    quantitative_alignment: float = Field(
        ..., ge=0.0, le=1.0,
        description="How well the textual sentiment aligns with the quantitative market context provided."
    )

    @model_validator(mode="after")
    def direction_matches_sentiment(self) -> "NewsDigestInterpretation":
        if self.nlp_sentiment_score > 0.05 and self.direction != 1:
            raise ValueError("Positive sentiment must have direction=1")
        if self.nlp_sentiment_score < -0.05 and self.direction != -1:
            raise ValueError("Negative sentiment must have direction=-1")
        if abs(self.nlp_sentiment_score) <= 0.05 and self.direction != 0:
            raise ValueError("Near-zero sentiment must have direction=0")
        return self


# ===========================================================================
# TOP-LEVEL ORCHESTRATION FUNCTION
# ===========================================================================


def analyze_news_item(
    news_item: NewsItem,
    market_context: MarketContext,
    ticker: str,
    llm: BaseChatModel,
    *,
    prompt: Optional[ChatPromptTemplate] = None,
    persist: bool = True,
    scorer: Optional[NewsSignalScorer] = None,
    repository: Optional[NewsSignalRepository] = None,
    analysis_service: Optional[NewsAnalysisService] = None,
) -> tuple[NewsInterpretation, NewsSignal]:
    """
    Top-level orchestration function: news item → interpretation → signal → DB.

    This function is the primary entry point for the news analysis pipeline.
    It is designed to be trivially convertible into a LangGraph node by wrapping
    it in a state dict handler (see integration notes).

    Args:
        news_item: Validated NewsItem instance.
        market_context: Quantitative MarketContext for the routed asset.
        ticker: yfinance-compatible ticker for the target asset.
        llm: Configured LangChain chat model (cloud LLM via OpenRouter).
        prompt: Optional custom ChatPromptTemplate (uses default if None).
        persist: Whether to save the signal to the database (default True).
        scorer: Optional NewsSignalScorer instance (creates default if None).
        repository: Optional NewsSignalRepository instance (creates default if None).
        analysis_service: Optional NewsAnalysisService instance (creates default if None).

    Returns:
        Tuple of (NewsInterpretation, NewsSignal).
        The NewsSignal contains the persisted signal with all scoring metadata.

    Raises:
        ValueError: On invalid inputs or LLM response issues.
        SQLAlchemyError: On database persistence failure (only if persist=True).
        Exception: Re-raises LLM API failures after logging.

    Design note for LangGraph conversion:
        Wrap this function in a node handler like:

        def news_analysis_node(state: dict) -> dict:
            interpretation, signal = analyze_news_item(
                news_item=state["news_item"],
                market_context=state["market_context"],
                ticker=state["ticker"],
                llm=state["llm"],
            )
            return {**state, "interpretation": interpretation, "signal": signal}
    """
    # --- Input validation ---
    if not ticker or not ticker.strip():
        raise ValueError("ticker must be a non-empty string")

    if not isinstance(market_context, MarketContext):
        raise ValueError(
            f"market_context must be a MarketContext instance; "
            f"got {type(market_context).__name__!r}"
        )

    logger.info(
        "Starting news analysis pipeline: title=%r source=%s ticker=%s",
        news_item.title[:80],
        news_item.source,
        ticker,
    )

    # --- Build services (with optional DI) ---
    service = analysis_service or NewsAnalysisService(llm=llm, prompt=prompt)
    scoring_engine = scorer or NewsSignalScorer()
    repo = repository or NewsSignalRepository()

    # --- Step 1: LLM analysis ---
    try:
        interpretation = service.analyze(news_item, market_context)
    except Exception as exc:
        logger.error(
            "News analysis failed at LLM step for %r: %s",
            news_item.title[:80],
            exc,
        )
        raise

    # --- Step 2: Signal scoring ---
    try:
        signal = scoring_engine.score(
            news_item=news_item,
            interpretation=interpretation,
            market_context=market_context,
            ticker=ticker,
        )
    except Exception as exc:
        logger.error(
            "News analysis failed at scoring step for %r: %s",
            news_item.title[:80],
            exc,
        )
        raise

    # --- Step 3: Persistence ---
    if persist:
        try:
            record_id = repo.save(
                news_item=news_item,
                interpretation=interpretation,
                signal=signal,
                market_context=market_context,
            )
            logger.info("Pipeline complete. DB record id=%d", record_id)
        except SQLAlchemyError as exc:
            logger.error(
                "News signal scoring succeeded but DB persistence failed: %s",
                exc,
            )
            raise
    else:
        logger.info(
            "Pipeline complete (persist=False). Signal: score=%.4f confidence=%.4f",
            signal.final_score,
            signal.confidence,
        )

    return interpretation, signal


# ===========================================================================
# DATABASE TABLE CREATION HELPER
# ===========================================================================


def create_news_signals_table() -> None:
    """
    Create the news_signals table if it does not already exist.
    """
    from core.database import engine

    NewsSignalDB.__table__.create(bind=engine, checkfirst=True)
    logger.info("news_signals table ensured.")
    
    
    
    
# ===========================================================================
# MACRO DIGEST ANALYSIS (Currency Aggregation)
# ===========================================================================

_NEWS_DIGEST_SYSTEM_PROMPT = """\
You are an expert macroeconomic analyst and forex market interpreter.
Your role is to analyze a COLLECTION of recent news headlines and summaries for a SINGLE currency, in the context of quantitative market data, and produce a synthesized macro view.

Instead of analyzing each news item individually, you must synthesize the overall sentiment, identify the dominant economic theme, and determine the net directional impact on the currency.

## Your responsibilities

1. Read all provided news items carefully. They all pertain to the same currency.
2. Identify the dominant economic theme across these news items (e.g., Interest Rate Decision, Inflation, GDP, Geopolitical).
3. Synthesize the net sentiment: Are the overall news bullish, bearish, or neutral for the currency?
4. Weigh the news items implicitly: official central bank statements carry more weight than general editorial commentary.
5. Compare the synthesized textual sentiment against the numeric market context provided.
6. Use the pre-calculated `calculated_surprise` and `calculated_volatility` values EXACTLY as provided — do not recalculate or modify them.
7. Reason about cross-asset impacts (e.g., net hawkish USD → negative for XAU and SPX).

## Strict output rules

- `direction` MUST match the sign of `nlp_sentiment_score`:
    positive sentiment → direction = +1
    negative sentiment → direction = -1
    neutral sentiment  → direction = 0
- `surprise_factor` MUST equal the provided `calculated_surprise` value exactly.
- `expected_volatility` MUST equal the provided `calculated_volatility` value exactly.
- `quantitative_alignment` MUST be between 0.0 and 1.0 (how well the net text sentiment matches the macro numeric data).
- `cross_assets` values MUST be signed integers: -1, 0, or +1.
- If the news items are mixed and conflicting, keep sentiment magnitude LOW.
- impact_horizon must be one of: "immediate", "intraday", "multi-session"

Keep your reasoning concise (3-5 sentences) but economically meaningful. Explain the transmission mechanism of the dominant theme.
"""

_NEWS_DIGEST_HUMAN_TEMPLATE = """\
## Currency Macro Digest

**Target Currency**: {currency}
**Number of News Items Analyzed**: {news_count}

### News Items:
{news_items_text}

---

## Quantitative Market Context

**Target Asset**: {target_asset}

**Volatility Context**:
- Historical Volatility (annualized): {hv}
- ATR (current 3-day): {atr_current}
- ATR (baseline 14-day): {atr_baseline}
- Implied Volatility (index): {iv}

**Macro Context**:
- Yield Spread (10Y - 2Y): {yield_spread}

**Pre-Calculated Metrics** (use these EXACTLY):
- Calculated Surprise Factor: {calculated_surprise}
- Calculated Expected Volatility Multiplier: {calculated_volatility}

---

Synthesize the macro view for {currency} from the news items above.
Produce a structured NewsDigestInterpretation following all output rules.

{format_instructions}
"""

# Phase 5: اسکیمای فشرده دستی به‌جای format_instructions خودکار Pydantic
# (نسخه خودکار ~400-600 توکن بود؛ این نسخه ~90 توکن و همان فیلدها را پوشش می‌دهد)
_COMPACT_DIGEST_FORMAT_INSTRUCTIONS = """\
Output ONLY a raw JSON object (no markdown fences) with exactly these fields:
{"nlp_sentiment_score": <float -1..1>, "direction": <int: 1 bullish, -1 bearish, 0 neutral — must match sentiment sign>, "reasoning": <string, 3-5 sentences>, "detected_event_category": <string or null>, "impact_horizon": <"immediate"|"intraday"|"multi-session">, "cross_assets": <object mapping asset→int -1/0/1, may be empty>, "surprise_factor": <float 0..1 — copy from context>, "expected_volatility": <float >=0 — copy from context>, "quantitative_alignment": <float 0..1>}"""


def build_news_digest_prompt() -> ChatPromptTemplate:
    parser = PydanticOutputParser(pydantic_object=NewsDigestInterpretation)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _NEWS_DIGEST_SYSTEM_PROMPT),
        ("human", _NEWS_DIGEST_HUMAN_TEMPLATE)
    ])
    # Phase 5: به‌جای format_instructions حجیم Pydantic از نسخه فشرده استفاده می‌کنیم
    return prompt.partial(format_instructions=_COMPACT_DIGEST_FORMAT_INSTRUCTIONS)


class NewsDigestAnalysisService:
    """سرویس تحلیل LLM برای Digest اخبار یک ارز."""
    
    def __init__(self, llm: BaseChatModel, prompt: Optional[ChatPromptTemplate] = None) -> None:
        self._llm = llm
        self._prompt = prompt or build_news_digest_prompt()
        self._parser = PydanticOutputParser(pydantic_object=NewsDigestInterpretation)
        # Chain with markdown stripping to handle LLMs that wrap JSON in code blocks
        self._chain = self._prompt | self._llm | RunnableLambda(_strip_json_markdown) | self._parser

    def analyze(
        self,
        currency: str,
        news_items: list[NewsItem],
        market_context: MarketContext,
    ) -> NewsDigestInterpretation:
        # ساخت متن اخبار برای پرامپت
        news_lines = []
        for idx, item in enumerate(news_items, 1):
            news_lines.append(f"[{idx}] Source: {item.source} | Title: {item.title}")
            if item.summary:
                # Phase 5: محدود کردن طول خلاصه برای کاهش مصرف توکن
                # (قبلاً 200 کاراکتر بود؛ 120 کاراکتر برای سیگنال کافی است)
                news_lines.append(f"    Summary: {item.summary[:120]}...")
        news_items_text = "\n".join(news_lines)

        prompt_inputs = {
            "currency": currency,
            "news_count": len(news_items),
            "news_items_text": news_items_text,
            "target_asset": market_context.target_asset,
            "hv": _format_optional(market_context.historical_volatility),
            "atr_current": _format_optional(market_context.atr_current),
            "atr_baseline": _format_optional(market_context.atr_baseline),
            "iv": _format_optional(market_context.implied_volatility),
            "yield_spread": _format_optional(market_context.yield_spread),
            "calculated_surprise": _format_optional(market_context.calculated_surprise),
            "calculated_volatility": _format_optional(market_context.calculated_volatility),
        }

        logger.info(
            "Calling LLM for news digest: currency=%s items_count=%d",
            currency, len(news_items)
        )

        try:
            result = invoke_with_retry(self._chain, prompt_inputs)
        except Exception as exc:
            logger.error("LLM call failed for digest %s: %s", currency, exc, exc_info=True)
            raise

        if not isinstance(result, NewsDigestInterpretation):
            raise ValueError(f"LLM returned unexpected type {type(result).__name__!r}")

        result = self._enforce_market_context_values(result, market_context)
        
        logger.info(
            "Digest interpretation complete: currency=%s direction=%d sentiment=%.3f theme=%s",
            currency, result.direction, result.nlp_sentiment_score, result.detected_event_category
        )
        return result

    @staticmethod
    def _enforce_market_context_values(
        interpretation: NewsDigestInterpretation,
        market_context: MarketContext,
    ) -> NewsDigestInterpretation:
        needs_correction = False
        surprise = market_context.calculated_surprise or 0.0
        volatility = market_context.calculated_volatility or 1.0

        if abs(interpretation.surprise_factor - surprise) > 0.001:
            needs_correction = True
        if abs(interpretation.expected_volatility - volatility) > 0.001:
            needs_correction = True

        if needs_correction:
            interpretation = interpretation.model_copy(update={
                "surprise_factor": _clamp(surprise, 0.0, 1.0),
                "expected_volatility": max(volatility, 0.0),
            })
        return interpretation


class NewsDigestScorer:
    """موتور امتیازدهی قطعی برای سیگنال‌های تجمیعی."""
    
    def score(
        self,
        currency: str,
        news_items: list[NewsItem],
        interpretation: NewsDigestInterpretation,
        market_context: MarketContext,
        ticker: str,
    ) -> NewsSignal:
        # استفاده از بالاترین reliability بین اخبار
        source_reliability = max(
            (item.source_reliability or 0.65 for item in news_items),
            default=0.65
        )
        
        resolved_category, event_category_weight = _resolve_event_category_weight(
            interpretation.detected_event_category
        )
        
        data_completeness_score = compute_data_completeness(market_context)
        data_quality_factor = compute_data_quality_factor(data_completeness_score)
        
        sentiment = interpretation.nlp_sentiment_score
        surprise = interpretation.surprise_factor
        expected_volatility = interpretation.expected_volatility
        quant_alignment = interpretation.quantitative_alignment
        direction = interpretation.direction
        
        # در حالت Digest، headline_body_alignment معنایی ندارد، پس آن را با quant_alignment برابر می‌کنیم
        headline_body_alignment = quant_alignment
        
        raw_score = (
            sentiment
            * event_category_weight
            * (1.0 + surprise)
            * data_quality_factor
        )
        final_score = _clamp(raw_score, -1.0, 1.0)
        
        confidence_raw = (
            (data_completeness_score * 0.30)
            + (min(abs(surprise), 1.0) * 0.25)
            + (source_reliability * 0.20)
            + (headline_body_alignment * 0.15)
            + (quant_alignment * 0.10)
        )
        confidence = _clamp(confidence_raw, 0.0, 1.0)
        
        is_tradable = (
            abs(final_score) >= TRADABILITY_MIN_SCORE
            and confidence >= TRADABILITY_MIN_CONFIDENCE
            and data_completeness_score >= TRADABILITY_MIN_COMPLETENESS
        )
        
        if expected_volatility >= 1.5:
            vol_level = "High"
        elif expected_volatility <= 0.5:
            vol_level = "Low"
        else:
            vol_level = "Normal"
            
        category_multiplier = HALF_LIFE_CATEGORY_MULTIPLIER.get(
            resolved_category, HALF_LIFE_CATEGORY_MULTIPLIER["Unknown"]
        )
        safe_volatility = max(expected_volatility, 0.25)
        signal_half_life_mins = int((HALF_LIFE_BASE_MINS * category_multiplier) / safe_volatility)
        signal_half_life_mins = max(signal_half_life_mins, 1)
        
        cross_asset_signals = {}
        for asset, asset_dir in interpretation.cross_assets.items():
            cross_asset_signals[asset] = round(abs(final_score) * 0.7 * float(asset_dir), 3)
            
        return NewsSignal(
            reasoning=interpretation.reasoning,
            asset_class=interpretation.asset_class if hasattr(interpretation, 'asset_class') else "FX",
            direction=direction,
            final_score=final_score,
            confidence=confidence,
            is_tradable=is_tradable,
            expected_volatility_level=vol_level,
            signal_half_life_mins=signal_half_life_mins,
            cross_asset_signals=cross_asset_signals,
            source="Aggregated",
            source_reliability=source_reliability,
            event_category_weight=event_category_weight,
            data_completeness_score=data_completeness_score,
            data_quality_factor=data_quality_factor,
            headline_body_alignment=headline_body_alignment,
            quantitative_alignment=quant_alignment,
            ticker=ticker,
        )


def analyze_news_digest(
    currency: str,
    news_items: list[NewsItem],
    market_context: MarketContext,
    ticker: str,
    llm: BaseChatModel,
    *,
    persist: bool = True,
) -> tuple[NewsDigestInterpretation, NewsSignal]:
    """Orchestration برای تحلیل تجمیعی اخبار یک ارز."""
    
    service = NewsDigestAnalysisService(llm=llm)
    scoring_engine = NewsDigestScorer()
    repo = NewsSignalRepository()
    
    interpretation = service.analyze(currency, news_items, market_context)
    signal = scoring_engine.score(currency, news_items, interpretation, market_context, ticker)
    
    if persist:
        # برای ذخیره در دیتابیس، اولین خبر را به عنوان representative به repo می‌دهیم
        # اما فیلدهای link و title را در DB نال می‌گذاریم و لینک‌ها را در json ذخیره می‌کنیم
        representative_item = news_items[0]
        
        # هک کوچک: مقادیر link و title را موقتا خالی می‌گذاریم تا به صورت single نباشد
        # NewsSignalRepository از news_item برای serialize استفاده می‌کند
        links_json = json.dumps([item.link for item in news_items if item.link])
        titles_str = " | ".join([item.title for item in news_items[:3]]) + ("..." if len(news_items) > 3 else "")
        
        # repo.save انتظار دارد interpretation از نوع NewsInterpretation باشد، 
        # چون فیلد asset_class را می‌خواند. پس یک کپی با فیلدهای منطبق می‌سازیم
        # اخطار: این بخش بستگی به ساختار NewsSignalRepository دارد.
        # اگر مشکلی بود خودمان مستقیم در DB ذخیره می‌کنیم. فعلا دستی ذخیره می‌کنیم برای امنیت:
        
        from core.database import NewsSignalDB, SessionLocal as DBSessionLocal
        import datetime as dt
        
        db_record = NewsSignalDB(
            created_at=dt.datetime.utcnow(),
            source="Aggregated",
            title=titles_str,
            summary=None,
            link=None,
            published_at=dt.datetime.utcnow(),
            event_title=market_context.event_title,
            asset_class=signal.asset_class,
            ticker=signal.ticker,
            direction=signal.direction,
            final_score=signal.final_score,
            confidence=signal.confidence,
            is_tradable=signal.is_tradable,
            expected_volatility_level=signal.expected_volatility_level,
            signal_half_life_mins=signal.signal_half_life_mins,
            source_reliability=signal.source_reliability,
            event_category_weight=signal.event_category_weight,
            data_completeness_score=signal.data_completeness_score,
            data_quality_factor=signal.data_quality_factor,
            headline_body_alignment=signal.headline_body_alignment,
            quantitative_alignment=signal.quantitative_alignment,
            reasoning=signal.reasoning,
            raw_news_payload_json=json.dumps([item.model_dump(mode='json') for item in news_items]),
            raw_market_context_json=market_context.model_dump_json(),
            is_aggregated=True,
            source_links_json=links_json,
        )
        
        session = DBSessionLocal()
        try:
            session.add(db_record)
            session.commit()
            session.refresh(db_record)
            logger.info("Aggregated NewsSignal saved: id=%d ccy=%s score=%.4f", db_record.id, currency, signal.final_score)
        except Exception as exc:
            session.rollback()
            logger.error("DB save failed for digest: %s", exc)
            raise
        finally:
            session.close()
            
    return interpretation, signal