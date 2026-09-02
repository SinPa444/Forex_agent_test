"""
agents/technical/signals/technical_signal.py
============================================
تبدیل score → direction (Signal) — جدا از Confidence.

Phase 1 (weakness W2): deadband یکپارچه شد:
  نسخه قبل: TechnicalAgent ±0.1 | MTF Scanner ±0.15 (دو آستانه‌ی ناسازگار)
  Phase 1:   هر دو ±0.15 (DIRECTION_DEADBAND)

تفاوت رفتار فقط برای scoreهای (0.10, 0.15) و (-0.15, -0.10) است:
امتیاز ۰.۱۲ که قبلاً BULLISH می‌شد، حالا NEUTRAL می‌شود — مطابق deadband
یکپارچه‌شده؛ در test/technical/test_direction.py مستند شده است.
"""

from __future__ import annotations

from typing import Optional

# آستانه‌ی یکپارچه (semantics: |score| کمتر از این = NEUTRAL)
DIRECTION_DEADBAND = 0.15


def direction_from_score(score: Optional[float]) -> int:
    """
    score → direction:
      score > +deadband  → +1 (BULLISH)
      score < -deadband  → -1 (BEARISH)
      وگرنه (یا None)    →  0 (NEUTRAL)
    """
    if score is None:
        return 0
    if score > DIRECTION_DEADBAND:
        return 1
    if score < -DIRECTION_DEADBAND:
        return -1
    return 0
