# nlp_event.py
"""
nlp_event.py
============
ماژول تحلیل مستقیم رویدادهای اقتصادی برای دستیار معاملاتی فارکس.

تحلیل رویدادهای منتشرشده Forex Factory (CPI، NFP، GDP، Rate Decision، ...)
بر اساس داده‌های عددی، بدون نیاز به خبر یا توییت.

لایه‌ها:
  1. Validation        : فیلتر released-only و non-economic
  2. Event Brief       : ساخت متن deterministic از اعداد
  3. Prompt builder    : build_event_prompt()
  4. Analysis service  : EventAnalysisService
  5. Scoring engine    : EventSignalScorer
  6. Repository        : EventSignalRepository
  7. Orchestration     : analyze_economic_event()
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
from sqlalchemy.exc import SQLAlchemyError

from core.database import EventSignalDB, SessionLocal
from agents.fundamental.data_fetcher import (
    HistoricalStdResult,
    fetch_historical_surprise_std_full,
)
from core.models import (
    EventAnalysisInput,
    EventInterpretationEvent,
    EventSignal,
    MarketContext,
)
from core.llm_utils import invoke_with_retry, _strip_json_markdown

logger = logging.getLogger(__name__)


# ===========================================================================
# CONSTANTS
# ===========================================================================

EVENT_IMPACT_WEIGHT: dict[str, float] = {
    "High": 1.00,
    "Medium": 0.70,
    "Low": 0.40,
    "Unknown": 0.50,
}

# Base half-life بر اساس category (دقیقه)
EVENT_HALF_LIFE_BASE_MINS: dict[str, int] = {
    "Interest Rate Decision": 240,
    "Non-Farm Payrolls": 180,
    "CPI": 150,
    "Inflation": 150,
    "GDP": 120,
    "Employment": 90,
    "Unemployment": 90,
    "Retail Sales": 90,
    "PMI": 60,
    "Manufacturing": 60,
    "Trade Balance": 45,
    "Housing": 45,
    "Consumer Confidence": 45,
    "Central Bank Commentary": 90,
    "Default": 60,
}

# Multiplier بر اساس impact
HALF_LIFE_IMPACT_MULTIPLIER: dict[str, float] = {
    "High": 1.5,
    "Medium": 1.0,
    "Low": 0.6,
    "Unknown": 0.8,
}

# آستانه‌های tradability
TRADABILITY_MIN_SCORE: float = 0.40
TRADABILITY_MIN_CONFIDENCE: float = 0.55
TRADABILITY_MIN_COMPLETENESS: float = 0.40


# ===========================================================================
# UTILITIES
# ===========================================================================


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _resolve_event_impact_weight(impact: str) -> float:
    """تبدیل impact label به weight عددی."""
    return EVENT_IMPACT_WEIGHT.get(impact, EVENT_IMPACT_WEIGHT["Unknown"])


def _resolve_event_category(input_category: Optional[str], event_title: str) -> str:
    """
    تشخیص category از روی input یا title.
    برای half-life و logging استفاده می‌شود.
    """
    if input_category and input_category != "Unknown":
        return input_category

    title_lower = event_title.lower()

    if (
        "interest rate" in title_lower
        or "rate decision" in title_lower
        or "fed funds" in title_lower
        or "federal funds rate" in title_lower
        or "main refinancing rate" in title_lower
        or "official bank rate" in title_lower
        or "cash rate" in title_lower
        or "overnight rate" in title_lower
        or "policy rate" in title_lower
    ):
        return "Interest Rate Decision"
    if "non-farm" in title_lower or "nfp" in title_lower:
        return "Non-Farm Payrolls"
    if "cpi" in title_lower or "consumer price" in title_lower:
        return "CPI"
    if "inflation" in title_lower or "pce" in title_lower or "ppi" in title_lower:
        return "Inflation"
    if "gdp" in title_lower:
        return "GDP"
    if "unemployment rate" in title_lower:
        return "Unemployment"
    if "employment" in title_lower or "jobless" in title_lower or "payroll" in title_lower:
        return "Employment"
    if "retail sales" in title_lower:
        return "Retail Sales"
    if "pmi" in title_lower or "ism" in title_lower:
        return "PMI"
    if "manufacturing" in title_lower or "industrial" in title_lower:
        return "Manufacturing"
    if "trade balance" in title_lower or "current account" in title_lower:
        return "Trade Balance"
    if "housing" in title_lower or "home" in title_lower or "building" in title_lower:
        return "Housing"
    if "confidence" in title_lower or "sentiment" in title_lower:
        return "Consumer Confidence"
    if "fomc" in title_lower or "minutes" in title_lower or "statement" in title_lower:
        return "Central Bank Commentary"

    return "Default"


# ===========================================================================
# VALIDATION
# ===========================================================================


def _validate_for_analysis(event_input: EventAnalysisInput) -> tuple[bool, str]:
    """
    اعتبارسنجی ورودی قبل از تحلیل.

    Returns:
        (is_valid, rejection_reason)
    """
    if event_input.actual is None:
        return False, "event_not_released"

    if event_input.impact == "Non-Economic":
        return False, "non_economic_event"

    if not event_input.title or not event_input.title.strip():
        return False, "missing_title"

    if not event_input.currency or not event_input.currency.strip():
        return False, "missing_currency"

    return True, ""


# ===========================================================================
# EVENT BRIEF GENERATOR (قلب ماژول)
# ===========================================================================


def build_event_brief(event_input: EventAnalysisInput) -> str:
    """
    ساخت یک brief deterministic از event برای ورودی LLM.

    این متن:
      - تفسیر ندارد (LLM تفسیر می‌کند)
      - فقط حقایق را به زبان طبیعی بیان می‌کند
      - شامل beat/miss/in_line objective است
      - شامل momentum analysis است
    """
    lines = []

    # Header
    lines.append(f"Economic Event: {event_input.title}")
    lines.append(f"Currency: {event_input.currency}")
    if event_input.impact:
        lines.append(f"Impact Level: {event_input.impact}")
    if event_input.event_date:
        lines.append(f"Released: {event_input.event_date.isoformat()}")

    lines.append("")

    # Values
    actual_str = event_input.raw_actual_str or f"{event_input.actual:g}"
    lines.append(f"Actual:   {actual_str}")

    if event_input.forecast is not None:
        forecast_str = event_input.raw_forecast_str or f"{event_input.forecast:g}"
        lines.append(f"Forecast: {forecast_str}")
    else:
        lines.append("Forecast: not available")

    if event_input.previous is not None:
        previous_str = event_input.raw_previous_str or f"{event_input.previous:g}"
        lines.append(f"Previous: {previous_str}")
    else:
        lines.append("Previous: not available")

    lines.append("")

    # Objective surprise interpretation
    if event_input.forecast is not None:
        diff = event_input.actual - event_input.forecast
        if abs(diff) < 1e-9:
            lines.append("Result: Came in EXACTLY in line with forecast (no surprise).")
        else:
            direction_word = "BEAT" if diff > 0 else "MISSED"
            lines.append(
                f"Result: Actual {direction_word} forecast by {abs(diff):.4g}."
            )

    # Momentum
    if event_input.previous is not None:
        delta = event_input.actual - event_input.previous
        if abs(delta) < 1e-9:
            lines.append("Momentum: Unchanged vs previous period.")
        elif delta > 0:
            lines.append(
                f"Momentum: Increased by {abs(delta):.4g} vs previous period."
            )
        else:
            lines.append(
                f"Momentum: Decreased by {abs(delta):.4g} vs previous period."
            )

    return "\n".join(lines)


# ===========================================================================
# PROMPT BUILDER
# ===========================================================================

_EVENT_SYSTEM_PROMPT = """\
You are an expert macroeconomic event analyst specializing in interpreting
economic data releases (CPI, NFP, GDP, rate decisions, etc.) and their
impact on currency and cross-asset markets.

You analyze RELEASED economic events where the actual value has been published.

## Your input includes

- Event details (name, currency, impact level, category)
- Actual, Forecast, and Previous values
- An auto-generated event brief with objective beat/miss/in_line summary
- Full quantitative market context (volatility, ATR, IV, yield spread)
- Pre-calculated surprise factor and expected volatility multiplier
- Optionally: detailed event description ("Usual Effect", "FF Notes", etc.)

## Your responsibilities

1. SURPRISE INTERPRETATION (PURE MATH - IGNORE ECONOMIC MEANING):
   - "beat"   = actual strictly greater than forecast
   - "miss"   = actual strictly less than forecast
   - "in_line" = actual exactly equal or very close to forecast
   WARNING: "beat" and "miss" refer ONLY to the math of actual vs forecast. 
   Do NOT let economic meaning affect this label. 
   If Actual=4.2 and Forecast=4.1, it is ALWAYS a "beat" mathematically, even if it is bad for the economy.

2. MOMENTUM vs PREVIOUS:
   - "accelerating" = actual moved meaningfully in stronger direction vs previous
   - "decelerating" = actual weakened vs previous
   - "stable" = roughly unchanged
   - "unknown" = no previous available

3. ECONOMIC IMPLICATION (choose one):
   - hawkish / dovish / neutral
   - risk_on / risk_off
   - inflationary / recessionary

4. DIRECTIONAL IMPACT on currency (CRITICAL RULES):
   The direction indicates if the currency strengthens (+1) or weakens (-1).
   
   A. STANDARD EVENTS (e.g., CPI, NFP, GDP, Retail Sales):
      - Actual > Forecast (math beat) -> economy strong -> currency strengthens -> direction = +1
      - Actual < Forecast (math miss) -> economy weak -> currency weakens -> direction = -1
      
   B. INVERSE EVENTS (e.g., Unemployment Rate, Jobless Claims, Trade Deficit):
      In these events, a HIGHER actual number means a WEAKER economy.
      - Actual > Forecast (math beat) -> economy weak -> currency weakens -> direction = -1
      - Actual < Forecast (math miss) -> economy strong -> currency strengthens -> direction = +1

   DO NOT mix the math label with the direction. 
   A "beat" on Unemployment Rate means direction = -1. 
   A "miss" on Unemployment Rate means direction = +1.
   If "Usual Effect" is provided in the event detail, USE IT as the highest priority rule.

5. CROSS-ASSET IMPLICATIONS & CENTRAL BANKS:
   - STRICT RULE: Only reference the Central Bank that controls the event's currency 
     (e.g., Fed for USD, BoC for CAD, ECB for EUR, BoE for GBP, BoJ for JPY). 
     DO NOT mention other central banks under any circumstances.
   - Hawkish USD → XAU bearish, SPX bearish
   - Dovish USD → XAU bullish, SPX bullish
   - Risk-on → JPY/CHF bearish, AUD/NZD bullish
   - Risk-off → JPY/CHF/USD bullish
   - Inflationary → bond yields up, commodities ambiguous

6. QUANTITATIVE ALIGNMENT:
   Score 0.0-1.0 how well your textual interpretation matches the numbers.

7. is_consistent_with_event_type:
   Set true if your interpretation follows the event's standard rule
   (e.g., NFP beat → bullish USD, Jobless Claims miss → bullish USD).
   Set false if you deviate from the standard (with reasoning).

## STRICT output rules

- direction MUST match the sign of nlp_sentiment_score
  (positive sentiment → +1, negative → -1, near-zero → 0)
- surprise_factor MUST equal the provided calculated_surprise EXACTLY
- expected_volatility MUST equal the provided calculated_volatility EXACTLY
- cross_assets values must be -1, 0, or +1 (signed integers)
- For "Low" impact events: keep sentiment magnitude modest (|score| ≤ 0.5)
- For "in_line" results: keep sentiment near zero (|score| ≤ 0.2)
- Use detail_text (if provided) to understand event semantics correctly

## Reasoning style

Be concise (3-5 sentences). Focus on:
- What the surprise means quantitatively
- Why this is bullish/bearish/neutral for the currency (mentioning the correct central bank)
- Cross-asset transmission mechanism (if relevant)
- Whether this is a meaningful signal or noise
"""

_EVENT_EXAMPLES_PROMPT = """\
## Example A — Hot CPI surprise (USD bullish)

Input:
  Event: "CPI y/y", USD, High impact
  Actual: 3.8%, Forecast: 3.5%, Previous: 3.6%
  calculated_surprise: 0.74, calculated_volatility: 1.65
  Usual Effect: 'Actual' greater than 'Forecast' is good for currency

Expected output:
  surprise_interpretation: "beat"
  momentum_vs_previous: "accelerating"
  economic_implication: "inflationary"
  direction: 1
  nlp_sentiment_score: ~0.75
  quantitative_alignment: 0.95
  cross_assets: {{"XAU": -1, "SPX": -1}}
  impact_horizon: "multi-session"
  is_consistent_with_event_type: true
  reasoning: "CPI beat by 0.3pp with momentum accelerating from 3.6 to 3.8.
              This is inflationary, supporting hawkish Fed expectations,
              bullish USD. Cross-asset: gold and equities pressured by higher
              rate expectations."

## Example B — In-line NFP (no signal)

Input:
  Event: "Non-Farm Employment Change", USD, High impact
  Actual: 185K, Forecast: 185K, Previous: 190K
  calculated_surprise: 0.04, calculated_volatility: 0.85

Expected output:
  surprise_interpretation: "in_line"
  momentum_vs_previous: "stable"
  economic_implication: "neutral"
  direction: 0
  nlp_sentiment_score: ~0.02
  quantitative_alignment: 0.95
  cross_assets: {{}}
  impact_horizon: "immediate"
  is_consistent_with_event_type: true
  reasoning: "NFP came in exactly at forecast. No incremental signal.
              Minor decrease vs previous is within noise. Neutral impact."

## Example C — Jobless Claims drop (inverse rule, math miss -> bullish)

Input:
  Event: "Unemployment Claims", USD, Medium impact
  Actual: 200K, Forecast: 220K, Previous: 225K
  calculated_surprise: 0.65, calculated_volatility: 1.0
  Usual Effect: 'Actual' less than 'Forecast' is good for currency

Expected output:
  surprise_interpretation: "miss"
  momentum_vs_previous: "accelerating"
  economic_implication: "hawkish"
  direction: 1
  nlp_sentiment_score: ~0.5
  is_consistent_with_event_type: true
  reasoning: "Jobless claims came in much lower than expected (200k vs 220k),
              indicating stronger labor market. Per usual effect, this is
              bullish USD as it supports Fed hawkishness."

## Example D — Unemployment Rate rises (inverse rule, math beat -> bearish)

Input:
  Event: "Unemployment Rate", USD, High impact
  Actual: 4.2%, Forecast: 4.1%, Previous: 4.0%
  calculated_surprise: 0.5, calculated_volatility: 1.0

Expected output:
  surprise_interpretation: "beat"
  momentum_vs_previous: "accelerating"
  economic_implication: "dovish"
  direction: -1
  nlp_sentiment_score: ~-0.5
  is_consistent_with_event_type: true
  reasoning: "Unemployment Rate beat the forecast mathematically (4.2% vs 4.1%).
              Since this is an inverse event, a higher number means a weaker labor market.
              This is dovish, bearish for USD."
"""

_EVENT_HUMAN_TEMPLATE = """\
## Event Details

Title: {title}
Currency: {currency}
Category: {category}
Impact: {impact}
Released: {event_date}

## Released Values

Actual:   {actual} (raw: {raw_actual_str})
Forecast: {forecast} (raw: {raw_forecast_str})
Previous: {previous} (raw: {raw_previous_str})

## Event Brief

{event_brief}

## Detailed Description

{detail_text}

---

## Quantitative Market Context

Target Asset: {target_asset}

Volatility:
- Historical Volatility (annual): {hv}
- ATR (3-day current):  {atr_current}
- ATR (14-day baseline): {atr_baseline}
- Implied Volatility:    {iv}

Macro:
- Yield Spread (10Y - 2Y): {yield_spread}

Pre-calculated (use EXACTLY):
- Surprise Factor:           {calculated_surprise}
- Expected Volatility Mult.: {calculated_volatility}

Historical std value:  {historical_std}
Historical std source: {historical_std_source}

---

Analyze this released economic event and produce a structured
EventInterpretation following all output rules.

{format_instructions}
"""


def build_event_prompt() -> ChatPromptTemplate:
    """ChatPromptTemplate برای تحلیل event."""
    parser = PydanticOutputParser(pydantic_object=EventInterpretationEvent)
    full_system = _EVENT_SYSTEM_PROMPT + "\n\n" + _EVENT_EXAMPLES_PROMPT
    prompt = ChatPromptTemplate.from_messages([
        ("system", full_system),
        ("human", _EVENT_HUMAN_TEMPLATE),
    ])
    return prompt.partial(format_instructions=parser.get_format_instructions())

def _format_optional(value: Any, precision: int = 4) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{precision}f}"
    return str(value)


def _build_prompt_inputs(
    event_input: EventAnalysisInput,
    market_context: MarketContext,
    event_brief: str,
    historical_std: float,
    historical_std_source: str,
) -> dict[str, str]:
    """ساخت dict ورودی template."""
    return {
        "title": event_input.title,
        "currency": event_input.currency,
        "category": event_input.category or "Unknown",
        "impact": event_input.impact,
        "event_date": event_input.event_date.isoformat() if event_input.event_date else "N/A",
        "actual": _format_optional(event_input.actual),
        "forecast": _format_optional(event_input.forecast),
        "previous": _format_optional(event_input.previous),
        "raw_actual_str": event_input.raw_actual_str or "N/A",
        "raw_forecast_str": event_input.raw_forecast_str or "N/A",
        "raw_previous_str": event_input.raw_previous_str or "N/A",
        "event_brief": event_brief,
        "detail_text": event_input.detail_text or "(no detail available)",
        "target_asset": market_context.target_asset,
        "hv": _format_optional(market_context.historical_volatility),
        "atr_current": _format_optional(market_context.atr_current, 6),
        "atr_baseline": _format_optional(market_context.atr_baseline, 6),
        "iv": _format_optional(market_context.implied_volatility, 2),
        "yield_spread": _format_optional(market_context.yield_spread, 3),
        "calculated_surprise": _format_optional(market_context.calculated_surprise, 4),
        "calculated_volatility": _format_optional(market_context.calculated_volatility, 4),
        "historical_std": _format_optional(historical_std),
        "historical_std_source": historical_std_source,
    }


# ===========================================================================
# ANALYSIS SERVICE
# ===========================================================================


class EventAnalysisService:
    """
    سرویس تحلیل LLM برای event اقتصادی.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        prompt: Optional[ChatPromptTemplate] = None,
    ) -> None:
        self._llm = llm
        self._prompt = prompt or build_event_prompt()
        self._parser = PydanticOutputParser(pydantic_object=EventInterpretationEvent)
        self._chain = self._prompt | self._llm | RunnableLambda(_strip_json_markdown) | self._parser

    def analyze(
        self,
        event_input: EventAnalysisInput,
        market_context: MarketContext,
        event_brief: str,
        historical_std: float,
        historical_std_source: str,
    ) -> EventInterpretationEvent:
        prompt_inputs = _build_prompt_inputs(
            event_input, market_context, event_brief,
            historical_std, historical_std_source,
        )

        logger.info(
            "Calling LLM for event: %r (%s, impact=%s)",
            event_input.title, event_input.currency, event_input.impact,
        )

        try:
            result = invoke_with_retry(self._chain, prompt_inputs)
        except Exception as exc:
            logger.error(
                "LLM call failed for event %r: %s",
                event_input.title, exc, exc_info=True,
            )
            raise

        if result is None:
            raise ValueError(f"LLM returned None for event: {event_input.title!r}")

        if not isinstance(result, EventInterpretationEvent):
            raise ValueError(
                f"LLM returned unexpected type {type(result).__name__!r}"
            )

        # enforce echo values
        result = self._enforce_market_context_values(result, market_context)

        logger.info(
            "LLM interpretation: direction=%d sentiment=%.3f "
            "surprise=%s momentum=%s implication=%s",
            result.direction, result.nlp_sentiment_score,
            result.surprise_interpretation, result.momentum_vs_previous,
            result.economic_implication,
        )
        return result

    @staticmethod
    def _enforce_market_context_values(
        interpretation: EventInterpretationEvent,
        market_context: MarketContext,
    ) -> EventInterpretationEvent:
        needs_correction = False
        surprise = market_context.calculated_surprise or 0.0
        volatility = market_context.calculated_volatility or 1.0

        if abs(interpretation.surprise_factor - surprise) > 0.001:
            logger.warning(
                "LLM surprise drift: LLM=%.4f Context=%.4f — correcting",
                interpretation.surprise_factor, surprise,
            )
            needs_correction = True

        if abs(interpretation.expected_volatility - volatility) > 0.001:
            logger.warning(
                "LLM volatility drift: LLM=%.4f Context=%.4f — correcting",
                interpretation.expected_volatility, volatility,
            )
            needs_correction = True

        if needs_correction:
            interpretation = interpretation.model_copy(update={
                "surprise_factor": _clamp(surprise, 0.0, 1.0),
                "expected_volatility": max(volatility, 0.0),
            })

        return interpretation


# ===========================================================================
# SCORING ENGINE
# ===========================================================================


class EventSignalScorer:
    """
    موتور امتیازدهی deterministic برای event signals.

    فرمول:
      final_score = clamp(sentiment * impact_weight * (1+surprise)
                          * data_quality * quant_alignment
                          * std_reliability_mult, -1, 1)

      confidence = weighted sum of:
        impact_weight, surprise, completeness,
        quant_alignment, std_reliability_mult
        with penalties for inconsistencies
    """

    def score(
        self,
        event_input: EventAnalysisInput,
        interpretation: EventInterpretationEvent,
        market_context: MarketContext,
        historical_std_result: HistoricalStdResult,
        ticker: str,
    ) -> EventSignal:
        # --- impact weight ---
        impact_weight = _resolve_event_impact_weight(event_input.impact)

        # --- data completeness ---
        data_completeness = self._compute_data_completeness(market_context)
        data_quality_factor = _clamp(
            0.60 + (0.40 * data_completeness), 0.60, 1.00
        )

        # --- alignment ---
        quant_alignment = interpretation.quantitative_alignment

        # --- historical std reliability ---
        std_reliable = historical_std_result.is_reliable()
        std_reliability_mult = 1.00 if std_reliable else 0.75

        # --- core values ---
        sentiment = interpretation.nlp_sentiment_score
        surprise = interpretation.surprise_factor
        volatility = interpretation.expected_volatility
        direction = interpretation.direction

        # --- final score ---
        raw_score = (
            sentiment
            * impact_weight
            * (1.0 + surprise)
            * data_quality_factor
            * quant_alignment
            * std_reliability_mult
        )
        final_score = _clamp(raw_score, -1.0, 1.0)

        # --- confidence ---
        confidence_raw = (
            (impact_weight * 0.30)
            + (min(abs(surprise), 1.0) * 0.25)
            + (data_completeness * 0.20)
            + (quant_alignment * 0.15)
            + (std_reliability_mult * 0.10)
        )

        # penalty: high surprise but weak sentiment
        if surprise > 0.7 and abs(sentiment) < 0.3:
            confidence_raw -= 0.10

        # penalty: fallback std
        if not std_reliable:
            confidence_raw -= 0.05

        # penalty: LLM said inconsistent
        if not interpretation.is_consistent_with_event_type:
            confidence_raw -= 0.10

        confidence = _clamp(confidence_raw, 0.0, 1.0)

        # --- tradability ---
        is_tradable = (
            abs(final_score) >= TRADABILITY_MIN_SCORE
            and confidence >= TRADABILITY_MIN_CONFIDENCE
            and data_completeness >= TRADABILITY_MIN_COMPLETENESS
            and event_input.impact != "Low"
        )

        # --- volatility level ---
        if volatility >= 1.5:
            vol_level = "High"
        elif volatility <= 0.5:
            vol_level = "Low"
        else:
            vol_level = "Normal"

        # --- half-life ---
        category = _resolve_event_category(
            event_input.category, event_input.title
        )
        base_time = EVENT_HALF_LIFE_BASE_MINS.get(
            category, EVENT_HALF_LIFE_BASE_MINS["Default"]
        )
        impact_mult = HALF_LIFE_IMPACT_MULTIPLIER.get(
            event_input.impact, HALF_LIFE_IMPACT_MULTIPLIER["Unknown"]
        )
        safe_vol = max(volatility, 0.25)
        half_life = int((base_time * impact_mult) / safe_vol)
        half_life = max(half_life, 5)

        # --- cross-asset signals ---
        cross_asset_signals: dict[str, float] = {}
        for asset, asset_dir in interpretation.cross_assets.items():
            cross_asset_signals[asset] = round(
                abs(final_score) * 0.7 * float(asset_dir), 3
            )

        # --- surprise value ---
        surprise_value = None
        if event_input.forecast is not None:
            surprise_value = event_input.actual - event_input.forecast

        logger.debug(
            "EventSignalScorer: %s/%s score=%.4f conf=%.4f tradable=%s half_life=%dmin",
            event_input.title, event_input.currency,
            final_score, confidence, is_tradable, half_life,
        )

        return EventSignal(
            reasoning=interpretation.reasoning,
            asset_class=interpretation.asset_class,
            direction=direction,
            final_score=final_score,
            confidence=confidence,
            is_tradable=is_tradable,
            expected_volatility_level=vol_level,
            signal_half_life_mins=half_life,
            cross_asset_signals=cross_asset_signals,
            event_title=event_input.title,
            event_currency=event_input.currency,
            event_impact=event_input.impact,
            event_category=event_input.category,
            event_date=event_input.event_date,
            actual=event_input.actual,
            forecast=event_input.forecast,
            previous=event_input.previous,
            surprise_value=surprise_value,
            event_impact_weight=impact_weight,
            data_quality_factor=data_quality_factor,
            quantitative_alignment=quant_alignment,
            historical_std_used=historical_std_result.value or 0.0,
            historical_std_source=historical_std_result.source,
            historical_std_reliable=std_reliable,
            surprise_interpretation=interpretation.surprise_interpretation,
            momentum_vs_previous=interpretation.momentum_vs_previous,
            economic_implication=interpretation.economic_implication,
            impact_horizon=interpretation.impact_horizon,
            ticker=ticker,
        )

    @staticmethod
    def _compute_data_completeness(market_context: MarketContext) -> float:
        """مشابه nlp_news و nlp_x — ۹ فیلد کلیدی."""
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
        non_null = sum(1 for f in key_fields if f is not None)
        return non_null / len(key_fields)


# ===========================================================================
# REPOSITORY
# ===========================================================================


class EventSignalRepository:
    """ذخیره‌سازی EventSignal در جدول event_signals."""

    @staticmethod
    def _serialize_event_input(event_input: EventAnalysisInput) -> str:
        try:
            data = event_input.model_dump()
            if data.get("event_date") and isinstance(data["event_date"], datetime):
                data["event_date"] = data["event_date"].isoformat()
            return json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("serialize event_input failed: %s", exc)
            return json.dumps({"error": "serialization_failed"})

    @staticmethod
    def _serialize_context(ctx: MarketContext) -> str:
        try:
            return ctx.model_dump_json()
        except (TypeError, ValueError) as exc:
            logger.warning("serialize market_context failed: %s", exc)
            return json.dumps({"error": "serialization_failed"})

    def save(
        self,
        event_input: EventAnalysisInput,
        signal: EventSignal,
        market_context: MarketContext,
        event_brief: str,
    ) -> int:
        db_record = EventSignalDB(
            created_at=datetime.utcnow(),
            event_title=signal.event_title,
            event_currency=signal.event_currency,
            event_impact=signal.event_impact,
            event_category=signal.event_category,
            event_date=signal.event_date,
            external_id=event_input.external_id,
            actual=signal.actual,
            forecast=signal.forecast,
            previous=signal.previous,
            surprise_value=signal.surprise_value,
            ticker=signal.ticker,
            asset_class=signal.asset_class,
            direction=signal.direction,
            final_score=signal.final_score,
            confidence=signal.confidence,
            is_tradable=signal.is_tradable,
            expected_volatility_level=signal.expected_volatility_level,
            signal_half_life_mins=signal.signal_half_life_mins,
            event_impact_weight=signal.event_impact_weight,
            data_quality_factor=signal.data_quality_factor,
            quantitative_alignment=signal.quantitative_alignment,
            historical_std_used=signal.historical_std_used,
            historical_std_source=signal.historical_std_source,
            historical_std_reliable=signal.historical_std_reliable,
            surprise_interpretation=signal.surprise_interpretation,
            momentum_vs_previous=signal.momentum_vs_previous,
            economic_implication=signal.economic_implication,
            impact_horizon=signal.impact_horizon,
            reasoning=signal.reasoning,
            raw_event_payload_json=self._serialize_event_input(event_input),
            raw_market_context_json=self._serialize_context(market_context),
            event_brief_text=event_brief,
        )

        session = SessionLocal()
        try:
            session.add(db_record)
            session.commit()
            session.refresh(db_record)
            record_id = db_record.id
            logger.info(
                "EventSignal saved: id=%d %s/%s score=%.4f tradable=%s",
                record_id, signal.event_title, signal.event_currency,
                signal.final_score, signal.is_tradable,
            )
            return record_id
        except SQLAlchemyError as exc:
            session.rollback()
            logger.error("DB save failed: %s", exc, exc_info=True)
            raise
        finally:
            session.close()


# ===========================================================================
# ORCHESTRATION
# ===========================================================================


def analyze_economic_event(
    event_input: EventAnalysisInput,
    market_context: MarketContext,
    ticker: str,
    llm: BaseChatModel,
    *,
    prompt: Optional[ChatPromptTemplate] = None,
    persist: bool = True,
    scorer: Optional[EventSignalScorer] = None,
    repository: Optional[EventSignalRepository] = None,
    analysis_service: Optional[EventAnalysisService] = None,
) -> tuple[EventInterpretationEvent, EventSignal]:
    """
    تابع orchestration اصلی: event → interpretation → signal → DB.

    Returns:
        (EventInterpretation, EventSignal)

    Raises:
        ValueError: اگر event released نیست یا non-economic است
    """
    # --- validation ---
    is_valid, reason = _validate_for_analysis(event_input)
    if not is_valid:
        raise ValueError(f"Event cannot be analyzed: {reason}")

    if not ticker or not ticker.strip():
        raise ValueError("ticker نمی‌تواند خالی باشد")

    if not isinstance(market_context, MarketContext):
        raise ValueError(
            f"market_context باید MarketContext باشد، got {type(market_context).__name__!r}"
        )

    logger.info(
        "Starting event analysis: %s/%s (impact=%s) → ticker=%s",
        event_input.title, event_input.currency, event_input.impact, ticker,
    )

    # --- fetch historical std (با metadata کامل) ---
    historical_std_result = fetch_historical_surprise_std_full(
        event_title=event_input.title,
        currency=event_input.currency,
    )
    historical_std = historical_std_result.value or 0.15

    logger.info(
        "Historical std: value=%.4f source=%s obs=%d reliable=%s",
        historical_std,
        historical_std_result.source,
        historical_std_result.observations,
        historical_std_result.is_reliable(),
    )

    # --- build event brief ---
    event_brief = build_event_brief(event_input)

    # --- build services ---
    service = analysis_service or EventAnalysisService(llm=llm, prompt=prompt)
    scoring_engine = scorer or EventSignalScorer()
    repo = repository or EventSignalRepository()

    # --- step 1: LLM analysis ---
    try:
        interpretation = service.analyze(
            event_input=event_input,
            market_context=market_context,
            event_brief=event_brief,
            historical_std=historical_std,
            historical_std_source=historical_std_result.source,
        )
    except Exception as exc:
        logger.error("Analysis failed: %s", exc)
        raise

    # --- step 2: scoring ---
    try:
        signal = scoring_engine.score(
            event_input=event_input,
            interpretation=interpretation,
            market_context=market_context,
            historical_std_result=historical_std_result,
            ticker=ticker,
        )
    except Exception as exc:
        logger.error("Scoring failed: %s", exc)
        raise

    # --- step 3: persistence ---
    if persist:
        try:
            record_id = repo.save(
                event_input=event_input,
                signal=signal,
                market_context=market_context,
                event_brief=event_brief,
            )
            logger.info("Pipeline complete. DB id=%d", record_id)
        except SQLAlchemyError as exc:
            logger.error("DB save failed: %s", exc)
            raise
    else:
        logger.info(
            "Pipeline complete (persist=False). score=%.4f conf=%.4f",
            signal.final_score, signal.confidence,
        )

    return interpretation, signal


# ===========================================================================
# HELPER: convert ForexFactoryEvent to EventAnalysisInput
# ===========================================================================


def event_input_from_forex_factory(
    event,  # ForexFactoryEvent type
    *,
    detail_text: Optional[str] = None,
) -> EventAnalysisInput:
    """
    تبدیل ForexFactoryEvent به EventAnalysisInput.

    detail_text اگر موجود باشد (مثلاً از historical DB)، اضافه می‌شود.
    """
    actual_float = event.actual_float()
    if actual_float is None:
        raise ValueError(
            f"Event has no parseable actual value: {event.title!r}"
        )

    return EventAnalysisInput(
        title=event.title,
        currency=event.currency,
        impact=event.impact,
        category=event.category,
        event_date=event.date,
        actual=actual_float,
        forecast=event.forecast_float(),
        previous=event.previous_float(),
        raw_actual_str=event.actual,
        raw_forecast_str=event.forecast,
        raw_previous_str=event.previous,
        detail_text=detail_text,
        source="Forex Factory",
    )


def event_input_from_db_record(
    record,  # EventHistoryDB type
) -> EventAnalysisInput:
    """تبدیل EventHistoryDB به EventAnalysisInput."""
    if record.actual is None:
        raise ValueError(
            f"DB record has no actual value: {record.title!r}"
        )

    return EventAnalysisInput(
        title=record.title,
        currency=record.currency or "USD",
        impact=record.impact or "Unknown",
        category=record.category,
        event_date=record.date,
        actual=float(record.actual),
        forecast=float(record.forecast) if record.forecast is not None else None,
        previous=float(record.previous) if record.previous is not None else None,
        raw_actual_str=record.raw_actual_str,
        raw_forecast_str=record.raw_forecast_str,
        raw_previous_str=record.raw_previous_str,
        detail_text=record.detail_text,
        source=record.source or "Forex Factory",
    )