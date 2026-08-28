"""
agents/technical/market_regime.py
=================================
Market Regime Engine.

خروجی:
  - `TRENDING`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`,
    `BREAKOUT`, `REVERSAL`, `UNCLEAR`.

این ماژول فقط از مقادیر deterministic استفاده می‌کند؛ LLM در آن دخالت ندارد.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core import technical_config as cfg


class MarketRegime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    UNCLEAR = "UNCLEAR"


@dataclass
class RegimeResult:
    regime: str
    description: str
    reasons: list[str]
    vol_z: Optional[float] = None
    adx: Optional[float] = None
    chop: Optional[float] = None


def detect_market_regime(
    *,
    adx: Optional[float] = None,
    chop: Optional[float] = None,
    vol_z: Optional[float] = None,
    breakout: bool = False,
    choch: Optional[int] = None,
    trend_label: str = "",
    atr_pct: Optional[float] = None,
    atr_median_pct: Optional[float] = None,
) -> RegimeResult:
    """
    تشخیص رژیم بر اساس ADX/CHOP/ATR و به‌صورت اختیاری ساختار BOS/CHoCH.

    اولویت:
      1. High/Low Volatility (بر اساس vol_z/ATR نسبت به median)
      2. Breakout (شکست ۲۰ کندل اخیر + continuation)
      3. Reversal (CHoCH یا تضاد ساختاری)
      4. Trending / Ranging (ADX+CHOP)
      5. UNCLEAR
    """
    reasons: list[str] = []
    vol_high = False
    vol_low = False

    if vol_z is not None:
        if vol_z >= cfg.ATR_HIGH_MULTIPLIER:
            vol_high = True
            reasons.append(f"ATR vol z={vol_z:.2f} >= {cfg.ATR_HIGH_MULTIPLIER}")
        elif vol_z <= cfg.ATR_LOW_MULTIPLIER:
            vol_low = True
            reasons.append(f"ATR vol z={vol_z:.2f} <= {cfg.ATR_LOW_MULTIPLIER}")

    if vol_high:
        return RegimeResult(
            regime=MarketRegime.HIGH_VOLATILITY.value,
            description="High volatility regime (ATR above median threshold).",
            reasons=reasons, vol_z=vol_z, adx=adx, chop=chop,
        )
    if vol_low:
        return RegimeResult(
            regime=MarketRegime.LOW_VOLATILITY.value,
            description="Low volatility regime (ATR below median threshold).",
            reasons=reasons, vol_z=vol_z, adx=adx, chop=chop,
        )

    if breakout:
        return RegimeResult(
            regime=MarketRegime.BREAKOUT.value,
            description="Recent price broke the recent range with continuation.",
            reasons=reasons + ["range breakout"], vol_z=vol_z, adx=adx, chop=chop,
        )

    if choch not in (0, None):
        return RegimeResult(
            regime=MarketRegime.REVERSAL.value,
            description=f"Change of character detected ({'bullish' if choch > 0 else 'bearish'}).",
            reasons=reasons + ["CHoCH"], vol_z=vol_z, adx=adx, chop=chop,
        )

    if adx is not None and chop is not None:
        if adx >= cfg.REGIME_ADX_TRENDING and chop <= cfg.REGIME_CHOP_TRENDING:
            return RegimeResult(
                regime=MarketRegime.TRENDING.value,
                description=f"Strong trend (ADX={adx:.1f}, CHOP={chop:.1f}).",
                reasons=reasons + ["strong trend"], vol_z=vol_z, adx=adx, chop=chop,
            )
        if adx < cfg.REGIME_ADX_RANGING or chop > cfg.REGIME_CHOP_RANGING:
            return RegimeResult(
                regime=MarketRegime.RANGING.value,
                description=f"Choppy/range market (ADX={adx:.1f}, CHOP={chop:.1f}).",
                reasons=reasons + ["ranging"], vol_z=vol_z, adx=adx, chop=chop,
            )

    return RegimeResult(
        regime=MarketRegime.UNCLEAR.value,
        description=f"Unable to classify regime (ADX={adx}, CHOP={chop}, trend={trend_label!r}).",
        reasons=reasons + ["unclear"], vol_z=vol_z, adx=adx, chop=chop,
    )
