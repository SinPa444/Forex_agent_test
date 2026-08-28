"""
agents/technical/indicators.py
==============================
لایه ایزولهٔ محاسبه Indicatorها برای Technical Engine.

مسئولیت:
  - توابع کمکی deterministic برای نشانگرهایی که TechnicalSignal به آن‌ها
    نیاز دارد (به‌ویژه ATR/volatility).
  - بازگشت None به‌جای NaN و عدم شکستن pipeline در دادهٔ کم‌عمق.
  - رفتار سازگار با `calculate_technical_metrics` (pandas_ta_classic).

این فایل خودش امتیاز نمی‌دهد؛ فقط دادهٔ خام را محاسبه می‌کند.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

try:
    import pandas_ta_classic as pta  # noqa: F401 — ثبت df.ta
except ImportError:  # pragma: no cover — در محیط تولید باید نصب باشد
    pta = None


from core import technical_config as cfg

logger = logging.getLogger(__name__)


def _last(out) -> Optional[float]:
    """آخرین مقدار معتبر از خروجی pandas-ta (Series یا DataFrame)."""
    if out is None:
        return None
    if isinstance(out, pd.DataFrame):
        if out.empty:
            return None
        out = out.iloc[:, 0]
    val = out.iloc[-1]
    return float(val) if pd.notna(val) else None


def _lower_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    return df


def _manual_atr_series(df: pd.DataFrame, length: int) -> Optional[pd.Series]:
    """ATR Wilder بدون pandas_ta_classic (fallback deterministic)."""
    if df is None or len(df) < length + 1:
        return None
    df = _lower_cols(df)
    if not {"high", "low", "close"}.issubset(df.columns):
        return None
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1).dropna()
    if len(tr) < length:
        return None
    values = list(tr)
    first = float(sum(values[:length]) / length)
    out: list[Optional[float]] = [None] * (length - 1) + [first]
    for i in range(length, len(values)):
        out.append((out[-1] * (length - 1) + values[i]) / length)
    return pd.Series(out, index=tr.index, dtype=float)


def _atr_series(df: pd.DataFrame, length: int) -> Optional[pd.Series]:
    """ATR سری — از pandas_ta_classic، با fallback دستی.""" 
    if df is None:
        return None
    df_l = _lower_cols(df)
    if hasattr(df_l, "ta"):
        try:
            out = df_l.ta.atr(length=length)
            if out is not None:
                return out
        except Exception:  # pragma: no cover — defensive
            pass
    return _manual_atr_series(df_l, length)


def compute_atr(df: pd.DataFrame, length: int = cfg.ATR_PERIOD) -> Optional[float]:
    """ATR (True Range) به واحد قیمت."""
    if df is None or len(df) < length:
        return None
    try:
        return _last(_atr_series(df, length))
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("ATR calc failed: %s", exc)
        return None


def compute_atr_pct(
    df: pd.DataFrame,
    price: Optional[float] = None,
    length: int = cfg.ATR_PERIOD,
    median_window: int = cfg.ATR_MEDIAN_WINDOW,
) -> dict[str, Optional[float]]:
    """
    درصد ATR به قیمت + median ATR% بازه اخیر.

    خروجی:
      atr_pct: ATR(close) / close
      atr_median_pct: median در پنجرهٔ `median_window` کندل اخیر
      vol_z: z-score تقریبی (استاندارد بر اساس آستانهٔ پیکربندی نیست)
    """
    result: dict[str, Optional[float]] = {
        "atr": None,
        "atr_pct": None,
        "atr_median_pct": None,
        "vol_z": None,
    }
    if df is None or len(df) < length:
        return result

    df = _lower_cols(df)
    if "close" not in df.columns or "high" not in df.columns or "low" not in df.columns:
        return result

    try:
        atr_series = _atr_series(df, length)
        if atr_series is None:
            return result
        if isinstance(atr_series, pd.DataFrame):
            if atr_series.empty:
                return result
            atr_series = atr_series.iloc[:, 0]

        close = float(df["close"].iloc[-1])
        if close is None or pd.isna(close) or close <= 0:
            return result

        atr_last = float(atr_series.iloc[-1])
        if pd.isna(atr_last):
            return result

        atr_pct = atr_last / close
        # Median ATR% روی پنجرهٔ اخیر (بدون آینده)
        window = atr_series.tail(median_window).dropna()
        med = float(window.median()) if len(window) > 0 else None

        result["atr"] = round(atr_last, 8)
        result["atr_pct"] = round(atr_pct, 8)
        if med is not None and med > 0:
            result["atr_median_pct"] = round(med, 8)
            # ولتاژ نرمال‌سازی شده: الان نسبت به مدیان — بعداً با sensitivity calibrate می‌شود
            result["vol_z"] = round(atr_pct / med, 4)
        return result
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("ATR-pct calc failed: %s", exc)
        return result


def compute_ema(df: pd.DataFrame, length: int) -> Optional[float]:
    """EMA آخرین مقدار."""
    if df is None or len(df) < length:
        return None
    try:
        return _last(df.ta.ema(length=length))
    except Exception as exc:  # pragma: no cover
        logger.warning("EMA(%d) calc failed: %s", length, exc)
        return None


def compute_sma(df: pd.DataFrame, length: int) -> Optional[float]:
    """SMA آخرین مقدار."""
    if df is None or len(df) < length:
        return None
    try:
        return _last(df.ta.sma(length=length))
    except Exception as exc:  # pragma: no cover
        logger.warning("SMA(%d) calc failed: %s", length, exc)
        return None


def compute_rsi(df: pd.DataFrame, length: int = 14) -> Optional[float]:
    """RSI آخرین مقدار."""
    if df is None or len(df) < length:
        return None
    try:
        return _last(df.ta.rsi(length=length))
    except Exception as exc:  # pragma: no cover
        logger.warning("RSI(%d) calc failed: %s", length, exc)
        return None


def compute_macd_histogram(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    """سه‌جزئی MACD (line, signal, histogram) به‌صورت dict."""
    result = {"macd": None, "signal": None, "histogram": None}
    if df is None or len(df) < slow:
        return result
    try:
        macd_df = df.ta.macd(fast=fast, slow=slow, signal=signal)
        if macd_df is None or macd_df.empty:
            return result
        cols = list(macd_df.columns)
        for key, prefix in (("macd", "MACD_"), ("signal", "MACDs_"), ("histogram", "MACDh_")):
            matched = [c for c in cols if str(c).startswith(prefix)]
            if matched:
                val = macd_df[matched[0]].iloc[-1]
                if pd.notna(val):
                    result[key] = float(val)
        return result
    except Exception as exc:  # pragma: no cover
        logger.warning("MACD calc failed: %s", exc)
        return result
