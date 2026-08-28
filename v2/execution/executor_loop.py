"""
execution/executor_loop.py
==========================
Executor — لوپ تند لایه اجرا (Phase 8). هر ۶۰ ثانیه (قابل تنظیم):

  ۱. یک کال دسته‌ای yfinance (1m) برای همه تیکرها + retry با backoff
  ۲. check_open_positions → بستن SL/TP/timeout + به‌روز رسانی اکویتی
  ۳. sync_pending_plans  → فیل پلن‌هایی که قیمت به ناحیه‌شان رسیده

صفر LLM. صفر اینجشن. فقط قیمت و حساب‌کتاب.

تفکیک مسئولیت:
  - این لوپ = «مدیریت پوزیشن» (تصمیم از قبل گرفته شده)
  - live_watcher (۱۵ دقیقه) = «تشخیص موقعیت جدید» (تولید پلن توسط پایپ‌لاین)

اجرا:
  # لوپ دائمی هر ۶۰ ثانیه:
  python -m execution.executor_loop --loop --interval 60

  # یک تیک (تست/cron):
  python -m execution.executor_loop --once

  # مشخص‌کردن بالانس اولیه (فقط اولین اجرا اثر دارد):
  python -m execution.executor_loop --loop --balance 10000
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from datetime import datetime
from typing import Optional

from core.database import init_db
from execution.paper_broker import (
    CURRENCY_TICKER,
    account_summary,
    check_open_positions,
    get_or_create_account,
    sync_pending_plans,
)

logger = logging.getLogger("executor")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

FETCH_MAX_ATTEMPTS: int = 3
FETCH_BACKOFF_SEC: float = 5.0
MINUTE_BAR_PERIOD: str = "1d"      # دیتای 1m فقط ۷ روز اخیر — 1d برای پولینگ کافی است


# ---------------------------------------------------------------------------
# واکشی قیمت (دسته‌ای، صفر LLM)
# ---------------------------------------------------------------------------

def fetch_prices(tickers: list[str]) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """
    یک کال دسته‌ای yf.download برای همه تیکرها با اینتروال ۱ دقیقه.

    خروجی:
      price_map:  {ticker: آخرین close}
      candle_map: {ticker: (low, high) آخرین کندل} — برای قانون SL-اول
    """
    import yfinance as yf

    price_map: dict[str, float] = {}
    candle_map: dict[str, tuple[float, float]] = {}

    data = yf.download(
        tickers=" ".join(tickers),
        period=MINUTE_BAR_PERIOD,
        interval="1m",
        progress=False,
        group_by="ticker",
        auto_adjust=False,
    )
    if data is None or len(data) == 0:
        return price_map, candle_map

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                df = data
            else:
                df = data[ticker]
            df = df.dropna(subset=["Close"])
            if len(df) == 0:
                continue
            last = df.iloc[-1]
            price_map[ticker] = float(last["Close"])
            candle_map[ticker] = (float(last["Low"]), float(last["High"]))
        except Exception as exc:
            logger.warning(f"[Executor] price parse failed for {ticker}: {exc}")

    return price_map, candle_map


def fetch_prices_with_retry(tickers: list[str]):
    for attempt in range(1, FETCH_MAX_ATTEMPTS + 1):
        try:
            prices, candles = fetch_prices(tickers)
            if prices:
                return prices, candles
            logger.warning(f"[Executor] empty price data (attempt {attempt}/{FETCH_MAX_ATTEMPTS})")
        except Exception as exc:
            logger.warning(f"[Executor] fetch failed (attempt {attempt}/{FETCH_MAX_ATTEMPTS}): {exc}")
        if attempt < FETCH_MAX_ATTEMPTS:
            time.sleep(FETCH_BACKOFF_SEC * attempt)
    return {}, {}


# ---------------------------------------------------------------------------
# تیک
# ---------------------------------------------------------------------------

def run_tick(currencies: list[str], rng: Optional[random.Random] = None) -> dict:
    """
    یک تیک اجرا. خروجی: خلاصه رویدادهای این تیک (برای لاگ/تست).
    """
    now = datetime.utcnow()
    tickers = [CURRENCY_TICKER[c] for c in currencies if c in CURRENCY_TICKER]
    if not tickers:
        logger.warning("[Executor] no known tickers to poll")
        return {"closed": [], "filled": [], "invalidated": [], "expired": []}

    price_map, candle_map = fetch_prices_with_retry(tickers)
    if not price_map:
        logger.error("[Executor] no prices this tick — skipping")
        return {"closed": [], "filled": [], "invalidated": [], "expired": []}

    closed = check_open_positions(price_map, candle_map=candle_map, now=now, rng=rng)
    sync_res = sync_pending_plans(price_map, now=now)

    summary = {
        "closed": closed,
        "filled": sync_res["filled"],
        "invalidated": sync_res["invalidated"],
        "expired": sync_res["expired"],
    }
    acc = account_summary()
    logger.info(
        f"[Executor] tick done | closed={len(closed)} filled={len(sync_res['filled'])} "
        f"inv={len(sync_res['invalidated'])} exp={len(sync_res['expired'])} | "
        f"open={acc['open']} balance={acc['balance']} equity={acc['equity']}"
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Executor — fast position-management loop, zero LLM")
    parser.add_argument("--currencies", nargs="+",
                        default=["USD", "EUR", "GBP", "JPY", "AUD", "CHF", "CAD", "NZD", "XAU", "OIL"])
    parser.add_argument("--interval", type=int, default=60, help="Loop interval seconds (default 60)")
    parser.add_argument("--loop", action="store_true", help="Run forever")
    parser.add_argument("--once", action="store_true", help="Single tick then exit")
    parser.add_argument("--balance", type=float, default=10000.0,
                        help="Initial balance (only used on first account creation)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    init_db()
    acc = get_or_create_account(initial_balance=args.balance)
    logger.info(f"[Executor] account: balance={acc.balance:.2f} equity={acc.equity:.2f}")

    if args.once or not args.loop:
        run_tick(args.currencies)
        return

    logger.info(f"[Executor] Loop mode: every {args.interval}s for {', '.join(args.currencies)}")
    while True:
        try:
            run_tick(args.currencies)
        except Exception as exc:
            logger.error(f"[Executor] tick failed: {exc}", exc_info=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
