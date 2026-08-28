"""
execution/paper_broker.py
=========================
Paper Broker — موتور اجرای مجازی (Phase 8). صفر LLM، کاملاً deterministic
به‌جز اسلیپیج شبیه‌سازی‌شده (تصادفی یکنواخت با seed اختیاری).

مسئولیت‌ها:
  - نگهداری حساب مجازی (paper_account): بالانس، اکویتی
  - سایزینگ ریسک-ثابت:  risk 1% پایه، 0.5% برای پلن‌های counter_bias
  - فیل واقع‌بینانه: اسپرد همیشه علیه معامله‌گر + اسلیپیج ۰ تا ۰٫۵ پیپ
  - چک SL/TP پوزیشن‌های باز با قانون محافظه‌کار: کندل دومرکزی → SL اول
  - بستن: محاسبه PnL پولی، آزادسازی ریسک، نوشتن WIN/LOSS در trade_outcomes
    (همان جدولی که مموری پایپ‌لاین از آن می‌خواند — حلقه بازخورد می‌بندد)
  - ابطال/انقضای پلن‌های PENDING (هم‌راستا با watcher؛ race-safe)

قیمت‌دهی: نقشه CURRENCY_TICKER همان نگاشت پایپ‌لاین است.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Optional

from core.database import (
    PaperAccountDB,
    PaperPositionDB,
    StrategyPlanDB,
    TradeOutcomeDB,
    session_scope,
)

logger = logging.getLogger("paper_broker")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DEFAULT_BALANCE: float = 10_000.0
RISK_PCT_BASE: float = 0.01            # ۱٪ ریسک هر معامله
RISK_PCT_COUNTER_BIAS: float = 0.005   # نیم‌سایز برای پلن‌های ضدبایاس

# اسپرد ثابت هر نماد (به واحد قیمت همان نماد) — همیشه علیه معامله‌گر
SPREAD_MAP: dict[str, float] = {
    "EURUSD=X": 0.00010,   # ~1 pip
    "GBPUSD=X": 0.00012,
    "USDJPY=X": 0.010,
    "AUDUSD=X": 0.00012,
    "USDCHF=X": 0.00012,
    "USDCAD=X": 0.00013,
    "NZDUSD=X": 0.00013,
    "DX-Y.NYB": 0.020,     # DXY — شبیه‌سازی (اسپات قابل معامله نیست)
    "GC=F":     0.35,      # طلا فیوچرز
    "CL=F":     0.03,      # نفت فیوچرز
}
DEFAULT_SPREAD: float = 0.0002

# اسلیپیج تصادفی یکنواخت: ۰ تا این مقدار (به واحد قیمت)، روی ورود و خروج
SLIPPAGE_MAX_MAP: dict[str, float] = {
    "EURUSD=X": 0.00005,
    "GBPUSD=X": 0.00006,
    "USDJPY=X": 0.005,
    "DX-Y.NYB": 0.010,
    "GC=F":     0.15,
    "CL=F":     0.015,
}
DEFAULT_SLIPPAGE_MAX: float = 0.0001

# TTL پوزیشن باز از plan.expires_at خوانده می‌شود (همان TTL افق پلن) —
# اگر expires_at نال بود، این سقف جایگزین اعمال می‌شود
DEFAULT_POSITION_TTL_H: float = 24.0

CURRENCY_TICKER: dict[str, str] = {
    "USD": "DX-Y.NYB",
    "EUR": "EURUSD=X",
    "GBP": "GBPUSD=X",
    "JPY": "USDJPY=X",
    "AUD": "AUDUSD=X",
    "CHF": "USDCHF=X",
    "CAD": "USDCAD=X",
    "NZD": "NZDUSD=X",
    "XAU": "GC=F",
    "OIL": "CL=F",
}


# ---------------------------------------------------------------------------
# حساب
# ---------------------------------------------------------------------------

def get_or_create_account(initial_balance: float = DEFAULT_BALANCE) -> PaperAccountDB:
    """ردیف تک‌تای حساب را برمی‌گرداند؛ اگر نبود، با بالانس اولیه می‌سازد."""
    with session_scope() as session:
        acc = session.query(PaperAccountDB).order_by(PaperAccountDB.id.asc()).first()
        if acc is None:
            acc = PaperAccountDB(balance=initial_balance, equity=initial_balance, margin_used=0.0)
            session.add(acc)
            session.flush()
            logger.info(f"[PaperBroker] Account created with balance {initial_balance:.2f}")
        # جدا کردن آبجکت از سشن برای استفاده بیرون
        session.expunge(acc)
        return acc


def _update_equity(session, floating_pnl: float) -> PaperAccountDB:
    acc = session.query(PaperAccountDB).order_by(PaperAccountDB.id.asc()).first()
    acc.equity = acc.balance + floating_pnl
    acc.updated_at = datetime.utcnow()
    return acc


# ---------------------------------------------------------------------------
# سایزینگ و فیل
# ---------------------------------------------------------------------------

def size_position(balance: float, entry_price: float, stop_loss: float,
                  counter_bias: bool = False) -> tuple[float, float]:
    """
    سایز پوزیشن با ریسک ثابت.
    خروجی: (size_units, risk_pct)
    size = (balance × risk_pct) ÷ |entry − sl|
    """
    risk_pct = RISK_PCT_COUNTER_BIAS if counter_bias else RISK_PCT_BASE
    dist = abs(entry_price - stop_loss)
    if dist <= 0:
        return 0.0, risk_pct
    size = (balance * risk_pct) / dist
    return size, risk_pct


def _apply_spread_slippage(ticker: str, direction: int, price: float,
                           is_entry: bool, rng: random.Random) -> float:
    """
    اسپرد + اسلیپیج — همیشه علیه معامله‌گر.
    لانگ: خرید با ask (بالاتر) / فروش با bid (پایین‌تر). شورت برعکس.
    """
    half_spread = SPREAD_MAP.get(ticker, DEFAULT_SPREAD) / 2.0
    slip = rng.uniform(0.0, SLIPPAGE_MAX_MAP.get(ticker, DEFAULT_SLIPPAGE_MAX))
    adverse = half_spread + slip
    if (direction == 1 and is_entry) or (direction == -1 and not is_entry):
        return price + adverse   # خرید لانگ / بازپس‌خرید شورت → گران‌تر
    return price - adverse       # فروش لانگ / فروش شورت → ارزان‌تر


def fill_plan(plan_id: int, ticker: str, current_price: float,
              now: Optional[datetime] = None, rng: Optional[random.Random] = None) -> Optional[int]:
    """
    تبدیل یک پلن PENDING به پوزیشن OPEN — race-safe در برابر watcher:
    فقط اگر status هنوز دقیقاً 'PENDING' باشد فیل می‌کند (یک تراکنش اتمیک).

    خروجی: id پوزیشن ساخته‌شده، یا None اگر پلن دیگر PENDING نبود.
    """
    now = now or datetime.utcnow()
    rng = rng or random.Random()
    with session_scope() as session:
        plan = session.query(StrategyPlanDB).filter(
            StrategyPlanDB.id == plan_id,
            StrategyPlanDB.status == "PENDING",
        ).first()
        if plan is None:
            return None  # watcher یا executor دیگر زودتر رسیده

        acc = session.query(PaperAccountDB).order_by(PaperAccountDB.id.asc()).first()
        if acc is None:
            logger.error("[PaperBroker] no paper account — call get_or_create_account first")
            return None

        # فیل روی بدترین لبه ناحیه (محافظه‌کارانه) + اسپرد + اسلیپیج
        raw_entry = plan.entry_high if plan.direction == 1 else plan.entry_low
        entry = _apply_spread_slippage(ticker, plan.direction, raw_entry, is_entry=True, rng=rng)

        size, risk_pct = size_position(acc.balance, entry, plan.stop_loss,
                                       counter_bias=bool(plan.counter_bias))
        if size <= 0:
            plan.status = "INVALIDATED"
            plan.triggered_at = now
            logger.warning(f"[PaperBroker] plan #{plan.id} zero distance to SL — invalidated")
            return None

        pos = PaperPositionDB(
            plan_id=plan.id, currency=plan.currency, timeframe=plan.timeframe,
            direction=plan.direction, status="OPEN",
            size_units=size, risk_pct=risk_pct, entry_price=entry,
            stop_loss=plan.stop_loss, take_profit=plan.take_profit,
            counter_bias=plan.counter_bias, opened_at=now,
            expires_at=plan.expires_at or (now + timedelta(hours=DEFAULT_POSITION_TTL_H)),
        )
        session.add(pos)
        session.flush()

        plan.status = "FILLED"
        plan.triggered_at = now

        # مارجین اشغال‌شده = ریسک درگیر این پوزیشن (مدل ساده)
        acc.margin_used = (acc.margin_used or 0.0) + acc.balance * risk_pct
        acc.updated_at = now

        logger.info(
            f"[PaperBroker] FILLED plan #{plan.id} {plan.currency} {plan.timeframe} "
            f"dir={plan.direction} entry={entry:.5f} size={size:.2f} "
            f"risk={risk_pct*100:.2f}% sl={plan.stop_loss:.5f} tp={plan.take_profit:.5f}"
        )
        return pos.id


# ---------------------------------------------------------------------------
# بستن پوزیشن
# ---------------------------------------------------------------------------

def _close_position(session, pos: PaperPositionDB, ticker: str, raw_exit: float,
                    reason: str, now: datetime, rng: random.Random) -> float:
    """بستن پوزیشن در قیمت خام + اسپرد/اسلیپیج علیه معامله‌گر. PnL را برمی‌گرداند."""
    exit_price = _apply_spread_slippage(ticker, pos.direction, raw_exit, is_entry=False, rng=rng)
    if pos.direction == 1:
        pnl = (exit_price - pos.entry_price) * pos.size_units
    else:
        pnl = (pos.entry_price - exit_price) * pos.size_units

    pos.status = {
        "tp": "CLOSED_TP", "sl": "CLOSED_SL",
        "timeout": "CLOSED_TIMEOUT", "invalid": "CLOSED_INVALID",
    }[reason]
    pos.closed_at = now
    pos.exit_price = exit_price
    pos.pnl_money = pnl
    pos.close_reason = reason

    acc = session.query(PaperAccountDB).order_by(PaperAccountDB.id.asc()).first()
    acc.balance += pnl
    acc.margin_used = max(0.0, (acc.margin_used or 0.0) - (acc.balance - pnl) * pos.risk_pct)
    acc.updated_at = now

    # بستن حلقه مموری: نتیجه به همان جدولی می‌رود که پایپ‌لاین از آن می‌خواند
    plan = session.query(StrategyPlanDB).filter(StrategyPlanDB.id == pos.plan_id).first()
    outcome = TradeOutcomeDB(
        created_at=pos.opened_at,
        currency=pos.currency,
        direction=pos.direction,
        entry_zone_low=pos.entry_price,
        entry_zone_high=pos.entry_price,
        stop_loss=pos.stop_loss,
        take_profit=pos.take_profit,
        fusion_state="paper_execution",
        status="WIN" if pnl > 0 else "LOSS",
        closed_at=now,
        evaluated_price=exit_price,
        timeframe=pos.timeframe,
        plan_id=pos.plan_id,
        decision_reasoning=(plan.reasoning if plan else None),
        entry_filled_at=pos.opened_at,
    )
    session.add(outcome)

    logger.info(
        f"[PaperBroker] CLOSED pos #{pos.id} {pos.currency} dir={pos.direction} "
        f"reason={reason} exit={exit_price:.5f} pnl={pnl:+.2f} balance={acc.balance:.2f}"
    )
    return pnl


def check_open_positions(price_map: dict[str, float],
                         candle_map: Optional[dict[str, tuple[float, float]]] = None,
                         now: Optional[datetime] = None,
                         rng: Optional[random.Random] = None) -> list[int]:
    """
    چک SL/TP همه پوزیشن‌های باز.

    price_map:  {ticker: آخرین قیمت}
    candle_map: {ticker: (low, high) آخرین کندل ۱دقیقه‌ای} — اختیاری ولی توصیه‌شده؛
                اگر هر دو SL و TP در یک کندل لمس شوند → SL اول (محافظه‌کار).

    خروجی: لیست id پوزیشن‌های بسته‌شده در این تیک.
    """
    now = now or datetime.utcnow()
    rng = rng or random.Random()
    closed: list[int] = []
    with session_scope() as session:
        opens = session.query(PaperPositionDB).filter(PaperPositionDB.status == "OPEN").all()
        for pos in opens:
            ticker = CURRENCY_TICKER.get(pos.currency)
            price = price_map.get(ticker)
            if price is None:
                continue

            lo, hi = (candle_map or {}).get(ticker, (price, price))

            if pos.direction == 1:
                sl_hit = lo <= pos.stop_loss
                tp_hit = hi >= pos.take_profit
            else:
                sl_hit = hi >= pos.stop_loss
                tp_hit = lo <= pos.take_profit

            if sl_hit:  # قانون محافظه‌کار: در تداخل، SL اول
                _close_position(session, pos, ticker, pos.stop_loss, "sl", now, rng)
                closed.append(pos.id)
            elif tp_hit:
                _close_position(session, pos, ticker, pos.take_profit, "tp", now, rng)
                closed.append(pos.id)

            # TTL پوزیشن باز = expires_at پلن مبنا (همان TTL افق: SWING 120h و...)
            if pos.id not in closed and pos.expires_at and now >= pos.expires_at:
                _close_position(session, pos, ticker, price, "timeout", now, rng)
                closed.append(pos.id)

        # به‌روز رسانی اکویتی با PnL شناور باقی‌مانده‌ها
        # نکته: SessionLocal با autoflush=False ساخته شده — تغییر وضعیت‌های همین
        # سشن در کوئری بعدی دیده نمی‌شود. پس: flush + حذف صریح بسته‌شده‌ها.
        session.flush()
        closed_ids = set(closed)
        remaining = [
            p for p in session.query(PaperPositionDB)
            .filter(PaperPositionDB.status == "OPEN").all()
            if p.id not in closed_ids
        ]
        floating = 0.0
        for pos in remaining:
            ticker = CURRENCY_TICKER.get(pos.currency)
            price = price_map.get(ticker)
            if price is None:
                continue
            if pos.direction == 1:
                floating += (price - pos.entry_price) * pos.size_units
            else:
                floating += (pos.entry_price - price) * pos.size_units
        _update_equity(session, floating)

    return closed


# ---------------------------------------------------------------------------
# چرخه عمر پلن‌های PENDING (هم‌راستا با watcher — race-safe)
# ---------------------------------------------------------------------------

def sync_pending_plans(price_map: dict[str, float],
                       now: Optional[datetime] = None) -> dict[str, list[int]]:
    """
    روی پلن‌های PENDING:
      - now > expires_at            → EXPIRED
      - عبور از invalidation_price  → INVALIDATED
      - ورود به ناحیه ورود          → fill_plan (پوزیشن OPEN)

    خروجی: {"filled": [...ids], "invalidated": [...], "expired": [...]}
    """
    now = now or datetime.utcnow()
    result: dict[str, list[int]] = {"filled": [], "invalidated": [], "expired": []}

    with session_scope() as session:
        pendings = session.query(StrategyPlanDB).filter(StrategyPlanDB.status == "PENDING").all()
        candidates = []
        for plan in pendings:
            ticker = CURRENCY_TICKER.get(plan.currency)
            price = price_map.get(ticker)
            if price is None:
                continue
            if plan.expires_at and now > plan.expires_at:
                plan.status = "EXPIRED"
                result["expired"].append(plan.id)
                continue
            if plan.invalidation_price is not None:
                if (plan.direction == 1 and price < plan.invalidation_price) or \
                   (plan.direction == -1 and price > plan.invalidation_price):
                    plan.status = "INVALIDATED"
                    result["invalidated"].append(plan.id)
                    continue
            if plan.entry_low <= price <= plan.entry_high:
                candidates.append((plan.id, ticker, price))
        # کامیت وضعیت‌های EXPIRED/INVALIDATED همین‌جا (با session_scope)

    # فیل در تراکنش جداگانه و race-safe (چک مجدد status==PENDING داخل fill_plan)
    for plan_id, ticker, price in candidates:
        pos_id = fill_plan(plan_id, ticker, price, now=now)
        if pos_id is not None:
            result["filled"].append(plan_id)

    return result


# ---------------------------------------------------------------------------
# گزارش
# ---------------------------------------------------------------------------

def account_summary() -> dict:
    """خلاصه حساب برای لاگ/CLI."""
    with session_scope() as session:
        acc = session.query(PaperAccountDB).order_by(PaperAccountDB.id.asc()).first()
        if acc is None:
            return {"balance": None, "equity": None, "open": 0, "closed": 0,
                    "wins": 0, "losses": 0, "win_rate": 0.0}
        open_n = session.query(PaperPositionDB).filter(PaperPositionDB.status == "OPEN").count()
        closed = session.query(PaperPositionDB).filter(PaperPositionDB.status != "OPEN").all()
        wins = sum(1 for p in closed if (p.pnl_money or 0) > 0)
        losses = len(closed) - wins
        wr = (wins / len(closed) * 100.0) if closed else 0.0
        return {
            "balance": acc.balance, "equity": acc.equity,
            "margin_used": acc.margin_used,
            "open": open_n, "closed": len(closed),
            "wins": wins, "losses": losses, "win_rate": wr,
        }
