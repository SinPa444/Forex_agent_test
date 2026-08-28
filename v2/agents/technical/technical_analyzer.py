"""
agents/technical/technical_analyzer.py
======================================
Phase 3 Technical Analysis Agent (Deterministic 10-Factor Scoring Engine).

An independent multi-agent module that fetches price history, calculates 
10 independent technical factors deterministically, combines them into a 
technical_score and technical_confidence, and uses an LLM ONLY for 
narrative strategy generation.

Design principles:
  - Deterministic scoring (Python math, no LLM for scores)
  - Distinguishes 0 (Neutral) from None (Not Evaluated)
  - Stateless and LangGraph-ready
"""

from __future__ import annotations

import logging
from typing import Optional, Literal
import pandas as pd
import numpy as np
import yfinance as yf

# Indicators — pandas-ta-classic (200+ indicators + 62 الگوی کندل استاندارد)
import pandas_ta_classic as pta  # noqa: F401 — ایمپورت، اکسسور df.ta را رجیستر می‌کند
from smartmoneyconcepts import smc

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field, field_validator

from core.llm_utils import invoke_with_retry, _strip_json_markdown
from core import technical_config as cfg
from agents.technical.technical_signal import TechnicalSignal, build_technical_signal
from agents.technical.market_structure import structure_score_for_component
from agents.technical.prompts import (
    TechnicalInterpretation,
    TECH_INTERPRETATION_SYSTEM_PROMPT,
    build_technical_narrative_prompt,
)

logger = logging.getLogger(__name__)

# ===========================================================================
# Constants
# ===========================================================================

HISTORY_PERIOD = "1y"

# Phase 7: یادداشت مهم — yfinance اینترول 4h/2h ندارد؛ این دو از دیتای 1h
# ری‌سیمپل می‌شوند (نگاه به _fetch_price_history).
TIMEFRAME_MAP = {
    "M15": "15m",
    "M30": "30m",
    "H1": "60m",
    "H2": "2h",    # resample از 1h
    "H4": "4h",    # resample از 1h
    "D1": "1d",
    "W1": "1wk",
}

# اینترول‌هایی که باید از 1h ری‌سیمپل شوند
_RESAMPLE_FROM_1H = {"2h": "2h", "4h": "4h"}

# تایم‌فریم بالاتر (HTF) برای مؤلفه MTF Confluence
HTF_MAP = {
    "M15": "60m",
    "M30": "60m",
    "H1": "4h",
    "H2": "4h",
    "H4": "1d",
    "D1": "1wk",
    "W1": "1wk",
}

# ===========================================================================
# Pydantic Models
# ===========================================================================

class ComponentScores(BaseModel):
    """Scores for each of the 10 technical factors [-1, 1]. None if not evaluated."""
    structure: Optional[float] = None
    trend: Optional[float] = None
    smc_location: Optional[float] = None
    mtf_confluence: Optional[float] = None
    momentum: Optional[float] = None
    volatility: Optional[float] = None
    price_action: Optional[float] = None
    liquidity_sweep: Optional[float] = None
    pdh_pdl: Optional[float] = None
    ote_zone: Optional[float] = None

class TechnicalMetrics(BaseModel):
    """Full raw technical data and component scores."""
    current_price: Optional[float] = None
    technical_score: Optional[float] = None  # Final aggregated score [-1, 1]
    technical_confidence: Optional[float] = None  # Reliability [0, 1]
    components: ComponentScores = Field(default_factory=ComponentScores)
    
    # Raw Data for LLM narrative
    trend_status: Optional[str] = None
    adx_value: Optional[float] = None
    recent_bos: Optional[str] = None
    recent_choch: Optional[str] = None
    active_bullish_ob: Optional[float] = None
    active_bearish_ob: Optional[float] = None
    active_bull_fvg_low: Optional[float] = None
    active_bull_fvg_high: Optional[float] = None
    active_bear_fvg_low: Optional[float] = None
    active_bear_fvg_high: Optional[float] = None
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    last_candle_type: Optional[str] = None
    htf_interval: Optional[str] = None
    htf_trend_status: Optional[str] = None
    liquidity_sweep_status: Optional[str] = None
    previous_day_high: Optional[float] = None
    previous_day_low: Optional[float] = None
    pdh_pdl_status: Optional[str] = None
    current_retracement: Optional[float] = None
    ote_status: Optional[str] = None
    chop_value: Optional[float] = None

class LLMStrategy(BaseModel):
    """LLM only generates strategy and reasoning, not scores."""
    strategy: str = Field(description="Specific trading strategy based on the deterministic scores")
    reasoning: str = Field(description="3-4 sentences explaining the strategy in context of the scores")

class TechnicalReport(BaseModel):
    """Final output combining Python math and LLM narrative."""
    direction: int = Field(description="1=Bullish, -1=Bearish, 0=Neutral")
    score: float = Field(ge=-1.0, le=1.0, description="Deterministic technical score")
    confidence: float = Field(ge=0.0, le=1.0, description="Reliability of the score")
    strategy: str
    reasoning: str

# ===========================================================================
# Data Fetcher & Math Layer (Deterministic)
# ===========================================================================

def _fetch_price_history(ticker: str, interval: str = "1d") -> Optional[pd.DataFrame]:
    """
    دانلود دیتای قیمت. Phase 7: اینترول‌های 2h/4h که yfinance ندارد
    از دیتای 1h ری‌سیمپل می‌شوند (OHLC استاندارد).

    باگ قبلی: «4h» مستقیم به yfinance پاس می‌شد که اینترول نامعتبر است
    و دانلود empty برمی‌گشت — یعنی H4 عملاً همیشه بدون دیتا بود.
    """
    # --- مسیر ری‌سیمپل: 2h/4h از 1h ---
    if interval in _RESAMPLE_FROM_1H:
        try:
            data = yf.download(ticker, period="3mo", interval="1h", progress=False, auto_adjust=True)
            if data is None or data.empty:
                return None
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            data.columns = [c.lower() for c in data.columns]
            rule = _RESAMPLE_FROM_1H[interval]
            resampled = data.resample(rule).agg({
                "open": "first", "high": "max", "low": "min", "close": "last",
                **({"volume": "sum"} if "volume" in data.columns else {}),
            }).dropna(subset=["close"])
            if resampled.empty:
                return None
            # برگرداندن نام ستون‌ها به فرمت Title Case که بقیه کد انتظار دارد
            resampled.columns = [c.capitalize() for c in resampled.columns]
            return resampled
        except Exception as e:
            logger.error(f"Failed to resample {interval} from 1h for {ticker}: {e}")
            return None

    period = "1y"
    if interval in ["60m", "1h"]: period = "3mo"   # 1mo برای H1 کمی بود؛ EMA200 روی 1h به ~۲۰۰ ساعت نیاز دارد
    elif interval == "30m": period = "1mo"
    elif interval == "15m": period = "5d"
    elif interval == "1wk": period = "5y"  # برای EMA200 روی HTF هفتگی کندل کافی لازم است

    try:
        data = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if data is None or data.empty: return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data
    except Exception as e:
        logger.error(f"Failed to download price history for {ticker}: {e}")
        return None

def _last_frame_for_signal(ticker: str, timeframe: str) -> Optional[pd.DataFrame]:
    """
    آخرین DataFrame برای محاسبهٔ ATR/swing/structure در TechnicalSignal P0.

    P1 می‌تواند این را به داخل `calculate_technical_metrics` منتقل کند تا
    دانلود تکراری حذف شود و `TechnicalMetrics` خودش df را نگه دارد.
    """
    interval = TIMEFRAME_MAP.get(timeframe, "1d")
    data = _fetch_price_history(ticker, interval=interval)
    return data


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _last_indicator_value(out) -> Optional[float]:
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

def calculate_technical_metrics(ticker: str, timeframe: str = "D1") -> TechnicalMetrics:
    """Calculates raw technical metrics and 7 deterministic component scores."""
    interval = TIMEFRAME_MAP.get(timeframe, "1d")
    data = _fetch_price_history(ticker, interval=interval)
    
    metrics = TechnicalMetrics()
    components = ComponentScores()
    
    if data is None or len(data) < 50:
        return metrics

    df = data.copy().reset_index(drop=True)
    df.columns = [c.lower() for c in df.columns]
    # نسخه با datetime index — برای smc.previous_high_low که بر اساس زمان resample می‌کند
    df_dt = data.copy()
    df_dt.columns = [c.lower() for c in df_dt.columns]
    # بعضی فیدها (مثل جفت‌ارزهای yfinance) ستون volume ندارند یا همیشه صفر است —
    # smartmoneyconcepts به این ستون نیاز دارد، پس در نبودش صفر پر می‌کنیم.
    if "volume" not in df.columns:
        df["volume"] = 0.0
    if "volume" not in df_dt.columns:
        df_dt["volume"] = 0.0
    closes = df["close"].dropna().tolist()
    highs = df["high"].dropna().tolist()
    lows = df["low"].dropna().tolist()
    
    metrics.current_price = float(closes[-1])
    
    # --- 1. Trend Score (13%) — EMA50/200 + تأیید Supertrend ---
    # هم‌راستایی EMA با Supertrend → 0.8 | ناهم‌راستایی → 0.4 (ترند ضعیف‌تر)
    try:
        ema50 = _last_indicator_value(df.ta.ema(length=50))
        ema200 = _last_indicator_value(df.ta.ema(length=200))

        # Supertrend direction: +1 صعودی / -1 نزولی
        st_dir = 0
        st_df = df.ta.supertrend(length=10, multiplier=3)
        if st_df is not None and not st_df.empty:
            st_dir_col = [c for c in st_df.columns if c.startswith("SUPERTd")]
            if st_dir_col and pd.notna(st_df[st_dir_col[0]].iloc[-1]):
                st_dir = int(st_df[st_dir_col[0]].iloc[-1])

        if ema50 and ema200:
            if ema50 > ema200 and metrics.current_price > ema50:
                ema_dir = 1
            elif ema50 < ema200 and metrics.current_price < ema50:
                ema_dir = -1
            else:
                ema_dir = 0

            if ema_dir == 0:
                metrics.trend_status = "Ranging"
                components.trend = 0.0
            elif st_dir == ema_dir:
                metrics.trend_status = ("Uptrend" if ema_dir == 1 else "Downtrend") + " (Supertrend confirmed)"
                components.trend = 0.8
            else:
                metrics.trend_status = ("Uptrend" if ema_dir == 1 else "Downtrend") + " (Supertrend divergence)"
                components.trend = 0.4 * ema_dir
    except Exception as e:
        logger.warning(f"Trend calc failed: {e}")

    # --- 2. Momentum Score (6%) — RSI + هیستوگرام MACD ---
    try:
        metrics.rsi = _last_indicator_value(df.ta.rsi(length=14))
        macd_df = df.ta.macd(fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            hist_col = [c for c in macd_df.columns if c.startswith("MACDh")]
            if hist_col and pd.notna(macd_df[hist_col[0]].iloc[-1]):
                metrics.macd_histogram = float(macd_df[hist_col[0]].iloc[-1])

        if metrics.rsi is not None and metrics.macd_histogram is not None:
            if metrics.macd_histogram > 0 and metrics.rsi > 55:
                components.momentum = 0.8
            elif metrics.macd_histogram < 0 and metrics.rsi < 45:
                components.momentum = -0.8
            else:
                components.momentum = 0.0
    except Exception as e:
        logger.warning(f"Momentum calc failed: {e}")

    # --- 3. Volatility / Regime Score (4%) — ADX + CHOP ---
    # ADX قدرت ترند، CHOP تشخیص رنج (>61.8 خردکننده / <38.2 ترند تمیز)
    try:
        adx_df = df.ta.adx(length=14)
        adx_val = None
        if adx_df is not None and not adx_df.empty:
            adx_col = [c for c in adx_df.columns if c.startswith("ADX")]
            if adx_col and pd.notna(adx_df[adx_col[0]].iloc[-1]):
                adx_val = float(adx_df[adx_col[0]].iloc[-1])
                metrics.adx_value = adx_val

        chop_val = _last_indicator_value(df.ta.chop(length=14))
        if chop_val is not None:
            metrics.chop_value = chop_val

        regime_score = 0.0
        if adx_val is not None:
            if adx_val > 25: regime_score += 0.5   # Strong regime
            elif adx_val < 20: regime_score -= 0.5  # Chop risk
        if chop_val is not None:
            if chop_val < 38.2: regime_score += 0.3   # Trending cleanly
            elif chop_val > 61.8: regime_score -= 0.3 # Choppy
        if adx_val is not None or chop_val is not None:
            components.volatility = _clamp(regime_score, -0.8, 0.8)
    except Exception as e:
        logger.warning(f"Volatility calc failed: {e}")

    # --- 4. Price Action Score (2%) — الگوهای کندل استاندارد (TA-Lib CDL) ---
    # ۱۰ الگوی پرارزش: engulfing, hammer, shootingstar, morningstar, eveningstar,
    # piercing, darkcloudcover, 3whitesoldiers, 3blackcrows, harami
    # امتیاز: یک الگو ±0.5 | هر الگوی هم‌جهت اضافه +0.25 (سقف ±1.0)
    _CDL_PATTERNS = [
        "engulfing", "hammer", "shootingstar", "morningstar", "eveningstar",
        "piercing", "darkcloudcover", "3whitesoldiers", "3blackcrows", "harami",
    ]
    try:
        pats_df = df.ta.cdl_pattern(name=_CDL_PATTERNS)
        if pats_df is not None and not pats_df.empty:
            last_row = pats_df.iloc[-1]
            bull_hits, bear_hits = [], []
            for col in pats_df.columns:
                val = last_row[col]
                if pd.notna(val) and val > 0:
                    bull_hits.append(col.replace("CDL_", "").title())
                elif pd.notna(val) and val < 0:
                    bear_hits.append(col.replace("CDL_", "").title())

            net = len(bull_hits) - len(bear_hits)
            if net > 0:
                components.price_action = min(1.0, 0.5 + 0.25 * (net - 1))
                metrics.last_candle_type = "Bullish: " + ", ".join(bull_hits)
            elif net < 0:
                components.price_action = -min(1.0, 0.5 + 0.25 * (abs(net) - 1))
                metrics.last_candle_type = "Bearish: " + ", ".join(bear_hits)
            else:
                components.price_action = 0.0
                metrics.last_candle_type = "Neutral"
    except Exception as e:
        logger.warning(f"Price Action calc failed: {e}")

    # --- 5. Market Structure & 6. SMC Location (20% & 15%) ---
    swings = None  # مقداردهی اولیه تا در صورت خطا، فاکتورهای ۸ و ۱۰ امن fail شوند
    try:
        swings = smc.swing_highs_lows(df, swing_length=5)
        if swings is None:
            swings = smc.swing_highs_lows(df)
            
        # Structure (BOS + CHoCH) — P0: هر دو ستون استخراج و استفاده می‌شوند.
        if swings is not None and not swings.empty:
            bos_df = smc.bos_choch(df, swings, close_break=True)
            if bos_df is None: bos_df = smc.bos_choch(df, swings)
            if bos_df is not None and not bos_df.empty:
                last_bos_dir, last_choch_dir = 0, 0
                bos_col = next((c for c in bos_df.columns if str(c).upper() == "BOS"), None)
                choch_col = next(
                    (c for c in bos_df.columns if str(c).upper() in ("CHOCH", "CHoCH") or "CHoCH" in str(c)),
                    None,
                )
                if bos_col:
                    bos_events = bos_df.dropna(subset=[bos_col])
                    if not bos_events.empty:
                        last_bos_dir = int(bos_events.iloc[-1][bos_col])
                        metrics.recent_bos = "Bullish BOS" if last_bos_dir == 1 else "Bearish BOS"
                if choch_col:
                    choch_events = bos_df.dropna(subset=[choch_col])
                    if not choch_events.empty:
                        last_choch_dir = int(choch_events.iloc[-1][choch_col])
                        metrics.recent_choch = (
                            "Bullish CHoCH" if last_choch_dir == 1 else "Bearish CHoCH"
                        )
                comp_struct = structure_score_for_component(last_bos_dir, last_choch_dir)
                if comp_struct is not None:
                    components.structure = comp_struct

        # SMC Location (OB & FVG)
        fvg_df = smc.fvg(df, join_consecutive=False)
        if fvg_df is None: fvg_df = smc.fvg(df)
        ob_df = None
        if swings is not None and not swings.empty:
            ob_df = smc.ob(df, swings, close_mitigation=False)
            if ob_df is None: ob_df = smc.ob(df, swings)
            
        loc_score = 0.0
        
        if fvg_df is not None and not fvg_df.empty:
            fvg_valid = fvg_df.dropna(subset=['FVG'])
            bull_unmit = fvg_valid[(fvg_valid['FVG'] == 1) & (fvg_valid['MitigatedIndex'].isna()) & (fvg_valid['Top'] < metrics.current_price)]
            bull_mit = fvg_valid[(fvg_valid['FVG'] == 1) & (fvg_valid['Top'] < metrics.current_price)]
            active_bull = bull_unmit if not bull_unmit.empty else bull_mit
            if not active_bull.empty:
                b = active_bull.iloc[-1]
                metrics.active_bull_fvg_low = float(b['Bottom'])
                metrics.active_bull_fvg_high = float(b['Top'])
                loc_score += 0.5
                
            bear_unmit = fvg_valid[(fvg_valid['FVG'] == -1) & (fvg_valid['MitigatedIndex'].isna()) & (fvg_valid['Bottom'] > metrics.current_price)]
            bear_mit = fvg_valid[(fvg_valid['FVG'] == -1) & (fvg_valid['Bottom'] > metrics.current_price)]
            active_bear = bear_unmit if not bear_unmit.empty else bear_mit
            if not active_bear.empty:
                b = active_bear.iloc[-1]
                metrics.active_bear_fvg_low = float(b['Bottom'])
                metrics.active_bear_fvg_high = float(b['Top'])
                loc_score -= 0.5
                
        if ob_df is not None and not ob_df.empty:
            ob_valid = ob_df.dropna(subset=['OB'])
            bull_ob_unmit = ob_valid[(ob_valid['OB'] == 1) & (ob_valid['MitigatedIndex'].isna()) & (ob_valid['Top'] < metrics.current_price)]
            bull_ob_mit = ob_valid[(ob_valid['OB'] == 1) & (ob_valid['Top'] < metrics.current_price)]
            active_bull_ob = bull_ob_unmit if not bull_ob_unmit.empty else bull_ob_mit
            if not active_bull_ob.empty:
                metrics.active_bullish_ob = float(active_bull_ob.iloc[-1]['Top'])
                loc_score += 0.8
                
            bear_ob_unmit = ob_valid[(ob_valid['OB'] == -1) & (ob_valid['MitigatedIndex'].isna()) & (ob_valid['Bottom'] > metrics.current_price)]
            bear_ob_mit = ob_valid[(ob_valid['OB'] == -1) & (ob_valid['Bottom'] > metrics.current_price)]
            active_bear_ob = bear_ob_unmit if not bear_ob_unmit.empty else bear_ob_mit
            if not active_bear_ob.empty:
                metrics.active_bearish_ob = float(active_bear_ob.iloc[-1]['Bottom'])
                loc_score -= 0.8
                
        components.smc_location = _clamp(loc_score, -1.0, 1.0)
        
    except Exception as e:
        logger.warning(f"SMC calc failed: {e}")

    # --- 8. Liquidity Sweep (12%) ---
    # جاروی نقدینگی: sweep سمت فروش (زیر کف‌ها) = شکار استاپ و برگشت صعودی (+0.8)
    # sweep سمت خرید (بالای سقف‌ها) = برگشت نزولی (-0.8)
    # اگر قیمت به سمت دیگر سطح برگشته باشد امتیاز کامل، وگرنه نصف (0.4)
    try:
        liq_df = smc.liquidity(df, swings) if swings is not None and not swings.empty else None
        if liq_df is not None and not liq_df.empty and "Liquidity" in liq_df.columns:
            liq_valid = liq_df.dropna(subset=["Liquidity"])
            swept = liq_valid[liq_valid["Swept"].notna()]
            recent_swept = swept[swept["Swept"] >= len(df) - 6]  # sweep در ~۵ کندل اخیر
            if not recent_swept.empty:
                last_sweep = recent_swept.loc[recent_swept["Swept"].idxmax()]
                level = float(last_sweep["Level"])
                side = int(last_sweep["Liquidity"])
                recovered = (metrics.current_price > level) if side == -1 else (metrics.current_price < level)
                sweep_score = 0.8 if recovered else 0.4
                if side == -1:
                    components.liquidity_sweep = sweep_score
                    metrics.liquidity_sweep_status = (
                        f"Sell-side liquidity swept at {level:.5f} (bullish stop hunt"
                        f"{', price recovered' if recovered else ', no recovery yet'})"
                    )
                else:
                    components.liquidity_sweep = -sweep_score
                    metrics.liquidity_sweep_status = (
                        f"Buy-side liquidity swept at {level:.5f} (bearish stop hunt"
                        f"{', price recovered' if recovered else ', no recovery yet'})"
                    )
            else:
                components.liquidity_sweep = 0.0
                metrics.liquidity_sweep_status = "No recent sweep"
    except Exception as e:
        logger.warning(f"Liquidity sweep calc failed: {e}")

    # --- 9. PDH/PDL (8%) ---
    # سقف/کف روز قبل: شکست اخیر PDH = ادامه صعودی (+0.6) | شکست PDL = نزولی (-0.6)
    # ایستادن روی PDL (بدون شکست) = حمایت (+0.4) | روی PDH = مقاومت (-0.4)
    try:
        phl_df = smc.previous_high_low(df_dt, time_frame="1D")
        if phl_df is not None and not phl_df.empty:
            last_phl = phl_df.iloc[-1]
            pdh = float(last_phl["PreviousHigh"]) if pd.notna(last_phl["PreviousHigh"]) else None
            pdl = float(last_phl["PreviousLow"]) if pd.notna(last_phl["PreviousLow"]) else None
            metrics.previous_day_high = pdh
            metrics.previous_day_low = pdl
            recent_break_high = bool(phl_df["BrokenHigh"].tail(3).fillna(0).sum() > 0)
            recent_break_low = bool(phl_df["BrokenLow"].tail(3).fillna(0).sum() > 0)
            tol = metrics.current_price * 0.001  # ~۰.۱٪ فاصله از سطح
            if recent_break_high and not recent_break_low:
                components.pdh_pdl = 0.6
                metrics.pdh_pdl_status = f"Broke above Previous Day High ({pdh})"
            elif recent_break_low and not recent_break_high:
                components.pdh_pdl = -0.6
                metrics.pdh_pdl_status = f"Broke below Previous Day Low ({pdl})"
            elif pdl is not None and abs(metrics.current_price - pdl) <= tol:
                components.pdh_pdl = 0.4
                metrics.pdh_pdl_status = f"At Previous Day Low support ({pdl})"
            elif pdh is not None and abs(metrics.current_price - pdh) <= tol:
                components.pdh_pdl = -0.4
                metrics.pdh_pdl_status = f"At Previous Day High resistance ({pdh})"
            else:
                components.pdh_pdl = 0.0
                metrics.pdh_pdl_status = "Inside previous day range"
    except Exception as e:
        logger.warning(f"PDH/PDL calc failed: {e}")

    # --- 10. OTE Zone (10%) ---
    # زون طلایی ورود: اصلاح ۶۱.۸٪ تا ۷۸.۶٪ آخرین موج — بهترین نقطه ورود در جهت موج
    try:
        ret_df = smc.retracements(df, swings) if swings is not None and not swings.empty else None
        if ret_df is not None and not ret_df.empty and "Direction" in ret_df.columns:
            ret_valid = ret_df.dropna(subset=["Direction"])
            if not ret_valid.empty:
                last_ret = ret_valid.iloc[-1]
                ret_pct = (
                    float(last_ret["CurrentRetracement%"])
                    if pd.notna(last_ret["CurrentRetracement%"]) else None
                )
                leg_dir = int(last_ret["Direction"])
                metrics.current_retracement = ret_pct
                if ret_pct is not None and 61.8 <= ret_pct <= 78.6:
                    if leg_dir == 1:
                        components.ote_zone = 0.8
                        metrics.ote_status = f"In bullish OTE zone ({ret_pct:.1f}% retracement of up-leg)"
                    else:
                        components.ote_zone = -0.8
                        metrics.ote_status = f"In bearish OTE zone ({ret_pct:.1f}% retracement of down-leg)"
                else:
                    components.ote_zone = 0.0
                    if ret_pct is not None:
                        metrics.ote_status = (
                            f"Outside OTE ({ret_pct:.1f}% retracement, "
                            f"leg={'bullish' if leg_dir == 1 else 'bearish'})"
                        )
                    else:
                        metrics.ote_status = "N/A"
    except Exception as e:
        logger.warning(f"OTE calc failed: {e}")

    # --- 7. MTF Confluence (10%) ---
    # مقایسه جهت روند تایم‌فریم بالاتر (HTF) با روند تایم‌فریم پایه:
    # هم‌جهت → +0.8 | ناهم‌جهت → -0.8 | خنثی → 0.0 | بدون داده → None (Not Evaluated)
    try:
        htf_interval = HTF_MAP.get(timeframe)
        htf_data = _fetch_price_history(ticker, interval=htf_interval) if htf_interval else None

        if htf_data is not None and len(htf_data) >= 50:
            htf_df = htf_data.copy()
            htf_df.columns = [c.lower() for c in htf_df.columns]
            htf_closes = htf_df["close"].dropna().tolist()

            htf_ema50 = _last_indicator_value(htf_df.ta.ema(length=50))
            htf_ema200 = _last_indicator_value(htf_df.ta.ema(length=200))
            htf_price = float(htf_closes[-1])

            htf_dir = 0
            if htf_ema200 is not None:
                if htf_ema50 > htf_ema200 and htf_price > htf_ema50:
                    htf_dir = 1
                elif htf_ema50 < htf_ema200 and htf_price < htf_ema50:
                    htf_dir = -1

            metrics.htf_interval = htf_interval
            metrics.htf_trend_status = {1: "Uptrend", -1: "Downtrend", 0: "Ranging"}[htf_dir]

            base_dir = 0
            if components.trend is not None:
                base_dir = 1 if components.trend > 0 else (-1 if components.trend < 0 else 0)

            if htf_dir == 0 or base_dir == 0:
                components.mtf_confluence = 0.0
            elif htf_dir == base_dir:
                components.mtf_confluence = 0.8
            else:
                components.mtf_confluence = -0.8
        else:
            # داده HTF کافی نیست → Not Evaluated (با 0 خنثی اشتباه نشود)
            components.mtf_confluence = None
    except Exception as e:
        logger.warning(f"MTF confluence calc failed: {e}")
        components.mtf_confluence = None

    metrics.components = components
    
    # --- Calculate Final Technical Score ---
    # وزن‌ها پس از ارتقا به ۱۰ فاکتور — ساختار و موقعیت SMC همچنان سنگین‌ترین‌اند
    # P0: وزن‌ها از core/technical_config.py خوانده می‌شوند (برای کالیبراسیون P1)
    weights = cfg.TECHNICAL_WEIGHTS
    
    # Only calculate score if at least Structure or SMC Location is evaluated
    if components.structure is not None or components.smc_location is not None:
        raw_score = sum(weights[k] * (getattr(components, k) or 0.0) for k in weights)
        metrics.technical_score = _clamp(raw_score, -1.0, 1.0)
        
        # --- Calculate Confidence ---
        # 1. Data Quality (0.2): Do we have EMA200 and SMC?
        data_q = 0.0
        if components.trend is not None: data_q += 0.5
        if components.smc_location is not None: data_q += 0.5
        data_q *= 0.2
        
        # 2. Setup Quality (0.3): Is there an OB or FVG nearby?
        setup_q = 0.0
        if metrics.active_bullish_ob or metrics.active_bearish_ob: setup_q += 0.5
        if metrics.active_bull_fvg_low or metrics.active_bear_fvg_low: setup_q += 0.5
        setup_q *= 0.3
        
        # 3. Component Agreement (0.5): Do structure, trend, momentum have same sign?
        signs = [components.structure, components.trend, components.momentum]
        pos = sum(1 for s in signs if s is not None and s > 0)
        neg = sum(1 for s in signs if s is not None and s < 0)
        agreement = max(pos, neg) / 3.0
        agreement *= 0.5
        
        metrics.technical_confidence = _clamp(data_q + setup_q + agreement, 0.0, 1.0)
    else:
        metrics.technical_score = None
        metrics.technical_confidence = 0.0

    return metrics

# ===========================================================================
# LLM Prompt & Agent Service
# ===========================================================================

_TECH_SYSTEM_PROMPT = """\
You are an expert technical analyst specializing in Smart Money Concepts (SMC).
You are given deterministic technical scores calculated by a Python engine. 
Your ONLY job is to formulate a concise trading strategy and narrative reasoning based on these scores. 
DO NOT calculate or output scores yourself. Just explain the setup and suggest the action based on the provided data.

If score is positive, focus on long strategies. If negative, focus on short strategies. If near 0, suggest standing aside.
"""

_TECH_HUMAN_TEMPLATE = """\
## Technical Data for {ticker} (Timeframe: {timeframe})

Deterministic Scores (calculated by Python):
- Technical Score: {tech_score} (-1 to 1)
- Technical Confidence: {tech_confidence} (0 to 1)
- Component Scores: Structure={structure}, Trend={trend}, SMC Location={smc_loc}, MTF Confluence={mtf_conf}, Momentum={mom}, Volatility(ADX)={vol}, Liquidity Sweep={liq_sweep}, PDH/PDL={pdh_pdl}, OTE Zone={ote}

Market Context:
- Current Price: {current_price}
- Trend Status: {trend_status} (ADX: {adx_value}, CHOP: {chop_value})
- Higher Timeframe ({htf_interval}) Trend: {htf_trend_status}
- Recent Break of Structure: {recent_bos}
- Recent Change of Character: {recent_choch}
- Liquidity: {liq_status}
- Previous Day High/Low: {pdh} / {pdl} — {pdh_pdl_status}
- OTE Zone: {ote_status}
- Active Bullish OB (Demand): {active_bullish_ob}
- Active Bearish OB (Supply): {active_bearish_ob}
- Active Bullish FVG Zone (Demand): {active_bull_fvg_low} to {active_bull_fvg_high}
- Active Bearish FVG Zone (Supply): {active_bear_fvg_low} to {active_bear_fvg_high}
- Momentum: RSI={rsi}, MACD Hist={macd_histogram}
- Last Candle: {last_candle_type}

Based ONLY on the data above, formulate the strategy and reasoning.
{format_instructions}
"""

class TechnicalAgent:
    """Independent Technical Analysis Agent."""
    
    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.parser = PydanticOutputParser(pydantic_object=LLMStrategy)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", _TECH_SYSTEM_PROMPT),
            ("human", _TECH_HUMAN_TEMPLATE)
        ]).partial(format_instructions=self.parser.get_format_instructions())
        
        self.chain = self.prompt | self.llm | RunnableLambda(_strip_json_markdown) | self.parser

    def analyze_structured(
        self,
        ticker: str,
        timeframe: str = "D1",
        temporal_context: Optional[dict] = None,
        mtf_matrix=None,
        mtf_scores_roles: Optional[dict] = None,
    ) -> tuple[TechnicalMetrics, TechnicalSignal, Optional[TechnicalInterpretation]]:
        """
        P0: خروجی کامل TechnicalSignal + تفسیر LLM (اختیاری).

        - TechnicalSignal کاملاً deterministic است.
        - LLM فقط narrative را تولید می‌کند؛ اگر شکست بخورد،
          TechnicalInterpretation=None و سیگنال همچنان قابل استفاده است.
        """
        metrics = calculate_technical_metrics(ticker, timeframe=timeframe)

        # df برای ATR/structure/swing لازم است؛ از همین محاسبهٔ آخرین fetch استفاده می‌کنیم.
        df = _last_frame_for_signal(ticker, timeframe)

        signal = build_technical_signal(
            ticker=ticker,
            timeframe=timeframe,
            metrics=metrics,
            df=df,
            mtf_matrix=mtf_matrix,
            mtf_scores_roles=mtf_scores_roles,
        )

        if signal.score is None or signal.direction_value == 0 and signal.score == 0.0:
            # هنوز از یک TechnicalSignal valid استفاده می‌کنیم؛ فقط LLM narrative را با خروجی
            # خنثی هم می‌توان تولید کرد اما برای صرفه‌جویی skipped می‌کنیم.
            return metrics, signal, None

        try:
            parser = PydanticOutputParser(pydantic_object=TechnicalInterpretation)
            prompt_tmpl = ChatPromptTemplate.from_messages([
                ("system", TECH_INTERPRETATION_SYSTEM_PROMPT),
                ("human", "{prompt_text}"),
            ]).partial(format_instructions=parser.get_format_instructions())
            chain = prompt_tmpl | self.llm | RunnableLambda(_strip_json_markdown) | parser
            interp = invoke_with_retry(chain, {
                "prompt_text": build_technical_narrative_prompt(signal),
            })
            if not isinstance(interp, TechnicalInterpretation):
                raise ValueError("LLM returned unexpected type")
            return metrics, signal, interp
        except Exception as exc:
            logger.error(f"[Tech Agent] Structured narrative failed for {ticker}: {exc}")
            return metrics, signal, None

    def analyze(self, ticker: str, timeframe: str = "D1", temporal_context: Optional[dict] = None) -> tuple[TechnicalMetrics, TechnicalReport]:
        """Fetches metrics, calls LLM for strategy, returns full report."""
        logger.info(f"[Tech Agent] Analyzing {ticker} on {timeframe}...")
        
        metrics = calculate_technical_metrics(ticker, timeframe=timeframe)
        
        # Determine Direction from Score
        if metrics.technical_score is None:
            direction = 0
            score = 0.0
            confidence = 0.0
            strategy = "Stand aside"
            reasoning = "Insufficient data to calculate technical score."
            return metrics, TechnicalReport(direction=direction, score=score, confidence=confidence, strategy=strategy, reasoning=reasoning)
            
        score = metrics.technical_score
        direction = 1 if score > 0.1 else (-1 if score < -0.1 else 0)
        confidence = metrics.technical_confidence or 0.0
        
        prompt_inputs = {
            "ticker": ticker,
            "timeframe": timeframe,
            "tech_score": f"{score:.2f}",
            "tech_confidence": f"{confidence:.2f}",
            "structure": f"{metrics.components.structure:.2f}" if metrics.components.structure is not None else "N/A",
            "trend": f"{metrics.components.trend:.2f}" if metrics.components.trend is not None else "N/A",
            "smc_loc": f"{metrics.components.smc_location:.2f}" if metrics.components.smc_location is not None else "N/A",
            "mtf_conf": f"{metrics.components.mtf_confluence:.2f}" if metrics.components.mtf_confluence is not None else "N/A",
            "liq_sweep": f"{metrics.components.liquidity_sweep:.2f}" if metrics.components.liquidity_sweep is not None else "N/A",
            "pdh_pdl": f"{metrics.components.pdh_pdl:.2f}" if metrics.components.pdh_pdl is not None else "N/A",
            "ote": f"{metrics.components.ote_zone:.2f}" if metrics.components.ote_zone is not None else "N/A",
            "liq_status": metrics.liquidity_sweep_status or "N/A",
            "pdh": f"{metrics.previous_day_high:.5f}" if metrics.previous_day_high is not None else "N/A",
            "pdl": f"{metrics.previous_day_low:.5f}" if metrics.previous_day_low is not None else "N/A",
            "pdh_pdl_status": metrics.pdh_pdl_status or "N/A",
            "ote_status": metrics.ote_status or "N/A",
            "htf_interval": metrics.htf_interval or "N/A",
            "htf_trend_status": metrics.htf_trend_status or "N/A",
            "mom": f"{metrics.components.momentum:.2f}" if metrics.components.momentum is not None else "N/A",
            "vol": f"{metrics.components.volatility:.2f}" if metrics.components.volatility is not None else "N/A",
            "current_price": f"{metrics.current_price:.4f}" if metrics.current_price else "N/A",
            "trend_status": metrics.trend_status or "Unknown",
            "adx_value": f"{metrics.adx_value:.1f}" if metrics.adx_value else "N/A",
            "chop_value": f"{metrics.chop_value:.1f}" if metrics.chop_value else "N/A",
            "recent_bos": metrics.recent_bos or "None detected",
            "recent_choch": metrics.recent_choch or "None detected",
            "active_bullish_ob": f"{metrics.active_bullish_ob:.4f}" if metrics.active_bullish_ob is not None else "None nearby",
            "active_bearish_ob": f"{metrics.active_bearish_ob:.4f}" if metrics.active_bearish_ob is not None else "None nearby",
            "active_bull_fvg_low": f"{metrics.active_bull_fvg_low:.4f}" if metrics.active_bull_fvg_low is not None else "N/A",
            "active_bull_fvg_high": f"{metrics.active_bull_fvg_high:.4f}" if metrics.active_bull_fvg_high is not None else "N/A",
            "active_bear_fvg_low": f"{metrics.active_bear_fvg_low:.4f}" if metrics.active_bear_fvg_low is not None else "N/A",
            "active_bear_fvg_high": f"{metrics.active_bear_fvg_high:.4f}" if metrics.active_bear_fvg_high is not None else "N/A",
            "rsi": f"{metrics.rsi:.1f}" if metrics.rsi is not None else "N/A",
            "macd_histogram": f"{metrics.macd_histogram:.4f}" if metrics.macd_histogram is not None else "N/A",
            "last_candle_type": metrics.last_candle_type or "N/A",
        }
        
        try:
            llm_strat = invoke_with_retry(self.chain, prompt_inputs)
            if not isinstance(llm_strat, LLMStrategy):
                raise ValueError("LLM returned unexpected type")
                
            logger.info(f"[Tech Agent] {ticker} Report: Dir={direction} Score={score:.2f} Conf={confidence:.2f}")
            return metrics, TechnicalReport(
                direction=direction, score=score, confidence=confidence,
                strategy=llm_strat.strategy, reasoning=llm_strat.reasoning
            )
            
        except Exception as exc:
            logger.error(f"[Tech Agent] LLM analysis failed for {ticker}: {exc}")
            return metrics, TechnicalReport(
                direction=direction, score=score, confidence=confidence,
                strategy="Stand aside", reasoning=f"Technical LLM narrative failed: {exc}"
            )

# ===========================================================================
# CLI Smoke Test
# ===========================================================================
if __name__ == "__main__":
    import os
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
        
    from langchain_openai import ChatOpenAI
    
    base_url = os.getenv("ARVAN_BASE_URL")
    api_key = os.getenv("ARVAN_API_KEY", "not-needed")
    
    if not base_url:
        print("Set ARVAN_BASE_URL env vars to test.")
        sys.exit(1)
        
    llm = ChatOpenAI(model="GLM-5.2", temperature=0.1, api_key=api_key, base_url=base_url)
    agent = TechnicalAgent(llm)
    
    print("=" * 60)
    print("Technical Agent (10-Factor Deterministic Scorer) Test")
    print("=" * 60)
    
    test_ticker = "EURUSD=X"
    print(f"\n>>> Analyzing {test_ticker} on H1")
    m, r = agent.analyze(test_ticker, timeframe="H1")
    
    print(f"\nDeterministic Math Layer:")
    print(f"  Technical Score: {r.score:.2f} | Confidence: {r.confidence:.2f} | Direction: {r.direction}")
    print(f"  Components: Struct={m.components.structure}, Trend={m.components.trend}, SMC={m.components.smc_location}")
    print(f"  Raw Context: Trend={m.trend_status}, BOS={m.recent_bos}, BullOB={m.active_bullish_ob}")
    
    print(f"\nLLM Narrative Layer:")
    print(f"  Strategy: {r.strategy}")
    print(f"  Reasoning: {r.reasoning}")