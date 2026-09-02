"""
agents/technical/structure/swings.py
====================================
تشخیص Swing High/Low — پایه‌ی فاکتورهای Structure، OB، Liquidity و OTE.

بازساخت Phase 1: اگر swing_length=5 خطا بدهد، fallback به پیش‌فرض کتابخانه
در یک try/except جدا انجام می‌شود (در نسخه قبل، خطا در swing کل بلوک
Structure + SMC Location را می‌کشت — بهبود آگاهانه در مسیر exception).
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from smartmoneyconcepts import smc

logger = logging.getLogger(__name__)

DEFAULT_SWING_LENGTH = 5


def detect_swings(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    تشخیص swingها. خروجی: DataFrame swingهای smartmoneyconcepts یا None.
    """
    try:
        swings = smc.swing_highs_lows(df, swing_length=DEFAULT_SWING_LENGTH)
    except Exception as e:
        logger.warning(f"swing_highs_lows(swing_length={DEFAULT_SWING_LENGTH}) failed: {e}")
        swings = None

    if swings is None:
        try:
            swings = smc.swing_highs_lows(df)
        except Exception as e:
            logger.warning(f"swing_highs_lows(default) failed: {e}")
            swings = None

    return swings


def swing_count(swings: Optional[pd.DataFrame]) -> Optional[int]:
    """تعداد swingهای تشخیص‌داده‌شده (معیار خام کیفیت ساختار)."""
    if swings is None or swings.empty:
        return 0
    return int(len(swings))
