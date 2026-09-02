"""
agents/technical/technical_analyzer.py — فکس سازگاری (Phase 1)
==============================================================
پیاده‌سازی به ماژول‌های زیربسته‌ها تقسیم شد (data/ indicators/ structure/
levels/ confluence/ signals/ multi_timeframe/). همه نام‌هایی که قبلاً از
این ماژول import می‌شدند بدون تغییر کار می‌کنند:

    from agents.technical.technical_analyzer import TechnicalAgent
    from agents.technical.technical_analyzer import calculate_technical_metrics
    from agents.technical.technical_analyzer import TIMEFRAME_MAP, HTF_MAP

نکته monkeypatch: موتور از `agents.technical.data.market_data
.fetch_price_history` استفاده می‌کند؛ backtest (walkforward) آن را پچ می‌کند.
آلیاس `_fetch_price_history` اینجا فقط برای خواندن است.
"""

from agents.technical.analyzer import TechnicalAgent
from agents.technical.data.market_data import (
    _RESAMPLE_FROM_1H,
    DATA_SOURCE,
    HTF_MAP,
    TIMEFRAME_MAP,
    fetch_price_history,
    fetch_price_history as _fetch_price_history,  # آلیاس سازگاری (فقط خواندن)
)
from agents.technical.engine import (
    MARKET_DATA_BREAKER,
    MIN_BARS,
    calculate_technical_metrics,
)
from agents.technical.models import ComponentScores, TechnicalMetrics
from agents.technical.signals.report import LLMStrategy, TechnicalReport

__all__ = [
    "TechnicalAgent",
    "calculate_technical_metrics",
    "TechnicalMetrics",
    "ComponentScores",
    "TechnicalReport",
    "LLMStrategy",
    "TIMEFRAME_MAP",
    "HTF_MAP",
    "_RESAMPLE_FROM_1H",
    "DATA_SOURCE",
    "fetch_price_history",
    "_fetch_price_history",
    "MARKET_DATA_BREAKER",
    "MIN_BARS",
]
