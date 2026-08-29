"""
Tests for calendar row parser.
"""

import pytest
from forex_factory_scraper.parser import CalendarRowParser, CalendarPageParser


class TestCalendarRowParser:
    def test_parse_valid_row(self, sample_row_html):
        parser = CalendarRowParser(sample_row_html)
        data = parser.parse()
        
        assert data is not None
        assert data["time"] == "8:30am"
        assert data["currency"] == "USD"
        assert data["impact"] == "High"
        assert data["title"] == "CPI m/m"
        assert data["actual"] == "0.3%"
        assert data["forecast"] == "0.2%"
        assert data["previous"] == "0.1%"
        assert data["actual_float"] == 0.3
        assert data["forecast_float"] == 0.2
        assert data["previous_float"] == 0.1
        assert data["external_event_id"] == "evt_12345"
    
    def test_parse_row_without_external_id(self):
        html = """
        <tr>
            <td><span class="time">8:30am</span></td>
            <td><span class="flag">USD</span></td>
            <td><span class="impact high">High</span></td>
            <td><a class="event__title">CPI m/m</a></td>
            <td><span class="value">0.3%</span></td>
            <td><span class="value">0.2%</span></td>
            <td><span class="value">0.1%</span></td>
        </tr>
        """
        parser = CalendarRowParser(html)
        data = parser.parse()
        assert data is not None
        assert data["external_event_id"] is None
    
    def test_parse_row_without_title(self):
        html = """
        <tr>
            <td><span class="time">8:30am</span></td>
            <td><span class="flag">USD</span></td>
            <td><span class="impact high">High</span></td>
            <td></td>
            <td><span class="value">0.3%</span></td>
            <td><span class="value">0.2%</span></td>
            <td><span class="value">0.1%</span></td>
        </tr>
        """
        parser = CalendarRowParser(html)
        data = parser.parse()
        assert data is None
    
    def test_extract_impact_from_class(self):
        html = """
        <tr>
            <td><span class="time">8:30am</span></td>
            <td><span class="flag">USD</span></td>
            <td><span class="impact medium">Medium</span></td>
            <td><a class="event__title">CPI m/m</a></td>
            <td><span class="value">0.3%</span></td>
            <td><span class="value">0.2%</span></td>
            <td><span class="value">0.1%</span></td>
        </tr>
        """
        parser = CalendarRowParser(html)
        data = parser.parse()
        assert data["impact"] == "Medium"
    
    def test_extract_impact_from_text(self):
        html = """
        <tr>
            <td><span class="time">8:30am</span></td>
            <td><span class="flag">USD</span></td>
            <td>Low</td>
            <td><a class="event__title">CPI m/m</a></td>
            <td><span class="value">0.3%</span></td>
            <td><span class="value">0.2%</span></td>
            <td><span class="value">0.1%</span></td>
        </tr>
        """
        parser = CalendarRowParser(html)
        data = parser.parse()
        assert data["impact"] == "Low"


class TestCalendarPageParser:
    def test_parse_page(self, sample_calendar_page_html):
        parser = CalendarPageParser(sample_calendar_page_html)
        rows = parser.get_event_rows()
        assert len(rows) == 2
    
    def test_get_week_range(self, sample_calendar_page_html):
        parser = CalendarPageParser(sample_calendar_page_html)
        start, end = parser.get_current_week_range()
        assert start == "Jun 26"
        assert end == "Jul 2, 2026"
    
    def test_get_navigation_urls(self, sample_calendar_page_html):
        parser = CalendarPageParser(sample_calendar_page_html)
        nav = parser.get_navigation_urls()
        assert nav.get("prev") == "/calendar?week=2026-06-19"
        assert nav.get("next") == "/calendar?week=2026-07-03"
    
    def test_parse_empty_page(self):
        parser = CalendarPageParser("<html></html>")
        rows = parser.get_event_rows()
        assert rows == []
        
        start, end = parser.get_current_week_range()
        assert start is None
        assert end is None