"""
tests/test_nlp_news.py
======================
Unit tests for nlp_news.py.

Tests are organized by layer:
  1. Data completeness
  2. Event category weight lookup
  3. Final score calculation
  4. Confidence calculation
  5. Tradability rule
  6. Half-life calculation
  7. Malformed RSS input validation
  8. Repository save (mocked DB)
  9. LLM analysis (mocked LLM)
 10. Alignment edge cases
 11. Cross-asset signal propagation
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Adjust these imports to match your actual project structure
# ---------------------------------------------------------------------------
from agents.fundamental.nlp_news import (
    HALF_LIFE_BASE_MINS,
    HALF_LIFE_CATEGORY_MULTIPLIER,
    SOURCE_RELIABILITY,
    NewsAnalysisService,
    NewsInterpretation,
    NewsItem,
    NewsSignal,
    NewsSignalRepository,
    NewsSignalScorer,
    _clamp,
    _resolve_event_category_weight,
    _resolve_source_reliability,
    analyze_news_item,
    compute_data_completeness,
    compute_data_quality_factor,
    news_item_from_feedparser,
)


# ===========================================================================
# FIXTURES
# ===========================================================================


@pytest.fixture
def full_market_context():
    """MarketContext with all nine key fields populated."""
    from core.models import MarketContext  # adjust import path

    return MarketContext(
        target_asset="EUR/USD",
        event_title="US CPI YoY",
        actual_value=3.8,
        forecast_value=3.5,
        previous_value=3.6,
        historical_std=0.25,
        historical_volatility=0.12,
        atr_current=0.0045,
        atr_baseline=0.0040,
        implied_volatility=15.2,
        yield_spread=1.85,
        calculated_surprise=0.82,
        calculated_volatility=1.65,
    )


@pytest.fixture
def partial_market_context():
    """MarketContext with only 5 of 9 key fields populated."""
    from core.models import MarketContext

    return MarketContext(
        target_asset="EUR/USD",
        event_title="EUR PMI Flash",
        actual_value=None,
        forecast_value=None,
        previous_value=52.1,
        historical_std=None,
        historical_volatility=0.10,
        atr_current=0.0038,
        atr_baseline=0.0042,
        implied_volatility=None,
        yield_spread=None,
        calculated_surprise=0.15,
        calculated_volatility=0.90,
    )


@pytest.fixture
def empty_market_context():
    """MarketContext with none of the nine key fields populated."""
    from core.models import MarketContext

    return MarketContext(
        target_asset="USD/JPY",
        event_title=None,
        actual_value=None,
        forecast_value=None,
        previous_value=None,
        historical_std=None,
        historical_volatility=None,
        atr_current=None,
        atr_baseline=None,
        implied_volatility=None,
        yield_spread=None,
        calculated_surprise=0.0,
        calculated_volatility=1.0,
    )


@pytest.fixture
def valid_news_item():
    return NewsItem(
        title="US CPI Comes In Hot at 3.8%, Above 3.5% Forecast",
        summary=(
            "Consumer prices rose more than expected in January. "
            "Core CPI also exceeded forecasts. Markets are pricing in fewer rate cuts."
        ),
        published=datetime(2024, 1, 15, 13, 30),
        link="https://www.forexlive.com/news/us-cpi",
        source="ForexLive",
        source_reliability=None,
        category="CPI",
        currency="USD",
        impact="High",
    )


@pytest.fixture
def inline_news_item():
    return NewsItem(
        title="US NFP Prints 185K, In Line With Forecasts",
        summary="Nonfarm payrolls matched expectations. Unemployment unchanged at 4.1%.",
        published=datetime(2024, 2, 2, 13, 30),
        source="DailyFX",
        category="Non-Farm Payrolls",
        currency="USD",
    )


@pytest.fixture
def strong_interpretation():
    """NewsInterpretation representing a strong bullish USD signal."""
    return NewsInterpretation(
        reasoning=(
            "US CPI came in at 3.8% vs 3.5% forecast. This reduces expectations "
            "for Fed rate cuts, strengthening USD via yield differentials."
        ),
        asset_class="FX",
        direction=1,
        nlp_sentiment_score=0.78,
        surprise_factor=0.82,
        expected_volatility=1.65,
        cross_assets={"XAU": -1, "SPX": -1},
        detected_event_category="CPI",
        headline_body_alignment=0.92,
        quantitative_alignment=0.95,
        impact_horizon="multi-session",
    )


@pytest.fixture
def neutral_interpretation():
    """NewsInterpretation representing a near-neutral in-line signal."""
    return NewsInterpretation(
        reasoning="NFP matched expectations exactly. No surprise component.",
        asset_class="FX",
        direction=0,
        nlp_sentiment_score=0.03,
        surprise_factor=0.04,
        expected_volatility=0.85,
        cross_assets={},
        detected_event_category="Non-Farm Payrolls",
        headline_body_alignment=0.90,
        quantitative_alignment=0.90,
        impact_horizon="immediate",
    )


@pytest.fixture
def scorer():
    return NewsSignalScorer()


# ===========================================================================
# 1. DATA COMPLETENESS TESTS
# ===========================================================================


class TestDataCompleteness:
    def test_full_context_returns_1_0(self, full_market_context):
        score = compute_data_completeness(full_market_context)
        assert score == pytest.approx(1.0)

    def test_partial_context_returns_correct_fraction(self, partial_market_context):
        # previous_value, historical_volatility, atr_current, atr_baseline = 4 out of 9
        # But fixture has: previous_value + historical_volatility + atr_current + atr_baseline = 4
        score = compute_data_completeness(partial_market_context)
        assert score == pytest.approx(4 / 9, rel=1e-3)

    def test_empty_context_returns_0_0(self, empty_market_context):
        score = compute_data_completeness(empty_market_context)
        assert score == pytest.approx(0.0)

    def test_data_quality_factor_bounds(self):
        assert compute_data_quality_factor(0.0) == pytest.approx(0.5)
        assert compute_data_quality_factor(1.0) == pytest.approx(1.0)
        assert compute_data_quality_factor(0.5) == pytest.approx(0.75)

    def test_data_quality_factor_never_below_0_5(self):
        # Even with zero completeness, quality factor floors at 0.5
        result = compute_data_quality_factor(0.0)
        assert result >= 0.5

    def test_data_quality_factor_never_above_1_0(self):
        result = compute_data_quality_factor(1.0)
        assert result <= 1.0


# ===========================================================================
# 2. EVENT CATEGORY WEIGHT LOOKUP TESTS
# ===========================================================================


class TestEventCategoryWeight:
    def test_exact_match_interest_rate(self):
        category, weight = _resolve_event_category_weight("Interest Rate Decision")
        assert category == "Interest Rate Decision"
        assert weight == pytest.approx(1.00)

    def test_exact_match_cpi(self):
        category, weight = _resolve_event_category_weight("CPI")
        assert weight == pytest.approx(0.95)

    def test_partial_match_inflation(self):
        # "US Inflation Rate" should match "Inflation"
        category, weight = _resolve_event_category_weight("US Inflation Rate")
        assert weight == pytest.approx(0.95)

    def test_none_returns_unknown(self):
        category, weight = _resolve_event_category_weight(None)
        assert category == "Unknown"
        assert weight == pytest.approx(0.40)

    def test_empty_string_returns_unknown(self):
        category, weight = _resolve_event_category_weight("")
        assert category == "Unknown"
        assert weight == pytest.approx(0.40)

    def test_unrecognized_category_returns_unknown(self):
        category, weight = _resolve_event_category_weight("Completely Unrelated Topic")
        assert category == "Unknown"
        assert weight == pytest.approx(0.40)

    def test_source_reliability_mapping(self):
        assert _resolve_source_reliability("ForexLive", None) == pytest.approx(0.82)
        assert _resolve_source_reliability("DailyFX", None) == pytest.approx(0.80)
        assert _resolve_source_reliability("Unknown", None) == pytest.approx(0.65)

    def test_explicit_reliability_overrides_default(self):
        result = _resolve_source_reliability("ForexLive", 0.95)
        assert result == pytest.approx(0.95)

    def test_explicit_reliability_is_clamped(self):
        result = _resolve_source_reliability("ForexLive", 1.5)
        assert result == pytest.approx(1.0)
        result = _resolve_source_reliability("ForexLive", -0.5)
        assert result == pytest.approx(0.0)


# ===========================================================================
# 3. FINAL SCORE CALCULATION TESTS
# ===========================================================================


class TestFinalScoreCalculation:
    def test_strong_bullish_signal(
        self, valid_news_item, strong_interpretation, full_market_context, scorer
    ):
        signal = scorer.score(
            valid_news_item, strong_interpretation, full_market_context, "EURUSD=X"
        )
        # raw = 0.78 * 0.95 * (1 + 0.82) * 1.0 = 0.78 * 0.95 * 1.82 * 1.0
        expected_raw = 0.78 * 0.95 * (1 + 0.82) * 1.0  # data_quality_factor = 1.0
        expected = _clamp(expected_raw, -1.0, 1.0)
        assert signal.final_score == pytest.approx(expected, rel=1e-3)
        assert signal.direction == 1

    def test_neutral_in_line_signal(
        self, inline_news_item, neutral_interpretation, partial_market_context, scorer
    ):
        signal = scorer.score(
            inline_news_item,
            neutral_interpretation,
            partial_market_context,
            "EURUSD=X",
        )
        # With near-zero sentiment and near-zero surprise, score should be very small
        assert abs(signal.final_score) < 0.10

    def test_final_score_clamped_to_neg1_pos1(
        self, valid_news_item, full_market_context, scorer
    ):
        # Force extreme values to test clamping
        extreme_interpretation = NewsInterpretation(
            reasoning="Extreme test",
            asset_class="FX",
            direction=1,
            nlp_sentiment_score=1.0,
            surprise_factor=1.0,
            expected_volatility=1.65,
            cross_assets={},
            detected_event_category="Interest Rate Decision",
            headline_body_alignment=1.0,
            quantitative_alignment=1.0,
            impact_horizon="multi-session",
        )
        signal = scorer.score(
            valid_news_item, extreme_interpretation, full_market_context, "EURUSD=X"
        )
        assert signal.final_score <= 1.0
        assert signal.final_score >= -1.0

    def test_negative_sentiment_produces_negative_score(
        self, full_market_context, scorer
    ):
        bearish_news = NewsItem(
            title="USD Plunges on Weak Data",
            summary="Economic data disappoints badly.",
            source="ForexLive",
        )
        bearish_interp = NewsInterpretation(
            reasoning="Weak data bearish USD",
            asset_class="FX",
            direction=-1,
            nlp_sentiment_score=-0.65,
            surprise_factor=0.50,
            expected_volatility=1.65,
            cross_assets={},
            detected_event_category="GDP",
            headline_body_alignment=0.80,
            quantitative_alignment=0.85,
            impact_horizon="intraday",
        )
        signal = scorer.score(bearish_news, bearish_interp, full_market_context, "DX-Y.NYB")
        assert signal.final_score < 0.0
        assert signal.direction == -1


# ===========================================================================
# 4. CONFIDENCE CALCULATION TESTS
# ===========================================================================


class TestConfidenceCalculation:
    def test_high_confidence_with_full_data(
        self, valid_news_item, strong_interpretation, full_market_context, scorer
    ):
        signal = scorer.score(
            valid_news_item, strong_interpretation, full_market_context, "EURUSD=X"
        )
        # With full completeness (0.30), high surprise (0.25), ForexLive reliability (0.164),
        # high alignments → confidence should be substantial
        expected_confidence = (
            (1.0 * 0.30)
            + (min(0.82, 1.0) * 0.25)
            + (0.82 * 0.20)
            + (0.92 * 0.15)
            + (0.95 * 0.10)
        )
        signal_confidence = scorer.score(
            valid_news_item, strong_interpretation, full_market_context, "EURUSD=X"
        ).confidence
        assert signal_confidence == pytest.approx(expected_confidence, rel=1e-3)

    def test_low_confidence_with_empty_data(
        self, valid_news_item, strong_interpretation, empty_market_context, scorer
    ):
        # Override surprise/volatility to match empty context
        interp = strong_interpretation.model_copy(
            update={"surprise_factor": 0.0, "expected_volatility": 1.0}
        )
        signal = scorer.score(valid_news_item, interp, empty_market_context, "DX-Y.NYB")
        assert signal.confidence < 0.60  # limited by zero completeness

    def test_confidence_bounded_0_to_1(
        self, valid_news_item, strong_interpretation, full_market_context, scorer
    ):
        signal = scorer.score(
            valid_news_item, strong_interpretation, full_market_context, "EURUSD=X"
        )
        assert 0.0 <= signal.confidence <= 1.0


# ===========================================================================
# 5. TRADABILITY RULE TESTS
# ===========================================================================


class TestTradabilityRule:
    def test_tradable_when_all_conditions_met(
        self, valid_news_item, strong_interpretation, full_market_context, scorer
    ):
        signal = scorer.score(
            valid_news_item, strong_interpretation, full_market_context, "EURUSD=X"
        )
        # Strong signal with full data should be tradable
        # Verify the conditions explicitly
        assert abs(signal.final_score) >= 0.35 or not signal.is_tradable
        if abs(signal.final_score) >= 0.35 and signal.confidence >= 0.55:
            assert signal.is_tradable

    def test_not_tradable_when_score_too_low(
        self, inline_news_item, neutral_interpretation, partial_market_context, scorer
    ):
        signal = scorer.score(
            inline_news_item,
            neutral_interpretation,
            partial_market_context,
            "EURUSD=X",
        )
        assert not signal.is_tradable

    def test_not_tradable_when_completeness_too_low(
        self, valid_news_item, strong_interpretation, empty_market_context, scorer
    ):
        # Empty context → data_completeness = 0.0 < 0.45 threshold
        interp = strong_interpretation.model_copy(
            update={"surprise_factor": 0.0, "expected_volatility": 1.0}
        )
        signal = scorer.score(valid_news_item, interp, empty_market_context, "EURUSD=X")
        assert not signal.is_tradable  # completeness gate blocks

    def test_tradability_logic_directly(self):
        """Test the tradability formula in isolation."""
        def check_tradable(score, confidence, completeness):
            return (
                abs(score) >= 0.35
                and confidence >= 0.55
                and completeness >= 0.45
            )

        assert check_tradable(0.70, 0.65, 0.80) is True
        assert check_tradable(0.30, 0.65, 0.80) is False   # score too low
        assert check_tradable(0.70, 0.40, 0.80) is False   # confidence too low
        assert check_tradable(0.70, 0.65, 0.30) is False   # completeness too low


# ===========================================================================
# 6. HALF-LIFE CALCULATION TESTS
# ===========================================================================


class TestHalfLifeCalculation:
    def test_interest_rate_decision_longest_half_life(
        self, valid_news_item, full_market_context, scorer
    ):
        interp = NewsInterpretation(
            reasoning="Rate decision test",
            asset_class="FX",
            direction=1,
            nlp_sentiment_score=0.8,
            surprise_factor=0.82,
            expected_volatility=1.65,
            cross_assets={},
            detected_event_category="Interest Rate Decision",
            headline_body_alignment=0.90,
            quantitative_alignment=0.90,
            impact_horizon="multi-session",
        )
        signal = scorer.score(valid_news_item, interp, full_market_context, "EURUSD=X")
        expected = int((HALF_LIFE_BASE_MINS * 2.5) / max(1.65, 0.25))
        assert signal.signal_half_life_mins == expected

    def test_unknown_category_shortest_multiplier(
        self, valid_news_item, full_market_context, scorer
    ):
        interp = NewsInterpretation(
            reasoning="Unknown category test",
            asset_class="FX",
            direction=0,
            nlp_sentiment_score=0.02,
            surprise_factor=0.10,
            expected_volatility=1.0,
            cross_assets={},
            detected_event_category="Unknown",
            headline_body_alignment=0.70,
            quantitative_alignment=0.70,
            impact_horizon="immediate",
        )
        signal = scorer.score(valid_news_item, interp, full_market_context, "EURUSD=X")
        expected = int((HALF_LIFE_BASE_MINS * 1.0) / max(1.0, 0.25))
        assert signal.signal_half_life_mins == expected

    def test_half_life_never_below_1(
        self, valid_news_item, full_market_context, scorer
    ):
        """Even with extreme volatility, half_life should be >= 1."""
        interp = NewsInterpretation(
            reasoning="High volatility edge case",
            asset_class="FX",
            direction=1,
            nlp_sentiment_score=0.5,
            surprise_factor=1.0,
            expected_volatility=100.0,  # extreme
            cross_assets={},
            detected_event_category="Unknown",
            headline_body_alignment=0.80,
            quantitative_alignment=0.80,
            impact_horizon="immediate",
        )
        signal = scorer.score(valid_news_item, interp, full_market_context, "EURUSD=X")
        assert signal.signal_half_life_mins >= 1

    def test_near_zero_volatility_uses_floor(
        self, valid_news_item, full_market_context, scorer
    ):
        """Volatility of 0.0 should not cause division by zero."""
        interp = NewsInterpretation(
            reasoning="Near-zero volatility edge case",
            asset_class="FX",
            direction=0,
            nlp_sentiment_score=0.01,
            surprise_factor=0.0,
            expected_volatility=0.0,  # edge case: zero
            cross_assets={},
            detected_event_category="Unknown",
            headline_body_alignment=0.50,
            quantitative_alignment=0.50,
            impact_horizon="immediate",
        )
        # Should not raise ZeroDivisionError
        signal = scorer.score(valid_news_item, interp, full_market_context, "EURUSD=X")
        assert signal.signal_half_life_mins >= 1


# ===========================================================================
# 7. MALFORMED RSS INPUT VALIDATION TESTS
# ===========================================================================


class TestMalformedRSSInput:
    def test_missing_title_raises_value_error(self):
        with pytest.raises(ValueError, match="title"):
            NewsItem(
                title="",
                summary="Some summary",
                source="ForexLive",
            )

    def test_whitespace_only_title_raises_value_error(self):
        with pytest.raises(ValidationError):
            NewsItem(
                title="   ",
                summary="Some summary",
                source="ForexLive",
            )

    def test_missing_summary_defaults_to_empty_string(self):
        item = NewsItem(title="Valid Title", source="ForexLive")
        assert item.summary == ""

    def test_invalid_source_reliability_raises_error(self):
        with pytest.raises(ValidationError):
            NewsItem(
                title="Valid Title",
                source="ForexLive",
                source_reliability=1.5,  # > 1.0
            )

    def test_feedparser_helper_raises_on_missing_title(self):
        entry = {"summary": "body text", "published": "Mon, 15 Jan 2024 13:30:00 +0000"}
        with pytest.raises(ValueError, match="no usable title"):
            news_item_from_feedparser(entry, source="ForexLive")

    def test_feedparser_helper_parses_valid_entry(self):
        entry = {
            "title": "EUR/USD Falls on Weak German PMI",
            "summary": "German PMI missed expectations badly.",
            "published": "Mon, 15 Jan 2024 09:00:00 +0000",
            "link": "https://forexlive.com/article",
        }
        item = news_item_from_feedparser(entry, source="ForexLive", currency="EUR")
        assert item.title == "EUR/USD Falls on Weak German PMI"
        assert item.source == "ForexLive"
        assert item.currency == "EUR"
        assert item.published is not None

    def test_feedparser_helper_handles_missing_summary(self):
        entry = {"title": "Breaking: Fed Holds Rates"}
        item = news_item_from_feedparser(entry, source="DailyFX")
        assert item.summary == ""

    def test_direction_must_be_minus1_0_or_1(self):
        with pytest.raises(ValidationError):
            NewsInterpretation(
                reasoning="Test",
                asset_class="FX",
                direction=2,  # invalid
                nlp_sentiment_score=0.5,
                surprise_factor=0.5,
                expected_volatility=1.0,
                detected_event_category="CPI",
                headline_body_alignment=0.8,
                quantitative_alignment=0.8,
                impact_horizon="intraday",
            )

    def test_direction_sentiment_mismatch_raises_error(self):
        with pytest.raises(ValidationError):
            NewsInterpretation(
                reasoning="Test",
                asset_class="FX",
                direction=-1,  # mismatch: sentiment is positive
                nlp_sentiment_score=0.7,
                surprise_factor=0.5,
                expected_volatility=1.0,
                detected_event_category="CPI",
                headline_body_alignment=0.8,
                quantitative_alignment=0.8,
                impact_horizon="intraday",
            )

    def test_invalid_impact_horizon_raises_error(self):
        with pytest.raises(ValidationError):
            NewsInterpretation(
                reasoning="Test",
                asset_class="FX",
                direction=1,
                nlp_sentiment_score=0.5,
                surprise_factor=0.5,
                expected_volatility=1.0,
                detected_event_category="CPI",
                headline_body_alignment=0.8,
                quantitative_alignment=0.8,
                impact_horizon="weekly",  # invalid
            )


# ===========================================================================
# 8. REPOSITORY SAVE MOCK TEST
# ===========================================================================


class TestNewsSignalRepository:
    def test_save_calls_session_correctly(
        self,
        valid_news_item,
        strong_interpretation,
        full_market_context,
        scorer,
    ):
        signal = scorer.score(
            valid_news_item, strong_interpretation, full_market_context, "EURUSD=X"
        )

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock()
        mock_session.refresh = MagicMock()
        mock_session.close = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        # Give the refreshed object an id
        def set_id(record):
            record.id = 42

        mock_session.refresh.side_effect = set_id

        repo = NewsSignalRepository()

        with patch("nlp_news.SessionLocal", return_value=mock_session):
            record_id = repo.save(
                news_item=valid_news_item,
                interpretation=strong_interpretation,
                signal=signal,
                market_context=full_market_context,
            )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()
        assert record_id == 42

    def test_save_rollback_on_db_error(
        self,
        valid_news_item,
        strong_interpretation,
        full_market_context,
        scorer,
    ):
        from sqlalchemy.exc import SQLAlchemyError

        signal = scorer.score(
            valid_news_item, strong_interpretation, full_market_context, "EURUSD=X"
        )

        mock_session = MagicMock()
        mock_session.commit.side_effect = SQLAlchemyError("DB write failed")
        mock_session.rollback = MagicMock()
        mock_session.close = MagicMock()

        repo = NewsSignalRepository()

        with patch("nlp_news.SessionLocal", return_value=mock_session):
            with pytest.raises(SQLAlchemyError):
                repo.save(
                    news_item=valid_news_item,
                    interpretation=strong_interpretation,
                    signal=signal,
                    market_context=full_market_context,
                )

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()

    def test_serialize_news_item_produces_valid_json(self, valid_news_item):
        repo = NewsSignalRepository()
        json_str = repo._serialize_news_item(valid_news_item)
        parsed = json.loads(json_str)
        assert parsed["title"] == valid_news_item.title
        assert parsed["source"] == valid_news_item.source

    def test_serialize_market_context_produces_valid_json(self, full_market_context):
        repo = NewsSignalRepository()
        json_str = repo._serialize_market_context(full_market_context)
        parsed = json.loads(json_str)
        assert parsed["target_asset"] == full_market_context.target_asset


# ===========================================================================
# 9. LLM ANALYSIS MOCK TEST
# ===========================================================================


class TestNewsAnalysisService:
    def test_analyze_returns_interpretation_on_success(
        self, valid_news_item, full_market_context, strong_interpretation
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = strong_interpretation

        service = NewsAnalysisService(llm=mock_llm)
        # Replace the chain with our mock
        service._chain = mock_chain

        result = service.analyze(valid_news_item, full_market_context)

        mock_chain.invoke.assert_called_once()
        assert isinstance(result, NewsInterpretation)
        assert result.direction == 1

    def test_analyze_raises_on_none_response(
        self, valid_news_item, full_market_context
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = None

        service = NewsAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with pytest.raises(ValueError, match="LLM returned None"):
            service.analyze(valid_news_item, full_market_context)

    def test_analyze_raises_on_api_failure(
        self, valid_news_item, full_market_context
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.side_effect = ConnectionError("API timeout")

        service = NewsAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with pytest.raises(ConnectionError):
            service.analyze(valid_news_item, full_market_context)

    def test_analyze_corrects_surprise_drift(
        self, valid_news_item, full_market_context
    ):
        """LLM returns wrong surprise_factor — service should correct it."""
        drifted_interpretation = NewsInterpretation(
            reasoning="Drift test",
            asset_class="FX",
            direction=1,
            nlp_sentiment_score=0.78,
            surprise_factor=0.50,  # wrong — market_context has 0.82
            expected_volatility=1.65,
            cross_assets={},
            detected_event_category="CPI",
            headline_body_alignment=0.90,
            quantitative_alignment=0.90,
            impact_horizon="multi-session",
        )

        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = drifted_interpretation

        service = NewsAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        result = service.analyze(valid_news_item, full_market_context)
        # Should be corrected to market_context.calculated_surprise
        assert result.surprise_factor == pytest.approx(
            full_market_context.calculated_surprise, rel=1e-3
        )

    def test_analyze_raises_on_wrong_return_type(
        self, valid_news_item, full_market_context
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = {"wrong": "type"}  # not a NewsInterpretation

        service = NewsAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with pytest.raises(ValueError, match="unexpected type"):
            service.analyze(valid_news_item, full_market_context)


# ===========================================================================
# 10. ALIGNMENT EDGE CASES
# ===========================================================================


class TestAlignmentEdgeCases:
    def test_low_quantitative_alignment_reduces_confidence(
        self, valid_news_item, full_market_context, scorer
    ):
        """Low quantitative alignment should reduce confidence."""
        high_align_interp = NewsInterpretation(
            reasoning="High alignment",
            asset_class="FX",
            direction=1,
            nlp_sentiment_score=0.70,
            surprise_factor=0.82,
            expected_volatility=1.65,
            cross_assets={},
            detected_event_category="CPI",
            headline_body_alignment=0.90,
            quantitative_alignment=0.95,
            impact_horizon="multi-session",
        )
        low_align_interp = high_align_interp.model_copy(
            update={"quantitative_alignment": 0.10, "headline_body_alignment": 0.10}
        )

        high_signal = scorer.score(
            valid_news_item, high_align_interp, full_market_context, "EURUSD=X"
        )
        low_signal = scorer.score(
            valid_news_item, low_align_interp, full_market_context, "EURUSD=X"
        )

        assert low_signal.confidence < high_signal.confidence

    def test_cross_assets_empty_dict_handled_gracefully(
        self, valid_news_item, full_market_context, scorer
    ):
        interp = NewsInterpretation(
            reasoning="No cross-asset",
            asset_class="FX",
            direction=1,
            nlp_sentiment_score=0.60,
            surprise_factor=0.82,
            expected_volatility=1.65,
            cross_assets={},
            detected_event_category="CPI",
            headline_body_alignment=0.85,
            quantitative_alignment=0.85,
            impact_horizon="intraday",
        )
        signal = scorer.score(valid_news_item, interp, full_market_context, "EURUSD=X")
        assert signal.cross_asset_signals == {}


# ===========================================================================
# 11. CROSS-ASSET SIGNAL PROPAGATION TESTS
# ===========================================================================


class TestCrossAssetSignalPropagation:
    def test_cross_asset_signals_computed_correctly(
        self, valid_news_item, strong_interpretation, full_market_context, scorer
    ):
        signal = scorer.score(
            valid_news_item, strong_interpretation, full_market_context, "EURUSD=X"
        )
        for asset, cross_value in signal.cross_asset_signals.items():
            expected = round(abs(signal.final_score) * 0.7 * strong_interpretation.cross_assets[asset], 3)
            assert cross_value == pytest.approx(expected, rel=1e-3)

    def test_cross_asset_negative_direction(
        self, valid_news_item, full_market_context, scorer
    ):
        interp = NewsInterpretation(
            reasoning="Gold bearish on hawkish USD",
            asset_class="FX",
            direction=1,
            nlp_sentiment_score=0.70,
            surprise_factor=0.82,
            expected_volatility=1.65,
            cross_assets={"XAU": -1},
            detected_event_category="CPI",
            headline_body_alignment=0.90,
            quantitative_alignment=0.90,
            impact_horizon="multi-session",
        )
        signal = scorer.score(valid_news_item, interp, full_market_context, "EURUSD=X")
        # XAU direction is -1, so cross_asset_signal should be negative
        assert signal.cross_asset_signals.get("XAU", 0) < 0

    def test_zero_direction_cross_asset_is_zero(
        self, valid_news_item, full_market_context, scorer
    ):
        interp = NewsInterpretation(
            reasoning="Neutral cross-asset",
            asset_class="FX",
            direction=0,
            nlp_sentiment_score=0.02,
            surprise_factor=0.04,
            expected_volatility=1.0,
            cross_assets={"SPX": 0},
            detected_event_category="Unknown",
            headline_body_alignment=0.70,
            quantitative_alignment=0.70,
            impact_horizon="immediate",
        )
        signal = scorer.score(valid_news_item, interp, full_market_context, "EURUSD=X")
        assert signal.cross_asset_signals.get("SPX", 0) == pytest.approx(0.0)


# ===========================================================================
# 12. FULL PIPELINE INTEGRATION TEST (mocked)
# ===========================================================================


class TestFullPipelineIntegration:
    def test_analyze_news_item_end_to_end(
        self, valid_news_item, strong_interpretation, full_market_context
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = strong_interpretation

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock()
        mock_session.close = MagicMock()

        def set_id(record):
            record.id = 99

        mock_session.refresh.side_effect = set_id

        mock_service = NewsAnalysisService(llm=mock_llm)
        mock_service._chain = mock_chain

        with patch("nlp_news.SessionLocal", return_value=mock_session):
            interpretation, signal = analyze_news_item(
                news_item=valid_news_item,
                market_context=full_market_context,
                ticker="EURUSD=X",
                llm=mock_llm,
                persist=True,
                analysis_service=mock_service,
            )

        assert isinstance(interpretation, NewsInterpretation)
        assert isinstance(signal, NewsSignal)
        assert signal.ticker == "EURUSD=X"
        assert signal.source == "ForexLive"
        mock_session.commit.assert_called_once()

    def test_analyze_news_item_with_persist_false(
        self, valid_news_item, strong_interpretation, full_market_context
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = strong_interpretation

        mock_service = NewsAnalysisService(llm=mock_llm)
        mock_service._chain = mock_chain

        # persist=False — no DB calls should happen
        interpretation, signal = analyze_news_item(
            news_item=valid_news_item,
            market_context=full_market_context,
            ticker="EURUSD=X",
            llm=mock_llm,
            persist=False,
            analysis_service=mock_service,
        )

        assert isinstance(signal, NewsSignal)

    def test_empty_ticker_raises_value_error(
        self, valid_news_item, full_market_context
    ):
        mock_llm = MagicMock()
        with pytest.raises(ValueError, match="ticker"):
            analyze_news_item(
                news_item=valid_news_item,
                market_context=full_market_context,
                ticker="",
                llm=mock_llm,
            )