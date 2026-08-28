"""
agents/technical/technical_signal.py
====================================
TechnicalSignal — خروجی ساخت‌یافته Technical Engine.

این ماژول P0 است:
  - enumهای SignalState، TrendState، VolatilityState
  - Pydantic `TechnicalSignal` با Output Contract کامل
  - `build_technical_signal(...)` که همهٔ ماژول‌های P0 را ترکیب می‌کند

LLM در این فایل نیست؛ فقط مقادیر deterministic.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

from core import technical_config as cfg
from agents.technical import indicators as ind_mod
from agents.technical import price_action as pa_mod
from agents.technical import market_structure as ms_mod
from agents.technical import market_regime as regime_mod
from agents.technical import data_quality as dq_mod
from agents.technical import smc as smc_mod
from agents.technical import confluence as conf_mod

# Keep imports used below sane (re-exported for callers)
from agents.technical.confluence import ConfluenceItem


class SignalState(str, Enum):
    STRONG_BULLISH = "STRONG_BULLISH"
    BULLISH = "BULLISH"
    WEAK_BULLISH = "WEAK_BULLISH"
    NEUTRAL = "NEUTRAL"
    WEAK_BEARISH = "WEAK_BEARISH"
    BEARISH = "BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"


class TrendState(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"
    UNCLEAR = "UNCLEAR"


class VolatilityState(str, Enum):
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class TechnicalSignal(BaseModel):
    """Output Contract Technical Engine."""

    symbol: str
    direction: str                        # SignalState
    direction_value: int                  # +1/-1/0
    score: float
    confidence: float
    market_regime: str
    time_horizon: str
    trend: str
    market_structure: str
    momentum: str
    volatility: str
    smc_context: str
    mtf_alignment: str
    confluence: list[ConfluenceItem] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "direction_value": self.direction_value,
            "score": self.score,
            "confidence": self.confidence,
            "market_regime": self.market_regime,
            "time_horizon": self.time_horizon,
            "trend": self.trend,
            "market_structure": self.market_structure,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "smc_context": self.smc_context,
            "mtf_alignment": self.mtf_alignment,
            "confluence": [x.__dict__ for x in self.confluence],
            "supporting_evidence": self.supporting_evidence,
            "contradicting_evidence": self.contradicting_evidence,
            "risks": self.risks,
            "reasoning_summary": self.reasoning_summary,
        }


# ===========================================================================
# Helpers
# ===========================================================================

def _signal_state(score: float) -> SignalState:
    if score >= cfg.SIGNAL_STATE_STRONG:
        return SignalState.STRONG_BULLISH
    if score >= cfg.SIGNAL_STATE_BULLISH:
        return SignalState.BULLISH
    if score >= cfg.SIGNAL_STATE_WEAK:
        return SignalState.WEAK_BULLISH
    if score <= -cfg.SIGNAL_STATE_STRONG:
        return SignalState.STRONG_BEARISH
    if score <= -cfg.SIGNAL_STATE_BULLISH:
        return SignalState.BEARISH
    if score <= -cfg.SIGNAL_STATE_WEAK:
        return SignalState.WEAK_BEARISH
    return SignalState.NEUTRAL


def _direction_value(score: float) -> int:
    return 1 if score > 0 else (-1 if score < 0 else 0)


def _trend_state(trend_status: Optional[str]) -> str:
    if not trend_status:
        return TrendState.UNCLEAR.value
    lower = trend_status.lower()
    if "up" in lower:
        return TrendState.BULLISH.value
    if "down" in lower:
        return TrendState.BEARISH.value
    if "ranging" in lower:
        return TrendState.RANGING.value
    return TrendState.UNCLEAR.value


def _volatility_state(atr_pct, atr_median_pct, regime_label) -> str:
    if regime_label in (regime_mod.MarketRegime.HIGH_VOLATILITY.value,):
        return VolatilityState.HIGH.value
    if regime_label in (regime_mod.MarketRegime.LOW_VOLATILITY.value,):
        return VolatilityState.LOW.value
    if atr_pct is not None and atr_median_pct is not None:
        if atr_pct >= cfg.ATR_HIGH_MULTIPLIER * atr_median_pct:
            return VolatilityState.HIGH.value
        if atr_pct <= cfg.ATR_LOW_MULTIPLIER * atr_median_pct:
            return VolatilityState.LOW.value
    return VolatilityState.NORMAL.value


def _detect_breakout(df: pd.DataFrame, price: Optional[float]) -> bool:
    if df is None or df.empty or price is None:
        return False
    df2 = df.copy()
    df2.columns = [str(c).lower() for c in df2.columns]
    if "high" not in df2.columns or "low" not in df2.columns:
        return False
    lookback = min(cfg.BREAKOUT_LOOKBACK_BARS, max(len(df2) - cfg.BREAKOUT_CONFIRM_BARS, 1))
    window = df2.iloc[-(lookback + cfg.BREAKOUT_CONFIRM_BARS):]
    # شکست بالای سقف بازه/پایین کف بازه در چند کندل اخیر
    if len(window) < cfg.BREAKOUT_CONFIRM_BARS + 1:
        return False
    base = df2.iloc[:-(cfg.BREAKOUT_CONFIRM_BARS)]
    if len(base) < lookback:
        return False
    recent_hi = df2["high"].tail(cfg.BREAKOUT_CONFIRM_BARS).max()
    recent_lo = df2["low"].tail(cfg.BREAKOUT_CONFIRM_BARS).min()
    base_hi = base["high"].tail(lookback).max()
    base_lo = base["low"].tail(lookback).min()
    return bool(recent_hi > base_hi or recent_lo < base_lo)


def _horizon_label(timeframe: str, roles: Optional[dict]) -> str:
    if roles and timeframe in roles:
        role = roles[timeframe]
        return {"HTF": "SWING", "MTF": "SWING", "LTF": "INTRADAY"}.get(role, "INTRADAY")
    if timeframe in ("W1", "D1", "H4", "H2"):
        return "SWING"
    return "INTRADAY"


# ===========================================================================
# Builder
# ===========================================================================

def build_technical_signal(
    *,
    ticker: str,
    timeframe: str,
    metrics,
    df: Optional[pd.DataFrame] = None,
    mtf_matrix=None,
    mtf_scores_roles: Optional[dict] = None,
) -> TechnicalSignal:
    """
    ساخت TechnicalSignal از TechnicalMetrics + (اختیاری) MTF Matrix.

    `df` فقط برای ATR% / swing / structure / breakout استفاده می‌شود.
    در walk-forward، df باید همان slice [0..t] باشد.
    """
    score = float(metrics.technical_score or 0.0) if metrics is not None else 0.0
    # Gate موجود در `calculate_technical_metrics`: فقط وقتی structure یا smc_location
    # ارزیابی شده باشد score معتبر است. در غیر این صورت 0 (Neutral) می‌ماند.
    technically_evaluated = metrics is not None and metrics.technical_score is not None
    direction_value = _direction_value(score)
    state = _signal_state(score)

    # --- ATR ---
    at = ind_mod.compute_atr_pct(df) if df is not None else {}
    atr_pct = at.get("atr_pct")
    atr_median_pct = at.get("atr_median_pct")

    # --- Data Quality ---
    dq = dq_mod.compute_data_quality(df) if df is not None else dq_mod.DataQualityResult()

    # --- Price Action / Structure ---
    swing = pa_mod.detect_swing_structure(df) if df is not None else pa_mod.SwingStructure()
    structure = ms_mod.detect_market_structure(df, swings=None) if df is not None else ms_mod.StructureSignal()

    # --- Regime ---
    breakout = _detect_breakout(df, metrics.current_price if metrics else None) if df is not None else False
    regime = regime_mod.detect_market_regime(
        adx=metrics.adx_value if metrics else None,
        chop=metrics.chop_value if metrics else None,
        vol_z=at.get("vol_z"),
        breakout=breakout,
        choch=structure.choch,
        trend_label=metrics.trend_status if metrics else "",
    )

    # --- SMC context ---
    smc_ctx = smc_mod.build_smc_context(metrics) if metrics is not None else smc_mod.SmcContext()

    # --- Confluence / score / confidence ---
    conf = conf_mod.build_confluence(
        metrics,
        weights=cfg.TECHNICAL_WEIGHTS,
        overall_dir=direction_value,
        regime=regime,
        data_quality=dq,
        mtf_matrix=mtf_matrix,
        timeframe=timeframe,
        smc_ctx=smc_ctx,
        structure_label=ms_mod.structure_label_for_signal(structure, swing),
    )

    # --- Final score: ConfluenceEngine با حفظ gate ارزیابی ---
    final_score = conf.score if technically_evaluated else 0.0
    direction_value = _direction_value(final_score)
    state = _signal_state(final_score)

    # --- MTF Alignment label ---
    mtf_alignment = "N/A"
    if mtf_matrix is not None:
        htf_label = getattr(mtf_matrix, "htf_bias_label", None)
        if htf_label:
            mtf_alignment = htf_label.replace("Neutral", "MIXED_OR_NEUTRAL")

    # --- Trend / Momentum / Volatility ---
    trend = _trend_state(metrics.trend_status if metrics else None)
    momentum = _momentum_state(metrics, direction_value)
    volatility = _volatility_state(atr_pct, atr_median_pct, regime.regime)

    # --- Evidence ---
    supporting = list(conf.supporting)
    contradicting = list(conf.contradicting)
    risks = list(conf.risks)

    if smc_ctx.evidence:
        supporting.extend(smc_ctx.evidence)
    if smc_ctx.risks:
        risks.extend(smc_ctx.risks)
    if regime.reasons:
        risks.extend([f"regime:{r}" for r in regime.reasons])

    # Reasoning summary deterministic
    parts = [
        f"score={final_score:+.2f}",
        f"confidence={conf.confidence:.2f}",
        f"regime={regime.regime}",
        f"trend={trend}",
        f"structure={ms_mod.structure_label_for_signal(structure, swing)}",
        f"smc={smc_ctx.label}",
    ]
    if mtf_alignment != "N/A":
        parts.append(f"mtf={mtf_alignment}")

    return TechnicalSignal(
        symbol=ticker,
        direction=state.value,
        direction_value=direction_value,
        score=final_score,
        confidence=conf.confidence,
        market_regime=regime.regime,
        time_horizon=_horizon_label(timeframe, mtf_scores_roles),
        trend=trend,
        market_structure=ms_mod.structure_label_for_signal(structure, swing),
        momentum=momentum,
        volatility=volatility,
        smc_context=smc_ctx.label,
        mtf_alignment=mtf_alignment,
        confluence=conf.items,
        supporting_evidence=supporting,
        contradicting_evidence=contradicting,
        risks=risks,
        reasoning_summary=" | ".join(parts),
    )


def _momentum_state(metrics, direction_value: int) -> str:
    if metrics is None:
        return SignalState.NEUTRAL.value
    comp = getattr(metrics, "components", None)
    mom = getattr(comp, "momentum", 0) if comp is not None else 0
    rsi = getattr(metrics, "rsi", None)
    macd_h = getattr(metrics, "macd_histogram", None)

    if mom is None or mom == 0:
        return "NEUTRAL"
    if mom > 0.0:
        if rsi is not None and rsi > 70:
            return "OVERBOUGHT_BULLISH"
        return "BULLISH" if mom > 0.4 else "WEAK_BULLISH"
    if mom < 0.0:
        if rsi is not None and rsi < 30:
            return "OVERSOLD_BEARISH"
        return "BEARISH" if mom < -0.4 else "WEAK_BEARISH"
    return "NEUTRAL"
