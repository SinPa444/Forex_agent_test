"""
agents/technical/confluence/confidence.py
=========================================
محاسبه Confidence [0, 1] — مستقل از Signal (قاعده طراحی پروژه).

semantics نسخه قبل (بدون تغییر):
  data_q     = 0.2 × (0.5×has_trend + 0.5×has_smc)
  setup_q    = 0.3 × (0.5×has_ob + 0.5×has_fvg)
  agreement  = 0.5 × (max(تعداد مثبت‌ها, تعداد منفی‌ها) / 3)
               روی ترازهای structure/trend/momentum
  confidence = clamp(data_q + setup_q + agreement, 0, 1)
"""

from __future__ import annotations

from typing import Optional

from ..models import ComponentScores

AGREEMENT_BASE = 3  # تعداد فاکتورهای ترازدهنده


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_confidence(
    components: ComponentScores,
    has_ob: bool,
    has_fvg: bool,
) -> float:
    """Confidence نهایی [0, 1] بر پایه کیفیت داده و هم‌راستایی ترازها."""
    # --- Data Quality ---
    data_q = 0.0
    if components.trend is not None:
        data_q += 0.5
    if components.smc_location is not None:
        data_q += 0.5
    data_q *= 0.2

    # --- Setup Quality ---
    setup_q = 0.0
    if has_ob:
        setup_q += 0.5
    if has_fvg:
        setup_q += 0.5
    setup_q *= 0.3

    # --- Agreement ---
    signs: list[Optional[float]] = [
        components.structure,
        components.trend,
        components.momentum,
    ]
    pos_count = sum(1 for s in signs if s is not None and s > 0)
    neg_count = sum(1 for s in signs if s is not None and s < 0)
    agreement = (max(pos_count, neg_count) / AGREEMENT_BASE) * 0.5

    return _clamp(data_q + setup_q + agreement, 0.0, 1.0)
