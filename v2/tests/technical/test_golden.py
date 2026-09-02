"""
tests/technical/test_golden.py
==============================
تست طلایی (Golden Test) بازساخت Phase 1.

قرارداد: بازساخت monolith → ماژول‌ها **semantics امتیازدهی را تغییر نداد**.
خروجی موتور جدید روی دیتای synthetic (seed=42) باید برای همه فیلد‌های
«قدیمی» با خروجی موتور قدیمی bit-identical باشد (H1/D1/H4).

اگر این تست شکست، یا (a) بازساخت semantics را تغییر داده، یا
(b) golden reference دست نخورده باقی نمانده — هر دو باید بررسی شوند
قبل از تغییر کد.
"""

from __future__ import annotations

import pytest

from agents.technical.technical_analyzer import calculate_technical_metrics

from .conftest import GOLDEN_KEYS, GOLDEN_TIMEFRAMES


def _capture(timeframe: str) -> dict:
    m = calculate_technical_metrics("TEST=X", timeframe=timeframe)
    out = {}
    for key in GOLDEN_KEYS:
        if key == "components":
            out[key] = m.components.model_dump()
        else:
            out[key] = getattr(m, key)
    return out


@pytest.mark.parametrize("timeframe", GOLDEN_TIMEFRAMES)
def test_golden_bit_identical(timeframe: str, patch_fetch, golden_reference):
    """خروجی موتور جدید == خروجی موتور قدیمی (همان داده)."""
    actual = _capture(timeframe)
    expected = golden_reference[timeframe]

    diffs = []
    for key in GOLDEN_KEYS:
        a, e = actual.get(key), expected.get(key)
        if isinstance(a, dict) and isinstance(e, dict):
            for sub in set(a) | set(e):
                if a.get(sub) != e.get(sub):
                    diffs.append(f"{key}.{sub}: old={e.get(sub)!r} new={a.get(sub)!r}")
        elif a != e:
            diffs.append(f"{key}: old={e!r} new={a!r}")

    assert not diffs, (
        f"Golden mismatch on {timeframe} ({len(diffs)} diffs):\n" + "\n".join(diffs)
    )


@pytest.mark.parametrize("timeframe", GOLDEN_TIMEFRAMES)
def test_engine_version_and_provenance_attached(timeframe: str, patch_fetch):
    """Phase 1: فیلد‌های حسابرسی (افزایشی) باید پر شوند."""
    from agents.technical.confluence.weights import ENGINE_VERSION

    m = calculate_technical_metrics("TEST=X", timeframe=timeframe)

    assert m.engine_version == ENGINE_VERSION
    assert m.provenance_json is not None
    assert m.data_quality_json is not None

    import json
    prov = json.loads(m.provenance_json)
    assert prov["source"] == "yfinance"  # fetch واقعی است (mock)
    assert prov["bars"] == 1200

    q = json.loads(m.data_quality_json)
    assert q["bars"] == 1200
    assert q["ok"] is True


def test_new_raw_fields_populated(patch_fetch):
    """Phase 1: ATR + ساختار خام + فاصله‌ها به‌عنوان داده خام ثبت می‌شوند."""
    m = calculate_technical_metrics("TEST=X", timeframe="H1")

    assert m.atr is not None and m.atr > 0
    assert m.structure_raw is not None
    assert m.structure_raw["score_mode"] == "bos_only"
    assert "last_bos" in m.structure_raw
    assert "sequence" in m.structure_raw
    assert m.level_distances is not None  # در این داده OB/FVG فعال وجود دارد


def test_short_data_returns_empty(monkeypatch):
    """دیتای کمتر از MIN_BARS → score None (semantics نسخه قبل)."""
    import pandas as pd
    from agents.technical.data import market_data

    df = pd.DataFrame(
        {"Open": [1.0] * 10, "High": [1.1] * 10, "Low": [0.9] * 10,
         "Close": [1.05] * 10, "Volume": [100.0] * 10},
        index=pd.date_range("2025-01-01", periods=10, freq="h", tz="UTC"),
    )
    monkeypatch.setattr(market_data, "fetch_price_history",
                        lambda ticker, interval="1d": df.copy())

    m = calculate_technical_metrics("TEST=X", timeframe="H1")
    assert m.technical_score is None
    assert m.technical_confidence is None  # semantics نسخه قبل: early return پیش از ست شدن
    assert m.current_price is None


def test_fetch_failure_returns_empty(monkeypatch):
    """fetch ناموفق → response خالی بدون استثنا (semantics نسخه قبل)."""
    from agents.technical.data import market_data

    def _boom(ticker, interval="1d"):
        return None

    monkeypatch.setattr(market_data, "fetch_price_history", _boom)
    m = calculate_technical_metrics("TEST=X", timeframe="H1")
    assert m.technical_score is None
