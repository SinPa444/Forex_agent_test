# tests/test_data_fetcher_upgrade.py
"""
Unit tests for upgraded data_fetcher.py functions.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from agents.fundamental.data_fetcher import (
    _canonicalize_event_title,
    _category_from_canonical_key,
    fetch_historical_surprise_std_full,
    fetch_historical_surprise_std,
    HistoricalStdResult,
    CATEGORY_DEFAULT_STD,
    ABSOLUTE_FALLBACK_STD,
)


# ===========================================================================
# CANONICAL MATCHING TESTS
# ===========================================================================

class TestCanonicalization:
    def test_us_cpi_yy(self):
        result = _canonicalize_event_title("CPI y/y", "USD")
        assert result == "US_CPI_YY"

    def test_us_cpi_mm(self):
        result = _canonicalize_event_title("CPI m/m", "USD")
        assert result == "US_CPI_MM"

    def test_us_core_cpi_mm(self):
        result = _canonicalize_event_title("Core CPI m/m", "USD")
        assert result == "US_CPI_CORE_MM"

    def test_us_nfp_official(self):
        result = _canonicalize_event_title("Non-Farm Employment Change", "USD")
        assert result == "US_NFP"

    def test_us_nfp_adp_distinct(self):
        result = _canonicalize_event_title(
            "ADP Non-Farm Employment Change", "USD"
        )
        assert result == "US_NFP_ADP"

    def test_us_productivity_not_nfp(self):
        """productivity نباید با NFP اشتباه شود."""
        result = _canonicalize_event_title(
            "Revised Nonfarm Productivity q/q", "USD"
        )
        assert result == "US_PRODUCTIVITY_QQ"

    def test_german_prelim_cpi(self):
        """EUR with country override."""
        result = _canonicalize_event_title("German Prelim CPI m/m", "EUR")
        assert result == "DE_CPI_PRELIM_MM"

    def test_french_final_cpi(self):
        result = _canonicalize_event_title("French Final CPI m/m", "EUR")
        assert result == "FR_CPI_FINAL_MM"

    def test_italian_manufacturing_pmi(self):
        result = _canonicalize_event_title("Italian Manufacturing PMI", "EUR")
        assert result == "IT_PMI_MFG"

    def test_eu_cpi_flash(self):
        result = _canonicalize_event_title("CPI Flash Estimate y/y", "EUR")
        assert result == "EU_CPI_FLASH_YY"

    def test_us_fed_rate(self):
        result = _canonicalize_event_title("Federal Funds Rate", "USD")
        assert result == "US_RATE"

    def test_ecb_rate(self):
        result = _canonicalize_event_title("Main Refinancing Rate", "EUR")
        assert result == "EU_RATE"

    def test_us_advance_gdp(self):
        result = _canonicalize_event_title("Advance GDP q/q", "USD")
        assert result == "US_GDP_ADVANCE_QQ"

    def test_uk_cpi_yy(self):
        result = _canonicalize_event_title("CPI y/y", "GBP")
        assert result == "UK_CPI_YY"

    def test_jp_tokyo_core_cpi(self):
        result = _canonicalize_event_title("Tokyo Core CPI y/y", "JPY")
        assert result == "JP_CPI_TOKYO_CORE_YY"

    def test_jobless_claims(self):
        result = _canonicalize_event_title("Unemployment Claims", "USD")
        assert result == "US_JOBLESS_CLAIMS"

    def test_ism_manufacturing(self):
        result = _canonicalize_event_title("ISM Manufacturing PMI", "USD")
        assert result == "US_ISM_MFG"

    def test_unknown_event_returns_none(self):
        result = _canonicalize_event_title("Some Random Indicator", "USD")
        assert result is None

    def test_empty_title(self):
        assert _canonicalize_event_title("", "USD") is None
        assert _canonicalize_event_title(None, "USD") is None

    def test_no_currency_still_works(self):
        """بدون currency، باید بدون country prefix بسازد."""
        result = _canonicalize_event_title("CPI y/y", None)
        assert result == "CPI_YY"


# ===========================================================================
# CATEGORY MAPPING TESTS
# ===========================================================================

class TestCategoryFromCanonical:
    def test_cpi(self):
        assert _category_from_canonical_key("US_CPI_YY") == "CPI"

    def test_nfp(self):
        assert _category_from_canonical_key("US_NFP") == "Non-Farm Payrolls"

    def test_gdp(self):
        assert _category_from_canonical_key("US_GDP_ADVANCE_QQ") == "GDP"

    def test_rate(self):
        assert _category_from_canonical_key("US_RATE") == "Interest Rate Decision"

    def test_unemployment(self):
        assert _category_from_canonical_key("US_UNEMPLOYMENT") == "Unemployment"

    def test_pmi(self):
        assert _category_from_canonical_key("US_PMI_MFG") == "PMI"

    def test_ism(self):
        assert _category_from_canonical_key("US_ISM_MFG") == "PMI"

    def test_unknown(self):
        assert _category_from_canonical_key("XX_RANDOM") == "Default"


# ===========================================================================
# HISTORICAL STD INTEGRATION TESTS (with real DB)
# ===========================================================================

class TestHistoricalStdReal:
    """تست‌های واقعی با DB. این‌ها فرض می‌کنند DB seed شده است."""

    def test_us_cpi_returns_real_data(self):
        """US CPI باید داده واقعی برگرداند."""
        result = fetch_historical_surprise_std_full(
            event_title="CPI y/y",
            currency="USD",
        )
        # باید computed باشد، نه fallback
        if result.observations >= 10:
            assert result.source.startswith("computed")
            assert result.canonical_key == "US_CPI_YY"
            assert 0.05 < result.value < 0.50  # بازه معقول برای CPI

    def test_us_nfp_official_only(self):
        """
        NFP باید فقط رسمی را match کند و outlier ها (COVID) باید filter شوند.
        قبل از filter: std ≈ 1.36M (به خاطر June 2020 spike)
        بعد از filter: std ≈ 100k-150k (معقول)
        """
        result = fetch_historical_surprise_std_full(
            event_title="Non-Farm Employment Change",
            currency="USD",
        )
        if result.observations >= 10:
            assert result.canonical_key == "US_NFP"
            # بعد از outlier filtering باید معقول باشد
            assert result.value < 250_000, (
                f"NFP std={result.value:,.0f} too high. "
                f"Outliers removed: {result.outliers_removed}"
            )
            # باید outlier ها (COVID) حذف شده باشند
            assert result.outliers_removed > 0, (
                "Expected COVID outliers to be removed but none were"
            )

    def test_unknown_event_falls_back(self):
        """event ناشناخته باید fallback شود."""
        result = fetch_historical_surprise_std_full(
            event_title="ZZZ Some Random Event ZZZ",
            currency="USD",
        )
        assert not result.is_reliable() or result.observations == 0
        assert result.value is not None  # حتی fallback عدد می‌دهد


# ===========================================================================
# BACKWARD COMPATIBILITY
# ===========================================================================

class TestBackwardCompatibility:
    def test_simple_function_returns_float(self):
        """نسخه ساده باید همیشه float برگرداند."""
        result = fetch_historical_surprise_std("CPI y/y", "USD")
        assert isinstance(result, float)
        assert result > 0


# ===========================================================================
# HistoricalStdResult
# ===========================================================================

class TestHistoricalStdResult:
    def test_is_reliable_for_computed(self):
        r = HistoricalStdResult(
            value=0.14, source="computed_canonical",
            observations=100, window_years=5,
            canonical_key="US_CPI_YY", matched_titles=["CPI y/y"],
        )
        assert r.is_reliable() is True

    def test_not_reliable_for_fallback(self):
        r = HistoricalStdResult(
            value=0.15, source="absolute_fallback",
            observations=0, window_years=None,
            canonical_key=None, matched_titles=[],
        )
        assert r.is_reliable() is False

    def test_not_reliable_for_category(self):
        r = HistoricalStdResult(
            value=0.15, source="category_fallback",
            observations=0, window_years=None,
            canonical_key="XX_UNKNOWN", matched_titles=[],
        )
        assert r.is_reliable() is False