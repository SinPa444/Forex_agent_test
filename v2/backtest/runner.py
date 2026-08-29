# backtest/runner.py
"""
backtest/runner.py
==================
رانر اصلی بک‌تست فاز ۴ — با تقسیم In-Sample / Out-of-Sample.

استفاده:
    python -m backtest.runner --currencies USD EUR XAU OIL
    python -m backtest.runner --currencies EUR --timeframe D1 --years 5

خروجی:
  - جدول متریک‌ها برای IS و OOS هر نماد
  - داوری PASS/FAIL بر اساس استانداردهای فاز ۳ (روی OOS)
  - ذخیره equity curve ها در backtest/results/
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Optional

import pandas as pd

from . import config
from .engine import BacktestResult, load_high_impact_dates, run_backtest
from .walkforward import compute_score_series

logger = logging.getLogger(__name__)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


# ===========================================================================
# IS/OOS split
# ===========================================================================

def split_is_oos(df: pd.DataFrame, oos_ratio: float = config.OOS_RATIO):
    """تقسیم زمانی: OOS همیشه انتهای سری است (بدون shuffle — داده سری زمانی است)."""
    n = len(df)
    cut = int(n * (1 - oos_ratio))
    return df.iloc[:cut], df.iloc[cut:]


# ===========================================================================
# Reporting
# ===========================================================================

def _verdict(r: BacktestResult) -> str:
    """داوری بر اساس معیارهای OOS."""
    checks = [
        r.sharpe >= config.PASS_SHARPE,
        abs(r.max_drawdown_pct) <= config.PASS_MAX_DD_PCT,
        r.profit_factor >= config.PASS_PROFIT_FACTOR,
        r.trades >= 10,  # تعداد معامله کافی برای معناداری آماری
    ]
    passed = sum(checks)
    if passed == 4:
        return "PASS ✅"
    if passed == 3:
        return "MARGINAL 🟡"
    return "FAIL ❌"


def print_result(r: BacktestResult) -> None:
    print(f"    bars={r.bars}  trades={r.trades}  exposure={r.exposure_pct:.0f}%")
    print(f"    Total Return : {r.total_return_pct:+.1f}%   (Buy&Hold: {r.buy_hold_return_pct:+.1f}%)")
    print(f"    CAGR         : {r.cagr_pct:+.1f}%")
    print(f"    Sharpe       : {r.sharpe:.2f}   Sortino: {r.sortino:.2f}")
    print(f"    Max Drawdown : {r.max_drawdown_pct:.1f}%")
    print(f"    Profit Factor: {r.profit_factor:.2f}")
    print(f"    Win Rate     : {r.win_rate_pct:.0f}%   Avg Trade: {r.avg_trade_pct:+.2f}%")
    print(f"    Costs Paid   : {r.total_cost_pct:.2f}% of equity")
    print(f"    Verdict      : {_verdict(r)}")


def run_symbol_backtest(
    currency: str,
    timeframe: str = "D1",
    years: int = 5,
    save_equity: bool = True,
) -> Optional[dict]:
    ticker = config.TICKER_MAP.get(currency.upper())
    if not ticker:
        logger.error("نمادی برای %s تعریف نشده", currency)
        return None

    print(f"\n{'=' * 60}")
    print(f"  {currency} ({ticker}) — {timeframe} — {years}y")
    print(f"{'=' * 60}")

    scored = compute_score_series(ticker, timeframe=timeframe, years=years)
    if scored is None or scored.empty:
        print("  ✗ داده کافی برای بک‌تست نیست")
        return None

    high_impact = load_high_impact_dates(currency)

    is_df, oos_df = split_is_oos(scored)

    r_is = run_backtest(is_df, ticker, label="IS", high_impact_dates=high_impact)
    r_oos = run_backtest(oos_df, ticker, label="OOS", high_impact_dates=high_impact)
    r_full = run_backtest(scored, ticker, label="FULL", high_impact_dates=high_impact)

    print("\n  ── In-Sample (۷۰٪ اول) ──")
    print_result(r_is)
    print("\n  ── Out-of-Sample (۳۰٪ آخر) ──  ← داوری اصلی روی این است")
    print_result(r_oos)
    print("\n  ── Full Period ──")
    print_result(r_full)

    # هشدار overfitting
    if r_is.sharpe > 1.5 and r_oos.sharpe < 0.5:
        print("\n  ⚠ OVERFITTING WARNING: Sharpe خوب روی IS ولی ضعیف روی OOS")

    if save_equity:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        eq = pd.DataFrame({
            "strategy": r_full.equity_curve,
            "buy_hold": (1 + scored["close"].pct_change().fillna(0)).cumprod(),
            "score": scored["score"],
        })
        path = os.path.join(RESULTS_DIR, f"equity_{currency}_{timeframe}.csv")
        eq.to_csv(path)
        print(f"\n  equity curve saved → {path}")

    return {"IS": r_is, "OOS": r_oos, "FULL": r_full}


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 — Realistic Backtest Runner")
    parser.add_argument("--currencies", nargs="+", default=["EUR"],
                        help="e.g. USD EUR XAU OIL")
    parser.add_argument("--timeframe", default="D1",
                        choices=["M15", "H1", "H4", "D1"])
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  PHASE 4 — Realistic Backtest (IS/OOS, real costs)")
    print(f"  Entry≥{config.ENTRY_THRESHOLD} | ExitBand<{config.EXIT_BAND} "
          f"| MinConf={config.MIN_CONFIDENCE} | MaxHold={config.MAX_HOLDING_BARS}bars")
    print("=" * 60)

    all_results = {}
    for ccy in args.currencies:
        try:
            res = run_symbol_backtest(
                ccy, timeframe=args.timeframe, years=args.years,
                save_equity=not args.no_save,
            )
            if res:
                all_results[ccy] = res
        except Exception as exc:
            logger.error("بک‌تست %s شکست خورد: %s", ccy, exc, exc_info=True)

    # --- جدول خلاصه نهایی ---
    if all_results:
        print("\n" + "=" * 78)
        print("  FINAL SUMMARY (Out-of-Sample — تنها معیار قابل اعتماد)")
        print("=" * 78)
        print(f"  {'Symbol':<8}{'Trades':>7}{'Return':>9}{'Sharpe':>8}{'MaxDD':>8}"
              f"{'PF':>7}{'WinR':>7}  Verdict")
        print("  " + "-" * 74)
        for ccy, res in all_results.items():
            r = res["OOS"]
            print(f"  {ccy:<8}{r.trades:>7}{r.total_return_pct:>+8.1f}%"
                  f"{r.sharpe:>8.2f}{r.max_drawdown_pct:>7.1f}%"
                  f"{r.profit_factor:>7.2f}{r.win_rate_pct:>6.0f}%  {_verdict(r)}")

        n_pass = sum(1 for res in all_results.values()
                     if _verdict(res["OOS"]).startswith("PASS"))
        print(f"\n  {n_pass}/{len(all_results)} symbols passed OOS criteria "
              f"(Sharpe≥{config.PASS_SHARPE}, MaxDD≤{config.PASS_MAX_DD_PCT}%, "
              f"PF≥{config.PASS_PROFIT_FACTOR})")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
