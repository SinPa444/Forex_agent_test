"""
agents/technical/mtf_scanner.py — فکس سازگاری (Phase 1)
========================================================
پیاده‌سازی: agents/technical/multi_timeframe/ (scanner.py + alignment.py).

    from agents.technical.mtf_scanner import scan_timeframes
    m = scan_timeframes("EURUSD=X")
"""

from agents.technical.multi_timeframe.alignment import (
    HTF_BIAS_DEADBAND,
    HTF_WEIGHTS,
    compute_htf_bias,
    compute_mtf_confluence,
)
from agents.technical.multi_timeframe.scanner import (
    DEFAULT_TIMEFRAMES,
    MTFMatrix,
    TIMEFRAME_ROLES,
    TimeframeScore,
    _score_one,
    scan_timeframes,
)
from agents.technical.signals.technical_signal import DIRECTION_DEADBAND

__all__ = [
    "scan_timeframes",
    "MTFMatrix",
    "TimeframeScore",
    "DEFAULT_TIMEFRAMES",
    "TIMEFRAME_ROLES",
    "DIRECTION_DEADBAND",
    "HTF_WEIGHTS",
    "HTF_BIAS_DEADBAND",
    "compute_htf_bias",
    "compute_mtf_confluence",
    "_score_one",
]
