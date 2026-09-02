"""
agents/technical/models.py
==========================
Pydantic Models موتور Technical (بازساخت Phase 1).

- ComponentScores: امتیاز ۱۰ فاکتور [-1, 1]؛ None = Not Evaluated
- TechnicalMetrics: داده‌های خام + امتیازهای مؤلفه + score/confidence نهایی

فیلد‌های Phase 1 (افزایشی، پیش‌فرض None):
  atr / structure_raw / level_distances / data_error /
  data_quality_json / provenance_json / engine_version
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ComponentScores(BaseModel):
    """Scores for each of the 10 technical factors [-1, 1]. None if not evaluated."""
    structure: Optional[float] = None
    trend: Optional[float] = None
    smc_location: Optional[float] = None
    mtf_confluence: Optional[float] = None
    momentum: Optional[float] = None
    volatility: Optional[float] = None
    price_action: Optional[float] = None
    liquidity_sweep: Optional[float] = None
    pdh_pdl: Optional[float] = None
    ote_zone: Optional[float] = None


class TechnicalMetrics(BaseModel):
    """Full raw technical data and component scores."""
    current_price: Optional[float] = None
    technical_score: Optional[float] = None  # Final aggregated score [-1, 1]
    technical_confidence: Optional[float] = None  # Reliability [0, 1]
    components: ComponentScores = Field(default_factory=ComponentScores)

    # Raw Data for LLM narrative
    trend_status: Optional[str] = None
    adx_value: Optional[float] = None
    recent_bos: Optional[str] = None
    active_bullish_ob: Optional[float] = None
    active_bearish_ob: Optional[float] = None
    active_bull_fvg_low: Optional[float] = None
    active_bull_fvg_high: Optional[float] = None
    active_bear_fvg_low: Optional[float] = None
    active_bear_fvg_high: Optional[float] = None
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    last_candle_type: Optional[str] = None
    htf_interval: Optional[str] = None
    htf_trend_status: Optional[str] = None
    liquidity_sweep_status: Optional[str] = None
    previous_day_high: Optional[float] = None
    previous_day_low: Optional[float] = None
    pdh_pdl_status: Optional[str] = None
    current_retracement: Optional[float] = None
    ote_status: Optional[str] = None
    chop_value: Optional[float] = None

    # --- Phase 1: فیلد‌های افزایشی (مقادیر خام جدید + حسابرسی) ---
    atr: Optional[float] = None
    structure_raw: Optional[Dict[str, Any]] = None       # BOS/CHoCH + age + sequence + swing count
    level_distances: Optional[Dict[str, Any]] = None     # فاصله٪ تا OB/FVG فعال
    data_error: Optional[str] = None                     # مثلاً "circuit_open"
    data_quality_json: Optional[str] = None              # DataQualityReport (JSON)
    provenance_json: Optional[str] = None                # FetchProvenance (JSON)
    engine_version: Optional[str] = None
