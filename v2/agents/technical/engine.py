"""
agents/technical/engine.py
==========================
موتور deterministic Technical — ترکیب ۱۰ فاکتور (بازساخت Phase 1).

API عمومی:
    calculate_technical_metrics(ticker, timeframe) -> TechnicalMetrics

نکات ساختاری Phase 1:
  - هر فاکتور توسط ماژول مستقل خودش محاسبه می‌شود (data/ indicators/
    structure/ levels/) با **عزل خطا جدا** (در نسخه قبل، Structure و
    SMC Location یک try/except مشترک داشتند — بهبود آگاهانه در مسیر
    exception؛ روی دیتای سالم خروجی bit-identical است).
  - CircuitBreaker روی fetch market data (۵ شکست → ۵ دقیقه مسدود).
  - FetchProvenance + DataQualityReport در هر response ثبت می‌شوند
    (برای decision log) — تأثیر روی score: صفر.
  - ATR و ساختار خام (BOS/CHoCH + age) و فاصله‌های OB/FVG به‌عنوان
    داده خام اضافه می‌شوند — بدون تغییر semantics امتیازدهی.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .confluence.confidence import compute_confidence
from .confluence.scorer import aggregate_score
from .confluence.weights import ENGINE_VERSION
from .data.circuit_breaker import CircuitBreaker
from .data import market_data
from .data.market_data import HTF_MAP, TIMEFRAME_MAP, FetchProvenance
from .data.validators import validate_ohlc
from .indicators.momentum import compute_momentum
from .indicators.patterns import compute_price_action
from .indicators.trend import compute_trend
from .indicators.volatility import compute_regime
from .levels.ote import compute_ote
from .levels.pdh_pdl import compute_pdh_pdl
from .models import ComponentScores, TechnicalMetrics
from .multi_timeframe.alignment import compute_mtf_confluence
from .structure.bos_choch import compute_structure, DEFAULT_SCORE_MODE
from .structure.fvg import compute_fvg
from .structure.liquidity import compute_liquidity
from .structure.order_blocks import compute_order_blocks
from .structure.swings import detect_swings

logger = logging.getLogger(__name__)

# --- آستانه‌های عمومی (مقادیر نسخه قبل، نام‌گذاری‌شده) ---
MIN_BARS = 50

# حداقل کندل برای اعتبارسنجی در data quality (report only)
# (همین MIN_BARS است — فقط برای خوانایی)

# CircuitBreaker بازار (سراسری؛ در tests ریست می‌شود)
MARKET_DATA_BREAKER = CircuitBreaker(failure_threshold=5, recovery_timeout=300.0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def calculate_technical_metrics(ticker: str, timeframe: str = "D1") -> TechnicalMetrics:
    """
    محاسبه‌ی متدیکهای فنی خام و امتیاز ۱۰ مؤلفه‌ی deterministic.

    (همان امضای نسخه قبل — ورودی/خروجی بدون تغییر)
    """
    interval = TIMEFRAME_MAP.get(timeframe, "1d")
    metrics = TechnicalMetrics()
    metrics.engine_version = ENGINE_VERSION

    # --- 0. Circuit breaker: اگر سرویس داده‌ها می‌شکند، تماس مجدد را نگذاریم ---
    if not MARKET_DATA_BREAKER.allow():
        logger.warning(
            "[Tech] circuit breaker OPEN — skipping market data for %s %s",
            ticker, timeframe,
        )
        metrics.data_error = "circuit_open"
        return metrics

    data = market_data.fetch_price_history(ticker, interval=interval)
    if data is None:
        MARKET_DATA_BREAKER.record_failure()
        return metrics
    MARKET_DATA_BREAKER.record_success()

    if len(data) < MIN_BARS:
        return metrics

    # --- 0.1. Provenance (الگوی AgenticTrading) + Data Quality (report only) ---
    provenance = FetchProvenance(
        source=market_data.DATA_SOURCE,
        interval=interval,
        period=market_data.period_for_interval(interval),
        bars=len(data),
        resampled_from=market_data._RESAMPLE_FROM_1H.get(interval),
    )
    metrics.provenance_json = provenance.model_dump_json()

    quality = validate_ohlc(data, interval)
    metrics.data_quality_json = quality.model_dump_json()
    if not quality.ok:
        logger.warning("[Tech] %s %s data quality issues: %s", ticker, timeframe, quality.issues)

    df = data.copy().reset_index(drop=True)
    df.columns = [c.lower() for c in df.columns]
    df_dt = data.copy()
    df_dt.columns = [c.lower() for c in df_dt.columns]

    if "volume" not in df.columns:
        df["volume"] = 0.0
    if "volume" not in df_dt.columns:
        df_dt["volume"] = 0.0

    closes = df["close"].dropna().tolist()
    current_price = float(closes[-1])
    metrics.current_price = current_price

    components = ComponentScores()

    # --- 1. Trend (13%) ---
    try:
        t = compute_trend(df, current_price)
        metrics.trend_status = t.status
        components.trend = t.score
    except Exception as e:
        logger.warning(f"Trend calc failed: {e}")

    # --- 2. Momentum (6%) ---
    try:
        mom = compute_momentum(df)
        metrics.rsi = mom.rsi
        metrics.macd_histogram = mom.macd_histogram
        components.momentum = mom.score
    except Exception as e:
        logger.warning(f"Momentum calc failed: {e}")

    # --- 3. Volatility / Regime (4%) + ATR خام ---
    try:
        reg = compute_regime(df)
        metrics.adx_value = reg.adx
        metrics.chop_value = reg.chop
        metrics.atr = reg.atr
        components.volatility = reg.score
    except Exception as e:
        logger.warning(f"Volatility calc failed: {e}")

    # --- 4. Price Action (2%) ---
    try:
        pa = compute_price_action(df)
        components.price_action = pa.score
        metrics.last_candle_type = pa.label
    except Exception as e:
        logger.warning(f"Price Action calc failed: {e}")

    # --- 5. Swings (پایه‌ی Structure/SWC/OTE) ---
    try:
        swings = detect_swings(df)
    except Exception as e:
        logger.warning(f"Swing detection failed: {e}")
        swings = None

    # --- 6. Structure (20%) — آخرین BOS ± CHoCH خام (Phase 1) ---
    try:
        st = compute_structure(df, swings, score_mode=DEFAULT_SCORE_MODE)
        components.structure = st.score
        if st.last_bos_dir is not None:
            metrics.recent_bos = "Bullish BOS" if st.last_bos_dir == 1 else "Bearish BOS"
        metrics.structure_raw = st.to_raw_dict()
    except Exception as e:
        logger.warning(f"Structure calc failed: {e}")

    # --- 7. SMC Location (15%) — OB ±0.8 / FVG ±0.5 ---
    fvg_res = None
    try:
        fvg_res = compute_fvg(df, current_price)
        metrics.active_bull_fvg_low = fvg_res.bull_low
        metrics.active_bull_fvg_high = fvg_res.bull_high
        metrics.active_bear_fvg_low = fvg_res.bear_low
        metrics.active_bear_fvg_high = fvg_res.bear_high
    except Exception as e:
        logger.warning(f"FVG calc failed: {e}")

    ob_res = None
    try:
        if swings is not None and not swings.empty:
            ob_res = compute_order_blocks(df, swings, current_price)
            metrics.active_bullish_ob = ob_res.bull_top
            metrics.active_bearish_ob = ob_res.bear_bottom
    except Exception as e:
        logger.warning(f"Order Block calc failed: {e}")

    loc_parts: list[float] = []
    if fvg_res is not None:
        loc_parts.append(fvg_res.contribution)
    if ob_res is not None:
        loc_parts.append(ob_res.contribution)
    if loc_parts:
        components.smc_location = _clamp(sum(loc_parts), -1.0, 1.0)

    # Phase 1: فاصله‌های خام تا سطوح فعال
    distances: dict[str, float] = {}
    if fvg_res is not None:
        if fvg_res.bull_distance_pct is not None:
            distances["bull_fvg_distance_pct"] = round(fvg_res.bull_distance_pct, 6)
        if fvg_res.bear_distance_pct is not None:
            distances["bear_fvg_distance_pct"] = round(fvg_res.bear_distance_pct, 6)
    if ob_res is not None:
        if ob_res.bull_distance_pct is not None:
            distances["bull_ob_distance_pct"] = round(ob_res.bull_distance_pct, 6)
        if ob_res.bear_distance_pct is not None:
            distances["bear_ob_distance_pct"] = round(ob_res.bear_distance_pct, 6)
    if distances:
        metrics.level_distances = distances

    # --- 8. Liquidity Sweep (12%) ---
    try:
        liq = compute_liquidity(df, swings, current_price)
        components.liquidity_sweep = liq.score
        metrics.liquidity_sweep_status = liq.status
    except Exception as e:
        logger.warning(f"Liquidity sweep calc failed: {e}")

    # --- 9. PDH/PDL (8%) — D1/W1: خودِ دیتا | BTF: fetch روزانه جدا ---
    try:
        phl = compute_pdh_pdl(df_dt, current_price)
        metrics.previous_day_high = phl.pdh
        metrics.previous_day_low = phl.pdl
        components.pdh_pdl = phl.score
        metrics.pdh_pdl_status = phl.status
    except Exception as e:
        logger.warning(f"PDH/PDL calc failed: {e}")

    # --- 10. OTE Zone (10%) ---
    try:
        ote = compute_ote(df, swings)
        metrics.current_retracement = ote.retracement
        components.ote_zone = ote.score
        metrics.ote_status = ote.status
    except Exception as e:
        logger.warning(f"OTE calc failed: {e}")

    # --- 11. MTF Confluence (10%) ---
    htf_interval = HTF_MAP.get(timeframe)
    try:
        htf_data = (
            market_data.fetch_price_history(ticker, interval=htf_interval)
            if htf_interval else None
        )
        mtf_value, htf_status = compute_mtf_confluence(components.trend, htf_data)
        if mtf_value is not None:
            metrics.htf_interval = htf_interval
            metrics.htf_trend_status = htf_status
        components.mtf_confluence = mtf_value
    except Exception as e:
        logger.warning(f"MTF confluence calc failed: {e}")
        components.mtf_confluence = None

    # --- تجمیع نهایی (semantics نسخه قبل) ---
    metrics.components = components
    metrics.technical_score = aggregate_score(components)
    if metrics.technical_score is not None:
        has_ob = bool(metrics.active_bullish_ob or metrics.active_bearish_ob)
        has_fvg = bool(metrics.active_bull_fvg_low or metrics.active_bear_fvg_low)
        metrics.technical_confidence = compute_confidence(components, has_ob, has_fvg)
    else:
        metrics.technical_confidence = 0.0

    return metrics
