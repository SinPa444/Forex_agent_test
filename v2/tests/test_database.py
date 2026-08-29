# tests/test_database.py
"""
تست‌های واحد برای database.py — ORM مدل‌ها و عملیات دیتابیس.

از یک دیتابیس SQLite در حافظه (in-memory) استفاده می‌کند
تا فایل اصلی دیتابیس آسیب نبیند.
"""

import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import (
    Base,
    SpeakerDB,
    EventHistoryDB,
    EconomicEventHistory,
    TradingSignalDB,
    NewsSignalDB,
)


@pytest.fixture
def db_session():
    """یک دیتابیس SQLite در حافظه می‌سازد و session برمی‌گرداند."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# =====================================================================
# تست SpeakerDB
# =====================================================================

class TestSpeakerDB:
    def test_create_speaker(self, db_session):
        speaker = SpeakerDB(
            name="Jerome Powell",
            role="Fed Chair",
            primary_asset="USD",
            weight=1.0,
            twitter_handle="@FedChair",
        )
        db_session.add(speaker)
        db_session.commit()

        result = db_session.query(SpeakerDB).filter_by(name="Jerome Powell").first()
        assert result is not None
        assert result.weight == 1.0
        assert result.role == "Fed Chair"

    def test_speaker_default_weight(self, db_session):
        speaker = SpeakerDB(
            name="Unknown Person",
            role="Analyst",
            primary_asset="EUR",
        )
        db_session.add(speaker)
        db_session.commit()
        result = db_session.query(SpeakerDB).filter_by(name="Unknown Person").first()
        assert result.weight == 0.5

    def test_speaker_unique_name(self, db_session):
        from sqlalchemy.exc import IntegrityError

        s1 = SpeakerDB(name="Test", role="A", primary_asset="USD")
        s2 = SpeakerDB(name="Test", role="B", primary_asset="EUR")
        db_session.add(s1)
        db_session.commit()
        db_session.add(s2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_speaker_repr(self, db_session):
        s = SpeakerDB(name="Lagarde", role="ECB", primary_asset="EUR", weight=0.95)
        assert "Lagarde" in repr(s)


# =====================================================================
# تست EventHistoryDB
# =====================================================================

class TestEventHistoryDB:
    def test_create_event(self, db_session):
        event = EventHistoryDB(
            title="US CPI YoY",
            category="CPI",
            currency="USD",
            impact="High",
            actual=3.8,
            forecast=3.5,
            previous=3.6,
        )
        db_session.add(event)
        db_session.commit()

        result = db_session.query(EventHistoryDB).filter_by(title="US CPI YoY").first()
        assert result is not None
        assert result.actual == 3.8
        assert result.forecast == 3.5

    def test_alias_works(self):
        """EconomicEventHistory باید همان EventHistoryDB باشد."""
        assert EconomicEventHistory is EventHistoryDB

    def test_query_with_alias(self, db_session):
        event = EconomicEventHistory(
            title="GDP", actual=2.5, forecast=2.3,
        )
        db_session.add(event)
        db_session.commit()
        result = db_session.query(EconomicEventHistory).first()
        assert result.title == "GDP"

    def test_nullable_fields(self, db_session):
        event = EventHistoryDB(title="PMI")
        db_session.add(event)
        db_session.commit()
        result = db_session.query(EventHistoryDB).first()
        assert result.actual is None
        assert result.forecast is None
        assert result.category is None

    def test_historical_std_calculation(self, db_session):
        """شبیه‌سازی محاسبه historical_std از تاریخچه."""
        import numpy as np

        events_data = [
            ("CPI", 3.2, 3.0), ("CPI", 2.8, 3.0), ("CPI", 3.5, 3.1),
            ("CPI", 2.9, 2.9), ("CPI", 3.3, 3.0), ("CPI", 3.1, 3.0),
        ]
        for title, actual, forecast in events_data:
            db_session.add(EventHistoryDB(
                title=title, actual=actual, forecast=forecast,
            ))
        db_session.commit()

        records = (
            db_session.query(EventHistoryDB.actual, EventHistoryDB.forecast)
            .filter(
                EventHistoryDB.title == "CPI",
                EventHistoryDB.actual.isnot(None),
                EventHistoryDB.forecast.isnot(None),
            )
            .all()
        )
        deviations = [r.actual - r.forecast for r in records]
        std = float(np.std(deviations, ddof=1))

        assert len(deviations) == 6
        assert std > 0
        assert std < 1


# =====================================================================
# تست TradingSignalDB
# =====================================================================

class TestTradingSignalDB:
    def test_create_trading_signal(self, db_session):
        signal = TradingSignalDB(
            event_title="US NFP",
            statement="Strong jobs data",
            reasoning="NFP beat expectations → hawkish USD",
            asset_class="USD",
            direction=1,
            final_score=0.85,
            confidence=0.9,
            is_tradable=True,
            expected_volatility_level="High",
            speaker_name="Jerome Powell",
            speaker_weight=1.0,
        )
        db_session.add(signal)
        db_session.commit()

        result = db_session.query(TradingSignalDB).first()
        assert result.final_score == 0.85
        assert result.is_tradable is True
        assert result.speaker_name == "Jerome Powell"

    def test_default_timestamp(self, db_session):
        signal = TradingSignalDB(
            asset_class="USD", direction=0, final_score=0.0,
        )
        db_session.add(signal)
        db_session.commit()
        result = db_session.query(TradingSignalDB).first()
        assert result.timestamp is not None

    def test_query_tradable_signals(self, db_session):
        for i, tradable in enumerate([True, True, False, True, False]):
            db_session.add(TradingSignalDB(
                asset_class="USD", direction=1, final_score=0.5 + i * 0.1,
                is_tradable=tradable,
            ))
        db_session.commit()
        tradable_count = (
            db_session.query(TradingSignalDB)
            .filter(TradingSignalDB.is_tradable == True)
            .count()
        )
        assert tradable_count == 3


# =====================================================================
# تست NewsSignalDB
# =====================================================================

class TestNewsSignalDB:
    def test_create_news_signal(self, db_session):
        signal = NewsSignalDB(
            source="ForexLive",
            title="US CPI Beats",
            summary="Inflation hot",
            link="https://forexlive.com/cpi",
            published_at=datetime.datetime(2024, 1, 15, 13, 30),
            event_title="US CPI YoY",
            asset_class="FX",
            ticker="EURUSD=X",
            direction=1,
            final_score=0.74,
            confidence=0.78,
            is_tradable=True,
            expected_volatility_level="High",
            signal_half_life_mins=98,
            source_reliability=0.82,
            event_category_weight=0.95,
            data_completeness_score=1.0,
            data_quality_factor=1.0,
            headline_body_alignment=0.92,
            quantitative_alignment=0.95,
            reasoning="CPI beat → USD bullish",
            raw_news_payload_json='{"title": "test"}',
            raw_market_context_json='{"target_asset": "EURUSD=X"}',
        )
        db_session.add(signal)
        db_session.commit()

        result = db_session.query(NewsSignalDB).first()
        assert result is not None
        assert result.ticker == "EURUSD=X"
        assert result.is_tradable is True
        assert result.source == "ForexLive"
        assert result.headline_body_alignment == 0.92

    def test_news_signal_repr(self, db_session):
        signal = NewsSignalDB(
            source="DailyFX", title="Test", asset_class="FX",
            ticker="GBPUSD=X", direction=-1, final_score=-0.45,
            confidence=0.6, is_tradable=True,
            expected_volatility_level="Normal",
            signal_half_life_mins=120,
            source_reliability=0.8, event_category_weight=0.7,
            data_completeness_score=0.78, data_quality_factor=0.89,
            headline_body_alignment=0.85, quantitative_alignment=0.80,
            reasoning="Test",
        )
        r = repr(signal)
        assert "GBPUSD=X" in r
        assert "BEAR" in r

    def test_query_by_ticker(self, db_session):
        for ticker in ["EURUSD=X", "EURUSD=X", "GBPUSD=X"]:
            db_session.add(NewsSignalDB(
                source="Test", title="T", asset_class="FX",
                ticker=ticker, direction=0, final_score=0.0,
                confidence=0.5, is_tradable=False,
                expected_volatility_level="Normal",
                signal_half_life_mins=90,
                source_reliability=0.65, event_category_weight=0.4,
                data_completeness_score=0.5, data_quality_factor=0.75,
                headline_body_alignment=0.5, quantitative_alignment=0.5,
                reasoning="Test",
            ))
        db_session.commit()

        eur_count = (
            db_session.query(NewsSignalDB)
            .filter(NewsSignalDB.ticker == "EURUSD=X")
            .count()
        )
        assert eur_count == 2

    def test_default_created_at(self, db_session):
        signal = NewsSignalDB(
            source="Test", title="T", asset_class="FX",
            ticker="DX-Y.NYB", direction=0, final_score=0.0,
            confidence=0.5, is_tradable=False,
            expected_volatility_level="Low",
            signal_half_life_mins=90,
            source_reliability=0.65, event_category_weight=0.4,
            data_completeness_score=0.5, data_quality_factor=0.75,
            headline_body_alignment=0.5, quantitative_alignment=0.5,
            reasoning="Test",
        )
        db_session.add(signal)
        db_session.commit()
        result = db_session.query(NewsSignalDB).first()
        assert result.created_at is not None

    def test_all_tables_created(self, db_session):
        """اطمینان از ساخت تمام جدول‌ها."""
        tables = Base.metadata.tables.keys()
        assert "speakers" in tables
        assert "economic_events_history" in tables
        assert "trading_signals" in tables
        assert "news_signals" in tables