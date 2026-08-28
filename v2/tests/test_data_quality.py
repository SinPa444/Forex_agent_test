"""
tests/test_data_quality.py
==========================
P0 tests — Data Quality Engine.
"""

from __future__ import annotations

import pandas as pd

from agents.technical.data_quality import compute_data_quality


def test_fresh_data():
    idx = pd.date_range("2026-08-01", periods=60, freq="D")
    df = pd.DataFrame(
        {
            "Open": [1.0] * 60,
            "High": [1.1] * 60,
            "Low": [0.9] * 60,
            "Close": [1.05] * 60,
            "Volume": [100] * 60,
        },
        index=idx,
    )
    res = compute_data_quality(df, reference_date=idx[-1])
    assert res.score > 0.8
    assert res.staleness_status == "FRESH"
    assert res.volume_available is True


def test_stale_data_penalty():
    idx = pd.date_range("2026-08-01", periods=60, freq="D")
    df = pd.DataFrame(
        {
            "Open": [1.0] * 60,
            "High": [1.1] * 60,
            "Low": [0.9] * 60,
            "Close": [1.05] * 60,
            "Volume": [100] * 60,
        },
        index=idx,
    )
    res = compute_data_quality(df, reference_date="2026-11-20")
    assert res.staleness_status == "STALE"
    assert res.score < 0.8


def test_missing_volume_small_penalty():
    idx = pd.date_range("2026-08-01", periods=60, freq="D")
    df = pd.DataFrame(
        {
            "Open": [1.0] * 60,
            "High": [1.1] * 60,
            "Low": [0.9] * 60,
            "Close": [1.05] * 60,
        },
        index=idx,
    )
    res = compute_data_quality(df, reference_date=idx[-1])
    assert res.volume_available is False
    assert res.score > 0.6
