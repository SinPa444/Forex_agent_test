"""
tests/test_confluence.py
========================
P0 tests — weighted Confluence Engine (deterministic).
"""

from __future__ import annotations

from types import SimpleNamespace

from agents.technical.confluence import build_confluence
from agents.technical.data_quality import DataQualityResult
from agents.technical.market_regime import MarketRegime, RegimeResult


def _fake_metrics():
    return SimpleNamespace(
        technical_confidence=0.55,
        active_bullish_ob=1.10,
        active_bearish_ob=None,
        active_bull_fvg_low=1.09,
        active_bull_fvg_high=1.11,
        active_bear_fvg_low=None,
        active_bear_fvg_high=None,
        components=SimpleNamespace(
            structure=0.8,
            trend=0.8,
            smc_location=0.8,
            mtf_confluence=0.0,
            momentum=0.8,
            volatility=0.4,
            price_action=0.0,
            liquidity_sweep=0.4,
            pdh_pdl=0.0,
            ote_zone=0.0,
        ),
    )


def test_confluence_score_bounds_and_evidence():
    m = _fake_metrics()
    dq = DataQualityResult(score=0.9, data_bars=100, volume_available=True)
    regime = RegimeResult(regime=MarketRegime.TRENDING.value, description="", reasons=[])
    conf = build_confluence(
        m,
        overall_dir=1,
        regime=regime,
        data_quality=dq,
        mtf_matrix=None,
        timeframe=None,
        smc_ctx=None,
        structure_label="BOS_BULLISH",
    )
    assert -1.0 <= conf.score <= 1.0
    assert 0.0 <= conf.confidence <= 1.0
    assert len(conf.items) <= 10
    assert len(conf.supporting) > 0
    assert any(i.name == "structure" for i in conf.items)
