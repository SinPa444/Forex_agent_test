# backtest/config.py
"""
backtest/config.py
==================
تنظیمات بک‌تست فاز ۴ — هزینه‌های واقعی معاملاتی per-symbol.

مدل هزینه:
  cost_per_side = (spread_pips + slippage_pips) * pip_size / price

  - spread و slippage به‌صورت «per side» اعمال می‌شوند (هم ورود هم خروج)
  - slippage در روزهای ایونت High impact در EVENT_SLIPPAGE_MULTIPLIER ضرب می‌شود
  - این مدل عمداً کمی محافظه‌کار (بدگمان) است تا نتیجه بک‌تست خوش‌بینانه نشود
"""

from __future__ import annotations

# --- نگاشت ارز/دارایی به تیکر yfinance (همان نگاشت سیستم اصلی) ---
TICKER_MAP: dict[str, str] = {
    "USD": "DX-Y.NYB",
    "EUR": "EURUSD=X",
    "GBP": "GBPUSD=X",
    "JPY": "USDJPY=X",
    "CHF": "USDCHF=X",
    "AUD": "AUDUSD=X",
    "CAD": "USDCAD=X",
    "NZD": "NZDUSD=X",
    "XAU": "GC=F",
    "OIL": "CL=F",
}

# --- هزینه معاملاتی per symbol ---
# pip_size: اندازه یک پیپ به واحد قیمت
# spread_pips: اسپرد معمول (round number، کمی بدگمانانه)
# slippage_pips: اسلیپیج پایه per side
SYMBOL_COSTS: dict[str, dict[str, float]] = {
    "EURUSD=X": {"pip_size": 0.0001, "spread_pips": 0.6, "slippage_pips": 0.4},
    "GBPUSD=X": {"pip_size": 0.0001, "spread_pips": 0.9, "slippage_pips": 0.5},
    "USDJPY=X": {"pip_size": 0.01,   "spread_pips": 0.8, "slippage_pips": 0.5},
    "USDCHF=X": {"pip_size": 0.0001, "spread_pips": 1.0, "slippage_pips": 0.6},
    "AUDUSD=X": {"pip_size": 0.0001, "spread_pips": 0.8, "slippage_pips": 0.5},
    "USDCAD=X": {"pip_size": 0.0001, "spread_pips": 1.0, "slippage_pips": 0.6},
    "NZDUSD=X": {"pip_size": 0.0001, "spread_pips": 1.2, "slippage_pips": 0.7},
    "DX-Y.NYB": {"pip_size": 0.01,   "spread_pips": 1.5, "slippage_pips": 1.0},
    "GC=F":     {"pip_size": 0.1,    "spread_pips": 3.5, "slippage_pips": 1.5},
    "CL=F":     {"pip_size": 0.01,   "spread_pips": 3.0, "slippage_pips": 2.0},
}

DEFAULT_COSTS: dict[str, float] = {
    "pip_size": 0.0001, "spread_pips": 1.0, "slippage_pips": 0.5,
}

# ضریب اسلیپیج در روزهای ایونت High impact (نقدینگی کم، گپ، widen شدن اسپرد)
EVENT_SLIPPAGE_MULTIPLIER: float = 3.0

# --- Annualization (Phase 1 / W5) ---
# تعداد کندل در سال برای هر اینترول — کنوانسیون یکپارچه ۲۵۲ روز معاملاتی:
# bars_per_year = 252 × (کندل در روز)
# Sharpe/Sortino/CAGR با bars-per-year متناظر سالانه می‌شوند،
# نه 252 برای همه تایم‌فریم‌ها (باگ W5 نسخه قبل).
BARS_PER_YEAR: dict[str, float] = {
    "1d": 252.0,     # 252 * 1
    "1wk": 52.0,     # 252 / 5
    "4h": 1512.0,    # 252 * 6
    "2h": 3024.0,    # 252 * 12
    "60m": 6048.0,   # 252 * 24
    "1h": 6048.0,
    "30m": 12096.0,  # 252 * 48
    "15m": 24192.0,  # 252 * 96
}
DEFAULT_BARS_PER_YEAR: float = 252.0

# --- قوانین تبدیل سیگنال به پوزیشن ---
ENTRY_THRESHOLD: float = 0.20   # |score| >= این → ورود
EXIT_BAND: float = 0.05         # |score| < این → خروج (سیگنال مرده)
MIN_CONFIDENCE: float = 0.40    # confidence کمتر از این → بدون معامله
MAX_HOLDING_BARS: int = 20      # سقف نگهداری پوزیشن (bars)

# --- تقسیم In-Sample / Out-of-Sample ---
OOS_RATIO: float = 0.30         # ۳۰٪ انتهای داده = out-of-sample

# --- معیارهای قبولی (همان استانداردهای فاز ۳) ---
PASS_SHARPE: float = 1.0
PASS_MAX_DD_PCT: float = 15.0
PASS_PROFIT_FACTOR: float = 1.5

# warmup: حداقل کندل قبل از شروع سیگنال‌دهی (EMA200 روی D1 نیاز دارد)
WARMUP_BARS: int = 210
