"""
tests/technical/test_data_layer.py
==================================
تست‌های لایه داده — validator، circuit breaker، resample، provenance.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest


# ===========================================================================
# DataQualityReport
# ===========================================================================

class TestValidateOhlc:
    def _clean(self, n=100) -> pd.DataFrame:
        idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
        close = np.full(n, 1.1)
        return pd.DataFrame(
            {"open": close, "high": close + 0.001, "low": close - 0.001,
             "close": close, "volume": np.full(n, 100.0)},
            index=idx,
        )

    def test_clean_data_ok(self):
        from agents.technical.data.validators import validate_ohlc
        r = validate_ohlc(self._clean())
        assert r.ok is True
        assert r.bars == 100
        assert r.issues == []

    def test_empty(self):
        from agents.technical.data.validators import validate_ohlc
        r = validate_ohlc(None)
        assert r.ok is False
        assert "empty_dataframe" in r.issues

    def test_nan_rows_flagged(self):
        from agents.technical.data.validators import validate_ohlc
        df = self._clean()
        df.loc[df.index[5], "close"] = np.nan
        r = validate_ohlc(df)
        assert r.ok is False
        assert r.nan_rows == 1

    def test_invalid_price_flagged(self):
        from agents.technical.data.validators import validate_ohlc
        df = self._clean()
        df.loc[df.index[5], "close"] = 0.0
        r = validate_ohlc(df)
        assert r.ok is False
        assert r.invalid_price_rows == 1

    def test_ohlc_inconsistency_flagged(self):
        from agents.technical.data.validators import validate_ohlc
        df = self._clean()
        # low بالای body → کندل ناممکن (ناسازگار)
        df.loc[df.index[5], "low"] = df.loc[df.index[5], "close"] + 0.005
        r = validate_ohlc(df)
        assert r.ohlc_inconsistent_rows == 1

    def test_duplicate_timestamps_flagged(self):
        from agents.technical.data.validators import validate_ohlc
        df = self._clean(50)
        dup = pd.concat([df, df.iloc[:3]])
        r = validate_ohlc(dup)
        assert r.duplicate_timestamps == 3

    def test_time_gaps_flagged(self):
        from agents.technical.data.validators import validate_ohlc
        df = self._clean(100)
        # یک گپ ۲۴ ساعته میانی
        idx = list(df.index)
        idx[50] = idx[50] + pd.Timedelta(hours=24)
        df.index = idx
        r = validate_ohlc(df)
        assert r.gaps >= 1
        assert any("time_gaps" in i for i in r.issues)

    def test_all_zero_volume_flagged(self):
        from agents.technical.data.validators import validate_ohlc
        df = self._clean()
        df["volume"] = 0.0
        r = validate_ohlc(df)
        assert r.zero_volume_ratio == 1.0


# ===========================================================================
# CircuitBreaker
# ===========================================================================

class TestCircuitBreaker:
    def test_closes_after_failures(self):
        from agents.technical.data.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        assert cb.allow() is True
        cb.record_failure()
        cb.record_failure()
        assert cb.allow() is True
        cb.record_failure()
        assert cb.allow() is False  # OPEN

    def test_success_resets(self):
        import time
        from agents.technical.data.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        cb.record_failure()
        assert cb.allow() is True  # هنوز CLOSED (یک شکست بعد از reset)

    def test_half_open_after_recovery(self):
        from agents.technical.data.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        assert cb.allow() is False
        time.sleep(0.06)
        assert cb.allow() is True  # HALF_OPEN → probe مجاز

    def test_half_open_failure_reopens(self):
        from agents.technical.data.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.06)
        assert cb.allow() is True
        cb.record_failure()
        assert cb.state.value == "open"


# ===========================================================================
# Resample (2h/4h از 1h) — همان تابع پروداکشن
# ===========================================================================

class TestResampleFrom1h:
    def _hourly(self, n=48) -> pd.DataFrame:
        idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
        close = np.linspace(1.0, 1.1, n)
        return pd.DataFrame(
            {"Open": close, "High": close + 0.001, "Low": close - 0.001,
             "Close": close, "Volume": np.full(n, 10.0)},
            index=idx,
        )

    def test_4h_aggregation(self):
        from agents.technical.data.market_data import resample_from_1h
        out = resample_from_1h(self._hourly(), "4h")
        assert out is not None
        assert len(out) == 12  # 48h / 4
        # اول کندل 4h: open=اولین 1h, close=آخرین 1h
        assert out.iloc[0]["Open"] == self._hourly().iloc[0]["Open"]
        assert out.iloc[0]["Close"] == self._hourly().iloc[3]["Close"]
        assert out.iloc[0]["High"] == self._hourly().iloc[:4]["High"].max()
        assert out.iloc[0]["Low"] == self._hourly().iloc[:4]["Low"].min()
        assert out.iloc[0]["Volume"] == self._hourly().iloc[:4]["Volume"].sum()
        # نام ستون‌ها Title Case (قرارداد با بقیه کد)
        assert set(["Open", "High", "Low", "Close", "Volume"]).issubset(out.columns)

    def test_2h(self):
        from agents.technical.data.market_data import resample_from_1h
        out = resample_from_1h(self._hourly(), "2h")
        assert out is not None
        assert len(out) == 24

    def test_empty_returns_none(self):
        from agents.technical.data.market_data import resample_from_1h
        assert resample_from_1h(None, "4h") is None
        assert resample_from_1h(pd.DataFrame(), "4h") is None


# ===========================================================================
# Provenance
# ===========================================================================

class TestProvenance:
    def test_period_for_interval(self):
        from agents.technical.data.market_data import period_for_interval
        assert period_for_interval("60m") == "3mo"
        assert period_for_interval("15m") == "5d"
        assert period_for_interval("1wk") == "5y"
        assert period_for_interval("1d") == "1y"
        assert period_for_interval("4h") == "3mo"  # مسیر ری‌سیمپل

    def test_fetch_provenance_shape(self):
        import json
        from agents.technical.data.market_data import FetchProvenance
        p = FetchProvenance(interval="1d", period="1y", bars=250,
                            resampled_from=None)
        d = json.loads(p.model_dump_json())
        assert d["source"] == "yfinance"
        assert d["interval"] == "1d"
        assert d["bars"] == 250
        assert d["resampled_from"] is None
        assert "fetched_at" in d
