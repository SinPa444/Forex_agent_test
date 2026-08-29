"""
Tests for detail parser.
"""

import pytest
from forex_factory_scraper.detail_parser import DetailParser, parse_detail_html
from forex_factory_scraper.models import EventDetailSchema


class TestDetailParser:
    def test_parse_detail(self, sample_detail_html):
        parser = DetailParser(sample_detail_html)
        schema = parser.parse()
        
        assert schema.detail_source == "Bureau of Labor Statistics"
        assert schema.detail_measures == "Consumer price inflation"
        assert schema.detail_usual_effect == "Actual > Forecast = good for currency"
        assert schema.detail_frequency == "Monthly"
        assert schema.detail_next_release == "Jul 20, 2026"
        assert schema.detail_ff_notes == "This is a key inflation indicator"
        assert schema.detail_why_traders_care == "Inflation affects monetary policy"
        assert schema.detail_derived_via == "Survey of households"
        assert schema.detail_also_called == "Consumer Price Index"
        assert schema.detail_acro_expand == "CPI (Consumer Price Index)"
        
        # Check raw fields
        assert schema.raw_detail_html is not None
        assert schema.raw_detail_text is not None
    
    def test_parse_empty_detail(self):
        parser = DetailParser("")
        schema = parser.parse()
        assert schema.detail_source is None
        assert schema.raw_detail_html == ""
    
    def test_parse_detail_without_dl(self):
        html = """
        <div class="event__detail">
            <p>Source: Bureau of Labor Statistics</p>
            <p>Measures: Consumer price inflation</p>
        </div>
        """
        parser = DetailParser(html)
        schema = parser.parse()
        # The parser uses the flexible text search as fallback
        assert schema.detail_source is not None or schema.detail_measures is not None
    
    def test_parse_detail_convenience(self, sample_detail_html):
        schema = parse_detail_html(sample_detail_html)
        assert isinstance(schema, EventDetailSchema)
        assert schema.detail_source == "Bureau of Labor Statistics"