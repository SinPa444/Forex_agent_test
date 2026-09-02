"""
agents/technical/structure/fvg.py
=================================
بخش FVG از فاکتور SMC Location (15%).

منطق امتیازدهی دقیقاً نسخه قبل:
  FVG بولیش فعال (زیر قیمت) → +0.5 | FVG بیرش فعال (بالای قیمت) → -0.5
  اولویت: FVGهای غیر-میتیه؛ اگر نبود، میتیه‌شده‌ها.

Phase 1: فاصله٪ تا لبه‌ی زون به‌عنوان داده خام (level_distances) —
بدون تأثیر روی score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from smartmoneyconcepts import smc

logger = logging.getLogger(__name__)

FVG_SCORE = 0.5


@dataclass
class FVGResult:
    bull_low: Optional[float] = None
    bull_high: Optional[float] = None
    bear_low: Optional[float] = None
    bear_high: Optional[float] = None
    bull_unmitigated: bool = False
    bear_unmitigated: bool = False
    bull_distance_pct: Optional[float] = None
    bear_distance_pct: Optional[float] = None
    contribution: float = 0.0


def compute_fvg(df: pd.DataFrame, current_price: float) -> FVGResult:
    """شناسایی FVG فعال (بالای/زیر قیمت) و امتیازدهی ±0.5."""
    result = FVGResult()
    fvg_df = smc.fvg(df, join_consecutive=False)
    if fvg_df is None:
        fvg_df = smc.fvg(df)
    if fvg_df is None or fvg_df.empty:
        return result

    fvg_valid = fvg_df.dropna(subset=["FVG"])

    # --- Bullish FVG زیر قیمت (demand) ---
    bull_unmit = fvg_valid[
        (fvg_valid["FVG"] == 1) & (fvg_valid["MitigatedIndex"].isna())
        & (fvg_valid["Top"] < current_price)
    ]
    bull_mit = fvg_valid[(fvg_valid["FVG"] == 1) & (fvg_valid["Top"] < current_price)]
    active_bull = bull_unmit if not bull_unmit.empty else bull_mit
    if not active_bull.empty:
        b = active_bull.iloc[-1]
        result.bull_low = float(b["Bottom"])
        result.bull_high = float(b["Top"])
        result.bull_unmitigated = not bull_unmit.empty
        result.contribution += FVG_SCORE
        if current_price > 0:
            result.bull_distance_pct = (current_price - result.bull_high) / current_price * 100.0

    # --- Bearish FVG بالای قیمت (supply) ---
    bear_unmit = fvg_valid[
        (fvg_valid["FVG"] == -1) & (fvg_valid["MitigatedIndex"].isna())
        & (fvg_valid["Bottom"] > current_price)
    ]
    bear_mit = fvg_valid[(fvg_valid["FVG"] == -1) & (fvg_valid["Bottom"] > current_price)]
    active_bear = bear_unmit if not bear_unmit.empty else bear_mit
    if not active_bear.empty:
        b = active_bear.iloc[-1]
        result.bear_low = float(b["Bottom"])
        result.bear_high = float(b["Top"])
        result.bear_unmitigated = not bear_unmit.empty
        result.contribution -= FVG_SCORE
        if current_price > 0:
            result.bear_distance_pct = (result.bear_low - current_price) / current_price * 100.0

    return result
