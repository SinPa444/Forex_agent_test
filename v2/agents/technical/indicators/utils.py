"""
agents/technical/indicators/utils.py
====================================
Helpers مشترک برای خروجی pandas-ta.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def last_indicator_value(out) -> Optional[float]:
    """
    آخرین مقدار معتبر از خروجی pandas-ta.

    pandas-ta در دیتای کم‌عمق گاهی به‌جای Series یک DataFrame تمام-NaN
    برمی‌گرداند (مثلاً EMA200 روی HTF هفتگی با <۲۰۰ کندل) که .iloc[-1]
    آن Series می‌شود و pd.notna روی آن کرش می‌کند — این helper هر دو
    حالت را امن هندل می‌کند.
    """
    if out is None:
        return None
    if isinstance(out, pd.DataFrame):
        if out.empty:
            return None
        out = out.iloc[:, 0]
    val = out.iloc[-1]
    return float(val) if pd.notna(val) else None
