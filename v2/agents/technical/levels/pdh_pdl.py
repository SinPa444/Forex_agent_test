"""
agents/technical/levels/pdh_pdl.py
==================================
فاکتور PDH/PDL (8%) — سطوح روز قبل.

منطق امتیازدهی دقیقاً نسخه قبل (elif زنجیره‌ای):
  شکست اخیر PDH (و PDL نشکسته)  → +0.6 «Broke above Previous Day High»
  شکست اخیر PDL (و PDH نشکسته)  → -0.6 «Broke below Previous Day Low»
  قیمت روی PDL (±0.1% از قیمت)   → +0.4 «At Previous Day Low support»
  قیمت روی PDH (±0.1% از قیمت)   → -0.4 «At Previous Day High resistance»
  وگرنه                          → 0.0  «Inside previous day range»

ستون‌های smc.previous_high_low: PreviousHigh, PreviousLow, BrokenHigh, BrokenLow.
»شکست اخیر« = پرچم BrokenHigh/BrokenLow در ۳ ردیف آخر خروجی کتابخانه.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from smartmoneyconcepts import smc

logger = logging.getLogger(__name__)

# --- پارامترهای ثابت (مقادیر نسخه قبل، نام‌گذاری‌شده) ---
PDH_PDL_TOL_PCT = 0.001   # ~۰.۱٪ فاصله از سطح (مطلق، روی قیمت فعلی)
BREAK_LOOKBACK = 3
BREAK_SCORE = 0.6
SIT_SCORE = 0.4
TIMEFRAME_DAILY = "1D"


@dataclass
class PDHPDLResult:
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    score: Optional[float] = None
    status: Optional[str] = None


def compute_pdh_pdl(df_daily: pd.DataFrame, current_price: float) -> PDHPDLResult:
    """
    شناسایی PDH/PDL.
    df_daily: دیتای خام (DatetimeIndex) — برای H1/15m دیتای روزانه جدا fetch
    می‌شود، برای D1/W1 خودِ دیتا روزانه است.
    """
    result = PDHPDLResult()
    phl_df = smc.previous_high_low(df_daily, time_frame=TIMEFRAME_DAILY)
    if phl_df is None or phl_df.empty:
        return result

    last_phl = phl_df.iloc[-1]
    if pd.notna(last_phl["PreviousHigh"]):
        result.pdh = float(last_phl["PreviousHigh"])
    if pd.notna(last_phl["PreviousLow"]):
        result.pdl = float(last_phl["PreviousLow"])

    recent_break_high = bool(phl_df["BrokenHigh"].tail(BREAK_LOOKBACK).fillna(0).sum() > 0)
    recent_break_low = bool(phl_df["BrokenLow"].tail(BREAK_LOOKBACK).fillna(0).sum() > 0)
    tol = current_price * PDH_PDL_TOL_PCT

    if recent_break_high and not recent_break_low:
        result.score = BREAK_SCORE
        result.status = f"Broke above Previous Day High ({result.pdh})"
    elif recent_break_low and not recent_break_high:
        result.score = -BREAK_SCORE
        result.status = f"Broke below Previous Day Low ({result.pdl})"
    elif result.pdl is not None and abs(current_price - result.pdl) <= tol:
        result.score = SIT_SCORE
        result.status = f"At Previous Day Low support ({result.pdl})"
    elif result.pdh is not None and abs(current_price - result.pdh) <= tol:
        result.score = -SIT_SCORE
        result.status = f"At Previous Day High resistance ({result.pdh})"
    else:
        result.score = 0.0
        result.status = "Inside previous day range"

    return result
