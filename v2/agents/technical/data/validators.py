"""
agents/technical/data/validators.py
===================================
لایه اعتبارسنجی داده (بازساخت Phase 1).

validate_ohlc یک DataQualityReport برمی‌گرداند — **فقط گزارش است**؛
در Phase 1 هیچ تأثیری روی score ندارد (اصل: تغییر semantics فقط با backtest).
هدف: هر snapshot در decision log بداند دیتایی که موتور دیده چه کیفیتی داشته.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# فاصله‌ای بیش از این مضرب از میانگین فاصله کندل‌ها = «گپ» محتمل
GAP_MULTIPLIER = 3.0


class DataQualityReport(BaseModel):
    """گزارش کیفیت یک DataFrame OHLCV."""

    ok: bool = True
    bars: int = 0
    issues: list[str] = Field(default_factory=list)
    nan_rows: int = 0
    invalid_price_rows: int = 0
    ohlc_inconsistent_rows: int = 0
    duplicate_timestamps: int = 0
    gaps: int = 0
    zero_volume_ratio: Optional[float] = None


def validate_ohlc(df: Optional[pd.DataFrame], interval: Optional[str] = None) -> DataQualityReport:
    """
    بررسی‌ها (همه فقط report، بدون خطا):
      - خالی / NaN در OHLC
      - قیمت نامعتبر (<= 0)
      - ناسازگاری OHLC (low > min(open,close) یا high < max(open,close))
      - timestamp تکراری
      - گپ زمانی (delta > 3x میانگین)
      - نسبت volume صفر (برای نمادهایی که volume دارند)
    """
    report = DataQualityReport()
    if df is None or df.empty:
        report.ok = False
        report.issues.append("empty_dataframe")
        return report

    report.bars = len(df)
    cols = [c.lower() for c in df.columns]
    work = df.copy()
    work.columns = [c.lower() for c in df.columns]

    ohlc = work[["open", "high", "low", "close"]].dropna() if all(
        c in cols for c in ("open", "high", "low", "close")
    ) else None

    # --- NaN در OHLC (rows که هرکدام از ۴ ستون NaN است) ---
    if all(c in cols for c in ("open", "high", "low", "close")):
        report.nan_rows = int(work[["open", "high", "low", "close"]].isna().any(axis=1).sum())
        if report.nan_rows:
            report.issues.append(f"nan_rows={report.nan_rows}")
            report.ok = False

    if ohlc is not None and not ohlc.empty:
        # --- قیمت نامعتبر ---
        bad_price = (
            (ohlc < 0).any(axis=1)
            | (ohlc[["open", "close"]] == 0).any(axis=1)
        )
        report.invalid_price_rows = int(bad_price.sum())
        if report.invalid_price_rows:
            report.issues.append(f"invalid_price_rows={report.invalid_price_rows}")
            report.ok = False

        # --- ناسازگاری ساختاری OHLC ---
        # کندل معتبر: low <= min(open,close) و high >= max(open,close)
        body_low = ohlc[["open", "close"]].min(axis=1)
        body_high = ohlc[["open", "close"]].max(axis=1)
        inconsistent = (ohlc["low"] > body_low) | (ohlc["high"] < body_high)
        report.ohlc_inconsistent_rows = int(inconsistent.sum())
        if report.ohlc_inconsistent_rows:
            report.issues.append(f"ohlc_inconsistent_rows={report.ohlc_inconsistent_rows}")
            report.ok = False

    # --- timestamp تکراری ---
    if isinstance(work.index, pd.DatetimeIndex):
        report.duplicate_timestamps = int(work.index.duplicated().sum())
        if report.duplicate_timestamps:
            report.issues.append(f"duplicate_timestamps={report.duplicate_timestamps}")

        # --- گپ زمانی ---
        deltas = work.index.to_series().diff().dropna()
        if len(deltas) > 2:
            median_delta = deltas.median()
            if median_delta > pd.Timedelta(0):
                report.gaps = int((deltas > median_delta * GAP_MULTIPLIER).sum())
                if report.gaps:
                    report.issues.append(f"time_gaps={report.gaps}")

    # --- volume صفر (فقط اگر ستون volume وجود داشته و همه صفر نباشد) ---
    if "volume" in cols:
        vol = pd.to_numeric(work["volume"], errors="coerce")
        nonzero = int((vol > 0).sum())
        if nonzero == 0:
            report.zero_volume_ratio = 1.0
            report.issues.append("all_volume_zero")
        elif report.bars:
            report.zero_volume_ratio = round(float((vol <= 0).sum()) / report.bars, 4)

    return report
