"""
agents/technical/indicators/patterns.py
=======================================
فاکتور Price Action (2%) — الگوهای کندل استاندارد (TA-Lib CDL).

۱۰ الگوی پرارزش؛ امتیاز: یک الگو ±0.5 | هر الگوی هم‌جهت اضافه +0.25 (سقف ±1.0).
فقط **آخرین کندل** بررسی می‌شود (همان نسخه قبل).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

# لیست الگوهای فعال (دقیقاً نسخه قبل)
CDL_PATTERNS = [
    "engulfing", "hammer", "shootingstar", "morningstar", "eveningstar",
    "piercing", "darkcloudcover", "3whitesoldiers", "3blackcrows", "harami",
]

SCORE_PER_EXTRA_PATTERN = 0.25
BASE_PATTERN_SCORE = 0.5


@dataclass
class PriceActionResult:
    bull_patterns: list[str] = field(default_factory=list)
    bear_patterns: list[str] = field(default_factory=list)
    net: int = 0
    score: Optional[float] = None
    label: Optional[str] = None


def compute_price_action(df: pd.DataFrame) -> PriceActionResult:
    """تشخیص الگوهای کندل آخرین بار و امتیازدهی خالص."""
    result = PriceActionResult()
    pats_df = df.ta.cdl_pattern(name=CDL_PATTERNS)
    if pats_df is None or pats_df.empty:
        return result

    last_row = pats_df.iloc[-1]
    bull_hits, bear_hits = [], []
    for col in pats_df.columns:
        val = last_row[col]
        if pd.notna(val) and val > 0:
            bull_hits.append(col.replace("CDL_", "").title())
        elif pd.notna(val) and val < 0:
            bear_hits.append(col.replace("CDL_", "").title())

    result.bull_patterns = bull_hits
    result.bear_patterns = bear_hits
    result.net = len(bull_hits) - len(bear_hits)

    if result.net > 0:
        result.score = min(1.0, BASE_PATTERN_SCORE + SCORE_PER_EXTRA_PATTERN * (result.net - 1))
        result.label = "Bullish: " + ", ".join(bull_hits)
    elif result.net < 0:
        result.score = -min(1.0, BASE_PATTERN_SCORE + SCORE_PER_EXTRA_PATTERN * (abs(result.net) - 1))
        result.label = "Bearish: " + ", ".join(bear_hits)
    else:
        result.score = 0.0
        result.label = "Neutral"

    return result
