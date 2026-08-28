"""
Tests for exporters.
"""

import pytest
import json
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from forex_factory_scraper.exporters import (
    export_to_csv,
    export_to_json,
    print_summary,
    export_events_to_csv,
    export_events_to_json,
)
from forex_factory_scraper.models import EventSchema, EventDetailSchema, QueryFilters
from forex_factory_scraper.database import Event


class TestExporters:
    def test_export_to_csv(self, db_session, sample_event_schema):
        # Insert event
        from forex_factory_scraper.repository import EventRepository
        repo = EventRepository(db_session)
        repo.upsert_event(sample_event_schema)
        db_session.commit()
        
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.csv"
            count = export_to_csv(output, filters=QueryFilters())
            assert count == 1
            assert output.exists()
            
            # Verify content
            with open(output, "r") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                assert len(rows) == 1
                assert rows[0]["title"] == "CPI m/m"
    
    def test_export_to_json(self, db_session, sample_event_schema):
        # Insert event
        from forex_factory_scraper.repository import EventRepository
        repo = EventRepository(db_session)
        repo.upsert_event(sample_event_schema)
        db_session.commit()
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.json"
            count = export_to_json(output, filters=QueryFilters())
            assert count == 1
            assert output.exists()
            
            # Verify content
            with open(output, "r") as f:
                data = json.load(f)
                assert len(data) == 1
                assert data[0]["title"] == "CPI m/m"
    
    def test_export_events_to_csv(self, sample_event_schema):
        events = [sample_event_schema]
        
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.csv"
            count = export_events_to_csv(events, output)
            assert count == 1
            assert output.exists()
    
    def test_export_events_to_json(self, sample_event_schema):
        events = [sample_event_schema]
        
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.json"
            count = export_events_to_json(events, output)
            assert count == 1
            assert output.exists()
            
            with open(output, "r") as f:
                data = json.load(f)
                assert len(data) == 1
                assert data[0]["title"] == "CPI m/m"
    
    def test_print_summary(self, sample_event_schema, capsys):
        events = [sample_event_schema]
        print_summary(events, title="Test Summary")
        
        captured = capsys.readouterr()
        assert "Test Summary" in captured.out
        assert "CPI m/m" in captured.out
        assert "USD" in captured.out
    
    def test_print_summary_empty(self, capsys):
        print_summary([], title="Empty")
        
        captured = capsys.readouterr()
        assert "Empty" in captured.out
        assert "No events found" in captured.out