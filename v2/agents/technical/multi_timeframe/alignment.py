"""
agents/technical/multi_timeframe/alignment.py
=============================================
الگوریتم‌های هم‌راستایی MTF (semantics نسخه قبل — بدون تغییر):

  compute_mtf_confluence — فاکتور MTF Confluence (10%) برای یک تایم‌فریم:
      HTF رانج می‌شود / داده کم → 0.0 (خنثی، penalty ندارد)
      base خنثی یا HTF خنثی       → 0.0
      هم‌جهت                      → +0.8 | برعکس → -0.8

  compute_htf_bias — جهت کلی از تایم‌فریم‌های بالاتر (D1/W1):
      هر دو ضعیف → 0 (Mixed) | تناقض → 0 (Mixed)
      وگرنه وزن‌دار (D1:0.6 / W1:0.4) با deadband ±0.3
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ..indicators.utils import last_indicator_value
from ..signals.technical_signal import DIRECTION_DEADBAND

# --- پارامترهای ثابت (مقادیر نسخه قبل، نام‌گذاری‌شده) ---
MIN_BARS = 50
EMA_FAST = 50
EMA_SLOW = 200
CONFLUENCE_MATCH = 0.8

# وزن HTFها در محاسبه‌ی جهت کلی (برای MTFMatrix)
HTF_WEIGHTS: dict[str, float] = {"D1": 0.6, "W1": 0.4}
HTF_BIAS_DEADBAND = 0.3


def _ema_direction(df: pd.DataFrame) -> int:
    """جهت EMA در یک دیتاست (1/-1/0) — الگوریتم مشترک base/HTF."""
    ema50 = last_indicator_value(df.ta.ema(length=EMA_FAST))
    ema200 = last_indicator_value(df.ta.ema(length=EMA_SLOW))
    price = float(df["close"].dropna().tolist()[-1])
    if ema200 is None:
        return 0
    if ema50 is not None and ema50 > ema200 and price > ema50:
        return 1
    if ema50 is not None and ema50 < ema200 and price < ema50:
        return -1
    return 0


def compute_mtf_confluence(
    base_trend_score: Optional[float],
    htf_data: Optional[pd.DataFrame],
) -> tuple[Optional[float], Optional[str]]:
    """
    فاکتور MTF Confluence (10%).
    برمی‌گرداند: (امتیاز | None، وضعیت HTF | None)
    """
    if htf_data is None or len(htf_data) < MIN_BARS:
        return None, None

    htf_df = htf_data.copy()
    htf_df.columns = [c.lower() for c in htf_df.columns]
    htf_dir = _ema_direction(htf_df)
    htf_status = {1: "Uptrend", -1: "Downtrend", 0: "Ranging"}[htf_dir]

    base_dir = 0
    if base_trend_score is not None:
        base_dir = 1 if base_trend_score > 0 else (-1 if base_trend_score < 0 else 0)

    if htf_dir == 0 or base_dir == 0:
        return 0.0, htf_status
    if htf_dir == base_dir:
        return CONFLUENCE_MATCH, htf_status
    return -CONFLUENCE_MATCH, htf_status


def compute_htf_bias(scores: dict) -> tuple[int, str]:
    """
    جهت کلی از تایم‌فریم‌های بالاتر (D1/W1).
    scores: {tf: score} — tfهایی که در HTF_WEIGHTS نیستند نادیده گرفته می‌شوند.
    برمی‌گرداند: (direction ∈ {1, -1, 0}, "HTF Bias: X" | "HTF Bias: Mixed")
    """
    weighted = sum(HTF_WEIGHTS.get(tf, 0.0) * (s or 0.0) for tf, s in scores.items())
    if weighted > HTF_BIAS_DEADBAND:
        return 1, "HTF Bias: Bullish"
    if weighted < -HTF_BIAS_DEADBAND:
        return -1, "HTF Bias: Bearish"
    return 0, "HTF Bias: Mixed"
