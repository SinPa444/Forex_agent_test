"""
core/technical_config.py
========================
مخزن مرکزی پیکربندی Technical Engine.

هدف:
  - وزن‌ها، آستانه‌ها و تنظیمات رژیم/کانفلوئنس از کد جدا شوند.
  - پیش‌توانِ Phase P1 (Weight Calibration / Walk-forward) باشد.
  - هیچ وابستگی سنگینی نداشته باشد و سریع import شود.

تمامی وزن‌های ۱۰ فاکتور برابر مقادیر فعلی پروژه حفظ شده‌اند تا P0 رفتار
scoring را از نظر وزن‌ها تغییر ندهد. تنها تغییر رفتار در P0، «استفادهٔ واقعی از
CHoCH» در فاکتور structure است که طبق Change Plan باید انجام شود.
"""

from __future__ import annotations

# ===========================================================================
# وزن‌های ۱۰ فاکتور (همان مقادیر فعلی technical_analyzer.py)
# ===========================================================================

TECHNICAL_WEIGHTS: dict[str, float] = {
    "structure": 0.20,
    "smc_location": 0.15,
    "trend": 0.13,
    "liquidity_sweep": 0.12,
    "mtf_confluence": 0.10,
    "ote_zone": 0.10,
    "pdh_pdl": 0.08,
    "momentum": 0.06,
    "volatility": 0.04,
    "price_action": 0.02,
}


# ===========================================================================
# Direction / Score thresholds
# ===========================================================================

# جهت‌دار شدن یک تایم‌فریم بر اساس |score|
DIRECTION_DEADBAND: float = 0.15

# جهت‌دار شدن TechnicalReport فعلی (تحلیل تک‌تایم‌فریم)
TECH_REPORT_DIRECTION_THRESHOLD: float = 0.10

# Signal State خرد
SIGNAL_STATE_STRONG: float = 0.60
SIGNAL_STATE_BULLISH: float = 0.25
SIGNAL_STATE_WEAK: float = 0.10


# ===========================================================================
# Regime detection
# ===========================================================================

REGIME_ADX_TRENDING: float = 25.0
REGIME_ADX_RANGING: float = 20.0
REGIME_CHOP_TRENDING: float = 38.2
REGIME_CHOP_RANGING: float = 61.8

# ضریب ATR برای HIGH / LOW volatility نسبت به ATR median اخیر
ATR_HIGH_MULTIPLIER: float = 1.5
ATR_LOW_MULTIPLIER: float = 0.7

# Breakout detection
BREAKOUT_LOOKBACK_BARS: int = 20
BREAKOUT_BARS: int = 3
BREAKOUT_CONFIRM_BARS: int = 2


# ===========================================================================
# ATR
# ===========================================================================

ATR_PERIOD: int = 14
ATR_MEDIAN_WINDOW: int = 50


# ===========================================================================
# Confluence / Confidence
# ===========================================================================

# آستانهٔ توافق مؤلفه‌ها (هم‌جهت بودن)
CONFLUENCE_AGREEMENT_THRESHOLD: float = 0.60

# جریمه‌های عدم تطابق
MTF_CONFLICT_PENALTY: float = 0.10
REGIME_UNFAVORABLE_PENALTY: float = 0.05
DATA_QUALITY_MIN: float = 0.0

# سطح نصاب برای Confidence حداکثری
DATA_QUALITY_GOOD: float = 0.85


# ===========================================================================
# Data Quality
# ===========================================================================

DATA_QUALITY_MIN_BARS: int = 50
DATA_QUALITY_STALE_DAYS_WARN: int = 3
DATA_QUALITY_STALE_DAYS_BAD: int = 10
DATA_QUALITY_MIN_VOLUME_RATIO: float = 0.02


def get_weights() -> dict[str, float]:
    """کپی از وزن‌های فعلی (برای سازگاری با کدهای قدیمی)."""
    return dict(TECHNICAL_WEIGHTS)
