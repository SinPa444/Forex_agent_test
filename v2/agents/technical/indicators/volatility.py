"""
agents/technical/indicators/volatility.py
=========================================
فاکتور Volatility/Regime (4%) — ADX + CHOP (+ ATR به‌عنوان داده خام Phase 1).

منطق امتیازدهی دقیقاً نسخه قبل:
  ADX > 25 → +0.5 | ADX < 20 → -0.5
  CHOP < 38.2 → +0.3 | CHOP > 61.8 → -0.3
  جمع clamp روی [-0.8, 0.8] | اگر هر دو None → None

ATR(14) در Phase 1 فقط به‌عنوان **داده خام** ثبت می‌شود (تأثیر روی score: صفر).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .utils import last_indicator_value

# --- پارامترهای ثابت (مقادیر نسخه قبل، نام‌گذاری‌شده) ---
ADX_LENGTH = 14
CHOP_LENGTH = 14
ATR_LENGTH = 14
ADX_STRONG = 25.0
ADX_CHOPPY = 20.0
CHOP_TRENDING = 38.2
CHOP_CHOPPY = 61.8
REGIME_SCORE_BOUND = 0.8


@dataclass
class RegimeResult:
    adx: Optional[float] = None
    chop: Optional[float] = None
    atr: Optional[float] = None
    score: Optional[float] = None


def compute_regime(df: pd.DataFrame) -> RegimeResult:
    """محاسبه فاکتور رژیم بازار (ADX + CHOP) و ATR خام."""
    result = RegimeResult()

    adx_df = df.ta.adx(length=ADX_LENGTH)
    if adx_df is not None and not adx_df.empty:
        adx_col = [c for c in adx_df.columns if c.startswith("ADX")]
        if adx_col and pd.notna(adx_df[adx_col[0]].iloc[-1]):
            result.adx = float(adx_df[adx_col[0]].iloc[-1])

    result.chop = last_indicator_value(df.ta.chop(length=CHOP_LENGTH))

    # ATR — داده خام Phase 1 (بدون تأثیر روی score)
    try:
        result.atr = last_indicator_value(df.ta.atr(length=ATR_LENGTH))
    except Exception:
        result.atr = None

    regime_score = 0.0
    if result.adx is not None:
        if result.adx > ADX_STRONG:
            regime_score += 0.5   # Strong regime
        elif result.adx < ADX_CHOPPY:
            regime_score -= 0.5   # Chop risk
    if result.chop is not None:
        if result.chop < CHOP_TRENDING:
            regime_score += 0.3   # Trending cleanly
        elif result.chop > CHOP_CHOPPY:
            regime_score -= 0.3   # Choppy
    if result.adx is not None or result.chop is not None:
        result.score = max(-REGIME_SCORE_BOUND, min(REGIME_SCORE_BOUND, regime_score))

    return result
