"""
orchestration/live_watcher.py
=============================
Live Watcher — لایه تشخیص ارزانِ بدون LLM که هر ربع (یا هر فاصله دلخواه)
چک می‌کند آیا اتفاق مهمی افتاده و فقط در آن صورت پایپ‌لاین اصلی
(run_phase3) را برای ارزهای تریگرشده بیدار می‌کند.

معماری دو مرحله‌ای:
  ۱. آشکارسازی (detector): چهار تریگر pure-Python — تقویم، اخبار، قیمت/نوسان، رژیم
  ۲. گیت شدت (severity gate): تغییر صرف کافی نیست؛ اهمیت لازم است.
     خبرهای ریز RSS انباشته می‌شوند و وقتی به آستانه رسیدند فایر می‌کنند.

مصرف LLM این ماژول: صفر. هیچ importی از LLM انجام نمی‌شود.

اجرا:
  # یک تیک (مناسب cron هر ۱۵ دقیقه):
  python -m orchestration.live_watcher --currencies USD EUR OIL XAU

  # بدون اجرای واقعی پایپ‌لاین (فقط گزارش تریگرها):
  python -m orchestration.live_watcher --currencies USD EUR OIL XAU --dry-run

  # حلقه داخلی (جایگزین cron):
  python -m orchestration.live_watcher --currencies USD EUR OIL XAU --loop --interval 900
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import datetime as dt

logger = logging.getLogger("live_watcher")

# ===========================================================================
# CONFIG
# ===========================================================================

STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "watcher_state.json"
)

# --- گیت شدت ---
SEVERITY_THRESHOLD: float = 0.5        # حداقل شدت برای فایر
COMBINED_THRESHOLD: float = 0.8        # مجموع دو تریگر برتر برای فایر ترکیبی
WEEKEND_THRESHOLD_MULT: float = 1.5    # آخر هفته آستانه سخت‌تر می‌شود

# --- تریگر تقویم ---
CAL_LOOKAHEAD_MIN: int = 60            # ایونت در ۶۰ دقیقه آینده
CAL_RELEASE_WINDOW_H: float = 2.0      # actualی که در ۲ ساعت اخیر رسیده

# --- تریگر اخبار ---
NEWS_LOOKBACK_H: int = 48              # هم‌راستا با پنجره phase2_graph
NEWS_TOP_N: int = 20                   # هم‌راستا با برش top-10 دایجست
NEWS_MIN_RELIABILITY: float = 0.8
NEWS_MIN_RELIABLE_COUNT: int = 2
NEWS_PER_ITEM_SCORE: float = 0.1
NEWS_PER_ITEM_CAP: float = 0.3

MARKET_MOVING_CATEGORIES = {
    "Interest Rate", "Inflation", "Employment", "GDP", "Central Bank",
}
MARKET_MOVING_KEYWORDS = (
    "rate decision", "interest rate", "fomc", "ecb", "boj", "boe", "fed ",
    "cpi", "inflation", "nfp", "nonfarm", "payroll", "gdp", "pce",
    "central bank", "powell", "lagarde", "ueda", "unemployment",
)

# --- تریگر قیمت/نوسان (H1) ---
PRICE_MOVE_ATR_HIGH: float = 1.0       # حرکت ۱ ساعته > ۱×ATR → شدت بالا
PRICE_MOVE_ATR_MED: float = 0.5        # حرکت > ۰٫۵×ATR → شدت متوسط
ATR_SPIKE_RATIO: float = 1.5           # ATR(6) / ATR(48) روی H1
SR_LOOKBACK_BARS: int = 20             # سقف/کف ۲۰ کندل اخیر H1 به‌عنوان S/R ساده

# --- ضد نوسان ---
COOLDOWN_MIN: int = 60                 # بعد از فایر، تریگر عادی همان ارز suppressed
MAX_STALENESS_H: float = 3.0           # سقف کهنگی: تحلیل اجباری (۰ = خاموش)

# --- fan-out وابستگی ---
# تریگر USD روی دارایی‌های USD-quoted هم اثر می‌گذارد
FANOUT_MAP: dict[str, list[str]] = {
    "USD": ["EUR", "XAU", "OIL", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"],
}


# ===========================================================================
# State
# ===========================================================================

class WatcherState:
    """وضعیت پایدار watcher در یک فایل JSON — بین تیک‌ها زنده می‌ماند."""

    def __init__(self, path: str = STATE_PATH):
        self.path = path
        self.data: dict = {"currencies": {}, "seen_actual_keys": []}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
        except Exception as exc:
            logger.warning(f"[Watcher] Failed to load state ({exc}) — starting fresh.")
            self.data = {"currencies": {}, "seen_actual_keys": []}

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as exc:
            logger.error(f"[Watcher] Failed to save state: {exc}")

    def ccy(self, currency: str) -> dict:
        return self.data["currencies"].setdefault(currency, {
            "news_links": [],            # لینک‌های top-10 آخرین وضعیت
            "pending_items": [],         # آیتم‌های جدیدِ انباشته‌شده زیر آستانه
            "last_regime": None,
            "last_fired_at": None,
            "last_analyzed_at": None,
        })

    @property
    def seen_actual_keys(self) -> set:
        return set(self.data.get("seen_actual_keys", []))

    def mark_actual_seen(self, key: str) -> None:
        keys = self.data.setdefault("seen_actual_keys", [])
        if key not in keys:
            keys.append(key)
        # جلوگیری از رشد بی‌رویه فایل state
        del keys[:-500]


# ===========================================================================
# Trigger Result
# ===========================================================================

@dataclass
class Trigger:
    source: str          # calendar / news / price / regime / fanout / staleness
    severity: float
    detail: str
    bypass_cooldown: bool = False  # فقط ریلیز تازه (اطلاعات جدید) cooldown را می‌شکند


# ===========================================================================
# Trigger 1: تقویم اقتصادی
# ===========================================================================

def check_calendar(currency: str, state: WatcherState, now: datetime) -> list[Trigger]:
    """
    دو حالت:
      - ایونت High/Medium در CAL_LOOKAHEAD_MIN دقیقه آینده (پیش‌بیداری)
      - ایونتی که actual آن تازه رسیده (ریلیز) — یک بار فایر می‌شود
    """
    from core.database import SessionLocal, EventHistoryDB

    triggers: list[Trigger] = []
    session = SessionLocal()
    try:
        # الف) ایونت پیش‌رو
        lookahead = now + timedelta(minutes=CAL_LOOKAHEAD_MIN)
        upcoming = session.query(EventHistoryDB).filter(
            EventHistoryDB.currency == currency,
            EventHistoryDB.impact.in_(["High", "Medium"]),
            EventHistoryDB.date >= now,
            EventHistoryDB.date <= lookahead,
        ).all()

        for ev in upcoming:
            sev = 1.0 if ev.impact == "High" else 0.5
            mins = max(0, int((ev.date - now).total_seconds() / 60)) if ev.date else 0
            triggers.append(Trigger(
                "calendar", sev,
                f"{ev.impact} event '{ev.title}' in ~{mins}min",
            ))

        # ب) ریلیز تازه (actual تازه رسیده)
        release_cutoff = now - timedelta(hours=CAL_RELEASE_WINDOW_H)
        released = session.query(EventHistoryDB).filter(
            EventHistoryDB.currency == currency,
            EventHistoryDB.impact.in_(["High", "Medium"]),
            EventHistoryDB.actual.isnot(None),
            EventHistoryDB.date >= release_cutoff,
            EventHistoryDB.date <= now,
        ).all()

        seen = state.seen_actual_keys
        for ev in released:
            key = f"{ev.title}|{ev.date}|{ev.actual}"
            if key in seen:
                continue
            state.mark_actual_seen(key)
            sev = 1.0 if ev.impact == "High" else 0.5
            triggers.append(Trigger(
                "calendar", sev,
                f"fresh release '{ev.title}': actual={ev.actual} vs forecast={ev.forecast}",
                bypass_cooldown=True,  # اطلاعات تازه وارد بازار شده — حتی در cooldown
            ))
    except Exception as exc:
        logger.warning(f"[Watcher][{currency}] calendar check failed: {exc}")
    finally:
        session.close()
    return triggers


# ===========================================================================
# Trigger 2: اخبار (تغییر هش + گیت اهمیت + انباشت)
# ===========================================================================

def _news_item_severity_contrib(items: list[dict]) -> float:
    """
    شدت تجمیعی مجموعه‌ای از آیتم‌های خبری جدید.
    آیتم‌ها به شکل dict با کلیدهای link/title/impact/reliability/category هستند.
    """
    if not items:
        return 0.0
    sev = 0.0

    if any((it.get("impact") or "").strip().title() == "High" for it in items):
        sev += 0.6

    reliable = [it for it in items if (it.get("reliability") or 0.0) >= NEWS_MIN_RELIABILITY]
    if len(reliable) >= NEWS_MIN_RELIABLE_COUNT:
        sev += 0.4

    def is_market_moving(it: dict) -> bool:
        cat = (it.get("category") or "").strip()
        if cat in MARKET_MOVING_CATEGORIES:
            return True
        title = (it.get("title") or "").lower()
        return any(kw in title for kw in MARKET_MOVING_KEYWORDS)

    if any(is_market_moving(it) for it in items):
        sev += 0.3

    sev += min(len(items) * NEWS_PER_ITEM_SCORE, NEWS_PER_ITEM_CAP)
    return min(sev, 1.0)


def check_news(currency: str, state: WatcherState, threshold: float) -> list[Trigger]:
    """
    هش لینک‌های top-N خبر تازه را با state مقایسه می‌کند.
    تغییر هش به‌تنهایی فایر نمی‌کند؛ آیتم‌های جدید از گیت اهمیت رد می‌شوند.
    آیتم‌های زیر آستانه در state انباشته می‌شوند تا بعداً تجمیعاً فایر کنند.
    """
    from core.database import SessionLocal, RawNewsItemDB

    session = SessionLocal()
    try:
        cutoff = dt.datetime.utcnow() - timedelta(hours=NEWS_LOOKBACK_H)
        records = (
            session.query(RawNewsItemDB)
            .filter(
                RawNewsItemDB.currency == currency,
                RawNewsItemDB.published_at.isnot(None),
                RawNewsItemDB.published_at >= cutoff,
            )
            .order_by(RawNewsItemDB.published_at.desc())
            .limit(NEWS_TOP_N * 3)   # کمی بیشتر می‌خوانیم تا top-N پایدار انتخاب شود
            .all()
        )
    except Exception as exc:
        logger.warning(f"[Watcher][{currency}] news query failed: {exc}")
        return []
    finally:
        session.close()

    if not records:
        return []

    # مرتب‌سازی هم‌راستا با phase2_graph: تازه‌ترین اول، tie-break با reliability
    records.sort(key=lambda r: (r.published_at.timestamp(), r.source_reliability or 0.0), reverse=True)
    top = records[:NEWS_TOP_N]

    current_links = sorted([r.link for r in top if r.link])
    if not current_links:
        return []

    st = state.ccy(currency)
    old_links = set(st.get("news_links", []))
    current_set = set(current_links)

    if current_set == old_links:
        return []  # هیچ تغییری — حتی گیت هم لازم نیست

    # آیتم‌های واقعاً جدید این تیک
    new_items = [
        {
            "link": r.link,
            "title": r.title,
            "impact": r.impact,
            "reliability": r.source_reliability or 0.0,
            "category": r.category,
        }
        for r in top if r.link and r.link not in old_links
    ]

    # انباشت: آیتم‌های جدید به pending اضافه می‌شوند و روی کل pending امتیاز می‌گیریم
    pending = st.get("pending_items", [])
    pending_links = {p.get("link") for p in pending}
    for it in new_items:
        if it["link"] not in pending_links:
            pending.append(it)
    st["pending_items"] = pending

    # state را به‌روز کن (حتی اگر فایر نکنیم — مبنای مقایسه تیک بعد)
    st["news_links"] = current_links

    sev = _news_item_severity_contrib(pending)
    if sev >= threshold:
        st["pending_items"] = []  # پاک‌سازی انباشت بعد از فایر
        return [Trigger(
            "news", sev,
            f"{len(pending)} accumulated new item(s) passed importance gate "
            f"({len(new_items)} this tick)",
        )]

    if new_items:
        logger.info(
            f"[Watcher][{currency}] news changed but below gate "
            f"(sev={sev:.2f} < {threshold}, pending={len(pending)})"
        )
    return []


# ===========================================================================
# Trigger 3: قیمت و نوسان (pure pandas روی yfinance)
# ===========================================================================

def _fetch_h1(ticker: str):
    """کندل‌های H1 پنج روز اخیر — ارزان و بدون LLM."""
    import yfinance as yf
    df = yf.download(ticker, period="5d", interval="1h", progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    import pandas as pd
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    return df.dropna(subset=["close"])


def check_price(currency: str, ticker: str, is_weekend: bool) -> list[Trigger]:
    """جهش نوسان، حرکت بزرگ ۱ساعته، شکست سقف/کف ۲۰ کندل اخیر."""
    if is_weekend:
        return []  # بازار بسته — داده قیمت معتبر نیست

    try:
        df = _fetch_h1(ticker)
    except Exception as exc:
        logger.warning(f"[Watcher][{currency}] price fetch failed for {ticker}: {exc}")
        return []
    if df is None or len(df) < 50:
        return []

    close = df["close"]
    high = df["high"] if "high" in df.columns else close
    low = df["low"] if "low" in df.columns else close

    tr = (high - low).rolling(1).max()  # تقریب TR بدون گپ (کافی برای watcher)
    atr14 = tr.rolling(14).mean()
    atr_now = float(atr14.iloc[-1]) if atr14.iloc[-1] == atr14.iloc[-1] else 0.0
    if atr_now <= 0:
        return []

    price = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    move_atr = abs(price - prev) / atr_now

    triggers: list[Trigger] = []

    if move_atr >= PRICE_MOVE_ATR_HIGH:
        triggers.append(Trigger("price", 0.8, f"1h move {move_atr:.1f}xATR on {ticker}"))
    elif move_atr >= PRICE_MOVE_ATR_MED:
        triggers.append(Trigger("price", 0.4, f"1h move {move_atr:.1f}xATR on {ticker}"))

    atr_short = tr.rolling(6).mean().iloc[-1]
    atr_base = tr.rolling(48).mean().iloc[-1]
    if atr_base and atr_base == atr_base and atr_base > 0:
        ratio = float(atr_short / atr_base)
        if ratio >= ATR_SPIKE_RATIO:
            triggers.append(Trigger("price", 0.7, f"ATR spike ratio {ratio:.2f} on {ticker}"))

    recent_high = float(high.iloc[-SR_LOOKBACK_BARS - 1:-1].max())
    recent_low = float(low.iloc[-SR_LOOKBACK_BARS - 1:-1].min())
    if price > recent_high:
        triggers.append(Trigger("price", 0.7, f"breakout above {SR_LOOKBACK_BARS}-bar high on {ticker}"))
    elif price < recent_low:
        triggers.append(Trigger("price", 0.7, f"breakdown below {SR_LOOKBACK_BARS}-bar low on {ticker}"))

    return triggers


# ===========================================================================
# Trigger 5 (Phase 7): رسیدن قیمت به زون پلن‌های در انتظار
# ===========================================================================

def check_zone_hit(currency: str, ticker: str, now: datetime) -> list[Trigger]:
    """
    پلن‌های PENDING ثبت‌شده توسط Strategy Engine را پایش می‌کند:
      - قیمت داخل زون ورود → پلن TRIGGERED می‌شود و پایپ‌لاین بیدار می‌شود
        (اطلاعات جدید واقعی → cooldown را می‌شکند)
      - عبور از invalidation_price قبل از ورود → پلن INVALIDATED
      - گذشتن از expires_at → پلن EXPIRED
    """
    from core.database import SessionLocal, StrategyPlanDB

    triggers: list[Trigger] = []
    session = SessionLocal()
    try:
        plans = (
            session.query(StrategyPlanDB)
            .filter(
                StrategyPlanDB.currency == currency,
                StrategyPlanDB.status == "PENDING",
            )
            .all()
        )
        if not plans:
            return []

        try:
            df = _fetch_h1(ticker)
            price = float(df["close"].iloc[-1]) if df is not None and not df.empty else None
        except Exception as exc:
            logger.warning(f"[Watcher][{currency}] price fetch for zone-hit failed: {exc}")
            return []
        if price is None:
            return []

        for plan in plans:
            # انقضا
            if plan.expires_at and now > plan.expires_at:
                plan.status = "EXPIRED"
                logger.info(f"[Watcher][{currency}] plan #{plan.id} expired ({plan.timeframe}).")
                continue

            # ابطال قبل از ورود
            if plan.invalidation_price is not None:
                if plan.direction == 1 and price < plan.invalidation_price:
                    plan.status = "INVALIDATED"
                    logger.info(f"[Watcher][{currency}] plan #{plan.id} invalidated (price {price} < {plan.invalidation_price}).")
                    continue
                if plan.direction == -1 and price > plan.invalidation_price:
                    plan.status = "INVALIDATED"
                    logger.info(f"[Watcher][{currency}] plan #{plan.id} invalidated (price {price} > {plan.invalidation_price}).")
                    continue

            # ورود به زون
            if plan.entry_low is not None and plan.entry_high is not None \
                    and plan.entry_low <= price <= plan.entry_high:
                plan.status = "TRIGGERED"
                plan.triggered_at = now
                triggers.append(Trigger(
                    "zone_hit", 0.9,
                    f"price {price:.5f} entered pending {plan.horizon_role}/{plan.timeframe} "
                    f"zone [{plan.entry_low}-{plan.entry_high}] (plan #{plan.id})",
                    bypass_cooldown=True,
                ))
        session.commit()
    except Exception as exc:
        logger.warning(f"[Watcher][{currency}] zone-hit check failed: {exc}")
    finally:
        session.close()
    return triggers


def run_trade_evaluator() -> None:
    """
    ارزیاب معاملات PENDING در هر تیک — حلقه حافظه همیشه بسته می‌ماند.
    خطاها بلع می‌شوند تا تیک watcher زنده بماند.
    """
    try:
        from orchestration.trade_evaluator import evaluate_pending_trades
        evaluate_pending_trades()
    except ImportError:
        logger.debug("[Watcher] trade_evaluator not available — memory loop skipped.")
    except Exception as exc:
        logger.warning(f"[Watcher] trade evaluator failed: {exc}")


# ===========================================================================
# Trigger 4: تغییر رژیم (پلنر rule-based — رایگان)
# ===========================================================================

def check_regime(currency: str, ticker: str, state: WatcherState) -> list[Trigger]:
    from agents.supervisor.head_agent import create_plan_rule_based

    try:
        plan = create_plan_rule_based(currency, ticker)
    except Exception as exc:
        logger.warning(f"[Watcher][{currency}] regime check failed: {exc}")
        return []

    st = state.ccy(currency)
    last = st.get("last_regime")
    st["last_regime"] = plan.market_regime

    if last is not None and last != plan.market_regime:
        return [Trigger("regime", 0.5, f"regime changed {last} -> {plan.market_regime}")]
    return []


# ===========================================================================
# fan-out وابستگی‌ها
# ===========================================================================

def apply_fanout(
    per_ccy_triggers: dict[str, list[Trigger]],
    universe: list[str],
) -> dict[str, list[Trigger]]:
    """تریگرهای USD به دارایی‌های USD-quoted داخل universe هم کپی می‌شوند."""
    out = {c: list(t) for c, t in per_ccy_triggers.items()}
    for src, deps in FANOUT_MAP.items():
        src_trigs = per_ccy_triggers.get(src, [])
        if not src_trigs:
            continue
        best = max(src_trigs, key=lambda t: t.severity)
        for dep in deps:
            if dep not in universe or dep == src:
                continue
            out.setdefault(dep, []).append(
                Trigger("fanout", best.severity, f"{src} trigger: {best.detail}")
            )
    return out


# ===========================================================================
# تجمیع، cooldown، staleness
# ===========================================================================

def decide_fire(
    triggers: list[Trigger],
    threshold: float,
) -> bool:
    """فایر اگر بهترین تریگر ≥ آستانه، یا دو تریگر برتر با هم ≥ آستانه ترکیبی."""
    if not triggers:
        return False
    sevs = sorted((t.severity for t in triggers), reverse=True)
    if sevs[0] >= threshold:
        return True
    if len(sevs) >= 2 and sevs[0] + sevs[1] >= COMBINED_THRESHOLD:
        return True
    return False


def in_cooldown(st: dict, now: datetime, triggers: list[Trigger]) -> bool:
    """
    بعد از فایر، تریگرهای بعدی همان ارز تا COOLDOWN_MIN suppressed می‌شوند —
    مگر تریگری که bypass_cooldown=True دارد (ریلیز تازه = اطلاعات جدید واقعی).
    پیش‌بیداری ایونتِ آینده cooldown را نمی‌شکند چون همان رویدادی است که
    قبلاً برایش فایر کرده‌ایم.
    """
    last = st.get("last_fired_at")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return False
    if (now - last_dt).total_seconds() < COOLDOWN_MIN * 60:
        return not any(t.bypass_cooldown for t in triggers)
    return False


def staleness_trigger(st: dict, now: datetime) -> list[Trigger]:
    """اگر ارز بیش از MAX_STALENESS_H تحلیل نشده، تحلیل اجباری."""
    if MAX_STALENESS_H <= 0:
        return []
    last = st.get("last_analyzed_at")
    if not last:
        return [Trigger("staleness", 0.5, "never analyzed")]
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return [Trigger("staleness", 0.5, "invalid last_analyzed_at")]
    hours = (now - last_dt).total_seconds() / 3600.0
    if hours >= MAX_STALENESS_H:
        return [Trigger("staleness", 0.5, f"last analysis {hours:.1f}h ago")]
    return []


# ===========================================================================
# تیک اصلی
# ===========================================================================

def run_tick(
    currencies: list[str],
    *,
    do_ingest: bool = True,
    dry_run: bool = False,
    state_path: str = STATE_PATH,
) -> list[str]:
    """
    یک چرخه کامل watcher. خروجی: لیست ارزهایی که پایپ‌لاین برایشان فایر شد.
    """
    from core.routing import resolve_asset_route

    now = dt.datetime.utcnow()
    state = WatcherState(state_path)

    # فیلتر سشن از temporal context موجود
    is_weekend = False
    try:
        from agents.fundamental.phase2_graph import get_temporal_context
        tctx = get_temporal_context()
        is_weekend = bool(tctx.get("is_weekend") or tctx.get("is_major_holiday"))
    except Exception as exc:
        logger.warning(f"[Watcher] temporal context unavailable ({exc}) — assuming weekday.")

    threshold = SEVERITY_THRESHOLD * (WEEKEND_THRESHOLD_MULT if is_weekend else 1.0)

    # مرحله ۰: ingestion ارزان (RSS + FF scrape — بدون LLM)
    if do_ingest:
        try:
            from orchestration.run_phase3 import run_ingestion_phase
            run_ingestion_phase()
        except Exception as exc:
            logger.error(f"[Watcher] ingestion failed: {exc} — continuing with stale data.")

    # مرحله ۰٫۵: ارزیاب معاملات باز — حلقه حافظه همیشه بسته می‌ماند
    run_trade_evaluator()

    # مرحله ۱: تریگرهای per-currency
    per_ccy: dict[str, list[Trigger]] = {}
    for ccy in currencies:
        route = resolve_asset_route(ccy)
        ticker = route.ticker if route else None

        trigs: list[Trigger] = []
        trigs += check_calendar(ccy, state, now)
        trigs += check_news(ccy, state, threshold)
        if ticker:
            trigs += check_price(ccy, ticker, is_weekend)
            trigs += check_zone_hit(ccy, ticker, now)   # Phase 7
            trigs += check_regime(ccy, ticker, state)

        st = state.ccy(ccy)
        trigs += staleness_trigger(st, now)

        if trigs:
            per_ccy[ccy] = trigs

    # مرحله ۲: fan-out (مثلاً USD → XAU/OIL/EUR)
    per_ccy = apply_fanout(per_ccy, currencies)

    # مرحله ۳: گیت شدت + cooldown
    targets: list[str] = []
    for ccy, trigs in per_ccy.items():
        st = state.ccy(ccy)
        if not decide_fire(trigs, threshold):
            for t in trigs:
                logger.info(f"[Watcher][{ccy}] below gate: [{t.source}] {t.severity:.2f} — {t.detail}")
            continue
        if in_cooldown(st, now, trigs):
            logger.info(f"[Watcher][{ccy}] triggers suppressed by cooldown.")
            continue

        targets.append(ccy)
        st["last_fired_at"] = now.isoformat()
        for t in sorted(trigs, key=lambda x: -x.severity):
            logger.info(f"[Watcher][{ccy}] FIRED [{t.source}] {t.severity:.2f} — {t.detail}")

    state.save()

    # مرحله ۴: بیدار کردن پایپ‌لاین
    if not targets:
        logger.info("[Watcher] No triggers fired. Pipeline stays asleep. Cost: 0 LLM calls.")
        return []

    logger.info(f"[Watcher] Waking pipeline for: {', '.join(targets)}")
    if dry_run:
        print(f"[DRY-RUN] would run: python -m orchestration.run_phase3 --currencies {' '.join(targets)} --skip-ingestion")
        return targets

    cmd = [sys.executable, "-m", "orchestration.run_phase3",
           "--currencies", *targets, "--skip-ingestion"]
    try:
        result = subprocess.run(cmd, timeout=3600)
        if result.returncode == 0:
            for ccy in targets:
                state.ccy(ccy)["last_analyzed_at"] = now.isoformat()
            state.save()
        else:
            logger.error(f"[Watcher] pipeline exited with code {result.returncode}")
    except Exception as exc:
        logger.error(f"[Watcher] failed to launch pipeline: {exc}")

    return targets


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Live Watcher — cheap trigger detection, zero LLM")
    parser.add_argument("--currencies", nargs="+", default=["USD", "EUR", "OIL", "XAU"])
    parser.add_argument("--dry-run", action="store_true", help="Report triggers without launching pipeline")
    parser.add_argument("--no-ingest", action="store_true", help="Skip RSS/FF ingestion this tick")
    parser.add_argument("--loop", action="store_true", help="Run forever (alternative to cron)")
    parser.add_argument("--interval", type=int, default=900, help="Loop interval seconds (default 900 = 15min)")
    parser.add_argument("--state", type=str, default=STATE_PATH, help="Path to watcher state JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    if not args.loop:
        run_tick(args.currencies, do_ingest=not args.no_ingest,
                 dry_run=args.dry_run, state_path=args.state)
        return

    logger.info(f"[Watcher] Loop mode: every {args.interval}s for {', '.join(args.currencies)}")
    while True:
        try:
            run_tick(args.currencies, do_ingest=not args.no_ingest,
                     dry_run=args.dry_run, state_path=args.state)
        except Exception as exc:
            logger.error(f"[Watcher] tick failed: {exc}", exc_info=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
