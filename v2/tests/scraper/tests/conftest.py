"""
Pytest fixtures for Forex Factory scraper tests.
"""

import pytest
import tempfile
from pathlib import Path
from typing import Generator, Dict, Any, List
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from forex_factory_scraper.database import Base, get_db_session
from forex_factory_scraper.models import (
    EventSchema, EventDetailSchema, QueryFilters
)
from forex_factory_scraper.utils import generate_composite_key


@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    yield db_path
    
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def db_session(temp_db: Path) -> Generator[Session, None, None]:
    """Create a database session for testing."""
    engine = create_engine(f"sqlite:///{temp_db}")
    Base.metadata.create_all(engine)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    
    # Override the get_db_session for tests
    @contextmanager
    def _get_test_session():
        yield session
    
    with patch("forex_factory_scraper.database.get_db_session", _get_test_session):
        yield session
    
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def sample_event_schema() -> EventSchema:
    """Create a sample event schema for testing."""
    return EventSchema(
        date="2026-06-26",
        time="08:30",
        currency="USD",
        impact="High",
        title="CPI m/m",
        actual="0.3%",
        forecast="0.2%",
        previous="0.1%",
        actual_float=0.3,
        forecast_float=0.2,
        previous_float=0.1,
        detail=EventDetailSchema(
            detail_source="Bureau of Labor Statistics",
            detail_measures="Consumer price inflation",
            detail_usual_effect="Actual > Forecast = good for currency",
            detail_frequency="Monthly",
        ),
        external_event_id="evt_12345",
        composite_key=generate_composite_key("2026-06-26", "08:30", "USD", "CPI m/m"),
        source_timezone="America/New_York",
    )


@pytest.fixture
def sample_event_schema_changed() -> EventSchema:
    """Create a sample event schema with changed values."""
    return EventSchema(
        date="2026-06-26",
        time="08:30",
        currency="USD",
        impact="High",
        title="CPI m/m",
        actual="0.4%",
        forecast="0.3%",
        previous="0.1%",
        actual_float=0.4,
        forecast_float=0.3,
        previous_float=0.1,
        detail=EventDetailSchema(
            detail_source="Bureau of Labor Statistics",
            detail_measures="Consumer price inflation",
            detail_usual_effect="Actual > Forecast = good for currency",
            detail_frequency="Monthly",
        ),
        external_event_id="evt_12345",
        composite_key=generate_composite_key("2026-06-26", "08:30", "USD", "CPI m/m"),
        source_timezone="America/New_York",
    )


@pytest.fixture
def sample_row_html() -> str:
    """Sample HTML for a calendar row."""
    return """
    <tr data-event-id="evt_12345">
        <td><span class="time">8:30am</span></td>
        <td><span class="flag">USD</span></td>
        <td><span class="impact high">High</span></td>
        <td><a class="event__title">CPI m/m</a></td>
        <td><span class="value">0.3%</span></td>
        <td><span class="value">0.2%</span></td>
        <td><span class="value">0.1%</span></td>
        <td class="calendar__detail"><i class="folder"></i></td>
    </tr>
    """


@pytest.fixture
def sample_detail_html() -> str:
    """Sample HTML for a detail section."""
    return """
    <div class="event__detail">
        <dl>
            <dt>Source:</dt>
            <dd>Bureau of Labor Statistics</dd>
            <dt>Measures:</dt>
            <dd>Consumer price inflation</dd>
            <dt>Usual Effect:</dt>
            <dd>Actual &gt; Forecast = good for currency</dd>
            <dt>Frequency:</dt>
            <dd>Monthly</dd>
            <dt>Next Release:</dt>
            <dd>Jul 20, 2026</dd>
            <dt>FF Notes:</dt>
            <dd>This is a key inflation indicator</dd>
            <dt>Why Traders Care:</dt>
            <dd>Inflation affects monetary policy</dd>
            <dt>Derived Via:</dt>
            <dd>Survey of households</dd>
            <dt>Also Called:</dt>
            <dd>Consumer Price Index</dd>
            <dt>Acro Expand:</dt>
            <dd>CPI (Consumer Price Index)</dd>
        </dl>
        <a href="https://example.com/release" class="latest-release">Latest Release</a>
    </div>
    """


@pytest.fixture
def sample_calendar_page_html() -> str:
    """Sample HTML for a calendar page."""
    return """
    <html>
        <body>
            <div class="calendar__header">
                <span class="week-range">Jun 26 - Jul 2, 2026</span>
            </div>
            <table class="calendar__table">
                <tbody class="calendar__tbody">
                    <tr data-event-id="evt_1">
                        <td><span class="time">8:30am</span></td>
                        <td><span class="flag">USD</span></td>
                        <td><span class="impact high">High</span></td>
                        <td><a class="event__title">CPI m/m</a></td>
                        <td><span class="value">0.3%</span></td>
                        <td><span class="value">0.2%</span></td>
                        <td><span class="value">0.1%</span></td>
                        <td class="calendar__detail"><i class="folder"></i></td>
                    </tr>
                    <tr data-event-id="evt_2">
                        <td><span class="time">10:00am</span></td>
                        <td><span class="flag">EUR</span></td>
                        <td><span class="impact medium">Medium</span></td>
                        <td><a class="event__title">Unemployment Rate</a></td>
                        <td><span class="value">6.5%</span></td>
                        <td><span class="value">6.7%</span></td>
                        <td><span class="value">6.8%</span></td>
                        <td class="calendar__detail"><i class="folder"></i></td>
                    </tr>
                </tbody>
            </table>
            <div class="calendar__nav">
                <a class="prev" href="/calendar?week=2026-06-19">Previous</a>
                <a class="next" href="/calendar?week=2026-07-03">Next</a>
            </div>
        </body>
    </html>
    """


@pytest.fixture
def mock_page():
    """Create a mock Playwright Page."""
    page = MagicMock()
    page.is_closed = MagicMock(return_value=False)
    page.goto = AsyncMock()
    page.content = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.query_selector = AsyncMock()
    page.query_selector_all = AsyncMock()
    page.click = AsyncMock()
    page.inner_html = AsyncMock()
    page.evaluate_handle = AsyncMock()
    return page


@pytest.fixture
def mock_scraper():
    """Create a mock scraper."""
    scraper = MagicMock()
    scraper.navigate_to_week = MagicMock(return_value=True)
    scraper.get_current_week_events = MagicMock()
    scraper.fetch_week_events = MagicMock()
    scraper.fetch_date_range = MagicMock()
    scraper.close = MagicMock()
    scraper.set_run_id = MagicMock()
    return scraper