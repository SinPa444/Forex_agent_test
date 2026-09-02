# backtest/walkforward.py
"""
backtest/walkforward.py
=======================
Walk-forward scorer: امتیاز ۱۰ فاکتوری را برای هر کندل تاریخی محاسبه می‌کند.

نکته کلیدی طراحی: به جای بازنویسی منطق اسکورینگ (ریسک انحراف از پروداکشن)،
دقیقاً همان تابع پروداکشن `calculate_technical_metrics` صدا زده می‌شود و
فقط لایه fetch داده با یک feeder تاریخی جایگزین می‌شود (monkeypatch موقت).
یعنی هر تغییری که آینده در موتور اسکور بدهی، خودکار وارد بک‌تست هم می‌شود.

جلوگیری از lookahead: در کندل t فقط داده‌های [0..t] به اسکورر داده می‌شود.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from agents.technical import technical_analyzer as ta_mod
from agents.technical.data import market_data

from . import config

logger = logging.getLogger(__name__)


# ===========================================================================
# Historical Data Feeder (monkeypatch target)
# ===========================================================================

class HistoricalFeeder:
    """
    جایگزین موقت `market_data.fetch_price_history` (لایه داده موتور).

    برای هر (ticker, interval) یک DataFrame کامل از قبل لود شده نگه می‌دارد
    و بر اساس cursor فعلی، فقط slice گذشته را برمی‌گرداند.
    """

    def __init__(self) -> None:
        self._data: dict[str, pd.DataFrame] = {}   # interval -> full df
        self._cursors: dict[str, int] = {}          # interval -> last allowed iloc

    def load(self, interval: str, df: pd.DataFrame) -> None:
        self._data[interval] = df
        self._cursors[interval] = len(df) - 1

    def set_base_cursor(self, base_interval: str, idx: int) -> None:
        """cursor تایم‌فریم پایه را ست می‌کند و HTF را بر اساس timestamp هم‌تراز می‌کند."""
        self._cursors[base_interval] = idx
        base_ts = self._data[base_interval].index[idx]

        for interval, df in self._data.items():
            if interval == base_interval:
                continue
            # آخرین کندل HTF که timestamp اش <= timestamp فعلی پایه است
            pos = int(df.index.searchsorted(base_ts, side="right")) - 1
            self._cursors[interval] = pos

    def fetch(self, ticker: str, interval: str = "1d") -> Optional[pd.DataFrame]:
        df = self._data.get(interval)
        if df is None:
            return None
        cursor = self._cursors.get(interval, len(df) - 1)
        if cursor < 0:
            return None
        return df.iloc[: cursor + 1]


def _normalize_yf(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def preload_history(
    ticker: str,
    timeframe: str,
    years: int = 5,
) -> Optional[HistoricalFeeder]:
    """
    دانلود یک‌باره داده پایه + HTF و ساخت feeder.

    D1 → تا years سال (1d) + 10y هفتگی برای HTF
    H4/H1/M15 → محدودیت yfinance (60m حدود ۲ سال، 15m حدود ۶۰ روز)

    Phase 1 (W3/W4): اینترول‌های 4h/2h در yfinance وجود ندارند —
    دیتای 1h دانلود و با همان `resample_from_1h` پروداکشن ری‌سیمپل می‌شود
    (حذف انحراف «بک‌تست H4 دیتا نداشت» که باعث خرابی preload می‌شد).
    """
    base_interval = ta_mod.TIMEFRAME_MAP.get(timeframe, "1d")
    htf_interval = ta_mod.HTF_MAP.get(timeframe)

    period_map = {"1d": f"{years}y", "4h": "700d", "60m": "700d", "15m": "55d"}
    base_period = period_map.get(base_interval, f"{years}y")

    raw_1h: Optional[pd.DataFrame] = None
    try:
        if base_interval in market_data._RESAMPLE_FROM_1H:
            # 4h/2h: دانلود 1h + ری‌سیمپل (همان تابع پروداکشن)
            raw_1h = _normalize_yf(
                yf.download(ticker, period=base_period, interval="1h",
                            progress=False, auto_adjust=True)
            )
            base_df = (
                market_data.resample_from_1h(raw_1h, base_interval)
                if raw_1h is not None else None
            )
        else:
            base_df = _normalize_yf(
                yf.download(ticker, period=base_period, interval=base_interval,
                            progress=False, auto_adjust=True)
            )
    except Exception as exc:
        logger.error("دانلود داده پایه %s (%s) شکست خورد: %s", ticker, base_interval, exc)
        return None

    if base_df is None or len(base_df) < config.WARMUP_BARS + 10:
        logger.error("داده کافی برای %s نیست (%s bars)", ticker, len(base_df) if base_df is not None else 0)
        return None

    feeder = HistoricalFeeder()
    feeder.load(base_interval, base_df)

    if htf_interval:
        try:
            if htf_interval in market_data._RESAMPLE_FROM_1H:
                # HTF خود 4h/2h است (مثلاً HTFِ H1 = 4h):
                # اگر دیتای 1h پایه همین بازه را پوشش می‌دهد، دوباره دانلود نمی‌کنیم
                if raw_1h is not None:
                    htf_df = market_data.resample_from_1h(raw_1h, htf_interval)
                else:
                    raw_htf = _normalize_yf(
                        yf.download(ticker, period=base_period, interval="1h",
                                    progress=False, auto_adjust=True)
                    )
                    htf_df = (
                        market_data.resample_from_1h(raw_htf, htf_interval)
                        if raw_htf is not None else None
                    )
            else:
                htf_period = "10y" if htf_interval == "1wk" else f"{years}y"
                htf_df = _normalize_yf(
                    yf.download(ticker, period=htf_period, interval=htf_interval,
                                progress=False, auto_adjust=True)
                )
            if htf_df is not None and not htf_df.empty:
                feeder.load(htf_interval, htf_df)
        except Exception as exc:
            logger.warning("دانلود HTF %s (%s) شکست خورد: %s — MTF factor غیرفعال می‌شود",
                           ticker, htf_interval, exc)

    return feeder


# ===========================================================================
# Walk-forward scoring
# ===========================================================================

def compute_score_series(
    ticker: str,
    timeframe: str = "D1",
    years: int = 5,
    feeder: Optional[HistoricalFeeder] = None,
    progress_every: int = 100,
) -> Optional[pd.DataFrame]:
    """
    محاسبه walk-forward امتیاز تکنیکال برای هر کندل.

    Returns:
        DataFrame با index تاریخ و ستون‌های:
        open/high/low/close, score, confidence
        یا None در صورت شکست
    """
    if feeder is None:
        feeder = preload_history(ticker, timeframe, years=years)
        if feeder is None:
            return None

    base_interval = ta_mod.TIMEFRAME_MAP.get(timeframe, "1d")
    base_df = feeder._data[base_interval]
    n = len(base_df)

    # Phase 1: لایه داده موتور در agents.technical.data.market_data است —
    # monkeypatch دقیقاً روی attribute همان ماژول (نسخه قبل از technical_analyzer
    # پچ می‌زد که بعد از بازساخت دیگر مسیر فعال نبود).
    original_fetch = market_data.fetch_price_history
    original_source = market_data.DATA_SOURCE
    market_data.fetch_price_history = feeder.fetch
    market_data.DATA_SOURCE = "historical_feeder"  # provenance در decision log درست شود

    scores: list[float] = []
    confidences: list[float] = []
    idx_used: list = []

    try:
        for i in range(config.WARMUP_BARS, n):
            feeder.set_base_cursor(base_interval, i)
            try:
                metrics = ta_mod.calculate_technical_metrics(ticker, timeframe=timeframe)
            except Exception as exc:
                logger.debug("scoring failed at bar %d: %s", i, exc)
                metrics = None

            if metrics is not None and metrics.technical_score is not None:
                scores.append(float(metrics.technical_score))
                confidences.append(float(metrics.technical_confidence or 0.0))
            else:
                scores.append(np.nan)
                confidences.append(np.nan)
            idx_used.append(base_df.index[i])

            if progress_every and (i - config.WARMUP_BARS) % progress_every == 0:
                logger.info("[walkforward] %s bar %d/%d", ticker, i, n)
    finally:
        market_data.fetch_price_history = original_fetch
        market_data.DATA_SOURCE = original_source

    result = base_df.iloc[config.WARMUP_BARS:].copy()
    result.columns = [c.lower() for c in result.columns]
    result = result.reindex(idx_used)
    result["score"] = scores
    result["confidence"] = confidences

    valid = result["score"].notna().mean()
    logger.info("[walkforward] %s done: %d bars, %.0f%% valid scores",
                ticker, len(result), valid * 100)
    return result
