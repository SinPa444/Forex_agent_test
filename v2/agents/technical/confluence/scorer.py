"""
agents/technical/confluence/scorer.py
=====================================
تجمیع امتیاز نهایی از ۱۰ فاکتور (semantics نسخه قبل):

  - اگر هیچ‌کدام از structure/smc_location محاسبه نشده باشند → None
    (دیتای ساختاری نامعتبر = score نامعتبر، حتی اگر فاکتورهای دیگر OK باشند)
  - وگرنه: مجموع وزن×امتیاز (None به‌عنوان 0) → clamp [-1, 1]
"""

from __future__ import annotations

from typing import Optional

from ..models import ComponentScores
from .weights import WEIGHTS


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def aggregate_score(components: ComponentScores) -> Optional[float]:
    """امتیاز نهایی technical score [-1, 1] یا None."""
    if components.structure is not None or components.smc_location is not None:
        raw = sum(
            WEIGHTS[name] * (getattr(components, name) or 0.0)
            for name in WEIGHTS
        )
        return _clamp(raw, -1.0, 1.0)
    return None
