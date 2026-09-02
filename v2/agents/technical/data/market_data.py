"""
agents/technical/data/market_data.py
====================================
لایه داده بازار (بازساخت Phase 1 — منطق دقیقاً همان نسخه قبل).

- fetch_price_history: دانلود yfinance + ری‌سیمپل 2h/4h از 1h
- FetchProvenance: ردپای هر fetch (منبع، پریود، تعداد کندل، re-sample، زمان)
  ← الگوی feed-provenance از AgenticTrading
- period_for_interval: نگاشت اینترول → پریود دانلود (تک منبع برای fetch و provenance)

نکته monkeypatch: موتور (engine.py) و backtest (walkforward.py) این تابع را
از طریق attribute ماژول صدا می‌زنند تا جایگزینی در بک‌تست امکان‌پذیر باشد.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# منبع داده (HistoricalFeeder در بک‌تست آن را "historical_feeder" می‌کند)
DATA_SOURCE: str = "yfinance"

# نگاشت تایم‌فریم → اینترول yfinance
TIMEFRAME_MAP = {
    "M15": "15m",
    "M30": "30m",
    "H1": "60m",
    "H2": "2h",    # resample از 1h
    "H4": "4h",    # resample از 1h
    "D1": "1d",
    "W1": "1wk",
}

# اینترول‌هایی که باید از 1h ری‌سیمپل شوند
_RESAMPLE_FROM_1H = {"2h": "2h", "4h": "4h"}

# تایم‌فریم بالاتر (HTF) برای مؤلفه MTF Confluence
HTF_MAP = {
    "M15": "60m",
    "M30": "60m",
    "H1": "4h",
    "H2": "4h",
    "H4": "1d",
    "D1": "1wk",
    "W1": "1wk",
}


def period_for_interval(interval: str) -> str:
    """پریود دانلود yfinance بر اساس اینترول (همان منطق نسخه قبل)."""
    if interval in _RESAMPLE_FROM_1H:
        return "3mo"  # برای ری‌سیمپل
    if interval in ("60m", "1h"):
        return "3mo"  # 1mo برای H1 کمی بود؛ EMA200 روی 1h به ~۲۰۰ ساعت نیاز دارد
    if interval == "30m":
        return "1mo"
    if interval == "15m":
        return "5d"
    if interval == "1wk":
        return "5y"  # برای EMA200 روی HTF هفتگی کندل کافی لازم است
    return "1y"


class FetchProvenance(BaseModel):
    """ردپای یک fetch داده (برای decision log و run manifest)."""

    source: str = DATA_SOURCE
    interval: str
    period: Optional[str] = None
    bars: Optional[int] = None
    resampled_from: Optional[str] = None
    fetched_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def resample_from_1h(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
    """
    ری‌سیمپل استاندارد OHLCV از 1h به اینترول هدف (2h/4h).
    تجمیع: open=first, high=max, low=min, close=last, volume=sum.
    خروجی: ستون‌های Title Case (Open/High/Low/Close[/Volume]) با DatetimeIndex.
    """
    if df is None or df.empty:
        return None
    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        work.columns = work.columns.get_level_values(0)
    work.columns = [c.lower() for c in work.columns]
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        **({"volume": "sum"} if "volume" in work.columns else {}),
    }
    resampled = work.resample(rule).agg(agg).dropna(subset=["close"])
    if resampled.empty:
        return None
    resampled.columns = [c.capitalize() for c in resampled.columns]
    return resampled


def fetch_price_history(ticker: str, interval: str = "1d") -> Optional[pd.DataFrame]:
    """
    دانلود دیتای قیمت. Phase 7: اینترول‌های 2h/4h که yfinance ندارد
    از دیتای 1h ری‌سیمپل می‌شوند (OHLC استاندارد).

    باگ قبلی: «4h» مستقیم به yfinance پاس می‌شد که اینترول نامعتبر است
    و دانلود empty برمی‌گشت — یعنی H4 عملاً همیشه بدون دیتا بود.
    """
    # --- مسیر ری‌سیمپل: 2h/4h از 1h ---
    if interval in _RESAMPLE_FROM_1H:
        try:
            data = yf.download(ticker, period="3mo", interval="1h",
                               progress=False, auto_adjust=True)
            if data is None or data.empty:
                return None
            resampled = resample_from_1h(data, _RESAMPLE_FROM_1H[interval])
            return resampled
        except Exception as e:
            logger.error(f"Failed to resample {interval} from 1h for {ticker}: {e}")
            return None

    period = period_for_interval(interval)
    try:
        data = yf.download(ticker, period=period, interval=interval,
                           progress=False, auto_adjust=True)
        if data is None or data.empty:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data
    except Exception as e:
        logger.error(f"Failed to download price history for {ticker}: {e}")
        return None
