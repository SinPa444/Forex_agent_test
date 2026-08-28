"""
agents/technical/smc.py
=======================
SMC Context Engine.

این ماژول از خروجی‌های موجود در `TechnicalMetrics` (که قبلاً در
`technical_analyzer.py` محاسبه شده‌اند) یک `smc_context` readable برای
`TechnicalSignal` و LLM می‌سازد.

این ماژول خودش OB/FVG را مجدداً محاسبه می‌کند؛ اگر در آینده نیاز به OB/FVG
برای همهٔ زمان‌ها باشد، پیاده‌سازی مستقل در همین فایل اضافه خواهد شد.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SmcContext:
    """پیکربندی SMC به‌صورت ساختاریافته."""
    label: str = "NEUTRAL"
    price_location: str = "NEUTRAL"     # AT_DEMAND / AT_SUPPLY / BETWEEN_ZONES
    active_bullish_ob: Optional[float] = None
    active_bearish_ob: Optional[float] = None
    active_bull_fvg: tuple[Optional[float], Optional[float]] = (None, None)
    active_bear_fvg: tuple[Optional[float], Optional[float]] = (None, None)
    liquidity_sweep: str = "NONE"
    ote_label: str = "N/A"
    pdh_pdl_label: str = "N/A"
    evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


def build_smc_context(metrics) -> SmcContext:
    """از `TechnicalMetrics` یک SMC Context بساز."""
    ctx = SmcContext()

    if metrics is None:
        ctx.label = "UNCLEAR"
        return ctx

    ctx.active_bullish_ob = metrics.active_bullish_ob
    ctx.active_bearish_ob = metrics.active_bearish_ob
    ctx.active_bull_fvg = (metrics.active_bull_fvg_low, metrics.active_bull_fvg_high)
    ctx.active_bear_fvg = (metrics.active_bear_fvg_low, metrics.active_bear_fvg_high)
    ctx.liquidity_sweep = metrics.liquidity_sweep_status or "NONE"
    ctx.ote_label = metrics.ote_status or "N/A"
    ctx.pdh_pdl_label = metrics.pdh_pdl_status or "N/A"

    near_bull_zone = (ctx.active_bullish_ob is not None or (ctx.active_bull_fvg[0] is not None))
    near_bear_zone = (ctx.active_bearish_ob is not None or (ctx.active_bear_fvg[0] is not None))

    if near_bull_zone and near_bear_zone:
        ctx.price_location = "BETWEEN_ZONES"
        ctx.label = "BETWEEN_SUPPLY_AND_DEMAND"
    elif near_bull_zone:
        ctx.price_location = "AT_DEMAND"
        ctx.label = "PRICE_AT_DEMAND"
    elif near_bear_zone:
        ctx.price_location = "AT_SUPPLY"
        ctx.label = "PRICE_AT_SUPPLY"
    else:
        ctx.price_location = "NEUTRAL"
        ctx.label = "NEUTRAL"

    # --- Evidence ---
    if metrics.active_bullish_ob is not None:
        ctx.evidence.append(f"Active bullish OB (demand) near {metrics.active_bullish_ob:.4f}")
    if metrics.active_bearish_ob is not None:
        ctx.evidence.append(f"Active bearish OB (supply) near {metrics.active_bearish_ob:.4f}")
    if ctx.active_bull_fvg[0] is not None:
        ctx.evidence.append(f"Active bullish FVG {ctx.active_bull_fvg[0]:.4f}-{ctx.active_bull_fvg[1]:.4f}")
    if ctx.active_bear_fvg[0] is not None:
        ctx.evidence.append(f"Active bearish FVG {ctx.active_bear_fvg[0]:.4f}-{ctx.active_bear_fvg[1]:.4f}")
    if metrics.liquidity_sweep_status:
        ctx.evidence.append(metrics.liquidity_sweep_status)
    if metrics.ote_status and metrics.ote_status != "N/A":
        ctx.evidence.append(metrics.ote_status)
    if metrics.pdh_pdl_status and metrics.pdh_pdl_status:
        ctx.evidence.append(metrics.pdh_pdl_status)

    # --- Risks ---
    if near_bull_zone and near_bear_zone:
        ctx.risks.append("Price is between demand and supply; no edge zone yet")
    smc_score = None
    if metrics.components is not None:
        smc_score = getattr(metrics.components, "smc_location", None)
    if ctx.label == "PRICE_AT_SUPPLY" and smc_score is not None and smc_score > 0.0:
        ctx.risks.append("Bullish SMC score but price is at supply zone — conflict needed")
    if ctx.label == "PRICE_AT_DEMAND" and smc_score is not None and smc_score < 0.0:
        ctx.risks.append("Bearish SMC score but price is at demand zone — conflict needed")
    return ctx
