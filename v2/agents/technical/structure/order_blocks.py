"""
agents/technical/structure/order_blocks.py
==========================================
بخش Order Block از فاکتور SMC Location (15%).

منطق امتیازدهی دقیقاً نسخه قبل:
  OB بولیش فعال (زیر قیمت) → +0.8 | OB بیرش فعال (بالای قیمت) → -0.8
  اولویت: OBهای غیر-میتیه؛ اگر نبود، میتیه‌شده‌ها.

Phase 1: فاصله٪ تا لبه‌ی بلوک به‌عنوان داده خام (level_distances).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from smartmoneyconcepts import smc

logger = logging.getLogger(__name__)

OB_SCORE = 0.8


@dataclass
class OrderBlockResult:
    bull_top: Optional[float] = None
    bear_bottom: Optional[float] = None
    bull_unmitigated: bool = False
    bear_unmitigated: bool = False
    bull_distance_pct: Optional[float] = None
    bear_distance_pct: Optional[float] = None
    contribution: float = 0.0


def compute_order_blocks(
    df: pd.DataFrame, swings: pd.DataFrame, current_price: float
) -> OrderBlockResult:
    """شناسایی Order Block فعال (بالای/زیر قیمت) و امتیازدهی ±0.8."""
    result = OrderBlockResult()
    # همان API نسخه قبل: smc.ob با close_mitigation=False
    ob_df = smc.ob(df, swings, close_mitigation=False)
    if ob_df is None:
        ob_df = smc.ob(df, swings)
    if ob_df is None or ob_df.empty:
        return result

    ob_valid = ob_df.dropna(subset=["OB"])

    # --- Bullish OB زیر قیمت (demand) ---
    bull_unmit = ob_valid[
        (ob_valid["OB"] == 1) & (ob_valid["MitigatedIndex"].isna())
        & (ob_valid["Top"] < current_price)
    ]
    bull_mit = ob_valid[(ob_valid["OB"] == 1) & (ob_valid["Top"] < current_price)]
    active_bull = bull_unmit if not bull_unmit.empty else bull_mit
    if not active_bull.empty:
        b = active_bull.iloc[-1]
        result.bull_top = float(b["Top"])
        result.bull_unmitigated = not bull_unmit.empty
        result.contribution += OB_SCORE
        if current_price > 0:
            result.bull_distance_pct = (current_price - result.bull_top) / current_price * 100.0

    # --- Bearish OB بالای قیمت (supply) ---
    bear_unmit = ob_valid[
        (ob_valid["OB"] == -1) & (ob_valid["MitigatedIndex"].isna())
        & (ob_valid["Bottom"] > current_price)
    ]
    bear_mit = ob_valid[(ob_valid["OB"] == -1) & (ob_valid["Bottom"] > current_price)]
    active_bear = bear_unmit if not bear_unmit.empty else bear_mit
    if not active_bear.empty:
        b = active_bear.iloc[-1]
        result.bear_bottom = float(b["Bottom"])
        result.bear_unmitigated = not bear_unmit.empty
        result.contribution -= OB_SCORE
        if current_price > 0:
            result.bear_distance_pct = (result.bear_bottom - current_price) / current_price * 100.0

    return result
