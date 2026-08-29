# data_fetcher.py
"""
data_fetcher.py
===============
دریافت و محاسبه تمام داده‌های کمّی بازار برای ساخت MarketContext.

تغییرات نسبت به نسخه قبلی:
  - upgrade کامل fetch_historical_surprise_std با استفاده واقعی از DB
  - اضافه شدن canonical event matching
  - window-based filtering با auto-expand
  - per-category fallback intelligent
  - HistoricalStdResult با metadata برای audit
  - logging شفاف از منبع هر std
  - skip بی‌صدا برای عناوین مصنوعی (Macro News Digest)
  - تیر fallback جدید: previous-based (actual - previous)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from core.database import EconomicEventHistory, SessionLocal
from core.models import MarketContext

logger = logging.getLogger(__name__)


# ===========================================================================
# CONSTANTS — yfinance configuration
# ===========================================================================

IMPLIED_VOLATILITY_INDEX: dict[str, list[str]] = {
    "USD": ["^VIX"],
    "EUR": ["^VIX"],
    "GBP": ["^VIX"],
    "JPY": ["^VIX"],
    "CHF": ["^VIX"],
    "AUD": ["^VIX"],
    "CAD": ["^VIX"],
    "NZD": ["^VIX"],
    "XAU": ["^GVZ", "^VIX"],
    "OIL": ["^OVX", "^VIX"],
    "DEFAULT": ["^VIX"],
}

YIELD_TICKERS: dict[str, dict[str, str]] = {
    "USD": {"10y": "^TNX", "2y": "^IRX"},
    "EUR": {"10y": "^TNX", "2y": "^IRX"},
    "GBP": {"10y": "^TNX", "2y": "^IRX"},
    "DEFAULT": {"10y": "^TNX", "2y": "^IRX"},
}

TICKER_TO_CURRENCY: dict[str, str] = {
    "EURUSD=X": "EUR",
    "GBPUSD=X": "GBP",
    "USDJPY=X": "JPY",
    "USDCHF=X": "CHF",
    "AUDUSD=X": "AUD",
    "USDCAD=X": "CAD",
    "NZDUSD=X": "NZD",
    "DX-Y.NYB": "USD",
    "DX=F":     "USD",
    "GC=F":     "XAU",
    "XAUUSD=X": "XAU",
    "CL=F":     "OIL",
    "BZ=F":     "OIL",
}

HISTORY_PERIOD: str = "60d"
TRADING_DAYS_PER_YEAR: int = 252
MAX_IV_HV_RATIO: float = 3.0
MAX_ATR_RATIO: float = 3.0


# ===========================================================================
# CONSTANTS — Historical std configuration
# ===========================================================================

# پیش‌فرض window زمانی برای جستجوی سورپرایزهای تاریخی
DEFAULT_WINDOW_YEARS: int = 5

# حداقل observation برای محاسبه std معتبر
DEFAULT_MIN_OBSERVATIONS: int = 10

# اگر در window پیش‌فرض observation کافی نبود، تا این حد expand کن
MAX_WINDOW_YEARS: int = 15

# اگر بعد از همه expand ها هم observation کافی نبود
ABSOLUTE_MIN_OBSERVATIONS: int = 5

# Per-category fallback std (بر اساس تحلیل واقعی DB پروژه)
CATEGORY_DEFAULT_STD: dict[str, float] = {
    "CPI": 0.15,
    "Inflation": 0.15,
    "Non-Farm Payrolls": 80_000,
    "Employment": 50_000,
    "GDP": 0.45,
    "PMI": 1.50,
    "Manufacturing": 2.50,
    "Retail Sales": 1.00,
    "Interest Rate Decision": 0.05,
    "Unemployment": 0.20,
    "Trade Balance": 5_000_000_000,
    "Housing": 0.50,
    "Consumer Confidence": 2.00,
    "Central Bank Commentary": 0.20,
}

# مقدار absolute fallback آخر اگر هیچ چیز نداشتیم
ABSOLUTE_FALLBACK_STD: float = 0.15


# ===========================================================================
# DATA CLASSES
# ===========================================================================

@dataclass
class HistoricalStdResult:
    """
    نتیجه محاسبه historical_std با metadata برای audit.

    source:
      "computed_canonical"      — از DB با canonical matching (actual - forecast)
      "computed_loose"          — از DB با matching ساده‌تر
      "computed_previous_based" — پروکسی: انحراف معیار (actual - previous)
      "synthetic_digest"        — عنوان مصنوعی دایجست اخبار (بدون کوئری)
      "category_fallback"       — از CATEGORY_DEFAULT_STD
      "absolute_fallback"       — از ABSOLUTE_FALLBACK_STD
      "no_data"                 — هیچ چیز نداشتیم (value=None)
    """
    value: Optional[float]
    source: str
    observations: int
    window_years: Optional[int]
    canonical_key: Optional[str]
    matched_titles: list[str]
    outliers_removed: int = 0

    def is_reliable(self) -> bool:
        """آیا این std بر اساس داده واقعی محاسبه شده؟"""
        return self.source.startswith("computed")


# ===========================================================================
# CANONICAL EVENT MATCHING
# ===========================================================================

# نگاشت کشور به prefix برای canonical key
COUNTRY_PREFIX: dict[str, str] = {
    "USD": "US",
    "EUR": "EU",  # یا کشور خاص اگر در title باشد
    "GBP": "UK",
    "JPY": "JP",
    "CHF": "CH",
    "AUD": "AU",
    "CAD": "CA",
    "NZD": "NZ",
    "CNY": "CN",
}

# country override بر اساس کلمات کلیدی در title (برای EUR)
EUR_COUNTRY_KEYWORDS: dict[str, str] = {
    "german": "DE",
    "germany": "DE",
    "french": "FR",
    "france": "FR",
    "italian": "IT",
    "italy": "IT",
    "spanish": "ES",
    "spain": "ES",
}

# نگاشت keyword به indicator code
# (ترتیب مهم است — اول specific‌ها چک می‌شوند)
INDICATOR_KEYWORDS: list[tuple[str, str]] = [
    # NFP — اول specific
    ("adp non-farm", "NFP_ADP"),
    ("adp nonfarm", "NFP_ADP"),
    ("non-farm employment", "NFP"),
    ("nonfarm employment", "NFP"),

    # Productivity (مهم: قبل از employment generic)
    ("nonfarm productivity", "PRODUCTIVITY"),
    ("non-farm productivity", "PRODUCTIVITY"),

    # CPI — order matters! specific patterns first
    ("tokyo core cpi", "CPI_TOKYO_CORE"),
    ("boj core cpi", "CPI_BOJ_CORE"),
    ("national core cpi", "CPI_NATIONAL_CORE"),
    ("cpi flash estimate", "CPI_FLASH"),
    ("prelim cpi", "CPI_PRELIM"),
    ("final cpi", "CPI_FINAL"),
    ("core cpi", "CPI_CORE"),
    ("cpi", "CPI"),
    ("consumer price", "CPI"),

    # Inflation
    ("pce", "PCE"),
    ("ppi", "PPI"),
    ("inflation rate", "INFLATION"),

    # GDP
    ("advance gdp", "GDP_ADVANCE"),
    ("prelim gdp", "GDP_PRELIM"),
    ("final gdp", "GDP_FINAL"),
    ("revised gdp", "GDP_REVISED"),
    ("second estimate gdp", "GDP_SECOND"),
    ("flash gdp", "GDP_FLASH"),
    ("gdp price index", "GDP_PRICE_INDEX"),
    ("gdp", "GDP"),

    # Rate decisions
    ("federal funds rate", "RATE"),
    ("main refinancing rate", "RATE"),
    ("official bank rate", "RATE"),
    ("cash rate", "RATE"),
    ("overnight rate", "RATE"),
    ("policy rate", "RATE"),
    ("interest rate", "RATE"),

    # Employment
    ("unemployment rate", "UNEMPLOYMENT"),
    ("unemployment change", "UNEMPLOYMENT_CHANGE"),
    ("employment change", "EMPLOYMENT_CHANGE"),
    ("jobless claims", "JOBLESS_CLAIMS"),
    ("unemployment claims", "JOBLESS_CLAIMS"),
    ("initial jobless", "JOBLESS_CLAIMS"),

    # Retail Sales
    ("core retail sales", "RETAIL_SALES_CORE"),
    ("retail sales", "RETAIL_SALES"),

    # PMI / ISM
    ("ism manufacturing prices", "ISM_MFG_PRICES"),
    ("ism manufacturing", "ISM_MFG"),
    ("ism services", "ISM_SVC"),
    ("ism non-manufacturing", "ISM_SVC"),
    ("final manufacturing pmi", "PMI_MFG_FINAL"),
    ("flash manufacturing pmi", "PMI_MFG_FLASH"),
    ("manufacturing pmi", "PMI_MFG"),
    ("final services pmi", "PMI_SVC_FINAL"),
    ("flash services pmi", "PMI_SVC_FLASH"),
    ("services pmi", "PMI_SVC"),
    ("construction pmi", "PMI_CONSTRUCTION"),
    ("pmi", "PMI"),

    # Period suffixes (after main indicator)
    # این‌ها نقش suffix دارند نه indicator اصلی

    # Other
    ("trade balance", "TRADE_BALANCE"),
    ("current account", "CURRENT_ACCOUNT"),
    ("industrial production", "INDUSTRIAL_PROD"),
    ("factory orders", "FACTORY_ORDERS"),
    ("consumer confidence", "CONSUMER_CONFIDENCE"),
    ("consumer sentiment", "CONSUMER_SENTIMENT"),
    ("building permits", "BUILDING_PERMITS"),
    ("housing starts", "HOUSING_STARTS"),
    ("home sales", "HOME_SALES"),
    ("durable goods", "DURABLE_GOODS"),
]

# period suffix patterns
PERIOD_SUFFIXES: list[tuple[str, str]] = [
    ("y/y", "YY"),
    ("yoy", "YY"),
    ("year-over-year", "YY"),
    ("m/m", "MM"),
    ("mom", "MM"),
    ("month-over-month", "MM"),
    ("q/q", "QQ"),
    ("qoq", "QQ"),
    ("quarter-over-quarter", "QQ"),
]


def _canonicalize_event_title(
    title: str,
    currency: Optional[str] = None,
) -> Optional[str]:
    """
    تبدیل title به canonical event key.

    مثال:
        "Non-Farm Employment Change", "USD"      → "US_NFP"
        "ADP Non-Farm Employment Change", "USD"  → "US_NFP_ADP"
        "CPI m/m", "USD"                          → "US_CPI_MM"
        "Core CPI m/m", "USD"                     → "US_CPI_CORE_MM"
        "German Prelim CPI m/m", "EUR"            → "DE_CPI_PRELIM_MM"
        "Federal Funds Rate", "USD"               → "US_RATE"
        "Main Refinancing Rate", "EUR"            → "EU_RATE"
        "Italian Manufacturing PMI", "EUR"        → "IT_PMI_MFG"

    Returns:
        canonical key یا None اگر نتوانیم match کنیم
    """
    if not title:
        return None

    title_lower = title.lower().strip()

    # ---- country prefix ----
    country = None
    if currency:
        country = COUNTRY_PREFIX.get(currency.upper(), currency.upper())

        # EUR override: country-specific events
        if currency.upper() == "EUR":
            for keyword, country_code in EUR_COUNTRY_KEYWORDS.items():
                if keyword in title_lower:
                    country = country_code
                    break

    # ---- indicator matching ----
    indicator = None
    for keyword, indicator_code in INDICATOR_KEYWORDS:
        if keyword in title_lower:
            indicator = indicator_code
            break

    if not indicator:
        # نتوانستیم indicator را تشخیص دهیم
        return None

    # ---- period suffix ----
    period = None
    for suffix, code in PERIOD_SUFFIXES:
        if suffix in title_lower:
            period = code
            break

    # ---- build canonical key ----
    parts = [country] if country else []
    parts.append(indicator)
    if period:
        parts.append(period)

    return "_".join(p for p in parts if p)


def _category_from_canonical_key(canonical_key: str) -> str:
    """
    استخراج category از canonical key.
    برای fallback به CATEGORY_DEFAULT_STD استفاده می‌شود.
    """
    if not canonical_key:
        return "Default"

    key_upper = canonical_key.upper()

    if "NFP" in key_upper:
        return "Non-Farm Payrolls"
    if "CPI" in key_upper:
        return "CPI"
    if "PCE" in key_upper or "PPI" in key_upper or "INFLATION" in key_upper:
        return "Inflation"
    if "GDP" in key_upper:
        return "GDP"
    if "RATE" in key_upper:
        return "Interest Rate Decision"
    if "UNEMPLOYMENT" in key_upper:
        return "Unemployment"
    if "EMPLOYMENT" in key_upper or "JOBLESS" in key_upper:
        return "Employment"
    if "RETAIL_SALES" in key_upper:
        return "Retail Sales"
    if "PMI" in key_upper or "ISM" in key_upper:
        return "PMI"
    if "MANUFACTURING" in key_upper or "INDUSTRIAL" in key_upper:
        return "Manufacturing"
    if "TRADE_BALANCE" in key_upper:
        return "Trade Balance"
    if "HOUSING" in key_upper or "HOME_SALES" in key_upper or "BUILDING" in key_upper:
        return "Housing"
    if "CONFIDENCE" in key_upper or "SENTIMENT" in key_upper:
        return "Consumer Confidence"

    return "Default"


# ===========================================================================
# YFINANCE DATA LAYER
# ===========================================================================

def _download_single(ticker: str, period: str = "5d") -> Optional[pd.DataFrame]:
    """دانلود امن یک ticker از yfinance."""
    try:
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if data is None or data.empty:
            logger.warning("yfinance داده‌ای برای %s برنگرداند", ticker)
            return None
        return data
    except Exception as exc:
        logger.error("خطا در دانلود %s: %s", ticker, exc)
        return None


def _get_last_close(data: Optional[pd.DataFrame]) -> Optional[float]:
    """آخرین مقدار Close را از DataFrame می‌گیرد."""
    if data is None or data.empty:
        return None
    try:
        close_series = data["Close"].dropna()
        if close_series.empty:
            return None
        last_value = close_series.iloc[-1]
        if hasattr(last_value, "iloc"):
            last_value = last_value.iloc[0]
        return float(last_value)
    except (IndexError, TypeError, ValueError) as exc:
        logger.error("خطا در خواندن آخرین Close: %s", exc)
        return None


def fetch_price_history(ticker: str) -> Optional[pd.DataFrame]:
    """تاریخچه ۶۰ روزه قیمت."""
    return _download_single(ticker, period=HISTORY_PERIOD)


def calculate_atr(
    price_data: Optional[pd.DataFrame],
    short_window: int = 3,
    long_window: int = 14,
) -> tuple[Optional[float], Optional[float]]:
    """محاسبه ATR برای دو بازه."""
    if price_data is None:
        return None, None

    if len(price_data) < long_window + 2:
        return None, None

    try:
        high = price_data["High"]
        low = price_data["Low"]
        close = price_data["Close"]

        if isinstance(high.columns if hasattr(high, "columns") else None, pd.MultiIndex):
            high = high.iloc[:, 0]
            low = low.iloc[:, 0]
            close = close.iloc[:, 0]

        close_prev = close.shift(1)
        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).dropna()

        if len(true_range) < long_window:
            return None, None

        atr_current = float(true_range.tail(short_window).mean())
        atr_baseline = float(true_range.tail(long_window).mean())
        return atr_current, atr_baseline

    except (KeyError, TypeError, ValueError) as exc:
        logger.error("خطا در محاسبه ATR: %s", exc)
        return None, None


def calculate_historical_volatility(
    price_data: Optional[pd.DataFrame],
    window: int = 30,
) -> Optional[float]:
    """نوسان تاریخی سالانه‌شده."""
    if price_data is None or len(price_data) < window + 1:
        return None

    try:
        close = price_data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        log_returns = np.log(close / close.shift(1)).dropna()
        if len(log_returns) < window:
            return None

        recent_returns = log_returns.tail(window)
        daily_std = float(np.std(recent_returns.values, ddof=1))
        return daily_std * math.sqrt(TRADING_DAYS_PER_YEAR)

    except (KeyError, TypeError, ValueError) as exc:
        logger.error("خطا در محاسبه HV: %s", exc)
        return None


def fetch_implied_volatility(currency: str) -> Optional[float]:
    """دریافت IV از شاخص‌های نوسان ضمنی."""
    candidates = IMPLIED_VOLATILITY_INDEX.get(
        currency, IMPLIED_VOLATILITY_INDEX["DEFAULT"]
    )
    for iv_ticker in candidates:
        data = _download_single(iv_ticker, period="5d")
        iv_value = _get_last_close(data)
        if iv_value is not None:
            return iv_value
    return None


def fetch_yield_spread(currency: str) -> Optional[float]:
    """محاسبه اسپرد بازده."""
    config = YIELD_TICKERS.get(currency, YIELD_TICKERS["DEFAULT"])
    data_10y = _download_single(config["10y"], period="5d")
    data_2y = _download_single(config["2y"], period="5d")

    if data_10y is None or data_2y is None:
        return None

    yield_10y = _get_last_close(data_10y)
    yield_2y = _get_last_close(data_2y)

    if yield_10y is None or yield_2y is None:
        return None

    return yield_10y - yield_2y


# ===========================================================================
# OUTLIER FILTERING
# ===========================================================================

def _filter_outliers_iqr(
    values: list[float],
    iqr_multiplier: float = 1.5,
) -> tuple[list[float], int]:
    """
    حذف outlier ها با روش IQR (Interquartile Range).

    رکوردهایی که خارج از Q1 - k*IQR و Q3 + k*IQR هستند حذف می‌شوند.
    این روش برای حذف COVID outlier ها و سایر regime-change انومالی‌ها
    استفاده می‌شود.

    Args:
        values: لیست مقادیر سورپرایز
        iqr_multiplier: ضریب IQR
                        1.5 = استاندارد (پیش‌فرض)
                        2.5 = liberal
                        3.0 = خیلی liberal

    Returns:
        (filtered_values, number_of_outliers_removed)
    """
    if len(values) < 4:
        return values, 0

    arr = np.array(values)
    q1 = np.percentile(arr, 25)
    q3 = np.percentile(arr, 75)
    iqr = q3 - q1

    if iqr == 0:
        return values, 0

    lower_bound = q1 - iqr_multiplier * iqr
    upper_bound = q3 + iqr_multiplier * iqr

    mask = (arr >= lower_bound) & (arr <= upper_bound)
    filtered = arr[mask].tolist()
    outliers_count = len(values) - len(filtered)

    return filtered, outliers_count


def _compute_robust_std(
    surprises: list[float],
    min_after_filter: int = ABSOLUTE_MIN_OBSERVATIONS,
) -> tuple[float, int]:
    """
    محاسبه std با outlier filtering.

    اگر بعد از فیلتر کمتر از min_after_filter باقی ماند،
    از همه داده‌ها استفاده می‌کند (بدون فیلتر).

    Returns:
        (std_value, outliers_removed_count)
    """
    if len(surprises) < 2:
        return 0.0, 0

    filtered, outliers_removed = _filter_outliers_iqr(surprises)

    # اگر بعد از فیلتر داده خیلی کم شد، از همه استفاده کن
    if len(filtered) < min_after_filter:
        logger.debug(
            "Skipping outlier filter: too few records would remain "
            "(%d < %d)",
            len(filtered), min_after_filter,
        )
        return float(np.std(surprises, ddof=1)), 0

    std_val = float(np.std(filtered, ddof=1))
    return std_val, outliers_removed



# ===========================================================================
# HISTORICAL STD — UPGRADED
# ===========================================================================

def _get_max_date_in_db(session) -> Optional[datetime]:
    """آخرین تاریخ event در DB (برای window نسبت به این)."""
    try:
        result = session.query(func.max(EconomicEventHistory.date)).scalar()
        return result
    except SQLAlchemyError as exc:
        logger.error("خطا در خواندن max date: %s", exc)
        return None


def _query_surprises_by_canonical(
    session,
    canonical_key: str,
    currency: str,
    window_years: Optional[int],
    reference_date: Optional[datetime],
) -> tuple[list[float], list[str]]:
    """
    Query سورپرایزها بر اساس canonical key.

    استراتژی matching:
      1. canonical key را به اجزایش بشکن
      2. titleهای محتمل را با ilike پیدا کن
      3. canonicalize هر title برای تأیید
      4. فقط title هایی که canonical key شان match می‌شود را نگه دار

    Returns:
        (surprises, matched_titles)
    """
    # بخش‌های canonical key (مثلاً ["US", "CPI", "MM"])
    parts = canonical_key.split("_")
    if not parts:
        return [], []

    # query base
    query = session.query(
        EconomicEventHistory.actual,
        EconomicEventHistory.forecast,
        EconomicEventHistory.title,
    ).filter(
        EconomicEventHistory.currency == currency.upper(),
        EconomicEventHistory.actual.isnot(None),
        EconomicEventHistory.forecast.isnot(None),
    )

    # window filter
    if window_years is not None and reference_date is not None:
        start_date = reference_date - timedelta(days=window_years * 365)
        query = query.filter(EconomicEventHistory.date >= start_date)

    # title filter — حداقل یکی از part های مهم
    # part های اول معمولاً country prefix هستند، skip می‌کنیم
    # part های indicator و period را برای ilike استفاده می‌کنیم
    indicator_parts = [p for p in parts[1:] if len(p) >= 2]

    # خواندن همه و filter در پایتون
    records = query.all()

    matched_surprises: list[float] = []
    matched_titles_set: set[str] = set()

    for actual, forecast, title in records:
        # canonicalize این رکورد
        record_canonical = _canonicalize_event_title(title, currency)
        if record_canonical == canonical_key:
            try:
                surprise = float(actual) - float(forecast)
                matched_surprises.append(surprise)
                matched_titles_set.add(title)
            except (TypeError, ValueError):
                continue

    return matched_surprises, sorted(matched_titles_set)


def _query_changes_by_canonical(
    session,
    canonical_key: str,
    currency: str,
    window_years: Optional[int],
    reference_date: Optional[datetime],
) -> tuple[list[float], list[str]]:
    """
    Fallback tier: تغییرات شاخص (actual - previous) به‌جای سورپرایز.

    وقتی داده forecast کافی نداریم، نوسان خودِ شاخص (actual منهای مقدار
    قبلی) تخمین معقولی از مقیاس نوسان آن می‌دهد. این tier پروکسی است و
    source آن به‌صورت جداگانه لاگ می‌شود تا با سورپرایز واقعی قاطی نشود.

    Returns:
        (changes, matched_titles)
    """
    query = session.query(
        EconomicEventHistory.actual,
        EconomicEventHistory.previous,
        EconomicEventHistory.title,
    ).filter(
        EconomicEventHistory.currency == currency.upper(),
        EconomicEventHistory.actual.isnot(None),
        EconomicEventHistory.previous.isnot(None),
    )

    if window_years is not None and reference_date is not None:
        start_date = reference_date - timedelta(days=window_years * 365)
        query = query.filter(EconomicEventHistory.date >= start_date)

    records = query.all()

    matched_changes: list[float] = []
    matched_titles_set: set[str] = set()

    for actual, previous, title in records:
        record_canonical = _canonicalize_event_title(title, currency)
        if record_canonical == canonical_key:
            try:
                matched_changes.append(float(actual) - float(previous))
                matched_titles_set.add(title)
            except (TypeError, ValueError):
                continue

    return matched_changes, sorted(matched_titles_set)


def _query_surprises_by_loose_match(
    session,
    event_title: str,
    currency: Optional[str],
    window_years: Optional[int],
    reference_date: Optional[datetime],
) -> tuple[list[float], list[str]]:
    """
    Loose matching fallback — فقط title ilike می‌زند.
    این کمتر دقیق است ولی برای event های ناشناخته بهتر از هیچ است.
    """
    query = session.query(
        EconomicEventHistory.actual,
        EconomicEventHistory.forecast,
        EconomicEventHistory.title,
    ).filter(
        EconomicEventHistory.title.ilike(f"%{event_title}%"),
        EconomicEventHistory.actual.isnot(None),
        EconomicEventHistory.forecast.isnot(None),
    )

    if currency:
        query = query.filter(EconomicEventHistory.currency == currency.upper())

    if window_years is not None and reference_date is not None:
        start_date = reference_date - timedelta(days=window_years * 365)
        query = query.filter(EconomicEventHistory.date >= start_date)

    records = query.all()

    surprises = []
    titles_set = set()
    for actual, forecast, title in records:
        try:
            surprises.append(float(actual) - float(forecast))
            titles_set.add(title)
        except (TypeError, ValueError):
            continue

    return surprises, sorted(titles_set)


def fetch_historical_surprise_std_full(
    event_title: Optional[str],
    currency: Optional[str] = None,
    *,
    window_years: int = DEFAULT_WINDOW_YEARS,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
    expand_window_if_needed: bool = True,
) -> HistoricalStdResult:
    """
    محاسبه historical std سورپرایزها از DB با metadata کامل.

    استراتژی:
      0. عنوان مصنوعی (Macro News Digest) → مستقیم fallback بی‌صدا
      1. canonical matching در window پیش‌فرض
      2. اگر کم بود، expand window تا MAX_WINDOW_YEARS
      3. اگر باز هم کم بود، loose matching
      4. اگر باز هم کم بود، previous-based proxy (actual - previous)
      5. اگر باز هم کم بود، category fallback
      6. اگر category نشناختیم، absolute fallback
      7. اگر هیچ چیز نداشتیم، no_data (value=None)

    هر بار که std محاسبه می‌شود، outlier ها با IQR (k=1.5) فیلتر می‌شوند
    تا COVID anomalies و سایر outlier ها روی نتیجه تأثیر نگذارند.

    Returns:
        HistoricalStdResult با metadata کامل
    """
    if not event_title or not currency:
        logger.debug("event_title یا currency خالی است — absolute fallback")
        return HistoricalStdResult(
            value=ABSOLUTE_FALLBACK_STD,
            source="absolute_fallback",
            observations=0,
            window_years=None,
            canonical_key=None,
            matched_titles=[],
        )

    # ایونت‌های مصنوعی (مثل "Macro News Digest for USD") ایونت اقتصادی
    # واقعی نیستند و هرگز داده تاریخی نخواهند داشت — بدون کوئری و
    # بدون warning، مستقیم absolute fallback برگردان.
    if event_title.strip().lower().startswith("macro news digest"):
        logger.debug(
            "historical_std [synthetic digest]: title=%r currency=%s — skipped",
            event_title, currency,
        )
        return HistoricalStdResult(
            value=ABSOLUTE_FALLBACK_STD,
            source="synthetic_digest",
            observations=0,
            window_years=None,
            canonical_key=None,
            matched_titles=[],
        )

    canonical_key = _canonicalize_event_title(event_title, currency)

    session = SessionLocal()
    try:
        reference_date = _get_max_date_in_db(session)
        if reference_date is None:
            reference_date = datetime.utcnow()

        # ---------- مرحله 1: canonical matching در window پیش‌فرض ----------
        if canonical_key:
            surprises, titles = _query_surprises_by_canonical(
                session, canonical_key, currency,
                window_years, reference_date,
            )

            if len(surprises) >= min_observations:
                std_val, outliers = _compute_robust_std(surprises)
                logger.info(
                    "historical_std [canonical/%dy]: key=%s std=%.4f "
                    "obs=%d outliers_removed=%d titles=%d",
                    window_years, canonical_key, std_val,
                    len(surprises), outliers, len(titles),
                )
                return HistoricalStdResult(
                    value=std_val if std_val > 0 else ABSOLUTE_FALLBACK_STD,
                    source="computed_canonical",
                    observations=len(surprises),
                    window_years=window_years,
                    canonical_key=canonical_key,
                    matched_titles=titles,
                    outliers_removed=outliers,
                )

            # ---------- مرحله 2: expand window ----------
            if expand_window_if_needed:
                for expanded_years in (10, MAX_WINDOW_YEARS):
                    if expanded_years <= window_years:
                        continue

                    surprises, titles = _query_surprises_by_canonical(
                        session, canonical_key, currency,
                        expanded_years, reference_date,
                    )

                    if len(surprises) >= min_observations:
                        std_val, outliers = _compute_robust_std(surprises)
                        logger.info(
                            "historical_std [canonical/%dy expanded]: "
                            "key=%s std=%.4f obs=%d outliers_removed=%d",
                            expanded_years, canonical_key, std_val,
                            len(surprises), outliers,
                        )
                        return HistoricalStdResult(
                            value=std_val if std_val > 0 else ABSOLUTE_FALLBACK_STD,
                            source="computed_canonical",
                            observations=len(surprises),
                            window_years=expanded_years,
                            canonical_key=canonical_key,
                            matched_titles=titles,
                            outliers_removed=outliers,
                        )

                # ---------- مرحله 3: همه تاریخ ----------
                surprises, titles = _query_surprises_by_canonical(
                    session, canonical_key, currency, None, None,
                )

                if len(surprises) >= ABSOLUTE_MIN_OBSERVATIONS:
                    std_val, outliers = _compute_robust_std(surprises)
                    logger.info(
                        "historical_std [canonical/all-time]: "
                        "key=%s std=%.4f obs=%d outliers_removed=%d",
                        canonical_key, std_val, len(surprises), outliers,
                    )
                    return HistoricalStdResult(
                        value=std_val if std_val > 0 else ABSOLUTE_FALLBACK_STD,
                        source="computed_canonical",
                        observations=len(surprises),
                        window_years=None,
                        canonical_key=canonical_key,
                        matched_titles=titles,
                        outliers_removed=outliers,
                    )

        # ---------- مرحله 4: loose matching ----------
        surprises, titles = _query_surprises_by_loose_match(
            session, event_title, currency, None, reference_date,
        )

        if len(surprises) >= min_observations:
            std_val, outliers = _compute_robust_std(surprises)
            logger.warning(
                "historical_std [loose match]: title=%r currency=%s "
                "std=%.4f obs=%d outliers_removed=%d titles=%d",
                event_title, currency, std_val,
                len(surprises), outliers, len(titles),
            )
            return HistoricalStdResult(
                value=std_val if std_val > 0 else ABSOLUTE_FALLBACK_STD,
                source="computed_loose",
                observations=len(surprises),
                window_years=None,
                canonical_key=canonical_key,
                matched_titles=titles,
                outliers_removed=outliers,
            )

        # ---------- مرحله 5: previous-based fallback (actual - previous) ----------
        if canonical_key:
            changes, titles = _query_changes_by_canonical(
                session, canonical_key, currency,
                window_years, reference_date,
            )

            if len(changes) >= ABSOLUTE_MIN_OBSERVATIONS:
                std_val, outliers = _compute_robust_std(changes)
                logger.info(
                    "historical_std [previous-based/%dy]: key=%s std=%.4f "
                    "obs=%d outliers_removed=%d",
                    window_years, canonical_key, std_val,
                    len(changes), outliers,
                )
                return HistoricalStdResult(
                    value=std_val if std_val > 0 else ABSOLUTE_FALLBACK_STD,
                    source="computed_previous_based",
                    observations=len(changes),
                    window_years=window_years,
                    canonical_key=canonical_key,
                    matched_titles=titles,
                    outliers_removed=outliers,
                )

        # ---------- مرحله 6: category fallback ----------
        if canonical_key:
            category = _category_from_canonical_key(canonical_key)
            if category in CATEGORY_DEFAULT_STD:
                default_val = CATEGORY_DEFAULT_STD[category]
                logger.warning(
                    "historical_std [category fallback]: "
                    "key=%s category=%s std=%.4f",
                    canonical_key, category, default_val,
                )
                return HistoricalStdResult(
                    value=default_val,
                    source="category_fallback",
                    observations=0,
                    window_years=None,
                    canonical_key=canonical_key,
                    matched_titles=[],
                )

        # ---------- مرحله 7: absolute fallback ----------
        logger.warning(
            "historical_std [absolute fallback]: "
            "title=%r currency=%s — no data",
            event_title, currency,
        )
        return HistoricalStdResult(
            value=ABSOLUTE_FALLBACK_STD,
            source="absolute_fallback",
            observations=0,
            window_years=None,
            canonical_key=canonical_key,
            matched_titles=[],
        )

    finally:
        session.close()


def fetch_historical_surprise_std(
    event_title: Optional[str],
    currency: Optional[str] = None,
) -> float:
    """
    نسخه ساده برای backward compatibility.

    فقط مقدار std را برمی‌گرداند. برای metadata کامل از
    fetch_historical_surprise_std_full استفاده کن.
    """
    result = fetch_historical_surprise_std_full(event_title, currency)
    return result.value if result.value is not None else ABSOLUTE_FALLBACK_STD


# ===========================================================================
# SURPRISE & VOLATILITY CALCULATION
# ===========================================================================

def calculate_surprise_factor(
    actual: Optional[float],
    forecast: Optional[float],
    historical_std: float,
) -> float:
    """فاکتور سورپرایز نرمالایز شده."""
    if actual is None or forecast is None:
        return 0.0

    effective_std = (
        historical_std
        if historical_std > 0.001
        else ABSOLUTE_FALLBACK_STD
    )

    surprise_raw = actual - forecast
    z_score = surprise_raw / effective_std
    factor = min(abs(z_score) / 3.0, 1.0)
    return max(factor, 0.0)


def calculate_volatility_multiplier(
    iv: Optional[float],
    hv: Optional[float],
    atr_current: Optional[float],
    atr_baseline: Optional[float],
) -> float:
    """ضریب نوسان ترکیبی."""
    ratios = []

    if iv is not None and hv is not None and hv > 0.001:
        iv_decimal = iv / 100.0 if iv > 1.0 else iv
        raw_ratio = iv_decimal / hv
        clamped_ratio = min(raw_ratio, MAX_IV_HV_RATIO)
        ratios.append(clamped_ratio)

    if atr_current is not None and atr_baseline is not None and atr_baseline > 0.0:
        raw_atr_ratio = atr_current / atr_baseline
        clamped_atr_ratio = min(raw_atr_ratio, MAX_ATR_RATIO)
        ratios.append(clamped_atr_ratio)

    if not ratios:
        return 1.0

    return sum(ratios) / len(ratios)


# ===========================================================================
# CURRENCY GUESSING
# ===========================================================================

def _guess_currency_from_ticker(ticker: str) -> str:
    if ticker in TICKER_TO_CURRENCY:
        return TICKER_TO_CURRENCY[ticker]

    ticker_upper = ticker.upper()
    pattern_map = {
        "EUR": ["EUR"],
        "GBP": ["GBP"],
        "JPY": ["JPY"],
        "CHF": ["CHF"],
        "AUD": ["AUD"],
        "CAD": ["CAD"],
        "NZD": ["NZD"],
        "XAU": ["XAU", "GOLD", "GC=F"],
        "OIL": ["OIL", "CL=F", "BZ=F"],
        "USD": ["USD", "DX-Y", "DX=F"],
    }
    for currency, patterns in pattern_map.items():
        if any(p in ticker_upper for p in patterns):
            return currency

    return "USD"


# ===========================================================================
# MAIN FUNCTION
# ===========================================================================

def fetch_market_context(
    ticker: str,
    currency: Optional[str] = None,
    event_title: Optional[str] = None,
    actual: Optional[float] = None,
    forecast: Optional[float] = None,
    previous: Optional[float] = None,
    *,
    event_currency: Optional[str] = None,
) -> MarketContext:
    """
    ساخت MarketContext کامل.

    Args:
        ticker: نماد yfinance
        currency: کد ارز ticker (برای IV/yield/HV)
        event_title: عنوان رویداد اقتصادی
        actual/forecast/previous: مقادیر event
        event_currency: کد ارز event (برای historical_std)
                        اگر None باشد، از currency استفاده می‌شود.
                        این جداست چون مثلاً ticker می‌تواند EURUSD=X باشد
                        ولی event می‌تواند US CPI باشد (event_currency=USD).
    """
    if not ticker or not ticker.strip():
        raise ValueError("ticker نمی‌تواند خالی باشد")

    if currency is None:
        currency = _guess_currency_from_ticker(ticker)

    # event_currency پیش‌فرض = currency
    if event_currency is None:
        event_currency = currency

    logger.info(
        "ساخت MarketContext: ticker=%s currency=%s event_currency=%s event=%s",
        ticker, currency, event_currency, event_title or "N/A",
    )

    # ---- yfinance data ----
    price_data = fetch_price_history(ticker)
    atr_current, atr_baseline = calculate_atr(price_data)
    hv = calculate_historical_volatility(price_data)
    iv = fetch_implied_volatility(currency)
    yield_spread = fetch_yield_spread(currency)

    # ---- historical std (UPGRADED) ----
    std_result = fetch_historical_surprise_std_full(
        event_title=event_title,
        currency=event_currency,
    )
    historical_std = std_result.value if std_result.value is not None else ABSOLUTE_FALLBACK_STD

    # ---- derived metrics ----
    calculated_surprise = calculate_surprise_factor(actual, forecast, historical_std)
    calculated_volatility = calculate_volatility_multiplier(iv, hv, atr_current, atr_baseline)

    context = MarketContext(
        target_asset=ticker,
        event_title=event_title,
        actual_value=actual,
        forecast_value=forecast,
        previous_value=previous,
        historical_std=historical_std,
        historical_volatility=hv,
        atr_current=atr_current,
        atr_baseline=atr_baseline,
        implied_volatility=iv,
        yield_spread=yield_spread,
        calculated_surprise=calculated_surprise,
        calculated_volatility=calculated_volatility,
    )

    _log_context_summary(context, std_result)
    return context


def _log_context_summary(
    context: MarketContext,
    std_result: HistoricalStdResult,
) -> None:
    """خلاصه MarketContext را به صورت خوانا لاگ می‌کند."""
    key_fields = [
        context.actual_value, context.forecast_value, context.previous_value,
        context.historical_std, context.historical_volatility,
        context.atr_current, context.atr_baseline,
        context.implied_volatility, context.yield_spread,
    ]
    completeness = sum(1 for f in key_fields if f is not None) / len(key_fields)

    def fmt(v, d=4):
        return f"{v:.{d}f}" if v is not None else "N/A"

    logger.info("┌─ MarketContext ─────────────────────────────")
    logger.info("│ asset=%s | completeness=%.0f%%", context.target_asset, completeness * 100)
    logger.info("│ ATR=%s/%s | HV=%s | IV=%s | spread=%s",
                fmt(context.atr_current, 6),
                fmt(context.atr_baseline, 6),
                fmt(context.historical_volatility),
                fmt(context.implied_volatility, 2),
                fmt(context.yield_spread, 3))
    logger.info("│ historical_std=%s (source=%s, obs=%d, outliers=%d, window=%s, key=%s)",
                fmt(context.historical_std),
                std_result.source,
                std_result.observations,
                std_result.outliers_removed,
                std_result.window_years,
                std_result.canonical_key)
    logger.info("│ surprise=%s | volatility=%s",
                fmt(context.calculated_surprise, 3),
                fmt(context.calculated_volatility, 3))
    logger.info("└─────────────────────────────────────────────")


# ===========================================================================
# TEST
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )

    print("=" * 60)
    print("تست data_fetcher.py — upgraded historical_std")
    print("=" * 60)

    test_cases = [
        {
            "label": "US CPI (canonical match should work)",
            "ticker": "EURUSD=X",
            "currency": "EUR",
            "event_title": "CPI y/y",
            "event_currency": "USD",
            "actual": 3.8,
            "forecast": 3.5,
            "previous": 3.6,
        },
        {
            "label": "US NFP (canonical NFP not NFP_ADP)",
            "ticker": "DX-Y.NYB",
            "currency": "USD",
            "event_title": "Non-Farm Employment Change",
            "event_currency": "USD",
            "actual": 220000,
            "forecast": 180000,
            "previous": 175000,
        },
        {
            "label": "Federal Funds Rate",
            "ticker": "DX-Y.NYB",
            "currency": "USD",
            "event_title": "Federal Funds Rate",
            "event_currency": "USD",
            "actual": 5.25,
            "forecast": 5.25,
            "previous": 5.00,
        },
        {
            "label": "German Prelim CPI (EUR with country override)",
            "ticker": "EURUSD=X",
            "currency": "EUR",
            "event_title": "German Prelim CPI m/m",
            "event_currency": "EUR",
            "actual": 0.4,
            "forecast": 0.3,
            "previous": 0.2,
        },
        {
            "label": "Unknown event (should fallback)",
            "ticker": "GBPUSD=X",
            "currency": "GBP",
            "event_title": "Some Random Indicator",
            "event_currency": "GBP",
            "actual": 1.0,
            "forecast": 1.0,
            "previous": 1.0,
        },
    ]

    for tc in test_cases:
        print(f"\n{'─' * 55}")
        print(f"  {tc['label']}")
        print(f"{'─' * 55}")

        ctx = fetch_market_context(
            ticker=tc["ticker"],
            currency=tc["currency"],
            event_title=tc["event_title"],
            event_currency=tc.get("event_currency"),
            actual=tc["actual"],
            forecast=tc["forecast"],
            previous=tc["previous"],
        )

        print(f"  historical_std       : {ctx.historical_std}")
        print(f"  calculated_surprise  : {ctx.calculated_surprise}")
        print(f"  calculated_volatility: {ctx.calculated_volatility}")