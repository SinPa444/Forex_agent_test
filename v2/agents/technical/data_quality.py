"""
agents/technical/data_quality.py
================================
Data Quality Engine.

مسئولیت:
  - بررسی stale داده (آخرین بار نسبت به تاریخ مرجع)
  - بررسی NaN ratio
  - بررسی وجود/کیفیت حجم
  - تولید score 0..1 برای استفاده در TechnicalConfidence

جلوگیری از Lookahead:
  - فقط دادهٔ [0..t] به این ماژول داده می‌شود؛ `reference_date` می‌تواند
    تاریخ کندل جاری باشد (معمولا آخرین index DataFrame).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from core import technical_config as cfg


@dataclass
class DataQualityResult:
    score: float = 0.0            # 0..1
    staleness_days: Optional[int] = None
    staleness_status: str = "UNKNOWN"
    nan_ratio: float = 0.0
    volume_available: bool = False
    data_bars: int = 0
    warnings: list[str] = field(default_factory=list)


def compute_data_quality(
    df: pd.DataFrame,
    reference_date=None,
) -> DataQualityResult:
    """محاسبه کیفیت داده برای یک DataFrame قیمت."""
    res = DataQualityResult()
    if df is None or df.empty:
        res.score = 0.0
        res.warnings = ["empty dataframe"]
        return res

    res.data_bars = len(df)
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    # --- 1. NaN ratio ---
    mandatory = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if not mandatory:
        res.warnings = ["missing OHLC columns"]
        return res
    nan_ratio = float(df[mandatory].isna().sum().sum()) / (len(df) * len(mandatory))
    res.nan_ratio = round(nan_ratio, 4)
    if nan_ratio > 0.02:
        res.warnings.append(f"NaN ratio {nan_ratio:.1%}")

    # --- 2. Volume ---
    res.volume_available = "volume" in df.columns
    if not res.volume_available:
        res.warnings.append("volume column missing")

    # --- 3. Staleness ---
    idx = df.index
    date_like = None
    if isinstance(idx, pd.DatetimeIndex):
        date_like = idx[-1]
    elif "date" in df.columns and pd.notna(df["date"].iloc[-1]):
        date_like = pd.to_datetime(df["date"].iloc[-1])

    if date_like is not None:
        ref = reference_date if reference_date is not None else pd.Timestamp.now().normalize()
        ref = pd.to_datetime(ref).normalize()
        latest = pd.to_datetime(date_like).normalize()
        res.staleness_days = int((ref - latest).days)
        if res.staleness_days > cfg.DATA_QUALITY_STALE_DAYS_BAD:
            res.staleness_status = "STALE"
            res.warnings.append(f"data stale by {res.staleness_days} days")
        elif res.staleness_days > cfg.DATA_QUALITY_STALE_DAYS_WARN:
            res.staleness_status = "WARN"
            res.warnings.append(f"data {res.staleness_days} days old")
        elif res.staleness_days < 0:
            # داده از تاریخ مرجع جدیدتر است → احتمال lookahead؛ فوراً پرچم بزن.
            res.staleness_status = "FUTURE"
            res.warnings.append("data newer than reference date — possible lookahead")
        else:
            res.staleness_status = "FRESH"

    # --- 4. Score ---
    score = 1.0
    if len(df) < cfg.DATA_QUALITY_MIN_BARS:
        score -= 0.2
    score -= min(nan_ratio * 10.0, 0.3)
    if not res.volume_available:
        score -= 0.05
    if res.staleness_status == "STALE":
        score -= 0.4
    elif res.staleness_status == "FUTURE":
        score -= 0.4
    elif res.staleness_status == "WARN":
        score -= 0.15
    res.score = round(max(0.0, min(1.0, score)), 4)
    return res
