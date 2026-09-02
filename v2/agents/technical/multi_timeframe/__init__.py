"""
لایه Multi-Timeframe (phase 7 — بدون LLM).

از این بسته می‌توان مستقیم import کرد:
    from agents.technical.multi_timeframe import scan_timeframes, MTFMatrix

نکته ساختاری: این __init__ به‌صورت lazy (PEP 562) است تا import زیرمجموعه‌ی
`alignment` (که engine در لحظه import استفاده می‌کند) زنجیره‌ی دایره‌ای
engine → multi_timeframe → scanner → engine نسازد.
"""

from typing import Any

_ALIGNMENT_NAMES = {
    "compute_htf_bias", "compute_mtf_confluence", "HTF_WEIGHTS", "HTF_BIAS_DEADBAND",
}
_SCANNER_NAMES = {
    "DEFAULT_TIMEFRAMES", "MTFMatrix", "TIMEFRAME_ROLES", "TimeframeScore",
    "scan_timeframes",
}

__all__ = sorted(_ALIGNMENT_NAMES | _SCANNER_NAMES)


def __getattr__(name: str) -> Any:
    if name in _ALIGNMENT_NAMES:
        from . import alignment
        return getattr(alignment, name)
    if name in _SCANNER_NAMES:
        from . import scanner
        return getattr(scanner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
