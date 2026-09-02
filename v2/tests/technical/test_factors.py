"""
tests/technical/test_factors.py
===============================
تست‌های واحد فاکتورها — هر فاکتور به‌صورت ایزوله با دیتای کنترل‌شده.

هدف: آستانه‌ها و clampها مستند بمانند؛ هیچ magic number پنهانی نباشد.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _df(closes: list[float], idx_n: int | None = None) -> pd.DataFrame:
    """ساخت df ساده OHLC از سری close (بدون swing قوی — برای indicatorها)."""
    n = len(closes)
    idx_n = idx_n or n
    close = np.array(closes, dtype=float)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame(
        {"open": open_, "high": close + 0.0005, "low": close - 0.0005,
         "close": close, "volume": np.full(n, 100.0)},
        index=pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"),
    )


# ===========================================================================
# Trend
# ===========================================================================

class TestTrend:
    def test_uptrend_supertrend_confirmed(self):
        from agents.technical.indicators.trend import compute_trend
        # روند صعودی طولانی — EMA50 > EMA200 و قیمت بالای EMA50
        closes = list(np.linspace(1.0, 1.4, 300))
        r = compute_trend(_df(closes), current_price=closes[-1])
        assert r.direction == 1
        assert r.score == 0.8
        assert "confirmed" in r.status

    def test_downtrend(self):
        from agents.technical.indicators.trend import compute_trend
        closes = list(np.linspace(1.4, 1.0, 300))
        r = compute_trend(_df(closes), current_price=closes[-1])
        assert r.direction == -1
        # semantics نسخه قبل (بدون تغییر در Phase 1):
        #   confirmed → 0.8 ثابت (هر دو جهت!) — quirk W10 که فعلاً حفظ می‌شود
        #   divergence → 0.4 × direction (با علامت)
        if r.status.endswith("(Supertrend confirmed)"):
            assert r.score == 0.8
        else:
            assert r.score == 0.4 * r.direction

    def test_shallow_data_still_evaluates(self):
        from agents.technical.indicators.trend import compute_trend
        # 60 کندل: pandas-ta EMA را «converging» محاسبه می‌کند (None نمی‌شود)؛
        # score باید در یکی از مقادیر مجاز فاکتور باشد
        closes = list(np.linspace(1.0, 1.1, 60))
        r = compute_trend(_df(closes), current_price=closes[-1])
        assert r.score in (None, 0.0, 0.4, -0.4, 0.8)


# ===========================================================================
# Momentum
# ===========================================================================

class TestMomentum:
    def test_bullish(self):
        from agents.technical.indicators.momentum import compute_momentum
        closes = list(np.linspace(1.0, 1.5, 120))
        r = compute_momentum(_df(closes))
        assert r.rsi is not None and r.macd_histogram is not None
        if r.macd_histogram > 0 and r.rsi > 55:
            assert r.score == 0.8

    def test_neutral_zone(self):
        from agents.technical.indicators.momentum import compute_momentum
        # رنجر — RSI حول 50، MACD نزدیک صفر
        base = 1.1
        closes = [base + (0.001 if i % 2 == 0 else -0.001) for i in range(120)]
        r = compute_momentum(_df(closes))
        if r.rsi is not None and r.macd_histogram is not None:
            assert r.score in (0.8, -0.8, 0.0)
            if not (r.macd_histogram > 0 and r.rsi > 55) and not (
                r.macd_histogram < 0 and r.rsi < 45
            ):
                assert r.score == 0.0

    def test_missing_data_score_none(self):
        from agents.technical.indicators.momentum import compute_momentum
        r = compute_momentum(_df(list(np.linspace(1.0, 1.1, 5))))
        # 5 کندل: RSI ممکن است نباشد → score None یا مقداری؛ اگر RSI نبود:
        if r.rsi is None:
            assert r.score is None


# ===========================================================================
# Volatility / Regime
# ===========================================================================

class TestRegime:
    def test_strong_trend_adx_bonus(self):
        from agents.technical.indicators.volatility import compute_regime
        closes = list(np.linspace(1.0, 1.5, 200))
        r = compute_regime(_df(closes))
        if r.adx is not None:
            assert r.adx > 25
            assert r.score is not None and r.score > 0

    def test_atr_also_reported(self):
        from agents.technical.indicators.volatility import compute_regime
        closes = list(np.linspace(1.0, 1.3, 200))
        r = compute_regime(_df(closes))
        assert r.atr is not None and r.atr > 0  # داده خام Phase 1

    def test_bounds(self):
        from agents.technical.indicators.volatility import compute_regime
        for closes in (list(np.linspace(1.0, 1.6, 200)), list(np.linspace(1.6, 1.0, 200))):
            r = compute_regime(_df(closes))
            if r.score is not None:
                assert -0.8 <= r.score <= 0.8


# ===========================================================================
# Price Action
# ===========================================================================

class TestPriceAction:
    def test_neutral_when_no_pattern(self):
        from agents.technical.indicators.patterns import compute_price_action
        closes = list(np.linspace(1.0, 1.2, 100))
        r = compute_price_action(_df(closes))
        assert r.net == 0
        assert r.score == 0.0
        assert r.label == "Neutral"

    def test_score_cap(self):
        # منطق سقف امتیاز مستقیم (بدون وابستگی به دیتا)
        from agents.technical.indicators.patterns import (
            BASE_PATTERN_SCORE, SCORE_PER_EXTRA_PATTERN,
        )
        assert min(1.0, BASE_PATTERN_SCORE + SCORE_PER_EXTRA_PATTERN * (3 - 1)) == 1.0


# ===========================================================================
# Structure (BOS/CHoCH)
# ===========================================================================

def _swing_df(n: int = 300) -> pd.DataFrame:
    """دیتای موجی با swingهای واضح (sin + روند)."""
    t = np.arange(n)
    price = 1.1 + 0.0002 * t + 0.01 * np.sin(t / 8.0)
    close = price
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame(
        {"open": open_, "high": close + 0.002, "low": close - 0.002,
         "close": close, "volume": np.full(n, 100.0)},
        index=pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"),
    )


class TestStructure:
    def test_bos_only_default(self):
        from agents.technical.structure.swings import detect_swings
        from agents.technical.structure.bos_choch import compute_structure

        df = _swing_df()
        swings = detect_swings(df)
        r = compute_structure(df, swings)
        assert r.score_mode == "bos_only"
        if r.last_bos_dir is not None:
            # semantics نسخه قبل: فقط آخرین BOS، ±0.8
            assert r.score == 0.8 * r.last_bos_dir

    def test_structure_raw_snapshot(self):
        from agents.technical.structure.swings import detect_swings
        from agents.technical.structure.bos_choch import compute_structure

        df = _swing_df()
        r = compute_structure(df, detect_swings(df))
        raw = r.to_raw_dict()
        assert raw["score_mode"] == "bos_only"
        assert "last_bos" in raw and "last_choch" in raw
        assert isinstance(raw["sequence"], list)
        assert raw["swing_count"] is not None

    def test_no_swings_score_none(self):
        from agents.technical.structure.bos_choch import compute_structure

        df = _df(list(np.linspace(1.0, 1.1, 60)))
        r = compute_structure(df, None)
        assert r.score is None
        assert r.swing_count == 0

    def test_choch_aware_mode_off_by_default(self):
        from agents.technical.structure.bos_choch import DEFAULT_SCORE_MODE
        assert DEFAULT_SCORE_MODE == "bos_only"  # روشن‌سازی فقط با backtest


# ===========================================================================
# Liquidity
# ===========================================================================

class TestLiquidity:
    def test_no_recent_sweep(self):
        from agents.technical.structure.swings import detect_swings
        from agents.technical.structure.liquidity import compute_liquidity

        # روند یک‌سویه بدون برگشت به سطوح → احتمالاً «No recent sweep»
        df = _df(list(np.linspace(1.0, 1.3, 200)))
        swings = detect_swings(df)
        r = compute_liquidity(df, swings, current_price=df["close"].iloc[-1])
        if r.score is not None:
            assert r.score in (-0.8, -0.4, 0.0, 0.4, 0.8)

    def test_no_swings_none(self):
        from agents.technical.structure.liquidity import compute_liquidity
        df = _df(list(np.linspace(1.0, 1.1, 60)))
        r = compute_liquidity(df, None, 1.1)
        assert r.score is None


# ===========================================================================
# Levels
# ===========================================================================

class TestOTEThresholds:
    def test_constants(self):
        from agents.technical.levels.ote import OTE_MAX_PCT, OTE_MIN_PCT
        assert OTE_MIN_PCT == 61.8
        assert OTE_MAX_PCT == 78.6

    def test_no_swings_none(self):
        from agents.technical.levels.ote import compute_ote
        df = _df(list(np.linspace(1.0, 1.1, 60)))
        r = compute_ote(df, None)
        assert r.score is None


class TestPDHConstants:
    def test_constants(self):
        from agents.technical.levels.pdh_pdl import (
            BREAK_LOOKBACK, BREAK_SCORE, PDH_PDL_TOL_PCT, SIT_SCORE,
        )
        assert BREAK_SCORE == 0.6
        assert SIT_SCORE == 0.4
        assert PDH_PDL_TOL_PCT == 0.001
        assert BREAK_LOOKBACK == 3
