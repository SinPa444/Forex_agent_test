# tests/test_historical_events_importer.py
"""
Unit tests for historical_events_importer.py
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, EventHistoryDB
from ingestion.historical_events_importer import (
    _parse_csv_datetime,
    _parse_numeric,
    _normalize_currency,
    _normalize_impact,
    _infer_category,
    _clean_detail_text,
    _parse_row,
    import_historical_csv,
    HistoricalImportResult,
)


# ===========================================================================
# FIXTURES
# ===========================================================================

@pytest.fixture
def temp_db():
    """دیتابیس in-memory برای تست."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return engine, Session


@pytest.fixture
def sample_csv():
    """فایل CSV نمونه با چند ردیف."""
    content = (
        "DateTime,Currency,Impact,Event,Actual,Forecast,Previous,Detail\n"
        '2007-01-01T04:30:00+03:30,CNY,High Impact Expected,Manufacturing PMI,54.8,,55.3,"Source: CFLP"\n'
        '2007-01-01T23:59:59+03:30,USD,Non-Economic,Bank Holiday,,,,"US holiday"\n'
        '2007-01-03T18:30:00+03:30,USD,High Impact Expected,ISM Manufacturing PMI,51.4,51.0,49.5,"Source: ISM"\n'
        '2007-01-03T16:45:00+03:30,USD,Medium Impact Expected,ADP Non-Farm Employment Change,-40K,120K,230K,"Source: ADP"\n'
        '2007-01-03T22:30:00+03:30,USD,High Impact Expected,FOMC Meeting Minutes,,,,"Source: Federal Reserve"\n'
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        path = f.name

    yield path

    os.unlink(path)


# ===========================================================================
# PARSING TESTS
# ===========================================================================

class TestParseDatetime:
    def test_iso_with_timezone(self):
        result = _parse_csv_datetime("2007-01-03T18:30:00+03:30")
        assert result is not None
        assert isinstance(result, datetime)

    def test_iso_no_timezone(self):
        result = _parse_csv_datetime("2007-01-03T18:30:00")
        assert result is not None

    def test_empty_string(self):
        assert _parse_csv_datetime("") is None

    def test_none(self):
        assert _parse_csv_datetime(None) is None

    def test_invalid(self):
        assert _parse_csv_datetime("not-a-date") is None


class TestParseNumeric:
    def test_percent(self):
        assert _parse_numeric("3.8%") == pytest.approx(3.8)

    def test_thousands(self):
        assert _parse_numeric("185K") == pytest.approx(185000)

    def test_negative_thousands(self):
        assert _parse_numeric("-40K") == pytest.approx(-40000)

    def test_millions(self):
        assert _parse_numeric("1.2M") == pytest.approx(1_200_000)

    def test_billions(self):
        assert _parse_numeric("4.5B") == pytest.approx(4_500_000_000)

    def test_compound_returns_none(self):
        # Bond auction format: "yield|bid-to-cover"
        assert _parse_numeric("3.94|1.8") is None

    def test_blank(self):
        assert _parse_numeric("") is None

    def test_dash(self):
        assert _parse_numeric("-") is None

    def test_none(self):
        assert _parse_numeric(None) is None

    def test_comma_thousands(self):
        assert _parse_numeric("1,250") == pytest.approx(1250)


class TestNormalizeImpact:
    def test_high_impact_expected(self):
        assert _normalize_impact("High Impact Expected") == "High"

    def test_medium_impact_expected(self):
        assert _normalize_impact("Medium Impact Expected") == "Medium"

    def test_low_impact_expected(self):
        assert _normalize_impact("Low Impact Expected") == "Low"

    def test_non_economic(self):
        assert _normalize_impact("Non-Economic") == "Non-Economic"

    def test_short_form(self):
        assert _normalize_impact("High") == "High"

    def test_empty(self):
        assert _normalize_impact("") == "Unknown"

    def test_none(self):
        assert _normalize_impact(None) == "Unknown"


class TestNormalizeCurrency:
    def test_lowercase(self):
        assert _normalize_currency("usd") == "USD"

    def test_with_spaces(self):
        assert _normalize_currency("  EUR  ") == "EUR"

    def test_none(self):
        assert _normalize_currency(None) == ""


class TestInferCategory:
    def test_cpi(self):
        assert _infer_category("US CPI y/y") == "CPI"

    def test_nfp(self):
        assert _infer_category("Non-Farm Employment Change") == "Non-Farm Payrolls"

    def test_pmi(self):
        assert _infer_category("ISM Manufacturing PMI") == "PMI"

    def test_fomc(self):
        assert _infer_category("FOMC Meeting Minutes") == "Central Bank Commentary"

    def test_bank_holiday(self):
        assert _infer_category("Bank Holiday") == "Bank Holiday"

    def test_unknown(self):
        assert _infer_category("Some Random Event Name") == "Unknown"


class TestCleanDetailText:
    def test_normal(self):
        text = "Source: ISM | Measures: Level of diffusion"
        assert _clean_detail_text(text) == text

    def test_extra_whitespace(self):
        result = _clean_detail_text("Source:    ISM   |   Measures: stuff")
        assert "    " not in result
        assert "Source: ISM" in result

    def test_empty(self):
        assert _clean_detail_text("") is None

    def test_none(self):
        assert _clean_detail_text(None) is None


# ===========================================================================
# ROW PARSING TESTS
# ===========================================================================

class TestParseRow:
    def test_full_valid_row(self):
        row = {
            "DateTime": "2007-01-03T18:30:00+03:30",
            "Currency": "USD",
            "Impact": "High Impact Expected",
            "Event": "ISM Manufacturing PMI",
            "Actual": "51.4",
            "Forecast": "51.0",
            "Previous": "49.5",
            "Detail": "Source: ISM",
        }
        parsed = _parse_row(row)
        assert parsed.title == "ISM Manufacturing PMI"
        assert parsed.currency == "USD"
        assert parsed.impact == "High"
        assert parsed.category == "PMI"
        assert parsed.actual_float == pytest.approx(51.4)
        assert parsed.forecast_float == pytest.approx(51.0)
        assert parsed.detail_text == "Source: ISM"

    def test_missing_title_raises(self):
        row = {"Currency": "USD", "Impact": "High", "Event": ""}
        with pytest.raises(ValueError, match="missing_title"):
            _parse_row(row)

    def test_missing_currency_raises(self):
        row = {"Currency": "", "Impact": "High", "Event": "CPI"}
        with pytest.raises(ValueError, match="missing_currency"):
            _parse_row(row)

    def test_empty_actual_handled(self):
        row = {
            "DateTime": "2007-01-01T04:30:00+03:30",
            "Currency": "CNY",
            "Impact": "High Impact Expected",
            "Event": "Manufacturing PMI",
            "Actual": "54.8",
            "Forecast": "",
            "Previous": "55.3",
            "Detail": "",
        }
        parsed = _parse_row(row)
        assert parsed.actual_float == pytest.approx(54.8)
        assert parsed.forecast_float is None
        assert parsed.previous_float == pytest.approx(55.3)
        assert parsed.detail_text is None


# ===========================================================================
# IMPORT TESTS
# ===========================================================================

class TestImportHistoricalCSV:
    def test_import_basic(self, sample_csv, temp_db, monkeypatch):
        engine, Session = temp_db
        monkeypatch.setattr(
            "historical_events_importer.SessionLocal",
            Session,
        )

        result = import_historical_csv(sample_csv)

        assert result.total_rows == 5
        # Bank Holiday + FOMC (no actual/forecast/previous) → skipped or saved without numeric
        # Bank Holiday is Non-Economic → skipped by default
        assert result.inserted >= 3  # PMI, ISM, ADP حداقل

        session = Session()
        try:
            count = session.query(EventHistoryDB).count()
            assert count >= 3

            # تأیید detail ذخیره شده
            pmi = session.query(EventHistoryDB).filter_by(
                title="ISM Manufacturing PMI"
            ).first()
            assert pmi is not None
            assert pmi.detail_text == "Source: ISM"
            assert pmi.actual == pytest.approx(51.4)
            assert pmi.source == "Forex Factory (Historical CSV)"
        finally:
            session.close()

    def test_skip_non_economic_default(self, sample_csv, temp_db, monkeypatch):
        engine, Session = temp_db
        monkeypatch.setattr(
            "historical_events_importer.SessionLocal",
            Session,
        )

        result = import_historical_csv(sample_csv, skip_non_economic=True)

        session = Session()
        try:
            bank_holiday = session.query(EventHistoryDB).filter_by(
                title="Bank Holiday"
            ).first()
            assert bank_holiday is None  # skipped
        finally:
            session.close()

    def test_include_non_economic(self, sample_csv, temp_db, monkeypatch):
        engine, Session = temp_db
        monkeypatch.setattr(
            "historical_events_importer.SessionLocal",
            Session,
        )

        result = import_historical_csv(sample_csv, skip_non_economic=False)

        session = Session()
        try:
            bank_holiday = session.query(EventHistoryDB).filter_by(
                title="Bank Holiday"
            ).first()
            assert bank_holiday is not None
        finally:
            session.close()

    def test_require_actual_and_forecast(self, sample_csv, temp_db, monkeypatch):
        engine, Session = temp_db
        monkeypatch.setattr(
            "historical_events_importer.SessionLocal",
            Session,
        )

        result = import_historical_csv(
            sample_csv,
            require_actual_and_forecast=True,
        )

        session = Session()
        try:
            # Manufacturing PMI (no forecast) → skipped
            pmi = session.query(EventHistoryDB).filter_by(
                title="Manufacturing PMI"
            ).first()
            assert pmi is None

            # ISM Manufacturing PMI (both actual + forecast) → saved
            ism = session.query(EventHistoryDB).filter_by(
                title="ISM Manufacturing PMI"
            ).first()
            assert ism is not None
        finally:
            session.close()

    def test_duplicate_handling(self, sample_csv, temp_db, monkeypatch):
        engine, Session = temp_db
        monkeypatch.setattr(
            "historical_events_importer.SessionLocal",
            Session,
        )

        # import اول
        result1 = import_historical_csv(sample_csv)

        # هیچ خطایی نباید رخ دهد
        assert result1.failed == 0
        assert result1.invalid == 0

        # حداقل چند رکورد باید insert شده باشد
        assert result1.inserted > 0

        # import دوم — هیچ insert جدیدی نباید رخ دهد
        result2 = import_historical_csv(sample_csv)

        assert result2.inserted == 0
        assert result2.failed == 0
        assert result2.invalid == 0

        # کل ردیف‌های import دوم باید skip یا update شوند
        # (در این sample، چون داده‌ها تغییر نکرده، همه باید skip شوند)
        assert result2.updated == 0
        assert result2.skipped == result2.total_rows


class TestImportResult:
    def test_result_initialization(self):
        result = HistoricalImportResult(csv_path="/path/test.csv")
        assert result.total_rows == 0
        assert result.inserted == 0
        assert result.rejection_reasons == {}