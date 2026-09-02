"""
tests/technical/conftest.py
===========================
Fixtures مشترک تست‌های موتور فنی — همه آفلاین (بدون شبکه).

دیتای synthetic با seed=42 دقیقاً همانی است که golden reference
(golden/reference.json) با آن گرفته شده است.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# تضمین دسترسی به ریشه v2 (خود pytest در حالت package هم آن را می‌آورد؛
# این خط برای اجرای مستقیم هم کار می‌کند)
_V2_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_V2_ROOT) not in sys.path:
    sys.path.insert(0, str(_V2_ROOT))

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_REFERENCE = GOLDEN_DIR / "reference.json"

GOLDEN_TIMEFRAMES = ("H1", "D1", "H4")

# --- فیلد‌های «قدیمی» که باید bit-identical بمانند (قرارداد بازساخت Phase 1) ---
GOLDEN_KEYS = [
    "current_price", "technical_score", "technical_confidence", "components",
    "trend_status", "adx_value", "chop_value", "recent_bos",
    "active_bullish_ob", "active_bearish_ob",
    "active_bull_fvg_low", "active_bull_fvg_high",
    "active_bear_fvg_low", "active_bear_fvg_high",
    "rsi", "macd_histogram", "last_candle_type",
    "htf_interval", "htf_trend_status", "liquidity_sweep_status",
    "previous_day_high", "previous_day_low", "pdh_pdl_status",
    "current_retracement", "ote_status",
]


def make_synthetic(seed: int = 42, n: int = 1200) -> pd.DataFrame:
    """
    دیتای OHLCV مصنوعی و deterministic — دقیقاً همان ساختار که
    golden/reference.json با آن تولید شده (seed=42, 1200×1h).
    """
    rng = np.random.default_rng(seed)
    drift = np.zeros(n)
    drift[100:400] = 0.0004      # up
    drift[400:500] = -0.0006     # down
    drift[500:900] = 0.0005      # main up leg
    drift[900:1000] = -0.0008    # pullback (OTE region)
    drift[1000:] = 0.0002
    rets = rng.normal(0, 0.0008, n) + drift
    close = 1.1000 * np.exp(np.cumsum(rets))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    spread = np.abs(rng.normal(0, 0.0004, n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vol = rng.integers(100, 1000, n).astype(float)
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


@pytest.fixture()
def synthetic_df() -> pd.DataFrame:
    """دیتای synthetic پیش‌فرض (seed=42)."""
    return make_synthetic()


@pytest.fixture()
def golden_reference() -> dict:
    """خروجی موتور قدیمی (pre-refactor) — قرارداد bit-identical."""
    import json
    with open(GOLDEN_REFERENCE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def patch_fetch(monkeypatch, synthetic_df):
    """
    پچ لایه fetch موتور (هدف جدید monkeypatch) با دیتای synthetic.
    یکتا: همه fetchها (پایه + HTF) همان df را برمی‌گردانند.
    """
    from agents.technical.data import market_data

    def _fake(ticker: str, interval: str = "1d"):
        return synthetic_df.copy()

    monkeypatch.setattr(market_data, "fetch_price_history", _fake)
    return market_data
