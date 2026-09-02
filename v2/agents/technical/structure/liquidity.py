"""
agents/technical/structure/liquidity.py
=======================================
فاکتور Liquidity Sweep (12%) — اسویپ سطوح نقدینگی اخیر.

منطق امتیازدهی دقیقاً نسخه قبل:
  sweep زیر کف‌ها (sell-side)  → شکار استاپ صعودی:  +0.8 (با برگشت) / +0.4
  sweep بالای سقف‌ها (buy-side) → شکار استاپ نزولی:  -0.8 / -0.4
  بدون اسویپ اخیر (در ~۵ کندل آخر) → 0.0

ستون‌های smc.liquidity: Liquidity (سمت ±1), Level (قیمت سطح), End, Swept (ایندکس اسویپ).
»recovered« از رابطه قیمت فعلی با سطح محاسبه می‌شود (نسخه قبل).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from smartmoneyconcepts import smc

logger = logging.getLogger(__name__)

# --- پارامترهای ثابت (مقادیر نسخه قبل، نام‌گذاری‌شده) ---
SWEEP_WINDOW = 6      # «~۵ کندل آخر»
RECOVERED_SCORE = 0.8
UNRECOVERED_SCORE = 0.4


@dataclass
class LiquidityResult:
    score: Optional[float] = None
    status: Optional[str] = None
    level: Optional[float] = None
    side: Optional[int] = None
    recovered: Optional[bool] = None


def compute_liquidity(
    df: pd.DataFrame, swings: Optional[pd.DataFrame], current_price: float
) -> LiquidityResult:
    """تشخیص اسویپ نقدینگی اخیر و امتیازدهی ±0.4/0.8."""
    result = LiquidityResult()
    if swings is None or swings.empty:
        return result

    liq_df = smc.liquidity(df, swings)
    if liq_df is None or liq_df.empty or "Liquidity" not in liq_df.columns:
        return result

    liq_valid = liq_df.dropna(subset=["Liquidity"])
    swept = liq_valid[liq_valid["Swept"].notna()]
    recent_swept = swept[swept["Swept"] >= len(df) - SWEEP_WINDOW]
    if recent_swept.empty:
        result.score = 0.0
        result.status = "No recent sweep"
        return result

    last_sweep = recent_swept.loc[recent_swept["Swept"].idxmax()]
    level = float(last_sweep["Level"])
    side = int(last_sweep["Liquidity"])
    recovered = (current_price > level) if side == -1 else (current_price < level)

    result.level = level
    result.side = side
    result.recovered = recovered

    sweep_score = RECOVERED_SCORE if recovered else UNRECOVERED_SCORE

    if side == -1:
        result.score = sweep_score
        result.status = (
            f"Sell-side liquidity swept at {level:.5f} (bullish stop hunt"
            f"{', price recovered' if recovered else ', no recovery yet'})"
        )
    else:
        result.score = -sweep_score
        result.status = (
            f"Buy-side liquidity swept at {level:.5f} (bearish stop hunt"
            f"{', price recovered' if recovered else ', no recovery yet'})"
        )

    return result
