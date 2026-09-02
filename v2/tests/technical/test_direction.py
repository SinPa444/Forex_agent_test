"""
tests/technical/test_direction.py
=================================
تست‌های deadband یکپارچه Phase 1 (weakness W2).

مستندسازی اختلاف آگاهانه:
  نسخه قبل: TechnicalAgent با deadband ±0.1 | MTF Scanner با ±0.15
  Phase 1:   هر دو DIRECTION_DEADBAND = ±0.15

اختلاف رفتار فقط برای score در بازه‌های (0.10, 0.15) و (-0.15, -0.10):
مثلاً score=0.12 در نسخه قدیمی BULLISH بود، حالا NEUTRAL است —
مطابق deadband یکپارچه‌شده (تصویب‌شده در Phase 1).
"""

from __future__ import annotations

import pytest

from agents.technical.signals.technical_signal import (
    DIRECTION_DEADBAND, direction_from_score,
)


class TestDeadband:
    def test_value(self):
        assert DIRECTION_DEADBAND == 0.15

    @pytest.mark.parametrize(
        "score, expected",
        [
            (0.5, 1), (-0.5, -1),
            (0.151, 1), (-0.151, -1),
            (0.15, 0),   # مرز: دقیقاً deadband → NEUTRAL (مطابق score > band)
            (-0.15, 0),
            (0.0, 0),
            (0.12, 0),   # ← اختلاف آگاهانه با نسخه قبل (که 0.1 را می‌گرفت)
            (-0.12, 0),
            (0.10, 0),
            (None, 0),
        ],
    )
    def test_direction(self, score, expected):
        assert direction_from_score(score) == expected


class TestW2DocumentedDifference:
    """
    بازه‌ی (0.10, 0.15): نسخه قبل BULLISH می‌شد، Phase 1 NEUTRAL.
    این تست اختلاف را برای همیشه مستند می‌کند (regression guard).
    """

    def test_band_between_old_and_new(self):
        assert direction_from_score(0.12) == 0      # Phase 1
        assert 0.12 > 0.1                            # قدیم BULLISH بود

    def test_scanner_and_agent_share_deadband(self):
        from agents.technical.multi_timeframe.scanner import (
            DIRECTION_DEADBAND as scanner_db,
        )
        assert scanner_db == DIRECTION_DEADBAND
