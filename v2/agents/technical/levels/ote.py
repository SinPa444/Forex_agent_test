"""
agents/technical/levels/ote.py
==============================
فاکتور OTE Zone (10%) — Optimal Trade Entry (ریتراس 61.8–78.6%).

منطق امتیازدهی دقیقاً نسخه قبل:
  داخل OTE روی leg صعودی → +0.8 | روی leg نزولی → -0.8
  خارج OTE               → 0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from smartmoneyconcepts import smc

logger = logging.getLogger(__name__)

# --- پارامترهای ثابت (مقادیر نسخه قبل، نام‌گذاری‌شده) ---
OTE_MIN_PCT = 61.8
OTE_MAX_PCT = 78.6
OTE_SCORE = 0.8


@dataclass
class OTEResult:
    retracement: Optional[float] = None
    leg_dir: Optional[int] = None
    score: Optional[float] = None
    status: Optional[str] = None


def compute_ote(df: pd.DataFrame, swings: Optional[pd.DataFrame]) -> OTEResult:
    """تحلیل ریتراس فعلی نسبت به leg آخر (Fibonacci OTE)."""
    result = OTEResult()
    if swings is None or swings.empty:
        return result

    ret_df = smc.retracements(df, swings)
    if ret_df is None or ret_df.empty or "Direction" not in ret_df.columns:
        return result

    ret_valid = ret_df.dropna(subset=["Direction"])
    if ret_valid.empty:
        return result

    last_ret = ret_valid.iloc[-1]
    if pd.notna(last_ret.get("CurrentRetracement%")):
        result.retracement = float(last_ret["CurrentRetracement%"])
    result.leg_dir = int(last_ret["Direction"])

    if result.retracement is not None and OTE_MIN_PCT <= result.retracement <= OTE_MAX_PCT:
        if result.leg_dir == 1:
            result.score = OTE_SCORE
            result.status = f"In bullish OTE zone ({result.retracement:.1f}% retracement of up-leg)"
        else:
            result.score = -OTE_SCORE
            result.status = f"In bearish OTE zone ({result.retracement:.1f}% retracement of down-leg)"
    else:
        result.score = 0.0
        if result.retracement is not None:
            result.status = (
                f"Outside OTE ({result.retracement:.1f}% retracement, "
                f"leg={'bullish' if result.leg_dir == 1 else 'bearish'})"
            )
        else:
            result.status = "N/A"

    return result
