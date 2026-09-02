"""
agents/technical/structure/bos_choch.py
=======================================
فاکتور Structure (20%) — BOS + CHoCH.

بازساخت Phase 1:
  - **داده خام غنی‌تر**: آخرین BOS و آخرین CHoCH هرکدام با age (تعداد کندل
    تا لحظه فعلی) + sequence چهار رویداد آخر + تعداد swing.
  - **score_mode**:
      "bos_only"     (پیش‌فرض — دقیقاً semantics نسخه قبل: فقط آخرین BOS)
      "choch_aware"  (خاموش در Phase 1؛ روشن‌سازی فقط با backtest A/B)
                     → آخرین رویداد حاکم: CHoCH = ±0.5 (ضعیف‌تر از ادامه ساختار)
                       BOS = ±0.8

به یاد داشته باشید: در نسخه قبل ستون CHOCH کتابخانه محاسبه می‌شد ولی
نادیده گرفته می‌شد (weakness W1) — حالا هر دو رویداد ثبت می‌شوند و
semantics پیش‌فرض بدون تغییر حفظ شده است.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd
from smartmoneyconcepts import smc

logger = logging.getLogger(__name__)

# --- مقادیر امتیاز (semantics نسخه قبل) ---
BOS_SCORE = 0.8
CHOCH_SCORE = 0.5  # فقط در mode choch_aware

# حالت پیش‌فرض — تغییر آن بدون backtest ممنوع است
DEFAULT_SCORE_MODE = "bos_only"

# چند رویداد آخر در sequence خام
SEQUENCE_LENGTH = 4


@dataclass
class StructureResult:
    last_bos_dir: Optional[int] = None
    last_bos_age_bars: Optional[int] = None
    last_choch_dir: Optional[int] = None
    last_choch_age_bars: Optional[int] = None
    sequence: list[Dict[str, Any]] = field(default_factory=list)
    swing_count: Optional[int] = None
    score: Optional[float] = None
    score_mode: str = DEFAULT_SCORE_MODE

    def to_raw_dict(self) -> Dict[str, Any]:
        """نسخه خام برای snapshot (decision log)."""
        return {
            "score_mode": self.score_mode,
            "last_bos": (
                {"dir": self.last_bos_dir, "age_bars": self.last_bos_age_bars}
                if self.last_bos_dir is not None else None
            ),
            "last_choch": (
                {"dir": self.last_choch_dir, "age_bars": self.last_choch_age_bars}
                if self.last_choch_dir is not None else None
            ),
            "sequence": self.sequence,
            "swing_count": self.swing_count,
        }


def compute_structure(
    df: pd.DataFrame,
    swings: Optional[pd.DataFrame],
    score_mode: str = DEFAULT_SCORE_MODE,
) -> StructureResult:
    """
    محاسبه ساختار بازار (BOS/CHoCH) از روی swingها.
    امتیاز پیش‌فرض (bos_only) دقیقاً نسخه قبل: آخرین BOS → ±0.8 یا None.
    """
    result = StructureResult(score_mode=score_mode)
    if swings is None or swings.empty:
        result.swing_count = 0
        return result

    result.swing_count = int(len(swings))

    bos_df = smc.bos_choch(df, swings, close_break=True)
    if bos_df is None:
        bos_df = smc.bos_choch(df, swings)
    if bos_df is None or bos_df.empty or "BOS" not in bos_df.columns:
        return result

    n = len(df)

    def _last_event(events_df: pd.DataFrame, col: str) -> tuple[Optional[int], Optional[int]]:
        if events_df.empty:
            return None, None
        last = events_df.iloc[-1]
        try:
            pos = int(events_df.index[-1])
        except (TypeError, ValueError):
            pos = n - 1
        return int(last[col]), (n - 1) - pos

    bos_events = bos_df.dropna(subset=["BOS"])
    result.last_bos_dir, result.last_bos_age_bars = _last_event(bos_events, "BOS")

    choch_events = pd.DataFrame()
    if "CHOCH" in bos_df.columns:
        choch_events = bos_df.dropna(subset=["CHOCH"])
        result.last_choch_dir, result.last_choch_age_bars = _last_event(choch_events, "CHOCH")

    # --- sequence چهار رویداد آخر (BOS + CHoCH) به ترتیب زمان ---
    merged: list[tuple[int, str, int]] = []
    if not bos_events.empty:
        for idx, row in bos_events.tail(SEQUENCE_LENGTH).iterrows():
            try:
                merged.append((int(idx), "BOS", int(row["BOS"])))
            except (TypeError, ValueError):
                continue
    if not choch_events.empty:
        for idx, row in choch_events.tail(SEQUENCE_LENGTH).iterrows():
            try:
                merged.append((int(idx), "CHoCH", int(row["CHOCH"])))
            except (TypeError, ValueError):
                continue
    merged.sort(key=lambda t: t[0])
    for pos, kind, direction in merged[-SEQUENCE_LENGTH:]:
        result.sequence.append({"type": kind, "dir": direction, "age_bars": (n - 1) - pos})

    # --- امتیاز ---
    if score_mode == "choch_aware":
        # آخرین رویداد حاکم (CHoCH ضعیف‌تر از BOS)
        candidates: list[tuple[int, int, int]] = []
        if result.last_bos_dir is not None and result.last_bos_age_bars is not None:
            candidates.append((n - 1 - result.last_bos_age_bars, result.last_bos_dir, BOS_SCORE))
        if result.last_choch_dir is not None and result.last_choch_age_bars is not None:
            candidates.append((n - 1 - result.last_choch_age_bars, result.last_choch_dir, CHOCH_SCORE))
        if candidates:
            candidates.sort(key=lambda t: t[0])
            _, direction, magnitude = candidates[-1]
            result.score = magnitude * direction
    else:
        # bos_only — semantics نسخه قبل
        if result.last_bos_dir is not None:
            result.score = BOS_SCORE * result.last_bos_dir

    return result
