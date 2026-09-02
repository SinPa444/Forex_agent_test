"""
tests/technical/test_backtest.py
================================
تست‌های ادغامی بک‌تست با موتور بازساخت‌شده (Phase 1).

نکته کلیدی طراحی walkforward: **بدون بازنویسی اسکورینگ**، دقیقاً همان
تابع پروداکشن صدا زده می‌شود و فقط لایه fetch جایگزین می‌شود. این تست‌ها
آن قرارداد را نگه می‌دارند:
  - identity: تابعی که walkforward می‌زند همان تابع پروداکشن است
  - patch target: monkeypatch روی market_data.fetch_price_history دیده می‌شود
  - W3/W4: preload H4 از 1h ری‌سیمپل می‌کند (نه fetch مستقیم 4h)
  - W5: annualization متناسب با تایم‌فریم
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ===========================================================================
# Identity + patch target (قرارداد walkforward ↔ production)
# ===========================================================================

class TestProductionIdentity:
    def test_facade_is_same_function_object(self):
        """technical_analyzer (فکس) باید همان تابع engine را refer کند."""
        from agents.technical import technical_analyzer as ta
        from agents.technical.engine import calculate_technical_metrics
        assert ta.calculate_technical_metrics is calculate_technical_metrics

    def test_patch_target_reaches_engine(self, monkeypatch):
        """
        monkeypatch روی market_data.fetch_price_history باید به موتور برسد —
        همان چیزی که backtest/walkforward انجام می‌دهد.
        """
        from agents.technical import technical_analyzer as ta
        from agents.technical.data import market_data

        calls: list[str] = []

        def _fake(ticker: str, interval: str = "1d"):
            calls.append(interval)
            return pd.DataFrame(
                {"Open": [1.0] * 5, "High": [1.1] * 5, "Low": [0.9] * 5,
                 "Close": [1.05] * 5, "Volume": [100.0] * 5},
                index=pd.date_range("2025-01-01", periods=5, freq="h", tz="UTC"),
            )

        monkeypatch.setattr(market_data, "fetch_price_history", _fake)
        ta.calculate_technical_metrics("TEST=X", timeframe="H1")
        assert calls == ["60m"]  # fetch واقعی دیده شده (دیتای کوتاه → score None)

    def test_walkforward_patches_same_target(self, monkeypatch):
        """
        walkforward باید fetch را روی همان attribute‌ای پچ کند که engine می‌خواند.
        (تست رگرسیونی: نسخه قبل technical_analyzer را پچ می‌زد که بعد از
        بازساخت دیگر مسیر فعال نبود.)
        """
        import inspect
        from agents.technical.data import market_data
        import backtest.walkforward as wf

        src = inspect.getsource(wf.compute_score_series)
        assert "market_data.fetch_price_history = feeder.fetch" in src
        assert "market_data.DATA_SOURCE" in src
        # و engine هم از همان attribute بخواند
        import agents.technical.engine as eng
        assert "market_data.fetch_price_history(" in inspect.getsource(eng)


# ===========================================================================
# W3/W4: preload 4h/2h از 1h
# ===========================================================================

def _fake_yf_1h(n=3000):
    """تولیدکننده df 1h (به‌جای yf.download)."""
    idx = pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC")
    close = 1.1 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.001, n)))
    return pd.DataFrame(
        {"Open": close, "High": close + 0.001, "Low": close - 0.001,
         "Close": close, "Volume": np.full(n, 100.0)},
        index=idx,
    )


class TestPreloadResample:
    def test_h4_preload_uses_resample(self, monkeypatch):
        import yfinance as yf
        from backtest.walkforward import preload_history

        downloads: list[tuple[str, str]] = []
        real_1h = _fake_yf_1h()

        def _fake_download(ticker, period="1y", interval="1d", **kw):
            downloads.append((period, interval))
            if interval == "1h":
                return real_1h.copy()
            return None  # 4h مستقیم «نباید» درخواست شود

        monkeypatch.setattr(yf, "download", _fake_download)

        feeder = preload_history("TEST=X", timeframe="H4")
        assert feeder is not None
        assert "4h" in feeder._data
        # 3000h → ~750 کندل 4h
        assert len(feeder._data["4h"]) == pytest.approx(750, abs=5)
        # فقط 1h دانلود شده (base) + 1d برای HTF
        assert ("700d", "1h") in downloads
        assert all(iv in ("1h", "1d") for _, iv in downloads)

    def test_h1_htf_4h_resampled(self, monkeypatch):
        """
        H1: base = 60m مستقیم؛ HTF = 4h که باید از 1h ری‌سیمپل شود
        (yfinance 4h ندارد). این مسیر دانلود جدا 1h را می‌زند.
        """
        import yfinance as yf
        from backtest.walkforward import preload_history

        downloads: list[str] = []
        real_1h = _fake_yf_1h()

        def _fake_download(ticker, period="1y", interval="1d", **kw):
            downloads.append(interval)
            if interval in ("1h", "60m"):
                return real_1h.copy()
            return None

        monkeypatch.setattr(yf, "download", _fake_download)
        feeder = preload_history("TEST=X", timeframe="H1")
        assert feeder is not None
        assert "60m" in feeder._data
        assert "4h" in feeder._data  # HTF از 1h ری‌سیمپل شده
        assert downloads.count("1h") == 1  # فقط برای HTF


# ===========================================================================
# W5: BARS_PER_YEAR
# ===========================================================================

class TestBarsPerYear:
    def test_config_table(self):
        from backtest import config
        assert config.BARS_PER_YEAR["1d"] == 252.0
        assert config.BARS_PER_YEAR["4h"] == 1512.0   # 252*6
        assert config.BARS_PER_YEAR["60m"] == 6048.0  # 252*24

    def test_from_index_daily(self):
        from backtest.engine import bars_per_year_from_index
        idx = pd.date_range("2020-01-01", periods=300, freq="D", tz="UTC")
        assert bars_per_year_from_index(idx) == pytest.approx(252.0)

    def test_from_index_4h(self):
        from backtest.engine import bars_per_year_from_index
        idx = pd.date_range("2020-01-01", periods=3000, freq="4h", tz="UTC")
        assert bars_per_year_from_index(idx) == pytest.approx(1512.0)

    def test_from_index_fallback(self):
        from backtest.engine import bars_per_year_from_index
        assert bars_per_year_from_index(pd.Index([1, 2, 3])) == 252.0

    def test_run_backtest_uses_bpy(self):
        """
        annualization: یک سری بازگشت ثابت روی index 4h باید Sharpe متفاوتی
        نسبت به فرض 252 کندل/سال بدهد (تفاوت sqrt(1512/252) ≈ 2.45).
        """
        from backtest.engine import run_backtest

        n = 2000
        idx = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
        close = 1.1 * np.exp(np.linspace(0, 0.5, n))
        df = pd.DataFrame(
            {"open": close, "high": close, "low": close, "close": close,
             "score": np.clip(np.random.default_rng(2).normal(0.3, 0.2, n), -1, 1),
             "confidence": np.full(n, 0.8)},
            index=idx,
        )

        r_auto = run_backtest(df, "EURUSD=X")
        assert r_auto.bars_per_year == pytest.approx(1512.0)

        r_252 = run_backtest(df, "EURUSD=X", bars_per_year=252.0)
        # Sharpe با sqrt(bars/year) سالانه می‌شود
        if r_auto.sharpe != 0 and r_252.sharpe != 0:
            ratio = r_auto.sharpe / r_252.sharpe
            assert ratio == pytest.approx(np.sqrt(1512 / 252), rel=0.05)
