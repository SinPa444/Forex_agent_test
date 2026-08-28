# database.py
"""
database.py
===========
تعریف تمام SQLAlchemy ORM مدل‌ها و اتصال به دیتابیس SQLite.

جدول‌ها:
  ۱. speakers              — سخنرانان تأثیرگذار بازار
  ۲. economic_events_history — تاریخچه رویدادهای اقتصادی
  ۳. trading_signals        — سیگنال‌های توییت/بیانیه (nlp_x)
  ۴. news_signals           — سیگنال‌های خبری (nlp_news)
"""

from __future__ import annotations

import contextlib
import datetime
import os
from datetime import timedelta
from typing import Any, Iterable, Iterator, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# =====================================================================
# اتصال به دیتابیس
# =====================================================================

Base = declarative_base()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_agent.db")
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """
    Applied automatically to every new DBAPI connection (including ones
    opened by ``create_all`` / migrations / SessionLocal()). Each pragma
    below is chosen for a specific, testable reason — nothing here is
    added "just in case":

      * ``journal_mode=WAL``   — readers no longer block the writer (and
        vice versa). This is the single biggest fix for the
        "unnecessary database/session activity" / locking symptoms
        described for this project, and it persists on disk as the
        journal mode of the database file itself.
      * ``synchronous=NORMAL`` — safe durability level when combined with
        WAL (SQLite still guarantees consistency after an OS crash;
        only an extremely rare power-loss scenario could lose the last
        WAL commit). Notably faster than the default FULL.
      * ``busy_timeout=5000``  — let SQLite retry for up to 5s on a
        locked database instead of raising immediately.
      * ``foreign_keys=ON``    — SQLite disables FK enforcement by
        default per connection; this project's schema doesn't declare
        FKs today, but turning it on is free and future-proof.
      * ``temp_store=MEMORY``  — SQLite's own temporary B-trees/sorts
        stay off disk, which speeds up ORDER BY / GROUP BY without
        touching application-level RAM usage.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA temp_store=MEMORY")
    finally:
        cursor.close()


# =====================================================================
# ۱. SpeakerDB — سخنرانان
# =====================================================================

class SpeakerDB(Base):
    """
    جدول speakers: اطلاعات سخنرانان تأثیرگذار بازار.

    مثال:
      name="Jerome Powell", role="Fed Chair",
      primary_asset="USD", weight=1.0
    """

    __tablename__ = "speakers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    role = Column(String, nullable=False)
    primary_asset = Column(String, nullable=False)
    weight = Column(Float, nullable=False, default=0.5)
    twitter_handle = Column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<SpeakerDB name={self.name!r} weight={self.weight}>"


# =====================================================================
# ۲. EventHistoryDB — تاریخچه رویدادهای اقتصادی
# =====================================================================

class EventHistoryDB(Base):
    """
    جدول economic_events_history: ذخیره رویدادهای اقتصادی گذشته و جاری.

    data_fetcher.py از این جدول برای محاسبه historical_std استفاده
    می‌کند — انحراف معیار (actual - forecast) رویدادهای مشابه.

    forex_factory_crawler.py رویدادها را در این جدول ذخیره می‌کند.
    Duplicate detection بر اساس (title + currency + date) انجام می‌شود.

    مثال:
      title="US CPI YoY", actual=3.8, forecast=3.5, previous=3.6
    """

    __tablename__ = "economic_events_history"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    category = Column(String, nullable=True)
    currency = Column(String, nullable=True, index=True)
    impact = Column(String, nullable=True)
    date = Column(DateTime, nullable=True, index=True)

    # مقادیر عددی (parse شده)
    actual = Column(Float, nullable=True)
    forecast = Column(Float, nullable=True)
    previous = Column(Float, nullable=True)

    # مقادیر رشته‌ای اصلی (برای audit)
    raw_actual_str = Column(String, nullable=True)
    raw_forecast_str = Column(String, nullable=True)
    raw_previous_str = Column(String, nullable=True)

    # metadata
    source = Column(String, nullable=True, default="Forex Factory")
    fetched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)
    
    # new
    detail_text = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<EventHistoryDB title={self.title!r} "
            f"currency={self.currency!r} "
            f"actual={self.actual} forecast={self.forecast}>"
        )


# نام مستعار — data_fetcher.py از این اسم استفاده می‌کند
EconomicEventHistory = EventHistoryDB


# =====================================================================
# ۳. TradingSignalDB — سیگنال‌های توییت/بیانیه
# =====================================================================

class TradingSignalDB(Base):
    """
    جدول trading_signals: سیگنال‌های تولید شده از nlp_x.py.

    نسخه ارتقایافته با audit trail کامل.
    تمام ستون‌های جدید nullable=True هستند تا
    رکوردهای قدیمی خراب نشوند (backward compatible).
    """

    __tablename__ = "trading_signals"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    # اطلاعات رویداد / متن
    event_title = Column(String, nullable=True)
    statement = Column(Text, nullable=True)

    # نتایج تحلیل (فیلدهای اصلی — موجود از قبل)
    reasoning = Column(Text, nullable=True)
    asset_class = Column(String, nullable=True)
    direction = Column(Integer, nullable=True)
    final_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    is_tradable = Column(Boolean, nullable=True)
    expected_volatility_level = Column(String, nullable=True)

    # ─── فیلدهای جدید (همه nullable برای سازگاری) ───

    # اطلاعات سخنران
    speaker_name = Column(String, nullable=True, index=True)
    speaker_role = Column(String, nullable=True)
    speaker_weight = Column(Float, nullable=True)

    # اطلاعات منبع
    source = Column(String, nullable=True, index=True)
    source_type = Column(String, nullable=True)
    source_reliability = Column(Float, nullable=True)
    link = Column(Text, nullable=True)
    external_id = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True)

    # متادیتای scoring
    statement_type_weight = Column(Float, nullable=True)
    data_completeness_score = Column(Float, nullable=True)
    data_quality_factor = Column(Float, nullable=True)
    statement_market_alignment = Column(Float, nullable=True)
    quantitative_alignment = Column(Float, nullable=True)
    signal_half_life_mins = Column(Integer, nullable=True)
    ticker = Column(String, nullable=True, index=True)

    # تحلیل سیاستی
    policy_signal_type = Column(String, nullable=True)
    impact_horizon = Column(String, nullable=True)

    # payload خام برای audit trail
    raw_text = Column(Text, nullable=True)
    raw_market_context_json = Column(Text, nullable=True)
    raw_input_payload_json = Column(Text, nullable=True)

    def __repr__(self) -> str:
        dir_map = {1: "BULL", -1: "BEAR", 0: "NEUT"}
        return (
            f"<TradingSignalDB id={self.id} "
            f"{self.asset_class} {dir_map.get(self.direction, '?')} "
            f"score={self.final_score} tradable={self.is_tradable} "
            f"speaker={self.speaker_name!r}>"
        )


# =====================================================================
# ۴. NewsSignalDB — سیگنال‌های خبری
# =====================================================================

class NewsSignalDB(Base):
    """
    جدول news_signals: سیگنال‌های تولید شده از nlp_news.py.
    اکنون از سیگنال‌های تک‌خبری و تجمیعی (Currency Macro Digest) پشتیبانی می‌کند.
    """

    __tablename__ = "news_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        nullable=False,
        index=True,
    )

    # منبع خبر (در حالت تجمیعی، source می‌تواند "Aggregated" باشد)
    source = Column(String(64), nullable=False, index=True)
    title = Column(Text, nullable=True)  # nullable شد برای حالت تجمیعی
    summary = Column(Text, nullable=True)
    link = Column(Text, nullable=True)   # nullable شد برای حالت تجمیعی
    published_at = Column(DateTime, nullable=True)

    # MarketContext
    event_title = Column(String(256), nullable=True)
    asset_class = Column(String(64), nullable=False, index=True)
    ticker = Column(String(32), nullable=False, index=True)

    # سیگنال
    direction = Column(Integer, nullable=False)
    final_score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    is_tradable = Column(Boolean, nullable=False, index=True)
    expected_volatility_level = Column(String(16), nullable=False)
    signal_half_life_mins = Column(Integer, nullable=False)

    # کیفیت و متادیتا
    source_reliability = Column(Float, nullable=False)
    event_category_weight = Column(Float, nullable=False)
    data_completeness_score = Column(Float, nullable=False)
    data_quality_factor = Column(Float, nullable=False)
    headline_body_alignment = Column(Float, nullable=False)
    quantitative_alignment = Column(Float, nullable=False)

    # استدلال
    reasoning = Column(Text, nullable=False)

    # payload های خام برای audit trail
    raw_news_payload_json = Column(Text, nullable=True)
    raw_market_context_json = Column(Text, nullable=True)

    # ─── فیلدهای جدید برای Macro Digest ───
    is_aggregated = Column(Boolean, nullable=False, default=False)
    source_links_json = Column(Text, nullable=True)  # لیست لینک‌های اخبار تجمیع شده

    def __repr__(self) -> str:
        dir_map = {1: "BULL", -1: "BEAR", 0: "NEUT"}
        agg_marker = " [DIGEST]" if self.is_aggregated else ""
        return (
            f"<NewsSignalDB id={self.id} {self.ticker} "
            f"{dir_map.get(self.direction, '?')} "
            f"score={self.final_score:.3f} tradable={self.is_tradable}{agg_marker}>"
        )
        
# =====================================================================
# ۵. EventSignalDB — سیگنال‌های event-driven
# =====================================================================

class EventSignalDB(Base):
    """
    جدول event_signals: سیگنال‌های تولید شده از nlp_event.py.

    این جدول مخصوص تحلیل مستقیم رویدادهای اقتصادی منتشرشده است
    (نه خبر، نه سخنران).
    """

    __tablename__ = "event_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        nullable=False,
        index=True,
    )

    # event reference
    event_title = Column(String, nullable=False, index=True)
    event_currency = Column(String, nullable=False, index=True)
    event_impact = Column(String, nullable=True)
    event_category = Column(String, nullable=True, index=True)
    event_date = Column(DateTime, nullable=True, index=True)
    external_id = Column(String, nullable=True)

    # values
    actual = Column(Float, nullable=False)
    forecast = Column(Float, nullable=True)
    previous = Column(Float, nullable=True)
    surprise_value = Column(Float, nullable=True)

    # signal core
    ticker = Column(String, nullable=False, index=True)
    asset_class = Column(String, nullable=False)
    direction = Column(Integer, nullable=False)
    final_score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    is_tradable = Column(Boolean, nullable=False, index=True)
    expected_volatility_level = Column(String, nullable=False)
    signal_half_life_mins = Column(Integer, nullable=False)

    # scoring metadata
    event_impact_weight = Column(Float, nullable=False)
    data_quality_factor = Column(Float, nullable=False)
    quantitative_alignment = Column(Float, nullable=False)
    historical_std_used = Column(Float, nullable=False)
    historical_std_source = Column(String, nullable=False)
    historical_std_reliable = Column(Boolean, nullable=False)

    # interpretation
    surprise_interpretation = Column(String, nullable=False)
    momentum_vs_previous = Column(String, nullable=False)
    economic_implication = Column(String, nullable=False)
    impact_horizon = Column(String, nullable=False)

    # reasoning
    reasoning = Column(Text, nullable=False)

    # raw payloads
    raw_event_payload_json = Column(Text, nullable=True)
    raw_market_context_json = Column(Text, nullable=True)
    event_brief_text = Column(Text, nullable=True)

    def __repr__(self) -> str:
        dir_map = {1: "BULL", -1: "BEAR", 0: "NEUT"}
        return (
            f"<EventSignalDB id={self.id} {self.event_title!r} "
            f"{self.event_currency} {dir_map.get(self.direction, '?')} "
            f"score={self.final_score:.3f} tradable={self.is_tradable}>"
        )
        
        
class RawNewsItemDB(Base):
    """
    جدول raw_news_items: ذخیره اخبار خام کرال شده برای جداسازی زمان کرال و تحلیل.
    از dedup_hash برای جلوگیری از درج اخبار تکراری استفاده می‌کند.
    """
    __tablename__ = "raw_news_items"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_hash = Column(String(64), unique=True, nullable=False, index=True)
    fetched_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    link = Column(Text, nullable=True)
    source = Column(String(64), nullable=False)
    published_at = Column(DateTime, nullable=True)
    
    currency = Column(String(8), nullable=True, index=True)
    source_reliability = Column(Float, nullable=True)
    category = Column(String(64), nullable=True)
    impact = Column(String(16), nullable=True)
    
    def __repr__(self) -> str:
        return (
            f"<RawNewsItemDB id={self.id} source={self.title!r} "
            f"source={self.source!r}>"
        )
        
# =====================================================================
# ۶. CompositeSignalDB — سیگنال‌های ترکیبی فاز ۲
# =====================================================================

class CompositeSignalDB(Base):
    """
    جدول composite_signals: سیگنال‌های ترکیبی فاز ۲.

    این جدول سیگنال‌های ترکیبی تولید شده توسط LangGraph را ذخیره می‌کند.
    """
    __tablename__ = "composite_signals"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    currency = Column(String, nullable=False, index=True)
    
    direction = Column(Integer, nullable=False)
    final_score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    is_tradable = Column(Boolean, nullable=False, index=True)
    
    confluence_status = Column(String, nullable=False)
    components_used= Column(String, nullable=False)
    reasoning = Column(Text, nullable=False)
    
    def __repr__(self) -> str:
        dir_map = {1: "BULL", -1: "BEAR", 0: "NEUT"}
        return (
            f"<CompositeSignalDB id={self.id} ccy={self.currency} "
            f"{dir_map.get(self.direction, '?')} score={self.final_score:.2f} "
            f"tradable={self.is_tradable} status={self.confluence_status}>"
        )
        
        
# =====================================================================
# ۷. RawSpeakerItemDB — متن‌های خام سخنرانان
# =====================================================================

class RawSpeakerItemDB(Base):
    """
    جدول raw_speaker_items: ذخیره بیانیه‌ها/توییت‌های خام قبل از تحلیل.
    از dedup_hash برای جلوگیری از درج متن‌های تکراری استفاده می‌کند.
    """
    __tablename__ = "raw_speaker_items"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_hash = Column(String(64), unique=True, nullable=False, index=True)
    fetched_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    
    speaker_name = Column(String, nullable=False, index=True)
    text = Column(Text, nullable=False)
    
    source = Column(String(64), nullable=False)
    source_type = Column(String(32), nullable=False)
    link = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    
    currency = Column(String(8), nullable=True, index=True)
    speaker_weight = Column(Float, nullable=True)
    
    def __repr__(self) -> str:
        return (
            f"<RawSpeakerItemDB id={self.id} speaker={self.speaker_name!r} "
            f"source={self.source!r}>"
        )

def insert_raw_speaker_item_if_new(
    session: Session, *, dedup_hash: str, **fields: Any
) -> tuple[Optional[RawSpeakerItemDB], bool]:
    """
    Idempotent insert into raw_speaker_items, keyed on the UNIQUE index dedup_hash.
    Returns (row, created).
    """
    existing = (
        session.query(RawSpeakerItemDB)
        .filter(RawSpeakerItemDB.dedup_hash == dedup_hash)
        .first()
    )
    if existing is not None:
        return existing, False

    row = RawSpeakerItemDB(dedup_hash=dedup_hash, **fields)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = (
            session.query(RawSpeakerItemDB)
            .filter(RawSpeakerItemDB.dedup_hash == dedup_hash)
            .first()
        )
        return existing, False
    session.refresh(row)
    return row, True


# =====================================================================
# ۸. TradeOutcomeDB — حافظه نتایج معاملات
# =====================================================================

class TradeOutcomeDB(Base):
    """
    جدول trade_outcomes: ردیابی سیگنال‌های تاییدشده و نتیجه نهایی آن‌ها.
    برای سیستم Memory و یادگیری از اشتباهات استفاده می‌شود.
    """
    __tablename__ = "trade_outcomes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    currency = Column(String, nullable=False, index=True)
    direction = Column(Integer, nullable=False)
    
    # trade plan details
    entry_zone_low = Column(Float, nullable=True)
    entry_zone_high = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)

    
    # metadate for memory context
    fusion_state = Column(String, nullable=True)
    fundamental_score = Column(Float, nullable=True)
    technical_score = Column(Float, nullable=True)
    temporal_session = Column(String, nullable=True)
        
    # outcome tracking
    status = Column(String, default="PENDING", nullable=False, index=True)  # PENDING, WIN, LOSS, EXPIRED
    closed_at = Column(DateTime, nullable=True)
    evaluated_price = Column(Float, nullable=True)

    # Phase 7: ستون‌های افزایشی (با migrate_trade_outcomes_table به DBهای قدیمی اضافه می‌شوند)
    timeframe = Column(String, nullable=True)          # کدام افق این معامله را ساخت (H4, H1, M15...)
    plan_id = Column(Integer, nullable=True)           # لینک منطقی به strategy_plans.id
    decision_reasoning = Column(Text, nullable=True)   # متن تصمیم — زیرساخت حافظه معنایی
    entry_filled_at = Column(DateTime, nullable=True)  # چه زمانی قیمت واقعاً به زون ورود رسید
    
    def __repr__(self) -> str:
        return f"<TradeOutcomeDB id={self.id} ccy={self.currency} dir={self.direction} status={self.status}>"


class StrategyPlanDB(Base):
    """
    جدول strategy_plans (Phase 7): برگه‌های استراتژی شرطی.

    هر ردیف یک پلن معاملاتی روی یک افق است که یا همین الان فعال است یا
    منتظر رسیدن قیمت به زون ورود می‌ماند. live_watcher در هر تیک قیمت را
    با زون پلن‌های PENDING چک می‌کند (تریگر zone-hit).

    چرخه عمر status:
      PENDING      — زون هنوز لمس نشده؛ watcher پایشش می‌کند
      TRIGGERED    — قیمت وارد زون شد (tوسط watcher؛ پایپ‌لاین دوباره ارزیابی می‌کند)
      FILLED       — پایپ‌لاین تایید کرد و تبدیل به trade_outcomes شد
      INVALIDATED  — قیمت از invalidation_price عبور کرد قبل از ورود
      EXPIRED      — TTL افق تمام شد بدون ورود
    """
    __tablename__ = "strategy_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    currency = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False)           # W1/D1/H4/H2/H1/M30/M15
    horizon_role = Column(String, nullable=False)        # HTF / MTF / LTF
    direction = Column(Integer, nullable=False)          # 1=long, -1=short

    status = Column(String, default="PENDING", nullable=False, index=True)

    entry_low = Column(Float, nullable=False)
    entry_high = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    rr_ratio = Column(Float, nullable=True)
    invalidation_price = Column(Float, nullable=True)    # عبور قیمت از این = پلن مرده

    reasoning = Column(Text, nullable=True)
    counter_bias = Column(Integer, default=0, nullable=False)  # 1 = خلاف bias اصلی (نیم‌سایز)

    expires_at = Column(DateTime, nullable=False, index=True)
    triggered_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (f"<StrategyPlanDB id={self.id} {self.currency} {self.timeframe} "
                f"dir={self.direction} status={self.status}>")


class PaperAccountDB(Base):
    """
    جدول paper_account (Phase 8): حساب مجازی — تک‌ردیفه.
    بالانس نقد + اکویتی لحظه‌ای (با PnL شناور پوزیشن‌های باز).
    """
    __tablename__ = "paper_account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    balance = Column(Float, nullable=False)              # بالانس نقد (بدون PnL شناور)
    equity = Column(Float, nullable=False)               # بالانس + PnL شناور پوزیشن‌های باز
    margin_used = Column(Float, default=0.0, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<PaperAccountDB balance={self.balance:.2f} equity={self.equity:.2f}>"


class PaperPositionDB(Base):
    """
    جدول paper_positions (Phase 8): پوزیشن‌های مجازی باز/بسته.

    چرخه عمر status:
      OPEN             — پوزیشن فعال؛ executor_loop هر ۶۰ ثانیه SL/TP را چک می‌کند
      CLOSED_TP        — حد سود لمس شد
      CLOSED_SL        — حد ضرر لمس شد (قانون محافظه‌کار: در کندل دومرکزی SL اول)
      CLOSED_TIMEOUT   — TTL افق پوزیشن تمام شد → بستن در مارکت
      CLOSED_INVALID   — پلن مبنا ابطال شد بعد از باز شدن (محافظه‌کاری دستی/سیستمی)

    سایز از فرمول ریسک ثابت می‌آید:
      size_units = (balance × risk_pct) ÷ |entry_price − stop_loss|
    ریسک پایه ۱٪؛ پلن‌های counter_bias → ۰٫۵٪.
    """
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, nullable=True, index=True)  # لینک منطقی به strategy_plans.id
    currency = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=True)             # H4/H1/M30...
    direction = Column(Integer, nullable=False)           # 1=long, -1=short

    status = Column(String, default="OPEN", nullable=False, index=True)

    size_units = Column(Float, nullable=False)
    risk_pct = Column(Float, nullable=False)              # 0.01 یا 0.005
    entry_price = Column(Float, nullable=False)           # بعد از اسپرد+اسلیپیج (بدترین لبه)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    counter_bias = Column(Integer, default=0, nullable=False)

    opened_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True, index=True)  # از plan.expires_at — ددلاین پوزیشن
    closed_at = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl_money = Column(Float, nullable=True)              # سود/ضرر پولی (به ارز حساب)
    close_reason = Column(String, nullable=True)          # tp / sl / timeout / invalid

    def __repr__(self) -> str:
        return (f"<PaperPositionDB id={self.id} {self.currency} dir={self.direction} "
                f"status={self.status} pnl={self.pnl_money}>")


def get_trade_memory_stats(currency: str) -> dict:
    """
    Fetches historical trade performance stats for memory injection.
    Returns a dictionary with total trades, wins, losses, and win rate.
    """
    session = SessionLocal()
    try:
        total = session.query(TradeOutcomeDB).filter(TradeOutcomeDB.currency == currency).count()
        wins = session.query(TradeOutcomeDB).filter(
            TradeOutcomeDB.currency == currency,
            TradeOutcomeDB.status == "WIN"
        ).count()
        losses = session.query(TradeOutcomeDB).filter(
            TradeOutcomeDB.currency == currency,
            TradeOutcomeDB.status == "LOSS"
        ).count()

        win_rate = (wins / total) * 100 if total > 0 else 0.0
        
        return{
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate
        }
    except Exception:
        return{"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0}
    finally:
        session.close()


def get_recent_losing_trades(currency: str, limit: int = 3) -> list[dict]:
    """
    Phase 7 — حافظه معنایی سطح ۱: چند معامله باخت اخیر همان ارز با متن تصمیم.

    خروجی برای تزریق به Risk Manager است تا علاوه بر آمار (win rate)،
    «چرا» باخت‌های اخیر هم دیده شود. رکوردهای قدیمی بدون decision_reasoning
    با توضیح خلاصه از فیلدهای موجود بازسازی می‌شوند.
    """
    session = SessionLocal()
    try:
        rows = (
            session.query(TradeOutcomeDB)
            .filter(
                TradeOutcomeDB.currency == currency,
                TradeOutcomeDB.status == "LOSS",
            )
            .order_by(TradeOutcomeDB.closed_at.desc().nullslast(),
                      TradeOutcomeDB.created_at.desc())
            .limit(limit)
            .all()
        )
        out = []
        for r in rows:
            reasoning = getattr(r, "decision_reasoning", None)
            if not reasoning:
                reasoning = (
                    f"dir={r.direction} entry=[{r.entry_zone_low},{r.entry_zone_high}] "
                    f"sl={r.stop_loss} tp={r.take_profit} fusion={r.fusion_state}"
                )
            out.append({
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "timeframe": getattr(r, "timeframe", None),
                "direction": r.direction,
                "reasoning": reasoning,
            })
        return out
    except Exception:
        return []
    finally:
        session.close()


# =====================================================================
# Session lifecycle helpers
# =====================================================================
#
# NOTE: This is purely additive. Existing code that calls `SessionLocal()`
# directly (open -> use -> close/commit by hand) is completely untouched
# and keeps working exactly as before. `session_scope()` is an optional,
# safer alternative for new/updated call sites: it guarantees commit on
# success, rollback on exception, and close in all cases, so a session
# can never be left open or a half-finished transaction left hanging.

@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """
    Safe, self-closing session lifecycle:

        open session -> use it -> commit (success) / rollback (exception)
        -> close session

    Usage:
        with session_scope() as session:
            session.add(SpeakerDB(...))
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# =====================================================================
# Historical time-range utilities
# =====================================================================

_VALID_RANGE_TYPES = (
    "today",
    "yesterday",
    "previous_day",
    "last_3_days",
    "last_7_days",
    "last_14_days",
    "last_30_days",
    "last_90_days",
    "custom_days",
    "custom_range",
    "latest",
    "all",
)


def resolve_time_range(
    range_type: str = "today",
    *,
    days: Optional[int] = None,
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
) -> tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    """
    Resolve a symbolic time range (e.g. "today", "last_7_days",
    "custom_range") into a concrete (start, end) UTC datetime boundary.

    This function only computes boundaries in Python — it never touches
    the database or loads any records. The boundaries are meant to be
    applied as a WHERE clause at the SQLite level (see
    `query_by_time_range` below), which is what keeps historical queries
    cheap regardless of how large the tables grow.

    Returns (None, None) for "latest" / "all", meaning "no date filter"
    (the caller is expected to apply LIMIT/ORDER BY instead).
    """
    rt = (range_type or "").strip().lower()
    if rt not in _VALID_RANGE_TYPES:
        raise ValueError(
            f"Unknown range_type: {range_type!r}. Valid options: {_VALID_RANGE_TYPES}"
        )

    now = datetime.datetime.utcnow()
    today_start = datetime.datetime(now.year, now.month, now.day)

    if rt == "today":
        return today_start, today_start + timedelta(days=1)
    if rt in ("yesterday", "previous_day"):
        return today_start - timedelta(days=1), today_start
    if rt == "last_3_days":
        return today_start - timedelta(days=3), today_start + timedelta(days=1)
    if rt == "last_7_days":
        return today_start - timedelta(days=7), today_start + timedelta(days=1)
    if rt == "last_14_days":
        return today_start - timedelta(days=14), today_start + timedelta(days=1)
    if rt == "last_30_days":
        return today_start - timedelta(days=30), today_start + timedelta(days=1)
    if rt == "last_90_days":
        return today_start - timedelta(days=90), today_start + timedelta(days=1)
    if rt == "custom_days":
        if days is None:
            raise ValueError("range_type='custom_days' requires `days`.")
        return today_start - timedelta(days=days), today_start + timedelta(days=1)
    if rt == "custom_range":
        if start_date is None or end_date is None:
            raise ValueError("range_type='custom_range' requires start_date and end_date.")
        return start_date, end_date

    # "latest" / "all" — no date boundary
    return None, None


# =====================================================================
# Historical query helpers (filter at the SQLite level, not in Python)
# =====================================================================

def query_by_time_range(
    session: Session,
    model: type,
    date_field: str,
    range_type: str = "today",
    *,
    days: Optional[int] = None,
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
    extra_filters: Optional[Iterable[Any]] = None,
    order_desc: bool = True,
    limit: Optional[int] = None,
):
    """
    Build a filtered, indexed query for `model` restricted to the
    requested time range. Returns a SQLAlchemy Query object — it is NOT
    executed here, so the caller controls exactly what gets loaded
    (.all(), .first(), .limit(n), or streamed via `iter_query_chunks`).

    All filtering (date range, extra_filters, ordering, limit) is pushed
    down into the SQL WHERE/ORDER BY/LIMIT clauses; nothing is loaded
    into Python and filtered there.

    Example:
        with session_scope() as s:
            q = query_by_time_range(s, NewsSignalDB, "created_at", "last_7_days")
            rows = q.limit(50).all()
    """
    column = getattr(model, date_field)
    start, end = resolve_time_range(
        range_type, days=days, start_date=start_date, end_date=end_date
    )

    query = session.query(model)
    if start is not None:
        query = query.filter(column >= start)
    if end is not None:
        query = query.filter(column < end)
    if extra_filters:
        for condition in extra_filters:
            query = query.filter(condition)

    query = query.order_by(column.desc() if order_desc else column.asc())
    if limit is not None:
        query = query.limit(limit)
    return query


def get_latest_record(
    session: Session,
    model: type,
    date_field: str,
    *,
    extra_filters: Optional[Iterable[Any]] = None,
):
    """
    Fetch only the single most recent row of `model`, via
    ``ORDER BY <date_field> DESC LIMIT 1``. Never scans or loads the full
    table — relies on the existing index on `date_field`.
    """
    column = getattr(model, date_field)
    query = session.query(model)
    if extra_filters:
        for condition in extra_filters:
            query = query.filter(condition)
    return query.order_by(column.desc()).limit(1).first()


def get_latest_trading_signal(session: Session, **filters: Any):
    """Latest row in trading_signals, optionally filtered (e.g. ticker='BTCUSD')."""
    extra = [getattr(TradingSignalDB, k) == v for k, v in filters.items()]
    return get_latest_record(session, TradingSignalDB, "timestamp", extra_filters=extra)


def get_latest_news_signal(session: Session, **filters: Any):
    """Latest row in news_signals, optionally filtered (e.g. ticker='ETHUSD')."""
    extra = [getattr(NewsSignalDB, k) == v for k, v in filters.items()]
    return get_latest_record(session, NewsSignalDB, "created_at", extra_filters=extra)


def get_latest_event_signal(session: Session, **filters: Any):
    """Latest row in event_signals, optionally filtered (e.g. event_currency='USD')."""
    extra = [getattr(EventSignalDB, k) == v for k, v in filters.items()]
    return get_latest_record(session, EventSignalDB, "created_at", extra_filters=extra)


def get_latest_composite_signal(session: Session, **filters: Any):
    """Latest row in composite_signals, optionally filtered (e.g. currency='USD')."""
    extra = [getattr(CompositeSignalDB, k) == v for k, v in filters.items()]
    return get_latest_record(session, CompositeSignalDB, "created_at", extra_filters=extra)


def get_latest_economic_event(session: Session, **filters: Any):
    """Latest row in economic_events_history, optionally filtered (e.g. currency='USD')."""
    extra = [getattr(EventHistoryDB, k) == v for k, v in filters.items()]
    return get_latest_record(session, EventHistoryDB, "date", extra_filters=extra)


def query_columns(session: Session, model: type, columns: list[str], **equality_filters: Any):
    """
    Select only the requested columns instead of full ORM objects — useful
    to avoid pulling large `raw_*_json` payload fields into memory when a
    query only needs a few scalar fields (e.g. ticker/direction/score).

    Returns a Query of tuples; call .all()/.limit()/.yield_per() on it.
    """
    cols = [getattr(model, c) for c in columns]
    query = session.query(*cols)
    for key, value in equality_filters.items():
        query = query.filter(getattr(model, key) == value)
    return query


def iter_query_chunks(query, chunk_size: int = 500) -> Iterator[Any]:
    """
    Stream a (potentially large) query in fixed-size chunks instead of
    materializing the whole result set with `.all()`. Keeps only
    `chunk_size` ORM objects resident in memory at any point in time.
    """
    for row in query.yield_per(chunk_size):
        yield row


# =====================================================================
# Deduplication helpers (idempotent ingestion)
# =====================================================================

def raw_news_item_exists(session: Session, dedup_hash: str) -> bool:
    """Cheap existence check using the existing unique index on dedup_hash."""
    return (
        session.query(RawNewsItemDB.id)
        .filter(RawNewsItemDB.dedup_hash == dedup_hash)
        .first()
        is not None
    )


def insert_raw_news_item_if_new(
    session: Session, *, dedup_hash: str, **fields: Any
) -> tuple[Optional[RawNewsItemDB], bool]:
    """
    Idempotent insert into raw_news_items, keyed on the existing UNIQUE
    index `RawNewsItemDB.dedup_hash`. Returns (row, created).

    A pre-check alone is race-prone across multiple processes/threads, so
    on top of the existence check this also catches the IntegrityError
    the unique constraint raises if two writers race — the database
    constraint remains the actual source of truth for uniqueness.
    """
    existing = (
        session.query(RawNewsItemDB)
        .filter(RawNewsItemDB.dedup_hash == dedup_hash)
        .first()
    )
    if existing is not None:
        return existing, False

    row = RawNewsItemDB(dedup_hash=dedup_hash, **fields)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = (
            session.query(RawNewsItemDB)
            .filter(RawNewsItemDB.dedup_hash == dedup_hash)
            .first()
        )
        return existing, False
    session.refresh(row)
    return row, True


def economic_event_exists(
    session: Session,
    *,
    title: str,
    currency: Optional[str] = None,
    date: Optional[datetime.datetime] = None,
) -> bool:
    """
    Existence check matching the documented duplicate-detection rule for
    economic_events_history: (title + currency + date).
    """
    query = session.query(EventHistoryDB.id).filter(EventHistoryDB.title == title)
    query = (
        query.filter(EventHistoryDB.currency == currency)
        if currency is not None
        else query.filter(EventHistoryDB.currency.is_(None))
    )
    query = (
        query.filter(EventHistoryDB.date == date)
        if date is not None
        else query.filter(EventHistoryDB.date.is_(None))
    )
    return query.first() is not None


def insert_economic_event_if_new(
    session: Session,
    *,
    title: str,
    currency: Optional[str] = None,
    date: Optional[datetime.datetime] = None,
    **fields: Any,
) -> tuple[Optional[EventHistoryDB], bool]:
    """
    Idempotent insert into economic_events_history using the
    (title + currency + date) duplicate-detection rule. Returns
    (row, created); if a matching row already exists, returns it
    unchanged instead of inserting a duplicate.
    """
    if economic_event_exists(session, title=title, currency=currency, date=date):
        existing = (
            session.query(EventHistoryDB)
            .filter(EventHistoryDB.title == title)
            .filter(
                EventHistoryDB.currency == currency
                if currency is not None
                else EventHistoryDB.currency.is_(None)
            )
            .filter(
                EventHistoryDB.date == date
                if date is not None
                else EventHistoryDB.date.is_(None)
            )
            .first()
        )
        return existing, False

    row = EventHistoryDB(title=title, currency=currency, date=date, **fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, True


# =====================================================================
# Data retention / controlled cleanup (explicit — never automatic)
# =====================================================================
#
# IMPORTANT: none of these run from init_db() or from the migration
# helpers below. Retention is only ever applied when explicitly called,
# e.g. from a scheduled maintenance job:
#
#     from database import cleanup_old_data, TradingSignalDB
#     with session_scope() as s:
#         deleted = cleanup_old_data(s, TradingSignalDB, retention_days=30)

_RETENTION_DATE_FIELDS: dict[type, str] = {
    EventHistoryDB: "date",
    TradingSignalDB: "timestamp",
    NewsSignalDB: "created_at",
    EventSignalDB: "created_at",
    RawNewsItemDB: "fetched_at",
    CompositeSignalDB: "created_at",
}


def cleanup_old_data(
    session: Session,
    model: type,
    retention_days: int,
    *,
    date_field: Optional[str] = None,
    dry_run: bool = False,
) -> int:
    """
    Explicit, opt-in deletion of rows in `model` older than
    `retention_days`, using that table's own semantically correct
    timestamp/date column (never a single shared column blindly reused
    across tables). Runs as one transaction and returns the number of
    rows removed (or that *would* be removed, if dry_run=True).

    Only rows strictly older than the cutoff are ever touched; nothing
    newer is deleted, and the whole table is never truncated.
    """
    if not isinstance(retention_days, int) or retention_days <= 0:
        raise ValueError("retention_days must be a positive integer.")

    field_name = date_field or _RETENTION_DATE_FIELDS.get(model)
    if field_name is None:
        raise ValueError(
            f"No known retention date field for {model!r}; pass date_field explicitly."
        )

    column = getattr(model, field_name)
    cutoff = datetime.datetime.utcnow() - timedelta(days=retention_days)

    query = session.query(model).filter(column < cutoff)
    count = query.count()

    if dry_run or count == 0:
        return count

    query.delete(synchronize_session=False)
    session.commit()
    return count


def cleanup_all_tables(retention_days: int, *, dry_run: bool = False) -> dict[str, int]:
    """
    Explicit maintenance operation: apply `cleanup_old_data` to every
    table that has a known retention date field, each using its own
    correct date column. Must be called intentionally (e.g. from a cron
    job / scheduler) — it is never invoked automatically by init_db().
    """
    results: dict[str, int] = {}
    for model, field_name in _RETENTION_DATE_FIELDS.items():
        with session_scope() as session:
            deleted = cleanup_old_data(
                session, model, retention_days, date_field=field_name, dry_run=dry_run
            )
        results[model.__tablename__] = deleted
    return results


# =====================================================================
# Migration helpers
# =====================================================================

def _get_existing_columns(table_name: str) -> set[str]:
    """Get set of existing column names for a SQLite table."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        return {row[1] for row in rows}


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the SQLite database."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                f"WHERE type='table' AND name='{table_name}'"
            )
        ).fetchall()
        return len(rows) > 0


def _add_missing_columns(table_name: str, expected_columns: dict[str, str]) -> None:
    """Add missing columns to an existing table."""
    if not _table_exists(table_name):
        print(f"  {table_name} does not exist yet; skipping migration.")
        return

    existing = _get_existing_columns(table_name)

    with engine.begin() as conn:
        for col_name, col_type in expected_columns.items():
            if col_name not in existing:
                conn.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"
                ))
                print(f"  Added column: {table_name}.{col_name} ({col_type})")


def _create_index_if_not_exists(index_name: str, table_name: str, column_name: str) -> None:
    """Create an index if it doesn't already exist."""
    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            f"ON {table_name} ({column_name})"
        ))


def migrate_trading_signals_table() -> None:
    """
    Additively migrate trading_signals table for nlp_x.py.
    All new columns are nullable for backward compatibility.
    """
    expected_columns = {
        "speaker_name": "TEXT",
        "speaker_role": "TEXT",
        "speaker_weight": "FLOAT",
        "source": "TEXT",
        "source_type": "TEXT",
        "source_reliability": "FLOAT",
        "link": "TEXT",
        "external_id": "TEXT",
        "published_at": "DATETIME",
        "statement_type_weight": "FLOAT",
        "data_completeness_score": "FLOAT",
        "data_quality_factor": "FLOAT",
        "statement_market_alignment": "FLOAT",
        "quantitative_alignment": "FLOAT",
        "signal_half_life_mins": "INTEGER",
        "ticker": "TEXT",
        "policy_signal_type": "TEXT",
        "impact_horizon": "TEXT",
        "raw_text": "TEXT",
        "raw_market_context_json": "TEXT",
        "raw_input_payload_json": "TEXT",
    }

    _add_missing_columns("trading_signals", expected_columns)

    # ایجاد indexهای مفید
    _create_index_if_not_exists("ix_trading_signals_speaker_name", "trading_signals", "speaker_name")
    _create_index_if_not_exists("ix_trading_signals_ticker", "trading_signals", "ticker")
    _create_index_if_not_exists("ix_trading_signals_source", "trading_signals", "source")


def migrate_economic_events_table() -> None:
    """
    Additively migrate economic_events_history table for forex_factory_crawler.py.
    """
    expected_columns = {
        "raw_actual_str": "TEXT",
        "raw_forecast_str": "TEXT",
        "raw_previous_str": "TEXT",
        "source": "TEXT",
        "fetched_at": "DATETIME",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
        "detail_text": "TEXT",
    }

    _add_missing_columns("economic_events_history", expected_columns)
    
    
def migrate_news_signals_table() -> None:
    """
    Additively migrate news_signals table for aggregated digest signals.
    """
    expected_columns = {
        "is_aggregated": "BOOLEAN",
        "source_links_json": "TEXT",
    }

    _add_missing_columns("news_signals", expected_columns)


def migrate_trade_outcomes_table() -> None:
    """
    Phase 7: افزودن ستون‌های جدید به trade_outcomes — افزایشی و idempotent.
    همه ستون‌ها nullable هستند؛ دیتای قدیمی دست نمی‌خورد.
    """
    expected_columns = {
        "timeframe": "TEXT",
        "plan_id": "INTEGER",
        "decision_reasoning": "TEXT",
        "entry_filled_at": "DATETIME",
    }
    _add_missing_columns("trade_outcomes", expected_columns)


def migrate_additional_indexes() -> None:
    """
    Additive composite indexes that speed up the most common historical
    query patterns (ticker/currency combined with a date-range filter),
    without touching any existing column, table, or single-column index.
    Safe to run every startup: `CREATE INDEX IF NOT EXISTS`.
    """
    composite_indexes = [
        ("ix_trading_signals_ticker_timestamp", "trading_signals", "ticker, timestamp"),
        ("ix_news_signals_ticker_created_at", "news_signals", "ticker, created_at"),
        ("ix_event_signals_ticker_created_at", "event_signals", "ticker, created_at"),
        ("ix_event_signals_currency_date", "event_signals", "event_currency, event_date"),
        ("ix_composite_signals_currency_created_at", "composite_signals", "currency, created_at"),
        ("ix_economic_events_history_currency_date", "economic_events_history", "currency, date"),
    ]
    for index_name, table_name, columns in composite_indexes:
        if _table_exists(table_name):
            _create_index_if_not_exists(index_name, table_name, columns)


# =====================================================================
# راه‌اندازی دیتابیس
# =====================================================================

def init_db() -> None:
    """
    ساخت تمام جدول‌ها و اجرای migration های افزایشی.

    Idempotent by design: safe to call repeatedly (every process start).
    It only ever *adds* missing tables/columns/indexes — it never drops,
    truncates, or recreates existing data, and it never touches
    :memory:; it always opens the persistent `crypto_agent.db` file on
    disk defined by DB_PATH above. SQLite connection pragmas (WAL,
    busy_timeout, etc.) are applied automatically on every connection via
    the `connect` event listener registered next to `engine`, so no
    separate pragma step is needed here.

    فراخوانی:
      python database.py
      یا
      from database import init_db; init_db()
    """
    Base.metadata.create_all(bind=engine)
    migrate_trading_signals_table()
    migrate_economic_events_table()
    migrate_news_signals_table()
    migrate_trade_outcomes_table()   # Phase 7
    migrate_additional_indexes()
    print(f"Database initialized at {DB_PATH}")
    print(f"Tables: {list(Base.metadata.tables.keys())}")


if __name__ == "__main__":
    init_db()