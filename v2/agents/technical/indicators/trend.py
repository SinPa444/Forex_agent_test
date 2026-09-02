"""
agents/technical/indicators/trend.py
====================================
فاکتور Trend (13%) — EMA50/200 + تأیید Supertrend.
منطق امتیازدهی دقیقاً نسخه قبل:
  هم‌راستایی EMA با Supertrend → 0.8 | ناهم‌راستایی → 0.4 (ترند ضعیف‌تر)
  Ranging (EMAها/قیمت هم‌راستا نیستند) → 0.0 | داده کافی نیست → None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .utils import last_indicator_value

# --- پارامترهای ثابت (مقادیر نسخه قبل، نام‌گذاری‌شده) ---
EMA_FAST = 50
EMA_SLOW = 200
SUPERTREND_LENGTH = 10
SUPERTREND_MULTIPLIER = 3.0


@dataclass
class TrendResult:
    ema50: Optional[float] = None
    ema200: Optional[float] = None
    supertrend_dir: int = 0
    direction: int = 0            # 1 / -1 / 0
    status: Optional[str] = None
    score: Optional[float] = None


def compute_trend(df: pd.DataFrame, current_price: float) -> TrendResult:
    """محاسبه فاکتور روند بر پایه EMA50/200 + Supertrend."""
    result = TrendResult()
    result.ema50 = last_indicator_value(df.ta.ema(length=EMA_FAST))
    result.ema200 = last_indicator_value(df.ta.ema(length=EMA_SLOW))

    st_df = df.ta.supertrend(length=SUPERTREND_LENGTH, multiplier=SUPERTREND_MULTIPLIER)
    if st_df is not None and not st_df.empty:
        st_dir_col = [c for c in st_df.columns if c.startswith("SUPERTd")]
        if st_dir_col and pd.notna(st_df[st_dir_col[0]].iloc[-1]):
            result.supertrend_dir = int(st_df[st_dir_col[0]].iloc[-1])

    if result.ema50 and result.ema200:
        if result.ema50 > result.ema200 and current_price > result.ema50:
            result.direction = 1
        elif result.ema50 < result.ema200 and current_price < result.ema50:
            result.direction = -1
        else:
            result.direction = 0

        if result.direction == 0:
            result.status = "Ranging"
            result.score = 0.0
        elif result.supertrend_dir == result.direction:
            result.status = ("Uptrend" if result.direction == 1 else "Downtrend") + " (Supertrend confirmed)"
            result.score = 0.8
        else:
            result.status = ("Uptrend" if result.direction == 1 else "Downtrend") + " (Supertrend divergence)"
            result.score = 0.4 * result.direction

    return result
