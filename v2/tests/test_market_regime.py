"""
tests/test_market_regime.py
===========================
P0 tests — Market Regime Engine.
"""

from __future__ import annotations

from agents.technical.market_regime import (
    MarketRegime,
    detect_market_regime,
)


def test_trending():
    r = detect_market_regime(adx=30.0, chop=25.0)
    assert r.regime == MarketRegime.TRENDING.value
    assert r.reasons


def test_ranging():
    r = detect_market_regime(adx=15.0, chop=70.0)
    assert r.regime == MarketRegime.RANGING.value


def test_high_volatility_priority():
    r = detect_market_regime(adx=30.0, chop=25.0, vol_z=2.0)
    assert r.regime == MarketRegime.HIGH_VOLATILITY.value


def test_breakout():
    r = detect_market_regime(adx=15.0, chop=70.0, breakout=True)
    assert r.regime == MarketRegime.BREAKOUT.value


def test_choch_reversal():
    r = detect_market_regime(adx=15.0, chop=70.0, choch=1)
    assert r.regime == MarketRegime.REVERSAL.value


def test_unclear():
    r = detect_market_regime(adx=None, chop=None)
    assert r.regime == MarketRegime.UNCLEAR.value
