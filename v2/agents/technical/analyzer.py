"""
agents/technical/analyzer.py
============================
TechnicalAgent — ترکیب موتور deterministic + روایت LLM (بازساخت Phase 1).

    agent = TechnicalAgent(llm)
    metrics, report = agent.analyze("EURUSD=X", timeframe="D1")

اصل: LLM فقط روایت می‌سازد؛ direction/score/confidence deterministic هستند.
زنجیره LLM، prompt و fallbackها دقیقاً نسخه قبل است (بدون تغییر).

تغییرات Phase 1 (مستند و کوچک):
  - W2: direction از score با deadband یکپارچه ±0.15 (قبلاً ±0.1)
  - W6: پارامتر مرده temporal_context حذف شد
  - LLM Guard: خروجی LLM در برابر سیگنال deterministic اعتبارسنجی می‌شود؛
    در تناقض آشکار یک retry و در شکست نهایی fallback «Stand aside» —
    llm_status ∈ {ok, guard_flagged, fallback} در report ثبت می‌شود
  - precomputed_metrics: اگر MTF اسکن قبلاً همان TF را محاسبه کرده باشد،
    دوباره fetch نمی‌شود (حذف double-fetch)
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from core.llm_utils import invoke_with_retry, _strip_json_markdown

from .engine import calculate_technical_metrics
from .llm_guard import validate_narrative
from .models import TechnicalMetrics
from .signals.report import LLMStrategy, TechnicalReport
from .signals.technical_signal import DIRECTION_DEADBAND, direction_from_score

logger = logging.getLogger(__name__)

# --- Prompt (متن دقیقاً نسخه قبل) ---
_TECH_SYSTEM_PROMPT = """\
You are an expert technical analyst specializing in Smart Money Concepts (SMC).
You are given deterministic technical scores calculated by a Python engine. 
Your ONLY job is to formulate a concise trading strategy and narrative reasoning based on these scores. 
DO NOT calculate or output scores yourself. Just explain the setup and suggest the action based on the provided data.

If score is positive, focus on long strategies. If negative, focus on short strategies. If near 0, suggest standing aside.
"""

_TECH_HUMAN_TEMPLATE = """\
## Technical Data for {ticker} (Timeframe: {timeframe})

Deterministic Scores (calculated by Python):
- Technical Score: {tech_score} (-1 to 1)
- Technical Confidence: {tech_confidence} (0 to 1)
- Component Scores: Structure={structure}, Trend={trend}, SMC Location={smc_loc}, MTF Confluence={mtf_conf}, Momentum={mom}, Volatility(ADX)={vol}, Liquidity Sweep={liq_sweep}, PDH/PDL={pdh_pdl}, OTE Zone={ote}

Market Context:
- Current Price: {current_price}
- Trend Status: {trend_status} (ADX: {adx_value}, CHOP: {chop_value})
- Higher Timeframe ({htf_interval}) Trend: {htf_trend_status}
- Recent Break of Structure: {recent_bos}
- Liquidity: {liq_status}
- Previous Day High/Low: {pdh} / {pdl} — {pdh_pdl_status}
- OTE Zone: {ote_status}
- Active Bullish OB (Demand): {active_bullish_ob}
- Active Bearish OB (Supply): {active_bearish_ob}
- Active Bullish FVG Zone (Demand): {active_bull_fvg_low} to {active_bull_fvg_high}
- Active Bearish FVG Zone (Supply): {active_bear_fvg_low} to {active_bear_fvg_high}
- Momentum: RSI={rsi}, MACD Hist={macd_histogram}
- Last Candle: {last_candle_type}

Based ONLY on the data above, formulate the strategy and reasoning.
{format_instructions}
"""

# --- retry guard: چند بار برای خروجی نامعتبر تلاش می‌کنیم ---
GUARD_MAX_ATTEMPTS = 2


class TechnicalAgent:
    """Independent Technical Analysis Agent."""

    def __init__(self, llm: BaseChatModel) -> None:
        self.llm = llm
        self.parser = PydanticOutputParser(pydantic_object=LLMStrategy)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", _TECH_SYSTEM_PROMPT),
            ("human", _TECH_HUMAN_TEMPLATE)
        ]).partial(format_instructions=self.parser.get_format_instructions())

        self.chain = self.prompt | self.llm | RunnableLambda(_strip_json_markdown) | self.parser

    def analyze(
        self,
        ticker: str,
        timeframe: str = "D1",
        precomputed_metrics: Optional[TechnicalMetrics] = None,
    ) -> tuple[TechnicalMetrics, TechnicalReport]:
        """
        اجرای کامل تحلیل: deterministic + LLM narrative.

        precomputed_metrics: اگر MTF اسکن قبلاً همین TF را محاسبه کرده
        باشد (همان ticker+TF) پاس می‌شود تا دوباره fetch نشود.
        """
        logger.info(f"[Tech Agent] Analyzing {ticker} on {timeframe}...")

        if precomputed_metrics is not None:
            metrics = precomputed_metrics
        else:
            metrics = calculate_technical_metrics(ticker, timeframe=timeframe)

        # Determine Direction from Score
        if metrics.technical_score is None:
            return metrics, TechnicalReport(
                direction=0,
                score=0.0,
                confidence=0.0,
                strategy="Stand aside",
                reasoning="Insufficient data to calculate technical score.",
                llm_status="fallback",
            )

        score = metrics.technical_score
        direction = direction_from_score(score)  # deadband یکپارچه Phase 1
        confidence = metrics.technical_confidence or 0.0

        prompt_inputs = {
            "ticker": ticker,
            "timeframe": timeframe,
            "tech_score": f"{score:.2f}",
            "tech_confidence": f"{confidence:.2f}",
            "structure": f"{metrics.components.structure:.2f}" if metrics.components.structure is not None else "N/A",
            "trend": f"{metrics.components.trend:.2f}" if metrics.components.trend is not None else "N/A",
            "smc_loc": f"{metrics.components.smc_location:.2f}" if metrics.components.smc_location is not None else "N/A",
            "mtf_conf": f"{metrics.components.mtf_confluence:.2f}" if metrics.components.mtf_confluence is not None else "N/A",
            "liq_sweep": f"{metrics.components.liquidity_sweep:.2f}" if metrics.components.liquidity_sweep is not None else "N/A",
            "pdh_pdl": f"{metrics.components.pdh_pdl:.2f}" if metrics.components.pdh_pdl is not None else "N/A",
            "ote": f"{metrics.components.ote_zone:.2f}" if metrics.components.ote_zone is not None else "N/A",
            "liq_status": metrics.liquidity_sweep_status or "N/A",
            "pdh": f"{metrics.previous_day_high:.5f}" if metrics.previous_day_high is not None else "N/A",
            "pdl": f"{metrics.previous_day_low:.5f}" if metrics.previous_day_low is not None else "N/A",
            "pdh_pdl_status": metrics.pdh_pdl_status or "N/A",
            "ote_status": metrics.ote_status or "N/A",
            "htf_interval": metrics.htf_interval or "N/A",
            "htf_trend_status": metrics.htf_trend_status or "N/A",
            "mom": f"{metrics.components.momentum:.2f}" if metrics.components.momentum is not None else "N/A",
            "vol": f"{metrics.components.volatility:.2f}" if metrics.components.volatility is not None else "N/A",
            "current_price": f"{metrics.current_price:.4f}" if metrics.current_price else "N/A",
            "trend_status": metrics.trend_status or "Unknown",
            "adx_value": f"{metrics.adx_value:.1f}" if metrics.adx_value else "N/A",
            "chop_value": f"{metrics.chop_value:.1f}" if metrics.chop_value else "N/A",
            "recent_bos": metrics.recent_bos or "None detected",
            "active_bullish_ob": f"{metrics.active_bullish_ob:.4f}" if metrics.active_bullish_ob is not None else "None nearby",
            "active_bearish_ob": f"{metrics.active_bearish_ob:.4f}" if metrics.active_bearish_ob is not None else "None nearby",
            "active_bull_fvg_low": f"{metrics.active_bull_fvg_low:.4f}" if metrics.active_bull_fvg_low is not None else "N/A",
            "active_bull_fvg_high": f"{metrics.active_bull_fvg_high:.4f}" if metrics.active_bull_fvg_high is not None else "N/A",
            "active_bear_fvg_low": f"{metrics.active_bear_fvg_low:.4f}" if metrics.active_bear_fvg_low is not None else "N/A",
            "active_bear_fvg_high": f"{metrics.active_bear_fvg_high:.4f}" if metrics.active_bear_fvg_high is not None else "N/A",
            "rsi": f"{metrics.rsi:.1f}" if metrics.rsi is not None else "N/A",
            "macd_histogram": f"{metrics.macd_histogram:.4f}" if metrics.macd_histogram is not None else "N/A",
            "last_candle_type": metrics.last_candle_type or "N/A",
        }

        last_reasons: list[str] = []
        for attempt in range(1, GUARD_MAX_ATTEMPTS + 1):
            try:
                llm_strat = invoke_with_retry(self.chain, prompt_inputs)
                if not isinstance(llm_strat, LLMStrategy):
                    raise ValueError("LLM returned unexpected type")

                guard = validate_narrative(llm_strat.strategy, llm_strat.reasoning, direction)
                if guard.ok:
                    logger.info(
                        f"[Tech Agent] {ticker} Report: Dir={direction} "
                        f"Score={score:.2f} Conf={confidence:.2f} (llm ok)"
                    )
                    return metrics, TechnicalReport(
                        direction=direction, score=score, confidence=confidence,
                        strategy=llm_strat.strategy, reasoning=llm_strat.reasoning,
                        llm_status="ok",
                    )

                last_reasons = guard.reasons
                logger.warning(
                    "[Tech Agent] %s %s narrative guard failed (attempt %d/%d): %s",
                    ticker, timeframe, attempt, GUARD_MAX_ATTEMPTS, guard.reasons,
                )
            except Exception as exc:
                logger.error(f"[Tech Agent] LLM analysis failed for {ticker}: {exc}")
                return metrics, TechnicalReport(
                    direction=direction, score=score, confidence=confidence,
                    strategy="Stand aside",
                    reasoning=f"Technical LLM narrative failed: {exc}",
                    llm_status="fallback",
                )

        # guard در هر دو تلاش ناموفق — روایت لنگ می‌ماند اما سیگنال deterministic سالم است
        logger.warning(
            "[Tech Agent] %s %s narrative flagged after %d attempts: %s",
            ticker, timeframe, GUARD_MAX_ATTEMPTS, last_reasons,
        )
        return metrics, TechnicalReport(
            direction=direction, score=score, confidence=confidence,
            strategy="Stand aside",
            reasoning=(
                f"LLM narrative failed guard validation ({'; '.join(last_reasons)}); "
                f"deterministic signal stands."
            ),
            llm_status="guard_flagged",
        )
