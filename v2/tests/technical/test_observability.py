"""
tests/technical/test_observability.py
=====================================
تست‌های Lایه observability — decision log (idempotency) + run manifest.

همه آفلاین: SQLite در tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ===========================================================================
# Decision log
# ===========================================================================

@pytest.fixture()
def tech_db(tmp_path, monkeypatch):
    """یک DB موقت با جدول technical_decisions."""
    import core.database as db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test_tech.db"
    engine = create_engine(f"sqlite:///{db_path}")
    db.Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(db, "SessionLocal", TestingSession)
    return db


def _fake_metrics(score=0.42, price=1.105):
    from agents.technical.models import ComponentScores, TechnicalMetrics
    return TechnicalMetrics(
        current_price=price,
        technical_score=score,
        technical_confidence=0.6,
        components=ComponentScores(structure=0.8, trend=0.8),
        data_quality_json='{"ok": true}',
        provenance_json='{"source": "yfinance"}',
    )


def _fake_report(score=0.42):
    from agents.technical.signals.report import TechnicalReport
    return TechnicalReport(
        direction=1, score=score, confidence=0.6,
        strategy="test", reasoning="test", llm_status="ok",
    )


class TestDecisionLog:
    def test_record_and_idempotent(self, tech_db):
        from core.database import SessionLocal, TechnicalDecisionDB
        from observability.decision_log import log_technical_decision, make_dedup_hash

        with SessionLocal() as s1:
            row_id = log_technical_decision(
                session=s1, ticker="EURUSD=X", timeframe="H1", run_id="run-test",
                metrics=_fake_metrics(), report=_fake_report(),
                engine_version="2.0.0", weights_hash="abc123",
            )
        assert row_id is not None

        # اجرای دقیقاً مشابه → ردیف جدید نمی‌سازد
        with SessionLocal() as s2:
            row_id2 = log_technical_decision(
                session=s2, ticker="EURUSD=X", timeframe="H1", run_id="run-test",
                metrics=_fake_metrics(), report=_fake_report(),
                engine_version="2.0.0", weights_hash="abc123",
            )
        assert row_id2 == row_id

        with SessionLocal() as s3:
            count = s3.query(TechnicalDecisionDB).filter(
                TechnicalDecisionDB.run_id == "run-test").count()
        assert count == 1

    def test_different_run_creates_new_row(self, tech_db):
        from core.database import SessionLocal, TechnicalDecisionDB
        from observability.decision_log import log_technical_decision

        for run in ("run-a", "run-b"):
            with SessionLocal() as s:
                log_technical_decision(
                    session=s, ticker="EURUSD=X", timeframe="H1", run_id=run,
                    metrics=_fake_metrics(), report=_fake_report(),
                    engine_version="2.0.0", weights_hash="abc123",
                )
        with SessionLocal() as s:
            count = s.query(TechnicalDecisionDB).count()
        assert count == 2

    def test_plan_trade_backfill(self, tech_db):
        from core.database import SessionLocal, TechnicalDecisionDB
        from observability.decision_log import log_technical_decision

        with SessionLocal() as s:
            log_technical_decision(
                session=s, ticker="EURUSD=X", timeframe="H1", run_id="run-x",
                metrics=_fake_metrics(), report=_fake_report(),
                engine_version="2.0.0", weights_hash="abc123",
            )
            s.query(TechnicalDecisionDB).filter(
                TechnicalDecisionDB.run_id == "run-x",
                TechnicalDecisionDB.timeframe == "H1",
            ).update({"plan_id": 7, "trade_id": 9}, synchronize_session=False)
            s.commit()

        with SessionLocal() as s:
            row = s.query(TechnicalDecisionDB).first()
        assert row.plan_id == 7
        assert row.trade_id == 9

    def test_failure_is_silent(self, tech_db):
        """خطا در لاگ نباید استثنا بدهد (قاعده سکوت در خطا)."""
        from observability.decision_log import log_technical_decision

        class _Broken:
            def __getattr__(self, name):
                raise RuntimeError("db exploded")

        result = log_technical_decision(
            session=_Broken(), ticker="X", timeframe="H1", run_id="r",
            metrics=_fake_metrics(), report=_fake_report(),
            engine_version="2.0.0", weights_hash="abc",
        )
        assert result is None

    def test_dedup_hash_deterministic(self):
        from observability.decision_log import make_dedup_hash
        h1 = make_dedup_hash("EURUSD=X", "H1", "run-1", 1.1, 0.42)
        h2 = make_dedup_hash("EURUSD=X", "H1", "run-1", 1.1, 0.42)
        h3 = make_dedup_hash("EURUSD=X", "H1", "run-1", 1.2, 0.42)
        assert h1 == h2
        assert h1 != h3


# ===========================================================================
# Run manifest
# ===========================================================================

class TestRunManifest:
    def test_start_update_finish(self, tmp_path):
        from observability.run_manifest import (
            finish_manifest, new_run_id, start_manifest, update_manifest,
        )

        run_id = new_run_id()
        path = start_manifest(
            run_id=run_id,
            engine_version="2.0.0",
            weights_hash="abc123",
            technical_code_hash="deadbeef",
            params={"currencies": ["EUR"]},
            runs_dir=tmp_path,
        )
        assert path.exists()

        update_manifest(path, verdict_EUR={"verdict": "PASS"})

        finish_manifest(path, status="completed")

        data = json.loads(path.read_text())
        assert data["run_id"] == run_id
        assert data["status"] == "completed"
        assert data["verdict_EUR"]["verdict"] == "PASS"
        assert data["packages"]  # نسخه‌ی پکیج‌ها ثبت شده
        assert data["started_at"] and data["finished_at"]

    def test_run_id_unique_within_process(self):
        from observability.run_manifest import new_run_id
        ids = {new_run_id() for _ in range(20)}
        assert len(ids) == 20

    def test_code_hash_changes_with_code(self, tmp_path):
        from observability.run_manifest import code_hash

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "a.py").write_text("x = 1\n")
        h1 = code_hash(pkg)
        (pkg / "a.py").write_text("x = 2\n")
        h2 = code_hash(pkg)
        assert h1 != h2
        assert len(h1) == 16
