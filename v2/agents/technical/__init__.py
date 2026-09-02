"""
agents/technical — موتور تحلیل فنی (دeterministic + روایت LLM)

بازساخت Phase 1: ماژول‌بندی به‌جای monolith
  data/             → fetch + provenance + data quality + circuit breaker
  indicators/       → trend / momentum / volatility / price action
  structure/        → swings / BOS-CHoCH / FVG / Order Block / Liquidity
  levels/           → PDH/PDL / OTE
  multi_timeframe/  → اسکن ۷ TF + هم‌راستایی (بدون LLM)
  confluence/       → وزن‌ها + تجمیع score + confidence
  signals/          → direction از score + مدل‌های خروجی
  engine.py         → calculate_technical_metrics (API عمومی)
  analyzer.py       → TechnicalAgent (deterministic + LLM narrative + guard)

نکته سازگاری: technical_analyzer.py و mtf_scanner.py به‌عنوان فکس
import قدیمی‌ها باقی مانده‌اند.
"""

from agents.technical.analyzer import TechnicalAgent
from agents.technical.engine import calculate_technical_metrics
from agents.technical.models import ComponentScores, TechnicalMetrics
from agents.technical.multi_timeframe.scanner import (
    MTFMatrix,
    TimeframeScore,
    scan_timeframes,
)
from agents.technical.signals.report import LLMStrategy, TechnicalReport
from agents.technical.signals.technical_signal import (
    DIRECTION_DEADBAND,
    direction_from_score,
)

__all__ = [
    "TechnicalAgent",
    "calculate_technical_metrics",
    "ComponentScores",
    "TechnicalMetrics",
    "MTFMatrix",
    "TimeframeScore",
    "scan_timeframes",
    "LLMStrategy",
    "TechnicalReport",
    "DIRECTION_DEADBAND",
    "direction_from_score",
]
