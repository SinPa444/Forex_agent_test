"""
agents/technical/indicators/momentum.py
=======================================
فاکتور Momentum (6%) — RSI + هیستوگرام MACD.
منطق امتیازدهی دقیقاً نسخه قبل:
  MACD hist > 0 و RSI > 55  → +0.8
  MACD hist < 0 و RSI < 45  → -0.8
  وگرنه                      → 0.0
  اگر هرکدام از RSI/MACD در دسترس نباشد → None (Not Evaluated)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .utils import last_indicator_value

# --- پارامترهای ثابت (مقادیر نسخه قبل، نام‌گذاری‌شده) ---
RSI_LENGTH = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_BULL_THRESHOLD = 55.0
RSI_BEAR_THRESHOLD = 45.0


@dataclass
class MomentumResult:
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    score: Optional[float] = None


def compute_momentum(df: pd.DataFrame) -> MomentumResult:
    """محاسبه فاکتور مومنتوم (RSI + MACD histogram)."""
    result = MomentumResult()
    result.rsi = last_indicator_value(df.ta.rsi(length=RSI_LENGTH))

    macd_df = df.ta.macd(fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    if macd_df is not None and not macd_df.empty:
        hist_col = [c for c in macd_df.columns if c.startswith("MACDh")]
        if hist_col and pd.notna(macd_df[hist_col[0]].iloc[-1]):
            result.macd_histogram = float(macd_df[hist_col[0]].iloc[-1])

    if result.rsi is not None and result.macd_histogram is not None:
        if result.macd_histogram > 0 and result.rsi > RSI_BULL_THRESHOLD:
            result.score = 0.8
        elif result.macd_histogram < 0 and result.rsi < RSI_BEAR_THRESHOLD:
            result.score = -0.8
        else:
            result.score = 0.0

    return result
