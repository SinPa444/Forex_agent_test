"""
agents/technical/confluence.py
==============================
Confluence Engine deterministic.

خروجی:
  - `technical_score` (از وزن‌های فعلی)
  - `confidence` improved (Data Quality + MTF alignment + regime + setup + evidence agreement)
  - `items` (لیست ConfluenceItem برای explainability)
  - `supporting_evidence`, `contradicting_evidence`, `risks`

این ماژول LLM ندارد؛ فقط روش deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core import technical_config as cfg
from agents.technical.market_regime import MarketRegime, RegimeResult
from agents.technical.data_quality import DataQualityResult


@dataclass
class ConfluenceItem:
    name: str
    value: float
    weight: float
    kind: str          # supporting / contradicting / neutral
    note: str = ""


@dataclass
class ConfluenceResult:
    score: float = 0.0
    confidence: float = 0.0
    items: list[ConfluenceItem] = field(default_factory=list)
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)


def _sign(value: float) -> int:
    return 1 if value > 0 else (-1 if value < 0 else 0)


def _kind_for(value: float, overall_dir: int) -> str:
    if value == 0 or overall_dir == 0:
        return "neutral"
    return "supporting" if _sign(value) == overall_dir else "contradicting"


def _component_value(metrics, name: str) -> Optional[float]:
    if metrics is None or not hasattr(metrics, "components"):
        return None
    comp = metrics.components
    if not hasattr(comp, name):
        return None
    v = getattr(comp, name)
    return float(v) if v is not None else None


def build_confluence(
    metrics,
    *,
    weights: Optional[dict[str, float]] = None,
    overall_dir: int = 0,
    regime: Optional[RegimeResult] = None,
    data_quality: Optional[DataQualityResult] = None,
    mtf_matrix=None,
    timeframe: Optional[str] = None,
    smc_ctx=None,
    structure_label: str = "UNCLEAR",
) -> ConfluenceResult:
    """ساخت ConfluenceResult از TechnicalMetrics + Contextهای تکمیلی."""
    w = weights or cfg.TECHNICAL_WEIGHTS
    result = ConfluenceResult()

    component_names = [
        "structure", "trend", "smc_location", "mtf_confluence", "momentum",
        "volatility", "price_action", "liquidity_sweep", "pdh_pdl", "ote_zone",
    ]

    # --- Score (same as technical_analyzer) ---
    raw_score = 0.0
    has_eval = False
    for name in component_names:
        value = _component_value(metrics, name)
        if value is None:
            continue
        has_eval = True
        raw_score += w.get(name, 0.0) * value
        overall = overall_dir
        result.items.append(
            ConfluenceItem(
                name=name,
                value=round(value, 4),
                weight=w.get(name, 0.0),
                kind=_kind_for(value, overall),
            )
        )

    result.score = round(max(-1.0, min(1.0, raw_score)), 4) if has_eval else 0.0

    # --- Evidence classification ---
    for item in result.items:
        if item.kind == "supporting":
            result.supporting.append(f"{item.name}={item.value:+.2f} (w={item.weight:.2f})")
        elif item.kind == "contradicting":
            result.contradicting.append(f"{item.name}={item.value:+.2f} (w={item.weight:.2f})")

    # --- Confidentiality ---
    baseline = float(metrics.technical_confidence or 0.0) if metrics is not None else 0.0
    dq = (data_quality.score if data_quality else 0.0)

    # MTF alignment score
    mtf_score = 0.5
    if mtf_matrix is not None and timeframe is not None:
        tf_obj = getattr(mtf_matrix, "scores", {}).get(timeframe)
        htf_bias = getattr(mtf_matrix, "htf_bias", 0)
        if tf_obj is not None and tf_obj.valid and tf_obj.direction != 0:
            if htf_bias != 0:
                mtf_score = 1.0 if tf_obj.direction == htf_bias else 0.0
            else:
                mtf_score = 0.5
        elif htf_bias != 0:
            mtf_score = 0.5
    elif mtf_matrix is not None:
        # no timeframe given; use HTF bias itself as neutral signal
        htf_bias = getattr(mtf_matrix, "htf_bias", 0)
        mtf_score = 0.75 if htf_bias != 0 else 0.5

    # Regime score
    regime_score = 0.5
    regime_label = regime.regime if regime else ""
    if regime_label in {MarketRegime.TRENDING.value, MarketRegime.BREAKOUT.value}:
        regime_score = 0.75
    elif regime_label == MarketRegime.HIGH_VOLATILITY.value:
        regime_score = 0.35
    elif regime_label == MarketRegime.RANGING.value:
        regime_score = 0.45
    elif regime_label == MarketRegime.UNCLEAR.value:
        regime_score = 0.4

    # setup quality (OB/FVG presence)
    setup = 0.0
    if metrics is not None:
        if getattr(metrics, "active_bullish_ob", None) or getattr(metrics, "active_bearish_ob", None):
            setup += 0.5
        if getattr(metrics, "active_bull_fvg_low", None) is not None or getattr(metrics, "active_bear_fvg_low", None) is not None:
            setup += 0.5

    confidence = 0.55 * baseline + 0.15 * dq + 0.15 * mtf_score + 0.10 * regime_score + 0.05 * setup
    confidence = max(0.0, min(1.0, confidence))

    # Penalties
    if mtf_matrix is not None and timeframe is not None:
        tf_obj = getattr(mtf_matrix, "scores", {}).get(timeframe)
        htf_bias = getattr(mtf_matrix, "htf_bias", 0)
        if tf_obj is not None and tf_obj.valid and htf_bias != 0 and tf_obj.direction != 0 and tf_obj.direction != htf_bias:
            confidence -= cfg.MTF_CONFLICT_PENALTY
            result.contradicting.append("MTF conflict with HTF bias")
    if regime_label == MarketRegime.HIGH_VOLATILITY.value:
        confidence -= cfg.REGIME_UNFAVORABLE_PENALTY
        result.risks.append("High volatility regime")
    if dq < cfg.DATA_QUALITY_GOOD:
        result.risks.append(f"Data quality below ideal ({dq:.2f})")

    result.confidence = round(max(0.0, min(1.0, confidence)), 4)

    # --- SMC / structure risks ---
    if smc_ctx is not None:
        if smc_ctx.label == "PRICE_AT_SUPPLY":
            result.risks.append("Price at supply OB/FVG")
        elif smc_ctx.label == "PRICE_AT_DEMAND":
            result.risks.append("Price at demand OB/FVG — needs confirmation")
    if structure_label and "CHoCH" in structure_label:
        result.risks.append("Recent CHoCH — structure may be reversing")
    elif structure_label and "UNCLEAR" == structure_label:
        result.risks.append("Unclear market structure")

    # --- Drivers ---
    if metric_has := hasattr(metrics, "technical_score") and metrics.technical_score is not None:
        result.drivers.append("deterministic 10-factor score")
    result.drivers.append(f"data_quality={dq:.2f}")
    result.drivers.append(f"mtf_alignment={mtf_score:.2f}")
    result.drivers.append(f"regime={regime_label or 'N/A'}")

    return result
