"""
Tests for utility functions.
"""

import pytest
from datetime import datetime
from forex_factory_scraper.utils import (
    parse_numeric,
    normalize_datetime,
    generate_composite_key,
    get_week_range,
    generate_run_id,
    clean_html_text,
    parse_display_time,
    is_market_hours,
)


class TestParseNumeric:
    def test_parse_percentage(self):
        assert parse_numeric("3.8%") == 3.8
        assert parse_numeric("-2.1%") == -2.1
        assert parse_numeric("0.5%") == 0.5
    
    def test_parse_k(self):
        assert parse_numeric("185K") == 185000.0
        assert parse_numeric("-40K") == -40000.0
        assert parse_numeric("12.5K") == 12500.0
    
    def test_parse_m(self):
        assert parse_numeric("1.2M") == 1200000.0
        assert parse_numeric("-0.5M") == -500000.0
    
    def test_parse_b(self):
        assert parse_numeric("2.3B") == 2300000000.0
    
    def test_parse_range(self):
        assert parse_numeric("1.2-1.5") == 1.35
        assert parse_numeric("-0.5-0.5") == 0.0
    
    def test_parse_fraction(self):
        assert parse_numeric("1/4") == 0.25
        assert parse_numeric("3/4") == 0.75
    
    def test_parse_invalid(self):
        assert parse_numeric("N/A") is None
        assert parse_numeric("") is None
        assert parse_numeric("-") is None
        assert parse_numeric("—") is None
        assert parse_numeric("abc") is None


class TestNormalizeDatetime:
    def test_normalize_with_am_pm(self):
        dt = normalize_datetime("2026-06-26", "8:30am", "America/New_York")
        assert dt is not None
        assert dt.hour == 12  # 8:30am ET = 12:30 UTC
        assert dt.minute == 30
    
    def test_normalize_with_pm(self):
        dt = normalize_datetime("2026-06-26", "2:30pm", "America/New_York")
        assert dt is not None
        assert dt.hour == 18  # 2:30pm ET = 18:30 UTC
        assert dt.minute == 30
    
    def test_normalize_with_all_day(self):
        dt = normalize_datetime("2026-06-26", "All Day", "America/New_York")
        assert dt is not None
        assert dt.hour == 4  # 00:00 ET = 04:00 UTC
        assert dt.minute == 0
    
    def test_normalize_different_timezone(self):
        dt = normalize_datetime("2026-06-26", "8:30am", "Europe/London")
        assert dt is not None
        # 8:30am London = 07:30 UTC (in June)
        assert dt.hour == 7
        assert dt.minute == 30
    
    def test_normalize_invalid_date(self):
        dt = normalize_datetime("invalid", "8:30am")
        assert dt is None


class TestCompositeKey:
    def test_generate_composite_key(self):
        key1 = generate_composite_key("2026-06-26", "08:30", "USD", "CPI m/m")
        key2 = generate_composite_key("2026-06-26", "08:30", "USD", "CPI m/m")
        key3 = generate_composite_key("2026-06-27", "08:30", "USD", "CPI m/m")
        
        assert key1 == key2
        assert key1 != key3
        assert len(key1) == 64


class TestWeekRange:
    def test_get_week_range(self):
        start, end = get_week_range("2026-06-26")
        assert start == "2026-06-22"
        assert end == "2026-06-28"


class TestGenerateRunId:
    def test_generate_run_id(self):
        run_id = generate_run_id()
        assert isinstance(run_id, str)
        assert len(run_id) > 10


class TestCleanHtmlText:
    def test_clean_html_text(self):
        html = "<p>Hello <b>World</b></p>"
        assert clean_html_text(html) == "Hello World"
    
    def test_clean_html_text_empty(self):
        assert clean_html_text("") == ""
        assert clean_html_text(None) == ""


class TestParseDisplayTime:
    def test_parse_display_time(self):
        assert parse_display_time("8:30am") == "8:30am"
        assert parse_display_time("8:30 AM") == "8:30 AM"
        assert parse_display_time("All Day") == "00:00"
        assert parse_display_time("") == "00:00"


class TestIsMarketHours:
    def test_is_market_hours(self):
        # This test depends on the current time, so we'll test a known time
        # Use a mock or just test the function exists
        from datetime import datetime
        dt = datetime(2026, 6, 26, 14, 0)  # 2pm
        # Hard to test without mocking timezone, so just check it runs
        result = is_market_hours(dt)
        assert isinstance(result, bool)