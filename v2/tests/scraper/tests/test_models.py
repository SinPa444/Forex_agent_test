"""
Tests for Pydantic models.
"""

import pytest
from datetime import datetime
from forex_factory_scraper.models import (
    EventSchema,
    EventDetailSchema,
    ScrapeRunSchema,
    QueryFilters,
)


class TestEventSchema:
    def test_valid_event(self):
        event = EventSchema(
            date="2026-06-26",
            time="08:30",
            currency="USD",
            impact="High",
            title="CPI m/m",
        )
        assert event.date == "2026-06-26"
        assert event.currency == "USD"
        assert event.impact == "High"
    
    def test_impact_validation(self):
        event = EventSchema(
            date="2026-06-26",
            time="08:30",
            currency="USD",
            impact="Invalid",
            title="CPI m/m",
        )
        assert event.impact == "Low"  # Default fallback
    
    def test_currency_uppercase(self):
        event = EventSchema(
            date="2026-06-26",
            time="08:30",
            currency="usd",
            impact="High",
            title="CPI m/m",
        )
        assert event.currency == "USD"
    
    def test_empty_currency(self):
        event = EventSchema(
            date="2026-06-26",
            time="08:30",
            currency="",
            impact="High",
            title="CPI m/m",
        )
        assert event.currency == "UNK"
    
    def test_default_scrape_timestamp(self):
        event = EventSchema(
            date="2026-06-26",
            time="08:30",
            currency="USD",
            impact="High",
            title="CPI m/m",
        )
        assert event.scrape_timestamp is not None
        assert isinstance(event.scrape_timestamp, datetime)
    
    def test_composite_key_generation(self):
        event = EventSchema(
            date="2026-06-26",
            time="08:30",
            currency="USD",
            impact="High",
            title="CPI m/m",
            composite_key="test_key",
        )
        assert event.composite_key == "test_key"


class TestEventDetailSchema:
    def test_valid_detail(self):
        detail = EventDetailSchema(
            detail_source="Bureau of Labor Statistics",
            detail_measures="Consumer price inflation",
            detail_usual_effect="Actual > Forecast = good for currency",
        )
        assert detail.detail_source == "Bureau of Labor Statistics"
    
    def test_has_content(self):
        detail = EventDetailSchema()
        assert not detail.has_content()
        
        detail.detail_source = "Source"
        assert detail.has_content()
    
    def test_to_dict(self):
        detail = EventDetailSchema(
            detail_source="Source",
            detail_measures="Measures",
        )
        d = detail.to_dict()
        assert d["detail_source"] == "Source"
        assert d["detail_measures"] == "Measures"
        assert "raw_detail_html" not in d  # Excluded because None


class TestScrapeRunSchema:
    def test_valid_scrape_run(self):
        run = ScrapeRunSchema(
            run_id="test_run_001",
            mode="live",
        )
        assert run.run_id == "test_run_001"
        assert run.mode == "live"
        assert run.status == "running"
        assert run.events_found == 0
    
    def test_complete_scrape_run(self):
        run = ScrapeRunSchema(
            run_id="test_run_001",
            mode="backfill",
            date_range_start="2026-01-01",
            date_range_end="2026-06-26",
            events_found=100,
            events_inserted=50,
            events_updated=30,
            events_skipped=20,
            status="success",
        )
        assert run.events_found == 100
        assert run.status == "success"


class TestQueryFilters:
    def test_empty_filters(self):
        filters = QueryFilters()
        assert filters.date_range_start is None
        assert filters.currency is None
    
    def test_filters_with_values(self):
        filters = QueryFilters(
            date_range_start="2026-01-01",
            date_range_end="2026-06-26",
            currency=["USD", "EUR"],
            impact=["High"],
            has_actual=True,
        )
        assert filters.date_range_start == "2026-01-01"
        assert filters.currency == ["USD", "EUR"]
        assert filters.has_actual is True
    
    def test_to_dict(self):
        filters = QueryFilters(
            date_range_start="2026-01-01",
            currency=["USD"],
        )
        d = filters.to_dict()
        assert d["date_range_start"] == "2026-01-01"
        assert d["currency"] == ["USD"]
        assert "date_range_end" not in d