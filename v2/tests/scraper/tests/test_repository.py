"""
Tests for repository layer.
"""

import pytest
from datetime import datetime
from sqlalchemy.orm import Session

from forex_factory_scraper.repository import EventRepository, ScrapeRunRepository
from forex_factory_scraper.models import EventSchema, QueryFilters, ScrapeRunSchema
from forex_factory_scraper.database import Event


class TestEventRepository:
    def test_upsert_insert(self, db_session: Session, sample_event_schema: EventSchema):
        repo = EventRepository(db_session)
        event, inserted, updated = repo.upsert_event(sample_event_schema)
        
        assert inserted is True
        assert updated is False
        assert event.id is not None
        assert event.title == "CPI m/m"
        
        # Verify in database
        db_event = db_session.query(Event).filter(Event.id == event.id).first()
        assert db_event is not None
        assert db_event.actual == "0.3%"
    
    def test_upsert_update(self, db_session: Session, sample_event_schema: EventSchema):
        repo = EventRepository(db_session)
        
        # Insert first
        event1, inserted, _ = repo.upsert_event(sample_event_schema)
        assert inserted is True
        
        # Update with changed data
        schema_changed = sample_event_schema.copy(deep=True)
        schema_changed.actual = "0.4%"
        schema_changed.actual_float = 0.4
        
        event2, _, updated = repo.upsert_event(schema_changed)
        assert updated is True
        
        # Verify update
        db_event = db_session.query(Event).filter(Event.id == event2.id).first()
        assert db_event.actual == "0.4%"
        assert db_event.actual_float == 0.4
        
        # Check revision was created
        revisions = repo.get_revisions(event2.id)
        assert len(revisions) == 1
        assert "actual" in revisions[0].changed_fields
    
    def test_upsert_skip_no_changes(self, db_session: Session, sample_event_schema: EventSchema):
        repo = EventRepository(db_session)
        
        # Insert first
        event1, inserted, _ = repo.upsert_event(sample_event_schema)
        assert inserted is True
        
        # Upsert again with same data
        event2, _, updated = repo.upsert_event(sample_event_schema)
        assert updated is False
        
        # No new revision
        revisions = repo.get_revisions(event2.id)
        assert len(revisions) == 0
    
    def test_query_events(self, db_session: Session, sample_event_schema: EventSchema):
        repo = EventRepository(db_session)
        
        # Insert event
        repo.upsert_event(sample_event_schema)
        
        # Query with filters
        filters = QueryFilters(
            currency=["USD"],
            impact=["High"],
            date_range_start="2026-06-01",
            date_range_end="2026-06-30",
        )
        events = repo.query_events(filters)
        assert len(events) == 1
        
        # Query with no matches
        filters = QueryFilters(
            currency=["EUR"],
        )
        events = repo.query_events(filters)
        assert len(events) == 0
    
    def test_count_events(self, db_session: Session, sample_event_schema: EventSchema):
        repo = EventRepository(db_session)
        
        # Insert event
        repo.upsert_event(sample_event_schema)
        
        count = repo.count_events(QueryFilters())
        assert count == 1
        
        count = repo.count_events(QueryFilters(currency=["USD"]))
        assert count == 1
        
        count = repo.count_events(QueryFilters(currency=["EUR"]))
        assert count == 0
    
    def test_get_currencies(self, db_session: Session, sample_event_schema: EventSchema):
        repo = EventRepository(db_session)
        
        # Insert event
        repo.upsert_event(sample_event_schema)
        
        currencies = repo.get_currencies()
        assert "USD" in currencies
    
    def test_get_impacts(self, db_session: Session, sample_event_schema: EventSchema):
        repo = EventRepository(db_session)
        
        # Insert event
        repo.upsert_event(sample_event_schema)
        
        impacts = repo.get_impacts()
        assert "High" in impacts


class TestScrapeRunRepository:
    def test_create_run(self, db_session: Session):
        repo = ScrapeRunRepository(db_session)
        
        run_schema = ScrapeRunSchema(
            run_id="test_run_001",
            mode="live",
        )
        run = repo.create_run(run_schema)
        assert run.id is not None
        assert run.run_id == "test_run_001"
    
    def test_update_run(self, db_session: Session):
        repo = ScrapeRunRepository(db_session)
        
        run_schema = ScrapeRunSchema(
            run_id="test_run_001",
            mode="live",
        )
        run = repo.create_run(run_schema)
        
        updated = repo.update_run("test_run_001", status="success", events_found=10)
        assert updated is not None
        assert updated.status == "success"
        assert updated.events_found == 10
    
    def test_get_last_run(self, db_session: Session):
        repo = ScrapeRunRepository(db_session)
        
        run1 = ScrapeRunSchema(run_id="run_001", mode="live")
        run2 = ScrapeRunSchema(run_id="run_002", mode="backfill")
        
        repo.create_run(run1)
        repo.create_run(run2)
        
        last = repo.get_last_run()
        assert last.run_id == "run_002"
        
        last_live = repo.get_last_run(mode="live")
        assert last_live.run_id == "run_001"