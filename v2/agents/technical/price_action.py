"""
agents/technical/price_action.py
================================
Price Action Engine.

مسئولیت:
  - شناسایی Swing High / Swing Low
  - برچسب HH / HL / LH / LL برای ساختار بازار
  - بازگرداندن توضیح قابل خواندن برای LLM/Risk

تأکید: این فایل فقط ساختار قیمتی را استخراج می‌کند؛ امتیازدهی در
`technical_analyzer` و کانفلوئنس انجام می‌شود.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

try:
    from smartmoneyconcepts import smc
except ImportError:  # pragma: no cover
    smc = None


@dataclass
class SwingPoint:
    """یک نقطه سویینگ (سقف یا کف)."""

    kind: str  # "high" | "low"
    price: float
    index: int


@dataclass
class SwingStructure:
    """ساختار سویینگ‌های اخیر + برچسب روند ساختاری."""

    last_swing_high: Optional[SwingPoint] = None
    last_swing_low: Optional[SwingPoint] = None
    prev_swing_high: Optional[SwingPoint] = None
    prev_swing_low: Optional[SwingPoint] = None
    labels: list[str] = field(default_factory=list)
    label: str = "UNCLEAR"
    description: str = ""
    valid: bool = False


def detect_swing_structure(
    df: pd.DataFrame,
    swing_length: int = 5,
) -> SwingStructure:
    """
    استخراج آخرین ۲ سقف و ۲ کف از `smc.swing_highs_lows`.

    خروجی برچسب‌ها:
      - HH: new high above previous high
      - HL: higher low
      - LH: lower high
      - LL: lower low
    """
    out = SwingStructure()
    if smc is None or df is None or df.empty:
        return out

    # لازم است ستون‌ها با حروف کوچک باشند
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        return out

    try:
        swings = smc.swing_highs_lows(df, swing_length=swing_length)
        if swings is None or swings.empty:
            return out

        # ستون HighLow: +1 = High، -1 = Low
        if "HighLow" not in swings.columns:
            return out
        highs = swings.dropna(subset=["HighLow"])

        rows = highs
        high_rows = rows[rows["HighLow"] == 1]
        low_rows = rows[rows["HighLow"] == -1]

        high_points = [
            SwingPoint(kind="high", price=float(r["High"]), index=int(r.name))
            for _, r in high_rows.iterrows()
        ]
        low_points = [
            SwingPoint(kind="low", price=float(r["Low"]), index=int(r.name))
            for _, r in low_rows.iterrows()
        ]

        if not high_points and not low_points:
            return out

        labels: list[str] = []

        # --- سقف‌ها ---
        if len(high_points) >= 2:
            prev_h, last_h = high_points[-2], high_points[-1]
            out.prev_swing_high = prev_h
            out.last_swing_high = last_h
            labels.append("HH" if last_h.price > prev_h.price else ("LH" if last_h.price < prev_h.price else "Equal-High"))
        elif high_points:
            out.last_swing_high = high_points[-1]

        # --- کف‌ها ---
        if len(low_points) >= 2:
            prev_l, last_l = low_points[-2], low_points[-1]
            out.prev_swing_low = prev_l
            out.last_swing_low = last_l
            labels.append("HL" if last_l.price > prev_l.price else ("LL" if last_l.price < prev_l.price else "Equal-Low"))
        elif low_points:
            out.last_swing_low = low_points[-1]

        # --- label کلی ---
        if len(high_points) >= 2 and len(low_points) >= 2:
            hh = high_points[-1].price > high_points[-2].price
            hl = low_points[-1].price > low_points[-2].price
            lh = high_points[-1].price < high_points[-2].price
            ll = low_points[-1].price < low_points[-2].price
            if hh and hl:
                out.label = "HH+HL"
            elif lh and ll:
                out.label = "LH+LL"
            elif hh and ll:
                out.label = "EXPANSION"
            elif lh and hl:
                out.label = "CONTRACTION"
            else:
                out.label = "UNCLEAR"
        elif labels:
            out.label = "+".join(labels)

        out.labels = labels
        out.description = "; ".join(
            [
                f"Last swing high: {out.last_swing_high.price}" if out.last_swing_high else "",
                f"Last swing low: {out.last_swing_low.price}" if out.last_swing_low else "",
                f"Labels: {labels}",
            ]
        ).strip("; ")
        out.valid = bool(high_points or low_points)
        return out
    except Exception as exc:  # pragma: no cover — defensive
        out.description = f"swing detection failed: {exc}"
        return out


def candlestick_summary(last_candle_type: Optional[str]) -> str:
    """اگر `TechnicalMetrics.last_candle_type` موجود باشد، همان را برمی‌گرداند."""
    if not last_candle_type:
        return "N/A"
    return last_candle_type
