import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # root v2

"""
evaluate_signals.py
===================
اسکریپت ارزیابی کیفیت سیگنال‌های فاندامنتال تولید شده توسط پایپ‌لاین رویدادها.

این اسکریپت ۴ لایه ارزیابی انجام می‌دهد:
1. ممیزی منطق و ریاضیات (تطابق جهت، قوانین معکوس، توزیع امتیازها)
2. بک‌تست رفتار قیمت (واکنش بازار ۲۴ ساعت پس از رویداد)
3. اعتبارسنجی نیمه‌عمر (Half-Life) و دارایی متقاطع (Cross-Asset)
4. بررسی کیفی استدلال‌ها (نمونه‌هایی از استدلال‌های LLM)
"""

import logging
import textwrap
from datetime import timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from core.database import SessionLocal, EventSignalDB
from core.routing import resolve_asset_route, translate_instrument_direction, RouteAlignment

logger = logging.getLogger(__name__)

# رویدادهایی که actual کمتر از forecast، یعنی صعودی ارز (Inverse Rules)
INVERSE_EVENTS_KEYWORDS = ["unemployment rate", "unemployment claims", "jobless claims"]


def is_inverse_event(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in INVERSE_EVENTS_KEYWORDS)

# ===========================================================================
# Layer 1: Logic & Math Audit
# ===========================================================================

def audit_logic_and_math():
    print("\n" + "=" * 80)
    print("  لایه ۱: ممیزی منطق و ریاضیات (Logic & Math Audit)")
    print("=" * 80)
    
    session = SessionLocal()
    try:
        signals = session.query(EventSignalDB).all()
        if not signals:
            print("هیچ سیگنالی در دیتابیس یافت نشد.")
            return

        total = len(signals)
        tradable_count = 0
        dir_sentiment_mismatch = 0
        inverse_rule_violation = 0
        surprise_mismatch = 0
        
        scores = []

        for sig in signals:
            if sig.is_tradable:
                tradable_count += 1
            
            scores.append(sig.final_score)
            
            # 1. Direction vs Sentiment check
            if sig.final_score > 0.05 and sig.direction != 1:
                dir_sentiment_mismatch += 1
            elif sig.final_score < -0.05 and sig.direction != -1:
                dir_sentiment_mismatch += 1
            elif abs(sig.final_score) <= 0.05 and sig.direction != 0:
                dir_sentiment_mismatch += 1
                
            # 2. Inverse Rule check
            if sig.surprise_value is not None and sig.surprise_value != 0:
                is_inverse = is_inverse_event(sig.event_title)
                if sig.surprise_value > 0:
                    expected_dir = -1 if is_inverse else 1
                    if sig.direction != expected_dir:
                        inverse_rule_violation += 1
                elif sig.surprise_value < 0:
                    expected_dir = 1 if is_inverse else -1
                    if sig.direction != expected_dir:
                        inverse_rule_violation += 1
                        
            # 3. Surprise Interpretation check
            if sig.surprise_value is not None:
                if sig.surprise_value > 0.01 and sig.surprise_interpretation != "beat":
                    surprise_mismatch += 1
                elif sig.surprise_value < -0.01 and sig.surprise_interpretation != "miss":
                    surprise_mismatch += 1
                elif abs(sig.surprise_value) <= 0.01 and sig.surprise_interpretation != "in_line":
                    surprise_mismatch += 1

        print(f"  کل سیگنال‌های بررسی شده : {total}")
        print(f"  سیگنال‌های Tradable     : {tradable_count} ({100*tradable_count/total:.1f}%)")
        print(f"  میانگین امتیاز (Score)  : {sum(scores)/total:.3f}")
        print(f"  بیشترین امتیاز         : {max(scores):.3f}")
        print(f"  کمترین امتیاز          : {min(scores):.3f}")
        print("-" * 80)
        print(f"  ⚠️ عدم تطابق جهت و امتیاز (Sentiment Mismatch): {dir_sentiment_mismatch}")
        print(f"  ⚠️ نقض قوانین معکوس (Inverse Rule Violation) : {inverse_rule_violation}")
        print(f"  ⚠️ خطا در تفسیر سورپرایز (Surprise Mismatch)  : {surprise_mismatch}")
        
        if dir_sentiment_mismatch == 0 and inverse_rule_violation == 0:
            print("\n  ✅ منطق امتیازدهی و قوانین جهت‌گیری کاملاً بدون خطا اجرا شده‌اند.")
        else:
            print("\n  ❌ خطاهایی در منطق وجود دارد. نیاز به بررسی پرامپت یا اسکورر دارد.")

    finally:
        session.close()


# ===========================================================================
# Layer 2: Price Action Backtest (24h)
# ===========================================================================

def get_market_data(ticker: str, event_date, hours: int = 24) -> Optional[pd.DataFrame]:
    """دریافت داده قیمت ساعتی برای بازه مشخص شده."""
    start = event_date - timedelta(hours=2)
    end = event_date + timedelta(hours=hours)
    
    try:
        data = yf.download(ticker, start=start, end=end, interval="1h", progress=False, auto_adjust=True)
        if data.empty or len(data) < 2:
            # fallback to daily if hourly not available
            data = yf.download(ticker, start=start, end=end + timedelta(days=1), progress=False, auto_adjust=True)
            if data.empty or len(data) < 2:
                return None
        return data
    except Exception as e:
        logger.debug(f"Price fetch failed for {ticker}: {e}")
        return None

def get_direction_from_data(data: pd.DataFrame, start_idx: int = 0, end_idx: int = -1) -> Optional[int]:
    """استخراج جهت حرکت از داده قیمت"""
    if data is None or data.empty:
        return None
    try:
        start_price = data["Close"].iloc[start_idx]
        end_price = data["Close"].iloc[end_idx]
        
        if isinstance(start_price, pd.Series): start_price = start_price.iloc[0]
        if isinstance(end_price, pd.Series): end_price = end_price.iloc[0]
        
        if end_price > start_price: return 1
        if end_price < start_price: return -1
        return 0
    except Exception:
        return None

def price_action_backtest():
    print("\n" + "=" * 80)
    print("  لایه ۲: بک‌تست قیمت (Price Action Backtest - 24h)")
    print("=" * 80)
    print("  (بررسی جهت حرکت بازار ۲۴ ساعت پس از رویداد در مقابل سیگنال تولید شده)")
    
    session = SessionLocal()
    try:
        signals = session.query(EventSignalDB).filter(
            EventSignalDB.is_tradable == True,
            EventSignalDB.event_date.isnot(None)
        ).all()
        
        if not signals:
            print("  هیچ سیگنال معامله‌پذیری با تاریخ مشخص برای بک‌تست یافت نشد.")
            return
            
        correct_predictions = 0
        incorrect_predictions = 0
        no_data_count = 0
        neutral_signals = 0
        
        for sig in signals:
            route = resolve_asset_route(sig.event_currency)
            if not route:
                continue
                
            data = get_market_data(sig.ticker, sig.event_date, hours=24)
            market_dir = get_direction_from_data(data)
            
            if market_dir is None:
                no_data_count += 1
                continue
                
            if sig.direction == 0:
                neutral_signals += 1
                continue
                
            # Convert native to instrument view for market comparison
            expected_instrument_dir = translate_instrument_direction(sig.direction, route.alignment)
            
            if market_dir == expected_instrument_dir:
                correct_predictions += 1
                status = "✅ HIT"
            else:
                incorrect_predictions += 1
                status = "❌ MISS"
                
            print(f"  {status} | {sig.event_title[:30]:<30} | Signal: {'Bull' if sig.direction==1 else 'Bear':<4} | Market: {'Up' if market_dir==1 else 'Down':<4} | Score: {sig.final_score:.2f}")

        total_evaluated = correct_predictions + incorrect_predictions
        print("-" * 80)
        if total_evaluated > 0:
            accuracy = 100 * correct_predictions / total_evaluated
            print(f"  کل سیگنال‌های ارزیابی شده: {total_evaluated}")
            print(f"  پیش‌بینی‌های درست        : {correct_predictions}")
            print(f"  پیش‌بینی‌های غلط         : {incorrect_predictions}")
            print(f"  دقت جهت‌گیری (Accuracy) : {accuracy:.1f}%")
        else:
            print("  داده کافی برای محاسبه دقت وجود ندارد.")
            
        if no_data_count > 0:
            print(f"  (توجه: {no_data_count} سیگنال به دلیل عدم دسترسی به داده قیمت در yfinance ارزیابی نشدند)")

    finally:
        session.close()


# ===========================================================================
# Layer 3: Half-Life and Cross-Asset Validation
# ===========================================================================

def half_life_and_cross_asset_validation():
    print("\n" + "=" * 80)
    print("  لایه ۳: اعتبارسنجی نیمه‌عمر و دارایی متقاطع (Half-Life & Cross-Asset)")
    print("=" * 80)
    
    session = SessionLocal()
    try:
        signals = session.query(EventSignalDB).filter(
            EventSignalDB.is_tradable == True,
            EventSignalDB.event_date.isnot(None)
        ).all()
        
        if not signals:
            print("  سیگنالی برای این ارزیابی یافت نشد.")
            return

        hl_hits = 0
        hl_misses = 0
        cross_hits = 0
        cross_misses = 0
        skipped = 0
        
        for sig in signals:
            if sig.direction == 0:
                skipped += 1
                continue
                
            data = get_market_data(sig.ticker, sig.event_date, hours=24)
            if data is None or len(data) < 2:
                skipped += 1
                continue
                
            # 1. Half-Life Check
            # half_life_mins is in minutes, data is 1h intervals (60 mins)
            # Ensure at least 1 candle is checked, cap at available data length
            hl_candles = max(1, int(sig.signal_half_life_mins / 60))
            hl_candles = min(hl_candles, len(data) - 1)
            
            market_dir_hl = get_direction_from_data(data, start_idx=0, end_idx=hl_candles)
            expected_dir = translate_instrument_direction(sig.direction, resolve_asset_route(sig.event_currency).alignment)
            
            if market_dir_hl is not None:
                if market_dir_hl == expected_dir:
                    hl_hits += 1
                    hl_status = "✅ HL HIT"
                else:
                    hl_misses += 1
                    hl_status = "❌ HL MISS"
            else:
                hl_status = "⏭ HL SKIP"

            # 2. Cross-Asset Check (only for USD signals vs Gold)
            cross_status = "⏭ XAU SKIP"
            if sig.event_currency == "USD":
                gold_data = get_market_data("GC=F", sig.event_date, hours=24)
                gold_dir = get_direction_from_data(gold_data)
                
                if gold_dir is not None:
                    # If USD is Bullish (1), Gold should be Bearish (-1)
                    expected_gold_dir = -sig.direction
                    if gold_dir == expected_gold_dir:
                        cross_hits += 1
                        cross_status = "✅ XAU HIT"
                    else:
                        cross_misses += 1
                        cross_status = "❌ XAU MISS"

            print(f"  {hl_status} | {cross_status} | {sig.event_title[:30]:<30} | HL: {sig.signal_half_life_mins}m")

        print("-" * 80)
        hl_total = hl_hits + hl_misses
        if hl_total > 0:
            print(f"  دقت نیمه‌عمر (Half-Life)   : {100*hl_hits/hl_total:.1f}% ({hl_hits}/{hl_total})")
        
        cross_total = cross_hits + cross_misses
        if cross_total > 0:
            print(f"  دقت دارایی متقاطع (XAU) : {100*cross_hits/cross_total:.1f}% ({cross_hits}/{cross_total})")
            
        if skipped > 0:
            print(f"  (توجه: {skipped} سیگنال به دلیل خنثی بودن یا نبود داده رد شدند)")

    finally:
        session.close()


# ===========================================================================
# Layer 4: Qualitative Reasoning Check
# ===========================================================================

def qualitative_check():
    print("\n" + "=" * 80)
    print("  لایه ۴: بررسی کیفی استدلال‌ها (Qualitative Reasoning Check)")
    print("=" * 80)
    
    session = SessionLocal()
    try:
        bull_signals = session.query(EventSignalDB).filter(
            EventSignalDB.direction == 1
        ).order_by(EventSignalDB.final_score.desc()).limit(3).all()
        
        bear_signals = session.query(EventSignalDB).filter(
            EventSignalDB.direction == -1
        ).order_by(EventSignalDB.final_score.asc()).limit(3).all()
        
        print("\n  🟢 نمونه سیگنال‌های صعودی قوی (Top Bullish):")
        for sig in bull_signals:
            print(f"\n  Event: {sig.event_title} ({sig.event_currency}) | Score: {sig.final_score:.3f}")
            print(f"  Data: Actual={sig.actual} Forecast={sig.forecast} Surprise={sig.surprise_interpretation}")
            print(f"  LLM Reasoning:")
            print(textwrap.indent(textwrap.fill(sig.reasoning, width=70), "    "))
            
        print("\n  🔴 نمونه سیگنال‌های نزولی قوی (Top Bearish):")
        for sig in bear_signals:
            print(f"\n  Event: {sig.event_title} ({sig.event_currency}) | Score: {sig.final_score:.3f}")
            print(f"  Data: Actual={sig.actual} Forecast={sig.forecast} Surprise={sig.surprise_interpretation}")
            print(f"  LLM Reasoning:")
            print(textwrap.indent(textwrap.fill(sig.reasoning, width=70), "    "))

    finally:
        session.close()


# ===========================================================================
# Main
# ===========================================================================
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING, # فقط ارورهای مهم yfinance را نشان بده
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    
    audit_logic_and_math()
    price_action_backtest()
    half_life_and_cross_asset_validation()
    qualitative_check()
    
    print("\n" + "=" * 80)
    print("  ارزیابی کامل شد.")
    print("=" * 80)