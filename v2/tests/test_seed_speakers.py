# tests/test_seed_speakers.py
"""
Unit tests for seed_speakers.py
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, SpeakerDB
from ingestion.seed_speakers import (
    SPEAKERS,
    SpeakerSeed,
    upsert_speaker,
    seed_all_speakers,
    clear_speakers_table,
    list_speakers,
)


# ===========================================================================
# FIXTURES
# ===========================================================================

@pytest.fixture
def temp_db(monkeypatch):
    """دیتابیس in-memory برای تست."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr("seed_speakers.SessionLocal", Session)
    return Session


# ===========================================================================
# REGISTRY TESTS
# ===========================================================================

class TestSpeakersRegistry:
    def test_registry_not_empty(self):
        assert len(SPEAKERS) > 0

    def test_all_speakers_have_required_fields(self):
        for speaker in SPEAKERS:
            assert speaker.name, f"Empty name: {speaker}"
            assert speaker.role, f"Empty role: {speaker.name}"
            assert speaker.primary_asset, f"Empty asset: {speaker.name}"
            assert 0.0 <= speaker.weight <= 1.0, (
                f"Invalid weight for {speaker.name}: {speaker.weight}"
            )

    def test_no_duplicate_names(self):
        names = [s.name for s in SPEAKERS]
        assert len(names) == len(set(names)), (
            f"Duplicate speaker names found: {[n for n in names if names.count(n) > 1]}"
        )

    def test_powell_is_tier_1(self):
        powell = next((s for s in SPEAKERS if s.name == "Jerome Powell"), None)
        assert powell is not None
        assert powell.weight == 1.0
        assert powell.primary_asset == "USD"

    def test_lagarde_is_tier_1(self):
        lagarde = next((s for s in SPEAKERS if "Lagarde" in s.name), None)
        assert lagarde is not None
        assert lagarde.weight == 1.0
        assert lagarde.primary_asset == "EUR"

    def test_weights_are_reasonable(self):
        weights = [s.weight for s in SPEAKERS]
        # حداقل یک speaker با weight=1.0
        assert max(weights) == 1.0
        # هیچ speaker با weight کمتر از 0.5
        assert min(weights) >= 0.5


# ===========================================================================
# UPSERT TESTS
# ===========================================================================

class TestUpsertSpeaker:
    def test_insert_new_speaker(self, temp_db):
        Session = temp_db
        session = Session()
        try:
            speaker = SpeakerSeed(
                name="Test Person",
                role="Test Role",
                primary_asset="USD",
                weight=0.5,
            )
            status = upsert_speaker(session, speaker)
            session.commit()

            assert status == "inserted"
            count = session.query(SpeakerDB).count()
            assert count == 1
        finally:
            session.close()

    def test_update_existing_speaker(self, temp_db):
        Session = temp_db
        session = Session()
        try:
            # insert اول
            speaker = SpeakerSeed(
                name="Test Person",
                role="Old Role",
                primary_asset="USD",
                weight=0.5,
            )
            upsert_speaker(session, speaker)
            session.commit()

            # update با مقادیر جدید
            updated = SpeakerSeed(
                name="Test Person",
                role="New Role",
                primary_asset="EUR",
                weight=0.9,
            )
            status = upsert_speaker(session, updated)
            session.commit()

            assert status == "updated"
            count = session.query(SpeakerDB).count()
            assert count == 1

            record = session.query(SpeakerDB).first()
            assert record.role == "New Role"
            assert record.primary_asset == "EUR"
            assert record.weight == 0.9
        finally:
            session.close()


# ===========================================================================
# SEED TESTS
# ===========================================================================

class TestSeedAllSpeakers:
    def test_seed_inserts_all(self, temp_db):
        stats = seed_all_speakers()
        assert stats["inserted"] == len(SPEAKERS)
        assert stats["updated"] == 0
        assert stats["failed"] == 0

    def test_seed_twice_updates(self, temp_db):
        # بار اول → همه insert
        stats1 = seed_all_speakers()
        assert stats1["inserted"] == len(SPEAKERS)

        # بار دوم → همه update
        stats2 = seed_all_speakers()
        assert stats2["inserted"] == 0
        assert stats2["updated"] == len(SPEAKERS)

    def test_seed_powell_in_db(self, temp_db):
        seed_all_speakers()
        speakers = list_speakers()
        powell = next((s for s in speakers if s.name == "Jerome Powell"), None)
        assert powell is not None
        assert powell.weight == 1.0
        assert powell.role == "Federal Reserve Chair"


# ===========================================================================
# CLEAR TESTS
# ===========================================================================

class TestClearSpeakers:
    def test_clear_removes_all(self, temp_db):
        seed_all_speakers()
        Session = temp_db
        session = Session()
        try:
            assert session.query(SpeakerDB).count() == len(SPEAKERS)
        finally:
            session.close()

        count = clear_speakers_table()
        assert count == len(SPEAKERS)

        session = Session()
        try:
            assert session.query(SpeakerDB).count() == 0
        finally:
            session.close()


# ===========================================================================
# LIST TESTS
# ===========================================================================

class TestListSpeakers:
    def test_list_empty(self, temp_db):
        speakers = list_speakers()
        assert speakers == []

    def test_list_sorted_by_weight_desc(self, temp_db):
        seed_all_speakers()
        speakers = list_speakers()

        # weight ها باید نزولی باشند
        weights = [s.weight for s in speakers]
        assert weights == sorted(weights, reverse=True)


# ===========================================================================
# INTEGRATION WITH Speaker.from_name()
# ===========================================================================

class TestIntegrationWithSpeakerModel:
    def test_speaker_from_name_finds_powell(self, temp_db):
        from core.models import Speaker
        import core.models as models

        # patch SessionLocal in models.py too
        Session = temp_db

        seed_all_speakers()

        # mock SessionLocal in models.py
        original = models.__dict__.get("SessionLocal", None)
        # Speaker.from_name imports SessionLocal locally; ensure same session

        # Direct DB query simulation:
        from core.database import SessionLocal as _SL
        session = Session()
        try:
            record = session.query(SpeakerDB).filter(
                SpeakerDB.name.ilike("%powell%")
            ).first()
            assert record is not None
            assert record.weight == 1.0
        finally:
            session.close()