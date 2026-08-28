"""
tests/test_technical_signal.py
==============================
P0 tests — TechnicalSignal output contract.

این تست‌ها فقط deterministic هستند؛ هیچ LLM/شبکه استفاده نمی‌شود.
"""

from __future__ import annotations

import pandas as pd
from types import SimpleNamespace

from agents.technical.technical_signal import (
    SignalState,
    build_technical_signal,
    _signal_state,
)
from agents.technical import technical_signal as ts


def _synthetic_df() -> pd.DataFrame:
    """DataFrame کوچک و مصنوعی برای تست بدون yfinance."""
    n = 80
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    # یک بازار صعودی ساده
    close = [100 + i * 0.5 for i in range(n)]
    base = pd.DataFrame(
        {
            "Open": close,
            "High": [c + 0.5 for c in close],
            "Low": [c - 0.5 for c in close],
            "Close": close,
            "Volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )
    return base


def _fake_metrics() -> SimpleNamespace:
    return SimpleNamespace(
        current_price=125.0,
        technical_score=0.42,
        technical_confidence=0.55,
        trend_status="Uptrend",
        adx_value=28.0,
        chop_value=30.0,
        active_bullish_ob=None,
        active_bearish_ob=None,
        active_bull_fvg_low=None,
        active_bull_fvg_high=None,
        active_bear_fvg_low=None,
        active_bear_fvg_high=None,
        liquidity_sweep_status=None,
        ote_status=None,
        pdh_pdl_status=None,
        rsi=58.0,
        macd_histogram=0.1,
        components=SimpleNamespace(
            structure=0.8,
            trend=0.8,
            smc_location=0.0,
            mtf_confluence=0.8,
            momentum=0.8,
            volatility=0.8,
            price_action=0.0,
            liquidity_sweep=0.4,
            pdh_pdl=0.0,
            ote_zone=0.0,
        ),
    )


def test_signal_state_thresholds():
    assert _signal_state(0.65) == SignalState.STRONG_BULLISH
    assert _signal_state(0.30) == SignalState.BULLISH
    assert _signal_state(0.12) == SignalState.WEAK_BULLISH
    assert _signal_state(0.0) == SignalState.NEUTRAL
    assert _signal_state(-0.65) == SignalState.STRONG_BEARISH


def test_build_technical_signal_contract():
    df = _synthetic_df()
    signal = build_technical_signal(
        ticker="EURUSD=X",
        timeframe="D1",
        metrics=_fake_metrics(),
        df=df,
        mtf_matrix=None,
        mtf_scores_roles=None,
    )

    assert signal.symbol == "EURUSD=X"
    assert signal.direction in set(SignalState)
    assert signal.direction_value in (-1, 0, 1)
    assert -1.0 <= signal.score <= 1.0
    assert 0.0 <= signal.confidence <= 1.0
    assert signal.market_regime
    assert signal.trend in ("BULLISH", "BEARISH", "RANGING", "UNCLEAR")
    assert signal.market_structure
    assert signal.momentum
    assert signal.volatility
    assert signal.smc_context
    assert isinstance(signal.supporting_evidence, list)
    assert isinstance(signal.contradicting_evidence, list)
    assert isinstance(signal.risks, list)
    assert len(signal.reasoning_summary) > 0

    # to_dict should be serializable
    d = signal.to_dict()
    assert d["symbol"] == "EURUSD=X"
    assert isinstance(d["confluence"], list)


def test_signal_neutral_when_technical_score_not_evaluated():
    """اگر technical_score None باشد حتی با مؤلفه‌های جزئی، score باید 0 بماند."""
    df = _synthetic_df()
    metrics = _fake_metrics()
    metrics.technical_score = None
    metrics.technical_confidence = 0.0
    metrics.components.structure = None
    metrics.components.smc_location = None
    # سایر مؤلفه‌ها موجود هستند اما چون gate ساختار/SMC برقرار نیست، score=0.

    signal = build_technical_signal(
        ticker="XAU",
        timeframe="D1",
        metrics=metrics,
        df=df,
        mtf_matrix=None,
        mtf_scores_roles=None,
    )
    assert signal.score == 0.0
    assert signal.direction == SignalState.NEUTRAL.value


def test_signal_neutral_if_no_components_evaluated():
    """اگر هیچ فاکتوری ارزیابی نشده، score باید 0 و سیگنال NEUTRAL باشد."""
    df = _synthetic_df()
    metrics = _fake_metrics()
    metrics.technical_score = None
    metrics.technical_confidence = 0.0
    metrics.components = SimpleNamespace(
        structure=None,
        trend=None,
        smc_location=None,
        mtf_confluence=None,
        momentum=None,
        volatility=None,
        price_action=None,
        liquidity_sweep=None,
        pdh_pdl=None,
        ote_zone=None,
    )

    signal = build_technical_signal(
        ticker="XAU",
        timeframe="D1",
        metrics=metrics,
        df=df,
        mtf_matrix=None,
        mtf_scores_roles=None,
    )
    assert signal.score == 0.0
    assert signal.direction == SignalState.NEUTRAL.value
