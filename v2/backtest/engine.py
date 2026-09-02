# backtest/engine.py
"""
backtest/engine.py
==================
موتور بک‌تست: تبدیل سری امتیاز به پوزیشن، اعمال هزینه‌های واقعی،
و محاسبه متریک‌های عملکرد (Sharpe / MaxDD / ProfitFactor / WinRate).

مدل اجرا (بدون lookahead):
  - سیگنال از close کندل t خوانده می‌شود
  - پوزیشن از کندل t+1 اعمال می‌شود (close-to-close)
  - هزینه (spread + slippage) در هر تغییر پوزیشن کسر می‌شود
  - slippage در روزهای ایونت High impact در ضریب EVENT_SLIPPAGE_MULTIPLIER ضرب می‌شود

قوانین پوزیشن:
  - score >= ENTRY_THRESHOLD و conf >= MIN_CONFIDENCE → Long
  - score <= -ENTRY_THRESHOLD و conf >= MIN_CONFIDENCE → Short
  - |score| < EXIT_BAND → خروج/فلت
  - سقف نگهداری MAX_HOLDING_BARS کندل
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from . import config

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252

# Phase 1 (W5): ثانیه‌ی یک سال معاملاتی (252 روز) — برای annualization
# بر پایه‌ی فاصله‌ی واقعی کندل‌ها از index
TRADING_SECONDS_PER_YEAR = TRADING_DAYS_PER_YEAR * 86400


def bars_per_year_from_index(index: pd.Index) -> float:
    """
    Phase 1 (W5): تخمین bars-per-year از فاصله‌ی median کندل‌های index.

    برای index نامنظم (گپ‌های آخر هفته در دیتای yfinance) median
    از mean مقاوم‌تر است. اگر قابل محاسبه نبود → DEFAULT_BARS_PER_YEAR.
    """
    try:
        deltas = pd.Series(index).diff().dropna()
        if len(deltas) >= 2:
            med = deltas.median()
            if med > pd.Timedelta(0):
                return TRADING_SECONDS_PER_YEAR / float(med.total_seconds())
    except Exception:
        pass
    return config.DEFAULT_BARS_PER_YEAR


# ===========================================================================
# Event-aware slippage (از دیتابیس پروژه)
# ===========================================================================

def load_high_impact_dates(currency: str) -> set:
    """
    تاریخ روزهایی که ایونت High impact برای این ارز داشتیم (از economic_events_history).
    اگر DB در دسترس نبود، مجموعه خالی → اسلیپیج پایه برای همه روزها.
    """
    dates: set = set()
    try:
        from core.database import SessionLocal, EconomicEventHistory
        session = SessionLocal()
        try:
            rows = (
                session.query(EconomicEventHistory.date)
                .filter(
                    EconomicEventHistory.currency == currency.upper(),
                    EconomicEventHistory.impact == "High",
                    EconomicEventHistory.date.isnot(None),
                )
                .all()
            )
            dates = {r[0].date() if hasattr(r[0], "date") else r[0] for r in rows}
        finally:
            session.close()
        logger.info("High-impact event days loaded for %s: %d", currency, len(dates))
    except Exception as exc:
        logger.warning("DB event days unavailable (%s) — flat slippage", exc)
    return dates


# ===========================================================================
# Position generation
# ===========================================================================

def scores_to_positions(
    df: pd.DataFrame,
    entry_threshold: float = config.ENTRY_THRESHOLD,
    exit_band: float = config.EXIT_BAND,
    min_confidence: float = config.MIN_CONFIDENCE,
    max_holding: int = config.MAX_HOLDING_BARS,
) -> pd.Series:
    """
    تبدیل سری score به سری position در {+1, 0, -1} (سیگنال close کندل t).

    state machine:
      flat → long/short فقط با |score| >= threshold و conf کافی
      in-position → خروج با |score| < exit_band یا اتمام max_holding
    """
    score = df["score"].values
    conf = df["confidence"].values
    n = len(df)

    positions = np.zeros(n, dtype=float)
    state = 0
    held = 0

    for i in range(n):
        s = score[i]
        c = conf[i]
        if np.isnan(s):
            positions[i] = state  # داده نیست → پوزیشن قبلی حفظ شود
            continue

        if state == 0:
            if c >= min_confidence and s >= entry_threshold:
                state = 1
                held = 0
            elif c >= min_confidence and s <= -entry_threshold:
                state = -1
                held = 0
        else:
            held += 1
            if abs(s) < exit_band or held >= max_holding:
                state = 0
                held = 0
            # flip مستقیم long→short مجاز است اگر سیگنال قوی خلاف جهت باشد
            elif state == 1 and c >= min_confidence and s <= -entry_threshold:
                state = -1
                held = 0
            elif state == -1 and c >= min_confidence and s >= entry_threshold:
                state = 1
                held = 0

        positions[i] = state

    return pd.Series(positions, index=df.index, name="position")


# ===========================================================================
# Backtest core
# ===========================================================================

@dataclass
class BacktestResult:
    """نتیجه کامل یک بک‌تست."""
    symbol: str
    label: str = ""
    bars: int = 0
    trades: int = 0
    total_return_pct: float = 0.0
    buy_hold_return_pct: float = 0.0
    cagr_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    win_rate_pct: float = 0.0
    avg_trade_pct: float = 0.0
    exposure_pct: float = 0.0
    total_cost_pct: float = 0.0
    bars_per_year: float = config.DEFAULT_BARS_PER_YEAR  # Phase 1 (W5)
    equity_curve: Optional[pd.Series] = field(default=None, repr=False)
    daily_returns: Optional[pd.Series] = field(default=None, repr=False)


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min() * 100)


def _profit_factor(trade_returns: list[float]) -> float:
    gains = sum(r for r in trade_returns if r > 0)
    losses = abs(sum(r for r in trade_returns if r < 0))
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def run_backtest(
    df: pd.DataFrame,
    symbol: str,
    label: str = "",
    high_impact_dates: Optional[set] = None,
    entry_threshold: float = config.ENTRY_THRESHOLD,
    min_confidence: float = config.MIN_CONFIDENCE,
    bars_per_year: Optional[float] = None,
) -> BacktestResult:
    """
    اجرای بک‌تست روی DataFrame خروجی walkforward (با ستون score/confidence/close).

    bars_per_year (Phase 1 / W5): اگر None، از فاصله‌ی واقعی index تخمین
    می‌شود (bars_per_year_from_index) — پیش‌فرض 252 فقط fallback است.
    """
    if bars_per_year is None:
        bars_per_year = bars_per_year_from_index(df.index)
    costs = config.SYMBOL_COSTS.get(symbol, config.DEFAULT_COSTS)
    pip_size = costs["pip_size"]

    signal_position = scores_to_positions(
        df, entry_threshold=entry_threshold, min_confidence=min_confidence
    )
    # اعمال از کندل بعدی (بدون lookahead)
    position = signal_position.shift(1).fillna(0.0)

    close = df["close"]
    log_ret = np.log(close / close.shift(1)).fillna(0.0)

    # --- هزینه‌ها: در هر تغییر پوزیشن، (spread + slippage) per side ---
    position_change = position.diff().abs().fillna(abs(position.iloc[0]))
    # flip مستقیم long→short دو ساید هزینه دارد؛ diff=2 → درست محاسبه می‌شود
    cost_fraction = (costs["spread_pips"] + costs["slippage_pips"]) * pip_size / close

    # ضریب روزهای ایونت High impact
    if high_impact_dates:
        event_mask = pd.Series(
            [1.0 if (d.date() if hasattr(d, "date") else d) in high_impact_dates else 0.0
             for d in df.index],
            index=df.index,
        )
        slip_mult = 1.0 + (config.EVENT_SLIPPAGE_MULTIPLIER - 1.0) * event_mask
        event_extra = (costs["slippage_pips"] * pip_size / close) * (slip_mult - 1.0)
        cost_fraction = cost_fraction + event_extra

    trade_cost = position_change * cost_fraction

    strategy_ret = position * log_ret - trade_cost
    equity = (1 + strategy_ret).cumprod()
    buy_hold = (1 + log_ret).cumprod()

    # --- شناسایی معاملات برای win rate / profit factor ---
    trade_returns: list[float] = []
    in_trade = False
    entry_equity = 1.0
    for i in range(len(position)):
        if not in_trade and position.iloc[i] != 0:
            in_trade = True
            entry_equity = equity.iloc[i]
        elif in_trade and position.iloc[i] == 0:
            trade_returns.append(float(equity.iloc[i] / entry_equity - 1))
            in_trade = False
    if in_trade:
        trade_returns.append(float(equity.iloc[-1] / entry_equity - 1))

    n_trades = len(trade_returns)
    wins = sum(1 for r in trade_returns if r > 0)

    n_bars = len(df)
    years = n_bars / bars_per_year
    total_ret = float(equity.iloc[-1] - 1)

    mean_r = float(strategy_ret.mean())
    std_r = float(strategy_ret.std())
    sharpe = (mean_r / std_r * np.sqrt(bars_per_year)) if std_r > 0 else 0.0
    downside = strategy_ret[strategy_ret < 0]
    sortino = (
        mean_r / float(downside.std()) * np.sqrt(bars_per_year)
        if len(downside) > 1 and float(downside.std()) > 0 else 0.0
    )

    return BacktestResult(
        symbol=symbol,
        label=label,
        bars=n_bars,
        trades=n_trades,
        total_return_pct=total_ret * 100,
        buy_hold_return_pct=float(buy_hold.iloc[-1] - 1) * 100,
        cagr_pct=((1 + total_ret) ** (1 / years) - 1) * 100 if years > 0 else 0.0,
        sharpe=sharpe,
        sortino=sortino,
        bars_per_year=bars_per_year,
        max_drawdown_pct=_max_drawdown(equity),
        profit_factor=_profit_factor(trade_returns),
        win_rate_pct=(wins / n_trades * 100) if n_trades else 0.0,
        avg_trade_pct=(float(np.mean(trade_returns)) * 100) if n_trades else 0.0,
        exposure_pct=float((position != 0).mean()) * 100,
        total_cost_pct=float(trade_cost.sum()) * 100,
        equity_curve=equity,
        daily_returns=strategy_ret,
    )
