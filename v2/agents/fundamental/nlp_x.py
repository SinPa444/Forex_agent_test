# nlp_x.py
"""
nlp_x.py
========
ماژول تحلیل متن مبتنی بر سخنران برای دستیار معاملاتی فارکس.

تحلیل توییت‌ها، بیانیه‌ها، سخنرانی‌ها، و مصاحبه‌های
سخنرانان تأثیرگذار بازار (بانکداران مرکزی، وزرای مالیه، ...)
در کنار داده‌های کمّی بازار.

لایه‌ها:
  1. Prompt builder     : build_speaker_prompt()
  2. Analysis service   : SpeakerAnalysisService
  3. Scoring engine     : SpeakerSignalScorer
  4. Repository         : SpeakerSignalRepository
  5. Orchestration      : analyze_speaker_text()  ← LangGraph node آینده
  6. Legacy wrapper     : generate_signal()  ← سازگاری با گذشته
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

from core.database import SessionLocal, TradingSignalDB
from core.models import (
    MarketContext,
    Speaker,
    SpeakerTextItem,
    SpeakerInterpretation,
    SpeakerSignal,
    # Legacy models for backward compatibility
    EventInterpretation,
    Signal,
)
from core.llm_utils import invoke_with_retry, _strip_json_markdown

logger = logging.getLogger(__name__)


# ===========================================================================
# CONSTANTS
# ===========================================================================

SOURCE_RELIABILITY: dict[str, float] = {
    "Official Transcript": 0.98,
    "Press Conference Live": 0.96,
    "Bloomberg": 0.92,
    "Reuters": 0.90,
    "TV Interview": 0.85,
    "X": 0.72,
    "Unknown": 0.60,
}

STATEMENT_TYPE_WEIGHT: dict[str, float] = {
    "tweet": 0.90,
    "headline_quote": 0.85,
    "interview": 0.95,
    "statement": 1.00,
    "speech": 1.10,
    "press_conference": 1.10,
    "testimony": 1.15,
    "official_transcript": 1.15,
    "unknown": 0.80,
}

SOURCE_TYPE_BASE_MINS: dict[str, int] = {
    "tweet": 45,
    "headline_quote": 40,
    "interview": 60,
    "statement": 75,
    "speech": 120,
    "press_conference": 135,
    "testimony": 150,
    "official_transcript": 150,
    "unknown": 60,
}

TRADABILITY_MIN_SCORE: float = 0.40
TRADABILITY_MIN_CONFIDENCE: float = 0.60
TRADABILITY_MIN_COMPLETENESS: float = 0.40


# ===========================================================================
# UTILITY FUNCTIONS
# ===========================================================================


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _resolve_source_reliability(source: str) -> float:
    return SOURCE_RELIABILITY.get(source, SOURCE_RELIABILITY["Unknown"])


def _resolve_statement_type_weight(source_type: str) -> float:
    return STATEMENT_TYPE_WEIGHT.get(
        source_type.lower(),
        STATEMENT_TYPE_WEIGHT["unknown"],
    )


def _resolve_source_type_base_mins(source_type: str) -> int:
    return SOURCE_TYPE_BASE_MINS.get(
        source_type.lower(),
        SOURCE_TYPE_BASE_MINS["unknown"],
    )


def _resolve_speaker(
    speaker_name: str,
    weight_override: Optional[float] = None,
) -> Speaker:
    """
    سخنران را resolve می‌کند:
      1. اگر weight_override داده شده → Speaker از DB + override وزن
      2. اگر نه → Speaker.from_name() (DB lookup یا fallback)
    """
    speaker = Speaker.from_name(speaker_name)

    if weight_override is not None:
        speaker = speaker.model_copy(
            update={"weight": _clamp(weight_override, 0.0, 1.0)}
        )

    return speaker


# ===========================================================================
# DATA COMPLETENESS (shared with nlp_news.py)
# ===========================================================================


def compute_data_completeness(market_context: MarketContext) -> float:
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
    """data_quality_factor = 0.60 + (0.40 * completeness) → range [0.60, 1.00]"""
    return _clamp(0.60 + (0.40 * data_completeness_score), 0.60, 1.00)


def compute_alignment_factor(
    statement_market_alignment: float,
    quantitative_alignment: float,
) -> float:
    """alignment_factor = 0.50 + 0.25*sma + 0.25*qa → range [0.50, 1.00]"""
    return _clamp(
        0.50 + (0.25 * statement_market_alignment) + (0.25 * quantitative_alignment),
        0.50,
        1.00,
    )


# ===========================================================================
# PROMPT BUILDER
# ===========================================================================

_SPEAKER_SYSTEM_PROMPT = """\
You are an expert macroeconomic analyst specializing in interpreting statements
from central bankers, finance ministers, and other influential market figures.

Your role is to analyze a speaker's remark in the context of their institutional
role and the quantitative market data provided, then produce a structured,
economically rigorous interpretation.

## Your responsibilities

1. Read the speaker's text carefully in the context of their role and institution.
2. Determine whether this is forward guidance, a reaction to realized data,
   a policy commitment, or general commentary.
3. Assess whether the remark contains NEW information or is a REITERATION
   of a previously known stance.
4. Determine if the tone is hawkish, dovish, inflationary, deflationary,
   recessionary, growth-supportive, risk-on, or risk-off.
5. Compare the speaker's claims against the numeric market context
   (actual vs. forecast vs. previous values).
6. Use the pre-calculated `calculated_surprise` and `calculated_volatility`
   values EXACTLY as provided — do not recalculate or modify them.
7. Reason about cross-asset impacts.
8. Score statement-market alignment: does the speaker's claim match the
   current quantitative market reality?
9. Score quantitative alignment: does the textual interpretation match
   the numeric data?

## Speaker context rules

- Consider the speaker's role but do NOT invent a specific weight or score
  for their credibility — that is handled externally by the scoring engine.
- A Fed Chair's forward guidance carries different implications than a
  junior official's casual comment — reflect this in your reasoning.
- If the speaker is reiterating a well-known stance with no new data,
  keep sentiment magnitude LOW and set is_reiteration = true.

## Strict output rules

- `direction` MUST match the sign of `nlp_sentiment_score`:
    positive sentiment → direction = +1
    negative sentiment → direction = -1
    neutral sentiment  → direction = 0
- `surprise_factor` MUST equal the provided `calculated_surprise` exactly.
- `expected_volatility` MUST equal the provided `calculated_volatility` exactly.
- `statement_market_alignment` and `quantitative_alignment`: 0.0 to 1.0.
- `cross_assets` values: signed integers (-1, 0, +1).
- `policy_signal_type`: one of forward_guidance, data_reaction,
  policy_commitment, commentary.
- `impact_horizon`: one of immediate, intraday, multi-session.
- `guidance_bias`: one of hawkish, dovish, neutral, risk_on, risk_off,
  inflationary, recessionary.
- If the text is vague or reiterative and data is weak, keep sentiment LOW.
- If actual equals forecast (surprise near zero), avoid exaggerated signals.
- Never invent numeric data.

## Alignment scoring guidance

- statement_market_alignment = 1.0: speaker's claim is fully consistent with
  the current quantitative market context.
- statement_market_alignment = 0.5: partially consistent; some contradictions.
- statement_market_alignment = 0.0: speaker claims "tight labor market" but
  unemployment is rising and NFP missed badly.
- quantitative_alignment = 1.0: text sentiment perfectly matches the numbers.
- quantitative_alignment = 0.0: text says "beats" but actual < forecast.

## Illustrative examples

### Example A — Hawkish Powell, consistent with hot CPI data

Speaker: Jerome Powell (Fed Chair)
Market Context: {{"target_asset": "USD", "event_category": "Inflation", \
"actual_value": 3.8, "forecast_value": 3.5, "previous_value": 3.6, \
"calculated_surprise": 0.82, "calculated_volatility": 1.65}}
Statement: "Inflation remains stubbornly above our target. We will not \
hesitate to raise rates further if the data warrants it."

Expected output:
  reasoning: "Powell explicitly signals willingness to hike further after \
CPI beat (3.8% vs 3.5% forecast). This is a policy_commitment with hawkish \
guidance_bias. Consistent with hot inflation data and elevated surprise. \
Transmission: higher rate expectations → stronger USD, weaker gold/equities."
  direction: 1, nlp_sentiment_score: 0.82
  policy_signal_type: "policy_commitment"
  guidance_bias: "hawkish"
  is_reiteration: false
  statement_market_alignment: 0.95
  quantitative_alignment: 0.95
  cross_assets: {{"XAU": -1, "SPX": -1}}
  impact_horizon: "multi-session"

### Example B — Low-information reiteration

Speaker: Fed Governor Waller
Market Context: {{"target_asset": "USD", "actual_value": null, \
"forecast_value": null, "calculated_surprise": 0.0, \
"calculated_volatility": 1.0}}
Statement: "We continue to monitor the data carefully and will act \
as appropriate."

Expected output:
  reasoning: "Generic, non-committal commentary with no new policy signal. \
This is a reiteration of standard Fed language. No quantitative data to \
compare against. Impact should be minimal."
  direction: 0, nlp_sentiment_score: 0.02
  policy_signal_type: "commentary"
  guidance_bias: "neutral"
  is_reiteration: true
  statement_market_alignment: 0.70
  quantitative_alignment: 0.70
  cross_assets: {{}}
  impact_horizon: "immediate"

Keep reasoning concise (3-5 sentences) but economically meaningful.
Always explain the transmission mechanism.
"""

_SPEAKER_HUMAN_TEMPLATE = """\
## Speaker Information

**Speaker Name**: {speaker_name}
**Speaker Role**: {speaker_role}
**Speaker Primarily Impacts**: {speaker_impacts}
**Source**: {source}
**Source Type**: {source_type}
**Published**: {published}

## Statement / Text

{text}

---

## Quantitative Market Context

**Target Asset**: {target_asset}
**Event Title**: {event_title}

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

**Pre-Calculated Metrics** (use these EXACTLY):
- Calculated Surprise Factor: {calculated_surprise}
- Calculated Expected Volatility Multiplier: {calculated_volatility}

---

Analyze this speaker's statement against the quantitative context above.
Produce a structured SpeakerInterpretation following all output rules.

{format_instructions}
"""

def build_speaker_prompt() -> ChatPromptTemplate:
    """ChatPromptTemplate برای تحلیل متن سخنران."""
    parser = PydanticOutputParser(pydantic_object=SpeakerInterpretation)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _SPEAKER_SYSTEM_PROMPT),
            ("human", _SPEAKER_HUMAN_TEMPLATE),
        ]
    )
    return prompt.partial(format_instructions=parser.get_format_instructions())


def _format_optional(value: Any, precision: int = 4) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{precision}f}"
    return str(value)


def _build_prompt_inputs(
    item: SpeakerTextItem,
    speaker: Speaker,
    market_context: MarketContext,
) -> dict[str, str]:
    return {
        "speaker_name": speaker.name,
        "speaker_role": speaker.role,
        "speaker_impacts": speaker.primarily_impacts,
        "source": item.source,
        "source_type": item.source_type,
        "published": item.published.isoformat() if item.published else "Unknown",
        "text": item.text,
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
# ANALYSIS SERVICE
# ===========================================================================


class SpeakerAnalysisService:
    """
    سرویس تحلیل LLM برای متن سخنران.

    LLM را فراخوانی می‌کند و SpeakerInterpretation ساختاریافته تولید می‌کند.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        prompt: Optional[ChatPromptTemplate] = None,
    ) -> None:
        self._llm = llm
        self._prompt = prompt or build_speaker_prompt()
        self._parser = PydanticOutputParser(pydantic_object=SpeakerInterpretation)
        self._chain = self._prompt | self._llm | RunnableLambda(_strip_json_markdown) | self._parser

    def analyze(
        self,
        item: SpeakerTextItem,
        speaker: Speaker,
        market_context: MarketContext,
    ) -> SpeakerInterpretation:
        prompt_inputs = _build_prompt_inputs(item, speaker, market_context)

        logger.info(
            "Calling LLM for speaker text: speaker=%r text=%r source_type=%s",
            speaker.name,
            item.text[:80],
            item.source_type,
        )

        try:
            result = invoke_with_retry(self._chain, prompt_inputs)
        except Exception as exc:
            logger.error(
                "LLM call failed for speaker %r: %s",
                speaker.name,
                exc,
                exc_info=True,
            )
            raise

        if result is None:
            raise ValueError(
                f"LLM returned None for speaker text: {item.text[:80]!r}"
            )

        if not isinstance(result, SpeakerInterpretation):
            raise ValueError(
                f"LLM returned unexpected type {type(result).__name__!r}"
            )

        result = self._enforce_market_context_values(result, market_context)

        logger.info(
            "LLM interpretation: direction=%d sentiment=%.3f "
            "policy=%s bias=%s reiteration=%s",
            result.direction,
            result.nlp_sentiment_score,
            result.policy_signal_type,
            result.guidance_bias,
            result.is_reiteration,
        )
        return result

    @staticmethod
    def _enforce_market_context_values(
        interpretation: SpeakerInterpretation,
        market_context: MarketContext,
    ) -> SpeakerInterpretation:
        needs_correction = False
        surprise = market_context.calculated_surprise or 0.0
        volatility = market_context.calculated_volatility or 1.0

        if abs(interpretation.surprise_factor - surprise) > 0.001:
            logger.warning(
                "LLM surprise drift: LLM=%.4f, Context=%.4f — correcting",
                interpretation.surprise_factor, surprise,
            )
            needs_correction = True

        if abs(interpretation.expected_volatility - volatility) > 0.001:
            logger.warning(
                "LLM volatility drift: LLM=%.4f, Context=%.4f — correcting",
                interpretation.expected_volatility, volatility,
            )
            needs_correction = True

        if needs_correction:
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


class SpeakerSignalScorer:
    """
    موتور امتیازدهی قطعی برای سیگنال‌های مبتنی بر سخنران.

    فرمول‌ها:
      final_score = clamp(sentiment * speaker_weight * stmt_type_weight
                          * (1+surprise) * data_quality * alignment, -1, 1)
      confidence = weighted sum (speaker_weight, sentiment, completeness,
                   alignments, reliability, surprise) with penalties
    """

    def score(
        self,
        item: SpeakerTextItem,
        speaker: Speaker,
        interpretation: SpeakerInterpretation,
        market_context: MarketContext,
        ticker: str,
    ) -> SpeakerSignal:
        # --- Source metadata ---
        source_reliability = _resolve_source_reliability(item.source)
        statement_type_weight = _resolve_statement_type_weight(item.source_type)

        # --- Data quality ---
        data_completeness_score = compute_data_completeness(market_context)
        data_quality_factor = compute_data_quality_factor(data_completeness_score)

        # --- Alignment ---
        sma = interpretation.statement_market_alignment
        qa = interpretation.quantitative_alignment
        alignment_factor = compute_alignment_factor(sma, qa)

        # --- Core values ---
        sentiment = interpretation.nlp_sentiment_score
        surprise = interpretation.surprise_factor
        volatility = interpretation.expected_volatility
        direction = interpretation.direction
        speaker_weight = speaker.weight

        # --- Final score ---
        raw_score = (
            sentiment
            * speaker_weight
            * statement_type_weight
            * (1.0 + surprise)
            * data_quality_factor
            * alignment_factor
        )
        final_score = _clamp(raw_score, -1.0, 1.0)

        # --- Confidence ---
        confidence_raw = (
            (speaker_weight * 0.30)
            + (abs(sentiment) * 0.20)
            + (data_completeness_score * 0.15)
            + (qa * 0.15)
            + (sma * 0.10)
            + (source_reliability * 0.05)
            + (min(abs(surprise), 1.0) * 0.05)
        )

        # Penalty: high surprise but weak sentiment → inconsistency
        if surprise > 0.8 and abs(sentiment) < 0.35:
            confidence_raw -= 0.05

        # Penalty: tweet with low alignment → unreliable
        if item.source_type == "tweet" and sma < 0.4:
            confidence_raw -= 0.05

        confidence = _clamp(confidence_raw, 0.0, 1.0)

        # --- Tradability ---
        is_tradable = (
            abs(final_score) >= TRADABILITY_MIN_SCORE
            and confidence >= TRADABILITY_MIN_CONFIDENCE
            and data_completeness_score >= TRADABILITY_MIN_COMPLETENESS
        )

        # --- Volatility level ---
        if volatility >= 1.5:
            vol_level = "High"
        elif volatility <= 0.5:
            vol_level = "Low"
        else:
            vol_level = "Normal"

        # --- Half-life ---
        base_time = _resolve_source_type_base_mins(item.source_type)
        speaker_persistence = 0.75 + (speaker_weight * 0.50)
        safe_volatility = max(volatility, 0.25)
        half_life = int((base_time * speaker_persistence) / safe_volatility)
        half_life = max(half_life, 1)

        # --- Cross-asset signals ---
        cross_asset_signals: dict[str, float] = {}
        for asset, asset_dir in interpretation.cross_assets.items():
            cross_asset_signals[asset] = round(
                abs(final_score) * 0.7 * float(asset_dir), 3
            )

        logger.debug(
            "SpeakerSignalScorer: ticker=%s speaker=%s score=%.4f "
            "confidence=%.4f tradable=%s half_life=%dmin",
            ticker, speaker.name, final_score,
            confidence, is_tradable, half_life,
        )

        return SpeakerSignal(
            reasoning=interpretation.reasoning,
            asset_class=interpretation.asset_class,
            direction=direction,
            final_score=final_score,
            confidence=confidence,
            is_tradable=is_tradable,
            expected_volatility_level=vol_level,
            signal_half_life_mins=half_life,
            cross_asset_signals=cross_asset_signals,
            speaker_name=speaker.name,
            speaker_role=speaker.role,
            speaker_weight=speaker.weight,
            source=item.source,
            source_type=item.source_type,
            source_reliability=source_reliability,
            statement_type_weight=statement_type_weight,
            data_completeness_score=data_completeness_score,
            data_quality_factor=data_quality_factor,
            statement_market_alignment=sma,
            quantitative_alignment=qa,
            ticker=ticker,
            policy_signal_type=interpretation.policy_signal_type,
            impact_horizon=interpretation.impact_horizon,
        )


# ===========================================================================
# REPOSITORY
# ===========================================================================


class SpeakerSignalRepository:
    """ذخیره‌سازی SpeakerSignal در جدول trading_signals."""

    @staticmethod
    def _serialize_item(item: SpeakerTextItem) -> str:
        try:
            data = item.model_dump()
            if data.get("published") and isinstance(data["published"], datetime):
                data["published"] = data["published"].isoformat()
            return json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("Could not serialize SpeakerTextItem: %s", exc)
            return json.dumps({"error": "serialization_failed"})

    @staticmethod
    def _serialize_context(ctx: MarketContext) -> str:
        try:
            return ctx.model_dump_json()
        except (TypeError, ValueError) as exc:
            logger.warning("Could not serialize MarketContext: %s", exc)
            return json.dumps({"error": "serialization_failed"})

    def save(
        self,
        item: SpeakerTextItem,
        speaker: Speaker,
        interpretation: SpeakerInterpretation,
        signal: SpeakerSignal,
        market_context: MarketContext,
    ) -> int:
        db_record = TradingSignalDB(
            timestamp=datetime.utcnow(),
            # Event / text
            event_title=market_context.event_title,
            statement=item.text,
            # Core signal
            reasoning=signal.reasoning,
            asset_class=signal.asset_class,
            direction=signal.direction,
            final_score=signal.final_score,
            confidence=signal.confidence,
            is_tradable=signal.is_tradable,
            expected_volatility_level=signal.expected_volatility_level,
            # Speaker info
            speaker_name=speaker.name,
            speaker_role=speaker.role,
            speaker_weight=speaker.weight,
            # Source info
            source=item.source,
            source_type=item.source_type,
            source_reliability=signal.source_reliability,
            link=item.link,
            external_id=item.external_id,
            published_at=item.published,
            # Scoring metadata
            statement_type_weight=signal.statement_type_weight,
            data_completeness_score=signal.data_completeness_score,
            data_quality_factor=signal.data_quality_factor,
            statement_market_alignment=signal.statement_market_alignment,
            quantitative_alignment=signal.quantitative_alignment,
            signal_half_life_mins=signal.signal_half_life_mins,
            ticker=signal.ticker,
            # Policy analysis
            policy_signal_type=signal.policy_signal_type,
            impact_horizon=signal.impact_horizon,
            # Raw payloads
            raw_text=item.text,
            raw_market_context_json=self._serialize_context(market_context),
            raw_input_payload_json=self._serialize_item(item),
        )

        session = SessionLocal()
        try:
            session.add(db_record)
            session.commit()
            session.refresh(db_record)
            record_id = db_record.id
            logger.info(
                "SpeakerSignal saved: id=%d speaker=%s ticker=%s score=%.4f",
                record_id, speaker.name, signal.ticker, signal.final_score,
            )
            return record_id
        except SQLAlchemyError as exc:
            session.rollback()
            logger.error("DB save failed: %s", exc, exc_info=True)
            raise
        finally:
            session.close()


# ===========================================================================
# TOP-LEVEL ORCHESTRATION
# ===========================================================================


def analyze_speaker_text(
    item: SpeakerTextItem,
    market_context: MarketContext,
    ticker: str,
    llm: BaseChatModel,
    *,
    prompt: Optional[ChatPromptTemplate] = None,
    persist: bool = True,
    scorer: Optional[SpeakerSignalScorer] = None,
    repository: Optional[SpeakerSignalRepository] = None,
    analysis_service: Optional[SpeakerAnalysisService] = None,
) -> tuple[SpeakerInterpretation, SpeakerSignal]:
    """
    تابع orchestration اصلی: متن سخنران → تفسیر → سیگنال → DB.

    برای تبدیل به LangGraph node:
        def speaker_node(state):
            interp, signal = analyze_speaker_text(
                item=state["item"], market_context=state["ctx"],
                ticker=state["ticker"], llm=state["llm"],
            )
            return {**state, "interpretation": interp, "signal": signal}
    """
    if not ticker or not ticker.strip():
        raise ValueError("ticker must be a non-empty string")

    if not isinstance(market_context, MarketContext):
        raise ValueError(
            f"market_context must be MarketContext; got {type(market_context).__name__!r}"
        )

    logger.info(
        "Starting speaker analysis: speaker=%r source_type=%s ticker=%s",
        item.speaker_name, item.source_type, ticker,
    )

    # --- Resolve speaker ---
    speaker = _resolve_speaker(item.speaker_name, item.speaker_weight_override)
    logger.info(
        "Speaker resolved: %s (role=%s, weight=%.2f)",
        speaker.name, speaker.role, speaker.weight,
    )

    # --- Build services ---
    service = analysis_service or SpeakerAnalysisService(llm=llm, prompt=prompt)
    scoring_engine = scorer or SpeakerSignalScorer()
    repo = repository or SpeakerSignalRepository()

    # --- Step 1: LLM analysis ---
    try:
        interpretation = service.analyze(item, speaker, market_context)
    except Exception as exc:
        logger.error("Analysis failed for speaker %r: %s", speaker.name, exc)
        raise

    # --- Step 2: Scoring ---
    try:
        signal = scoring_engine.score(
            item=item,
            speaker=speaker,
            interpretation=interpretation,
            market_context=market_context,
            ticker=ticker,
        )
    except Exception as exc:
        logger.error("Scoring failed for speaker %r: %s", speaker.name, exc)
        raise

    # --- Step 3: Persistence ---
    if persist:
        try:
            record_id = repo.save(
                item=item,
                speaker=speaker,
                interpretation=interpretation,
                signal=signal,
                market_context=market_context,
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
# LEGACY COMPATIBILITY WRAPPERS
# ===========================================================================


def generate_signal(
    interpretation: EventInterpretation,
    speaker_weight: float,
) -> Signal:
    """
    ⚠️ LEGACY WRAPPER — سازگاری با کد قدیمی.

    این تابع رفتار generate_signal() اصلی را حفظ می‌کند.
    برای کد جدید از analyze_speaker_text() استفاده کنید.
    """
    raw_score = (
        interpretation.nlp_sentiment_score
        * speaker_weight
        * (1.0 + interpretation.surprise_factor)
    )
    final_score = _clamp(raw_score, -1.0, 1.0)

    tone_certainty = abs(interpretation.nlp_sentiment_score)
    confidence = (speaker_weight * 0.7) + (tone_certainty * 0.3)
    if interpretation.surprise_factor > 0.8 and tone_certainty < 0.5:
        confidence -= 0.1
    confidence = _clamp(confidence, 0.0, 1.0)

    safe_vol = max(interpretation.expected_volatility, 0.25)
    half_life = max(int(60 / safe_vol), 1)

    cross_signals: dict[str, float] = {}
    if interpretation.cross_assets:
        for asset, dir_val in interpretation.cross_assets.items():
            cross_signals[asset] = round(abs(final_score) * 0.7 * dir_val, 3)

    is_tradable = abs(final_score) >= 0.4 and confidence >= 0.6

    if interpretation.expected_volatility >= 1.5:
        vol_level = "High"
    elif interpretation.expected_volatility <= 0.5:
        vol_level = "Low"
    else:
        vol_level = "Normal"

    return Signal(
        reasoning=interpretation.reasoning,
        asset_class=interpretation.asset_class,
        direction=interpretation.direction,
        final_score=round(final_score, 3),
        confidence=round(confidence, 2),
        is_tradable=is_tradable,
        expected_volatility_level=vol_level,
        signal_half_life_mins=half_life,
        cross_asset_signals=cross_signals,
    )