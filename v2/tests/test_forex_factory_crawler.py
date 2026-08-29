

from __future__ import annotations

import requests
import json

url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
r = requests.get(url, timeout=15)
print("status:", r.status_code)
print("content-type:", r.headers.get("content-type"))
data = r.json()

print("type:", type(data))
print("len:", len(data) if hasattr(data, "__len__") else "N/A")

print("\nFIRST ITEM:")
print(json.dumps(data[0], indent=2, ensure_ascii=False) if isinstance(data, list) and data else data)# tests/test_forex_factory_crawler.py
"""
Unit tests for forex_factory_crawler.py
"""


from datetime import datetime

import pytest
import requests

from ingestion.forex_factory_crawler import (
    ForexFactoryEvent,
    ForexFactoryFetchResult,
    _parse_numeric,
    _parse_event_datetime,
    _normalize_currency,
    _normalize_impact,
    _map_title_to_category,
    _build_event_from_raw,
    fetch_forex_factory_events,
    fetch_forex_factory_events_debug,
)


SAMPLE_RAW_EVENTS = [
    {
        "title": "US CPI y/y",
        "country": "USD",
        "date": "2026-06-16T08:30:00-04:00",
        "impact": "High",
        "actual": "3.8%",
        "forecast": "3.5%",
        "previous": "3.6%",
    },
    {
        "title": "ECB President Lagarde Speaks",
        "country": "EUR",
        "date": "2026-06-16T09:00:00-04:00",
        "impact": "Medium",
        "actual": "",
        "forecast": "",
        "previous": "",
    },
    {
        "title": "BusinessNZ Services Index",
        "country": "NZD",
        "date": "2026-06-14T18:30:00-04:00",
        "impact": "Low",
        "forecast": "",
        "previous": "48.9",
    },
]


class MockResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class MockSession:
    def __init__(self, payload=None, status_code: int = 200, exc: Exception | None = None):
        self.payload = payload
        self.status_code = status_code
        self.exc = exc

    def get(self, url, timeout=15):
        if self.exc:
            raise self.exc
        return MockResponse(self.payload, self.status_code)


# ===========================================================================
# _parse_numeric
# ===========================================================================

def test_parse_numeric_percent():
    assert _parse_numeric("3.8%") == pytest.approx(3.8)


def test_parse_numeric_thousands():
    assert _parse_numeric("185K") == pytest.approx(185000)


def test_parse_numeric_millions():
    assert _parse_numeric("1.2M") == pytest.approx(1_200_000)


def test_parse_numeric_billions():
    assert _parse_numeric("4.5B") == pytest.approx(4_500_000_000)


def test_parse_numeric_negative():
    assert _parse_numeric("-0.2") == pytest.approx(-0.2)


def test_parse_numeric_blank():
    assert _parse_numeric("") is None


def test_parse_numeric_invalid():
    assert _parse_numeric("not-a-number") is None


# ===========================================================================
# normalization helpers
# ===========================================================================

def test_normalize_currency():
    assert _normalize_currency(" usd ") == "USD"


def test_normalize_impact_high():
    assert _normalize_impact("high") == "High"


def test_normalize_impact_moderate_to_medium():
    assert _normalize_impact("Moderate") == "Medium"


def test_map_title_to_category():
    assert _map_title_to_category("US CPI y/y") == "CPI"
    assert _map_title_to_category("ECB President Lagarde Speaks") == "Central Bank Commentary"
    assert _map_title_to_category("Some Unknown Release") == "Unknown"


# ===========================================================================
# datetime parsing
# ===========================================================================

def test_parse_event_datetime_iso():
    dt = _parse_event_datetime("2026-06-14T18:30:00-04:00")
    assert dt is not None
    assert isinstance(dt, datetime)


def test_parse_event_datetime_blank():
    assert _parse_event_datetime("") is None


def test_parse_event_datetime_invalid():
    assert _parse_event_datetime("not-a-date") is None


# ===========================================================================
# build_event_from_raw
# ===========================================================================

def test_build_event_maps_country_to_currency():
    event = _build_event_from_raw(SAMPLE_RAW_EVENTS[0])
    assert event.currency == "USD"
    assert event.impact == "High"
    assert event.category == "CPI"


def test_build_event_with_raw_payload():
    event = _build_event_from_raw(SAMPLE_RAW_EVENTS[0], include_raw=True)
    assert event.raw_payload is not None
    assert event.raw_payload["country"] == "USD"


def test_build_event_missing_title_raises():
    with pytest.raises(ValueError, match="missing_title"):
        _build_event_from_raw({"country": "USD", "impact": "High"})


def test_build_event_missing_currency_raises():
    with pytest.raises(ValueError, match="missing_currency"):
        _build_event_from_raw({"title": "US CPI y/y", "impact": "High"})


# ===========================================================================
# fetch functions
# ===========================================================================

def test_fetch_all_events_no_filters():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    events = fetch_forex_factory_events(session=session)
    assert len(events) == 3
    assert all(isinstance(event, ForexFactoryEvent) for event in events)


def test_fetch_with_impact_filter():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    events = fetch_forex_factory_events(
        impact_filter=["High"],
        session=session,
    )
    assert len(events) == 1
    assert events[0].impact == "High"


def test_fetch_with_currency_filter():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    events = fetch_forex_factory_events(
        currency_filter=["EUR"],
        session=session,
    )
    assert len(events) == 1
    assert events[0].currency == "EUR"


def test_fetch_with_title_keyword_filter():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    events = fetch_forex_factory_events(
        title_keywords=["lagarde"],
        session=session,
    )
    assert len(events) == 1
    assert "Lagarde" in events[0].title


def test_fetch_released_only():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    events = fetch_forex_factory_events(
        released_only=True,
        session=session,
    )
    # Only first event has actual
    assert len(events) == 1
    assert events[0].has_actual is True


def test_fetch_upcoming_only():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    events = fetch_forex_factory_events(
        released_only=False,
        session=session,
    )
    assert len(events) == 2
    assert all(event.has_actual is False for event in events)


def test_fetch_require_forecast():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    events = fetch_forex_factory_events(
        require_forecast=True,
        session=session,
    )
    assert len(events) == 1
    assert events[0].has_forecast is True


def test_fetch_datetime_range():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    start_dt = datetime.fromisoformat("2026-06-16T00:00:00-04:00")
    events = fetch_forex_factory_events(
        start_datetime=start_dt,
        session=session,
    )
    assert len(events) == 2
    assert all(event.date is not None for event in events)


def test_fetch_debug_summary_counts():
    session = MockSession(payload=SAMPLE_RAW_EVENTS)
    result = fetch_forex_factory_events_debug(
        impact_filter=["High"],
        session=session,
    )
    assert isinstance(result, ForexFactoryFetchResult)
    assert result.summary.raw_count == 3
    assert result.summary.parsed_count == 3
    assert result.summary.accepted_count == 1
    assert result.summary.filtered_out_count == 2
    assert result.summary.invalid_count == 0
    assert result.summary.rejection_reasons["impact_filter"] == 2


def test_fetch_invalid_item_count():
    bad_payload = SAMPLE_RAW_EVENTS + [{"country": "USD", "impact": "High"}]
    session = MockSession(payload=bad_payload)
    result = fetch_forex_factory_events_debug(session=session)
    assert result.summary.raw_count == 4
    assert result.summary.invalid_count == 1
    assert result.summary.rejection_reasons["missing_title"] == 1


def test_fetch_invalid_top_level_shape_returns_empty():
    session = MockSession(payload={"not": "a list"})
    result = fetch_forex_factory_events_debug(session=session)
    assert result.summary.raw_count == 0
    assert result.events == []


def test_fetch_timeout_returns_empty():
    session = MockSession(exc=requests.exceptions.Timeout())
    result = fetch_forex_factory_events_debug(session=session)
    assert result.events == []
    assert result.summary.raw_count == 0


def test_event_numeric_methods():
    event = _build_event_from_raw(SAMPLE_RAW_EVENTS[0])
    assert event.actual_float() == pytest.approx(3.8)
    assert event.forecast_float() == pytest.approx(3.5)
    assert event.previous_float() == pytest.approx(3.6)
    
    
# ===========================================================================
# PERSISTENCE TESTS
# ===========================================================================

from unittest.mock import MagicMock, patch
from ingestion.forex_factory_crawler import (
    save_event,
    save_events,
    EventPersistenceResult,
    _find_existing_event,
    _should_update,
)


@pytest.fixture
def sample_event():
    return ForexFactoryEvent(
        title="US CPI y/y",
        currency="USD",
        impact="High",
        actual="3.8%",
        forecast="3.5%",
        previous="3.6%",
        date=datetime.fromisoformat("2026-06-16T08:30:00-04:00"),
        category="CPI",
    )


@pytest.fixture
def sample_event_no_actual():
    return ForexFactoryEvent(
        title="US CPI y/y",
        currency="USD",
        impact="High",
        actual="",
        forecast="3.5%",
        previous="3.6%",
        date=datetime.fromisoformat("2026-06-16T08:30:00-04:00"),
        category="CPI",
    )


@pytest.fixture
def db_session():
    """In-memory SQLite for testing."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestSaveEvent:
    def test_insert_new_event(self, sample_event, db_session):
        status = save_event(sample_event, session=db_session)
        db_session.commit()
        assert status == "inserted"

        from core.database import EventHistoryDB
        count = db_session.query(EventHistoryDB).count()
        assert count == 1

        record = db_session.query(EventHistoryDB).first()
        assert record.title == "US CPI y/y"
        assert record.currency == "USD"
        assert record.actual == pytest.approx(3.8)
        assert record.forecast == pytest.approx(3.5)
        assert record.raw_actual_str == "3.8%"
        assert record.source == "Forex Factory"

    def test_skip_duplicate_no_changes(self, sample_event, db_session):
        save_event(sample_event, session=db_session)
        db_session.commit()

        status = save_event(sample_event, session=db_session)
        db_session.commit()
        assert status == "skipped"

        from core.database import EventHistoryDB
        count = db_session.query(EventHistoryDB).count()
        assert count == 1

    def test_update_when_actual_added(
        self, sample_event_no_actual, sample_event, db_session
    ):
        # ابتدا بدون actual ذخیره کن
        save_event(sample_event_no_actual, session=db_session)
        db_session.commit()

        from core.database import EventHistoryDB
        record_before = db_session.query(EventHistoryDB).first()
        assert record_before.actual is None

        # حالا با actual ذخیره کن
        status = save_event(sample_event, session=db_session)
        db_session.commit()
        assert status == "updated"

        record_after = db_session.query(EventHistoryDB).first()
        assert record_after.actual == pytest.approx(3.8)
        assert record_after.raw_actual_str == "3.8%"
        assert record_after.updated_at is not None

        # باید فقط یک رکورد باشد
        count = db_session.query(EventHistoryDB).count()
        assert count == 1

    def test_update_when_forecast_changes(self, db_session):
        event_v1 = ForexFactoryEvent(
            title="US GDP q/q",
            currency="USD",
            impact="High",
            forecast="2.0%",
            previous="1.8%",
            date=datetime.fromisoformat("2026-06-20T08:30:00-04:00"),
            category="GDP",
        )
        save_event(event_v1, session=db_session)
        db_session.commit()

        event_v2 = ForexFactoryEvent(
            title="US GDP q/q",
            currency="USD",
            impact="High",
            forecast="2.2%",
            previous="1.8%",
            date=datetime.fromisoformat("2026-06-20T08:30:00-04:00"),
            category="GDP",
        )
        status = save_event(event_v2, session=db_session)
        db_session.commit()
        assert status == "updated"

        from core.database import EventHistoryDB
        record = db_session.query(EventHistoryDB).first()
        assert record.forecast == pytest.approx(2.2)


class TestSaveEvents:
    def test_batch_save_multiple(self, db_session):
        events = [
            ForexFactoryEvent(
                title="US CPI y/y", currency="USD", impact="High",
                actual="3.8%", forecast="3.5%", previous="3.6%",
                date=datetime.fromisoformat("2026-06-16T08:30:00-04:00"),
                category="CPI",
            ),
            ForexFactoryEvent(
                title="ECB Rate Decision", currency="EUR", impact="High",
                forecast="4.0%", previous="4.25%",
                date=datetime.fromisoformat("2026-06-17T07:45:00-04:00"),
                category="Interest Rate Decision",
            ),
            ForexFactoryEvent(
                title="UK GDP m/m", currency="GBP", impact="Medium",
                actual="0.2%", forecast="0.1%", previous="0.0%",
                date=datetime.fromisoformat("2026-06-18T02:00:00-04:00"),
                category="GDP",
            ),
        ]

        with patch("forex_factory_crawler.SessionLocal", return_value=db_session):
            result = save_events(events)

        assert result.total == 3
        assert result.inserted == 3
        assert result.updated == 0
        assert result.skipped == 0
        assert result.failed == 0

    def test_batch_save_with_duplicates(self, db_session):
        event = ForexFactoryEvent(
            title="US CPI y/y", currency="USD", impact="High",
            actual="3.8%", forecast="3.5%", previous="3.6%",
            date=datetime.fromisoformat("2026-06-16T08:30:00-04:00"),
            category="CPI",
        )

        with patch("forex_factory_crawler.SessionLocal", return_value=db_session):
            result1 = save_events([event])
            result2 = save_events([event])

        assert result1.inserted == 1
        assert result2.skipped == 1

    def test_batch_save_empty_list(self, db_session):
        with patch("forex_factory_crawler.SessionLocal", return_value=db_session):
            result = save_events([])
        assert result.total == 0
        assert result.inserted == 0


class TestShouldUpdate:
    def test_should_update_when_actual_added(self):
        from core.database import EventHistoryDB
        existing = EventHistoryDB(
            title="CPI", currency="USD", actual=None, forecast=3.5, previous=3.6,
        )
        event = ForexFactoryEvent(
            title="CPI", currency="USD", impact="High",
            actual="3.8%", forecast="3.5%", previous="3.6%",
            category="CPI",
        )
        assert _should_update(existing, event) is True

    def test_should_not_update_when_same(self):
        from core.database import EventHistoryDB
        existing = EventHistoryDB(
            title="CPI", currency="USD", actual=3.8, forecast=3.5,
            previous=3.6, impact="High",
        )
        event = ForexFactoryEvent(
            title="CPI", currency="USD", impact="High",
            actual="3.8%", forecast="3.5%", previous="3.6%",
            category="CPI",
        )
        assert _should_update(existing, event) is False

    def test_should_update_when_forecast_changes(self):
        from core.database import EventHistoryDB
        existing = EventHistoryDB(
            title="CPI", currency="USD", actual=None, forecast=3.5,
            previous=3.6, impact="High",
        )
        event = ForexFactoryEvent(
            title="CPI", currency="USD", impact="High",
            forecast="3.7%", previous="3.6%",
            category="CPI",
        )
        assert _should_update(existing, event) is True