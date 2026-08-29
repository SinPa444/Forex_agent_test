# tests/test_nlp_x.py
"""
تست‌های واحد برای nlp_x.py.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

from core.models import (
    MarketContext, Speaker, SpeakerTextItem,
    SpeakerInterpretation, SpeakerSignal,
    EventInterpretation, Signal,
)
from agents.fundamental.nlp_x import (
    SOURCE_RELIABILITY,
    STATEMENT_TYPE_WEIGHT,
    SOURCE_TYPE_BASE_MINS,
    SpeakerAnalysisService,
    SpeakerSignalScorer,
    SpeakerSignalRepository,
    analyze_speaker_text,
    generate_signal,
    compute_data_completeness,
    compute_data_quality_factor,
    compute_alignment_factor,
    _clamp,
    _resolve_source_reliability,
    _resolve_statement_type_weight,
    _resolve_source_type_base_mins,
    _resolve_speaker,
)




# ===========================================================================
# FIXTURES
# ===========================================================================


@pytest.fixture
def full_market_context():
    return MarketContext(
        target_asset="EURUSD=X",
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
def empty_market_context():
    return MarketContext(
        target_asset="USD",
        calculated_surprise=0.0,
        calculated_volatility=1.0,
    )


@pytest.fixture
def powell_item():
    return SpeakerTextItem(
        text="Inflation remains stubbornly above our target. We will not hesitate to raise rates further.",
        speaker_name="Jerome Powell",
        published=datetime(2024, 1, 15, 14, 0),
        source="Bloomberg",
        source_type="press_conference",
        link="https://bloomberg.com/powell",
    )


@pytest.fixture
def generic_tweet_item():
    return SpeakerTextItem(
        text="We continue to monitor the data carefully.",
        speaker_name="Fed Governor Waller",
        source="X",
        source_type="tweet",
        retweet_count=50,
        like_count=200,
    )


@pytest.fixture
def hawkish_interpretation():
    return SpeakerInterpretation(
        reasoning="Powell signals willingness to hike. Hot CPI data supports hawkish stance.",
        asset_class="USD",
        direction=1,
        nlp_sentiment_score=0.82,
        surprise_factor=0.82,
        expected_volatility=1.65,
        cross_assets={"XAU": -1, "SPX": -1},
        statement_market_alignment=0.95,
        quantitative_alignment=0.95,
        policy_signal_type="policy_commitment",
        impact_horizon="multi-session",
        detected_event_category="Inflation",
        is_reiteration=False,
        guidance_bias="hawkish",
    )


@pytest.fixture
def neutral_interpretation():
    return SpeakerInterpretation(
        reasoning="Generic commentary, no new information.",
        asset_class="USD",
        direction=0,
        nlp_sentiment_score=0.02,
        surprise_factor=0.0,
        expected_volatility=1.0,
        cross_assets={},
        statement_market_alignment=0.70,
        quantitative_alignment=0.70,
        policy_signal_type="commentary",
        impact_horizon="immediate",
        detected_event_category="Unknown",
        is_reiteration=True,
        guidance_bias="neutral",
    )


@pytest.fixture
def scorer():
    return SpeakerSignalScorer()


@pytest.fixture
def powell_speaker():
    return Speaker(
        name="Jerome Powell",
        role="Fed Chair",
        primarily_impacts="USD",
        weight=1.0,
    )


@pytest.fixture
def generic_speaker():
    return Speaker(
        name="Fed Governor Waller",
        role="Fed Governor",
        primarily_impacts="USD",
        weight=0.7,
    )


# ===========================================================================
# DATA COMPLETENESS
# ===========================================================================


class TestDataCompleteness:
    def test_full_context(self, full_market_context):
        assert compute_data_completeness(full_market_context) == pytest.approx(1.0)

    def test_empty_context(self, empty_market_context):
        assert compute_data_completeness(empty_market_context) == pytest.approx(0.0)

    def test_quality_factor_full(self):
        assert compute_data_quality_factor(1.0) == pytest.approx(1.0)

    def test_quality_factor_empty(self):
        assert compute_data_quality_factor(0.0) == pytest.approx(0.60)

    def test_quality_factor_half(self):
        assert compute_data_quality_factor(0.5) == pytest.approx(0.80)

    def test_alignment_factor_full(self):
        assert compute_alignment_factor(1.0, 1.0) == pytest.approx(1.0)

    def test_alignment_factor_zero(self):
        assert compute_alignment_factor(0.0, 0.0) == pytest.approx(0.50)

    def test_alignment_factor_mixed(self):
        assert compute_alignment_factor(0.8, 0.6) == pytest.approx(
            0.50 + 0.25 * 0.8 + 0.25 * 0.6
        )


# ===========================================================================
# STATEMENT TYPE WEIGHT
# ===========================================================================


class TestStatementTypeWeight:
    def test_testimony_highest(self):
        assert _resolve_statement_type_weight("testimony") == 1.15

    def test_tweet_lower(self):
        assert _resolve_statement_type_weight("tweet") == 0.90

    def test_unknown_fallback(self):
        assert _resolve_statement_type_weight("something_random") == 0.80

    def test_case_insensitive(self):
        assert _resolve_statement_type_weight("SPEECH") == 1.10


# ===========================================================================
# SOURCE RELIABILITY
# ===========================================================================


class TestSourceReliability:
    def test_bloomberg(self):
        assert _resolve_source_reliability("Bloomberg") == 0.92

    def test_x(self):
        assert _resolve_source_reliability("X") == 0.72

    def test_unknown(self):
        assert _resolve_source_reliability("Random Blog") == 0.60


# ===========================================================================
# FINAL SCORE
# ===========================================================================


class TestFinalScore:
    def test_strong_hawkish_signal(
        self, powell_item, powell_speaker, hawkish_interpretation,
        full_market_context, scorer
    ):
        signal = scorer.score(
            powell_item, powell_speaker, hawkish_interpretation,
            full_market_context, "EURUSD=X",
        )
        # sentiment(0.82) * weight(1.0) * stmt_type(1.1) * (1+0.82)
        # * quality(1.0) * alignment(0.5+0.25*0.95+0.25*0.95)
        assert signal.final_score > 0.5
        assert signal.direction == 1

    def test_neutral_low_score(
        self, generic_tweet_item, generic_speaker,
        neutral_interpretation, empty_market_context, scorer
    ):
        signal = scorer.score(
            generic_tweet_item, generic_speaker,
            neutral_interpretation, empty_market_context, "DX-Y.NYB",
        )
        assert abs(signal.final_score) < 0.10

    def test_score_clamped(
        self, powell_item, powell_speaker, full_market_context, scorer
    ):
        extreme = SpeakerInterpretation(
            reasoning="Extreme test",
            asset_class="USD", direction=1,
            nlp_sentiment_score=1.0, surprise_factor=1.0,
            expected_volatility=1.65, cross_assets={},
            statement_market_alignment=1.0, quantitative_alignment=1.0,
            policy_signal_type="policy_commitment",
            impact_horizon="multi-session",
            detected_event_category="CPI",
            is_reiteration=False, guidance_bias="hawkish",
        )
        signal = scorer.score(
            powell_item, powell_speaker, extreme,
            full_market_context, "EURUSD=X",
        )
        assert -1.0 <= signal.final_score <= 1.0

    def test_negative_direction(self, full_market_context, scorer):
        item = SpeakerTextItem(
            text="Economy is weakening significantly.",
            speaker_name="Test Speaker",
            source="Reuters",
            source_type="interview",
        )
        speaker = Speaker(name="Test", role="Analyst",
                          primarily_impacts="USD", weight=0.8)
        interp = SpeakerInterpretation(
            reasoning="Weak economy → dovish",
            asset_class="USD", direction=-1,
            nlp_sentiment_score=-0.65, surprise_factor=0.50,
            expected_volatility=1.65, cross_assets={},
            statement_market_alignment=0.80,
            quantitative_alignment=0.85,
            policy_signal_type="data_reaction",
            impact_horizon="intraday",
            detected_event_category="GDP",
            is_reiteration=False, guidance_bias="dovish",
        )
        signal = scorer.score(item, speaker, interp,
                              full_market_context, "DX-Y.NYB")
        assert signal.final_score < 0.0
        assert signal.direction == -1


# ===========================================================================
# CONFIDENCE
# ===========================================================================


class TestConfidence:
    def test_high_confidence_powell(
        self, powell_item, powell_speaker, hawkish_interpretation,
        full_market_context, scorer
    ):
        signal = scorer.score(
            powell_item, powell_speaker, hawkish_interpretation,
            full_market_context, "EURUSD=X",
        )
        expected = (
            (1.0 * 0.30) + (0.82 * 0.20) + (1.0 * 0.15)
            + (0.95 * 0.15) + (0.95 * 0.10)
            + (0.92 * 0.05) + (0.82 * 0.05)
        )
        assert signal.confidence == pytest.approx(expected, rel=1e-2)

    def test_penalty_high_surprise_low_sentiment(
        self, full_market_context, scorer
    ):
        item = SpeakerTextItem(
            text="We note the data.", speaker_name="Test",
            source="X", source_type="tweet",
        )
        speaker = Speaker(name="Test", weight=0.5, role="Analyst",
                          primarily_impacts="USD")
        interp = SpeakerInterpretation(
            reasoning="Vague", asset_class="USD", direction=0,
            nlp_sentiment_score=0.02, surprise_factor=0.9,
            expected_volatility=1.0, cross_assets={},
            statement_market_alignment=0.3,
            quantitative_alignment=0.5,
            policy_signal_type="commentary",
            impact_horizon="immediate",
            detected_event_category="Unknown",
            is_reiteration=True, guidance_bias="neutral",
        )
        signal = scorer.score(item, speaker, interp,
                              full_market_context, "DX-Y.NYB")
        # Should have both penalties applied
        assert signal.confidence < 0.50

    def test_confidence_bounded(
        self, powell_item, powell_speaker, hawkish_interpretation,
        full_market_context, scorer
    ):
        signal = scorer.score(
            powell_item, powell_speaker, hawkish_interpretation,
            full_market_context, "EURUSD=X",
        )
        assert 0.0 <= signal.confidence <= 1.0


# ===========================================================================
# TRADABILITY
# ===========================================================================


class TestTradability:
    def test_tradable_strong_signal(
        self, powell_item, powell_speaker, hawkish_interpretation,
        full_market_context, scorer
    ):
        signal = scorer.score(
            powell_item, powell_speaker, hawkish_interpretation,
            full_market_context, "EURUSD=X",
        )
        if abs(signal.final_score) >= 0.40 and signal.confidence >= 0.60:
            assert signal.is_tradable

    def test_not_tradable_neutral(
        self, generic_tweet_item, generic_speaker,
        neutral_interpretation, empty_market_context, scorer
    ):
        signal = scorer.score(
            generic_tweet_item, generic_speaker,
            neutral_interpretation, empty_market_context, "DX-Y.NYB",
        )
        assert not signal.is_tradable

    def test_tradability_logic(self):
        assert (abs(0.50) >= 0.40 and 0.70 >= 0.60 and 0.50 >= 0.40) is True
        assert (abs(0.30) >= 0.40) is False
        assert (0.50 >= 0.60) is False


# ===========================================================================
# HALF-LIFE
# ===========================================================================


class TestHalfLife:
    def test_tweet_shorter_than_testimony(
        self, full_market_context, scorer
    ):
        speaker = Speaker(name="T", role="R", primarily_impacts="USD", weight=0.8)
        interp = SpeakerInterpretation(
            reasoning="T", asset_class="USD", direction=0,
            nlp_sentiment_score=0.02, surprise_factor=0.0,
            expected_volatility=1.0, cross_assets={},
            statement_market_alignment=0.7, quantitative_alignment=0.7,
            policy_signal_type="commentary", impact_horizon="immediate",
            detected_event_category="Unknown",
            is_reiteration=True, guidance_bias="neutral",
        )
        tweet_item = SpeakerTextItem(
            text="Test", speaker_name="T", source="X", source_type="tweet",
        )
        testimony_item = SpeakerTextItem(
            text="Test", speaker_name="T",
            source="Official Transcript", source_type="testimony",
        )
        tweet_sig = scorer.score(tweet_item, speaker, interp,
                                 full_market_context, "DX-Y.NYB")
        testimony_sig = scorer.score(testimony_item, speaker, interp,
                                     full_market_context, "DX-Y.NYB")
        assert tweet_sig.signal_half_life_mins < testimony_sig.signal_half_life_mins

    def test_half_life_minimum_1(self, full_market_context, scorer):
        speaker = Speaker(name="T", role="R", primarily_impacts="USD", weight=0.5)
        interp = SpeakerInterpretation(
            reasoning="T", asset_class="USD", direction=0,
            nlp_sentiment_score=0.01, surprise_factor=0.0,
            expected_volatility=100.0, cross_assets={},
            statement_market_alignment=0.5, quantitative_alignment=0.5,
            policy_signal_type="commentary", impact_horizon="immediate",
            detected_event_category="Unknown",
            is_reiteration=True, guidance_bias="neutral",
        )
        item = SpeakerTextItem(
            text="T", speaker_name="T", source="X", source_type="tweet",
        )
        signal = scorer.score(item, speaker, interp,
                              full_market_context, "DX-Y.NYB")
        assert signal.signal_half_life_mins >= 1

    def test_higher_weight_increases_half_life(
        self, full_market_context, scorer
    ):
        interp = SpeakerInterpretation(
            reasoning="T", asset_class="USD", direction=0,
            nlp_sentiment_score=0.02, surprise_factor=0.0,
            expected_volatility=1.0, cross_assets={},
            statement_market_alignment=0.7, quantitative_alignment=0.7,
            policy_signal_type="commentary", impact_horizon="immediate",
            detected_event_category="Unknown",
            is_reiteration=True, guidance_bias="neutral",
        )
        item = SpeakerTextItem(
            text="T", speaker_name="T", source="X", source_type="statement",
        )
        low_weight = Speaker(name="T", role="R", primarily_impacts="USD", weight=0.3)
        high_weight = Speaker(name="T", role="R", primarily_impacts="USD", weight=1.0)

        sig_low = scorer.score(item, low_weight, interp,
                               full_market_context, "DX-Y.NYB")
        sig_high = scorer.score(item, high_weight, interp,
                                full_market_context, "DX-Y.NYB")
        assert sig_high.signal_half_life_mins > sig_low.signal_half_life_mins


# ===========================================================================
# VALIDATION
# ===========================================================================


class TestValidation:
    def test_empty_text_rejected(self):
        with pytest.raises(ValidationError):
            SpeakerTextItem(text="", speaker_name="Test")

    def test_empty_speaker_name_rejected(self):
        with pytest.raises(ValidationError):
            SpeakerTextItem(text="Some text", speaker_name="")

    def test_invalid_source_type_rejected(self):
        with pytest.raises(ValidationError):
            SpeakerTextItem(
                text="Test", speaker_name="Test",
                source_type="podcast",
            )

    def test_direction_sentiment_mismatch(self):
        with pytest.raises(ValidationError):
            SpeakerInterpretation(
                reasoning="T", asset_class="USD",
                direction=-1, nlp_sentiment_score=0.7,
                surprise_factor=0.5, expected_volatility=1.0,
                statement_market_alignment=0.8,
                quantitative_alignment=0.8,
                policy_signal_type="commentary",
                impact_horizon="intraday",
                detected_event_category="CPI",
                is_reiteration=False, guidance_bias="hawkish",
            )

    def test_invalid_policy_signal_type(self):
        with pytest.raises(ValidationError):
            SpeakerInterpretation(
                reasoning="T", asset_class="USD",
                direction=1, nlp_sentiment_score=0.5,
                surprise_factor=0.5, expected_volatility=1.0,
                statement_market_alignment=0.8,
                quantitative_alignment=0.8,
                policy_signal_type="random_type",
                impact_horizon="intraday",
                detected_event_category="CPI",
                is_reiteration=False, guidance_bias="hawkish",
            )


# ===========================================================================
# REPOSITORY
# ===========================================================================


class TestSpeakerSignalRepository:
    def test_save_success(
        self, powell_item, powell_speaker, hawkish_interpretation,
        full_market_context, scorer
    ):
        signal = scorer.score(
            powell_item, powell_speaker, hawkish_interpretation,
            full_market_context, "EURUSD=X",
        )
        mock_session = MagicMock()
        mock_session.refresh.side_effect = lambda r: setattr(r, "id", 42)

        repo = SpeakerSignalRepository()
        with patch("nlp_x.SessionLocal", return_value=mock_session):
            record_id = repo.save(
                powell_item, powell_speaker, hawkish_interpretation,
                signal, full_market_context,
            )
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        assert record_id == 42

    def test_save_rollback_on_error(
        self, powell_item, powell_speaker, hawkish_interpretation,
        full_market_context, scorer
    ):
        from sqlalchemy.exc import SQLAlchemyError
        
        signal = scorer.score(
            powell_item, powell_speaker, hawkish_interpretation,
            full_market_context, "EURUSD=X",
        )
        mock_session = MagicMock()
        mock_session.commit.side_effect = SQLAlchemyError("fail")

        # from sqlalchemy.exc import SQLAlchemyError
        repo = SpeakerSignalRepository()
        with patch("nlp_x.SessionLocal", return_value=mock_session):
            with pytest.raises(SQLAlchemyError):
                repo.save(
                    powell_item, powell_speaker, hawkish_interpretation,
                    signal, full_market_context,
                )
        mock_session.rollback.assert_called_once()


# ===========================================================================
# LLM ANALYSIS (MOCK)
# ===========================================================================


class TestSpeakerAnalysisService:
    def test_analyze_returns_interpretation(
        self, powell_item, powell_speaker, full_market_context,
        hawkish_interpretation
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = hawkish_interpretation

        service = SpeakerAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        result = service.analyze(powell_item, powell_speaker, full_market_context)
        assert isinstance(result, SpeakerInterpretation)
        assert result.direction == 1

    def test_analyze_corrects_surprise_drift(
        self, powell_item, powell_speaker, full_market_context,
        hawkish_interpretation
    ):
        drifted = hawkish_interpretation.model_copy(
            update={"surprise_factor": 0.50}
        )
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = drifted

        service = SpeakerAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        result = service.analyze(powell_item, powell_speaker, full_market_context)
        assert result.surprise_factor == pytest.approx(0.82, rel=1e-3)

    def test_analyze_raises_on_none(
        self, powell_item, powell_speaker, full_market_context
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = None

        service = SpeakerAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with pytest.raises(ValueError, match="None"):
            service.analyze(powell_item, powell_speaker, full_market_context)


# ===========================================================================
# SPEAKER LOOKUP (MOCK)
# ===========================================================================


class TestSpeakerLookup:
    def test_resolve_with_override(self):
        with patch("nlp_x.Speaker.from_name") as mock_from:
            mock_from.return_value = Speaker(
                name="Test", role="R", primarily_impacts="USD", weight=0.5
            )
            speaker = _resolve_speaker("Test", weight_override=0.95)
            assert speaker.weight == pytest.approx(0.95)

    def test_resolve_without_override(self):
        with patch("nlp_x.Speaker.from_name") as mock_from:
            mock_from.return_value = Speaker(
                name="Powell", role="Fed Chair",
                primarily_impacts="USD", weight=1.0
            )
            speaker = _resolve_speaker("Powell")
            assert speaker.weight == pytest.approx(1.0)


# ===========================================================================
# BACKWARD COMPATIBILITY
# ===========================================================================


class TestBackwardCompatibility:
    def test_legacy_generate_signal(self):
        interp = EventInterpretation(
            reasoning="Strong NFP beat",
            asset_class="USD",
            direction=1,
            nlp_sentiment_score=0.9,
            surprise_factor=0.9,
            expected_volatility=1.5,
            cross_assets={"XAU": -1},
        )
        signal = generate_signal(interp, speaker_weight=1.0)

        assert isinstance(signal, Signal)
        assert signal.final_score > 0
        assert signal.direction == 1
        assert "XAU" in signal.cross_asset_signals

    def test_legacy_neutral_signal(self):
        interp = EventInterpretation(
            reasoning="No impact",
            asset_class="USD",
            direction=0,
            nlp_sentiment_score=0.0,
            surprise_factor=0.0,
            expected_volatility=1.0,
        )
        signal = generate_signal(interp, speaker_weight=0.5)
        assert signal.final_score == 0.0
        assert not signal.is_tradable


# ===========================================================================
# FULL PIPELINE (MOCK)
# ===========================================================================


class TestFullPipeline:
    def test_end_to_end(
        self, powell_item, hawkish_interpretation, full_market_context
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = hawkish_interpretation

        mock_session = MagicMock()
        mock_session.refresh.side_effect = lambda r: setattr(r, "id", 99)

        service = SpeakerAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with patch("nlp_x.SessionLocal", return_value=mock_session):
            with patch("nlp_x.Speaker.from_name") as mock_from:
                mock_from.return_value = Speaker(
                    name="Jerome Powell", role="Fed Chair",
                    primarily_impacts="USD", weight=1.0,
                )
                interp, signal = analyze_speaker_text(
                    item=powell_item,
                    market_context=full_market_context,
                    ticker="EURUSD=X",
                    llm=mock_llm,
                    persist=True,
                    analysis_service=service,
                )

        assert isinstance(interp, SpeakerInterpretation)
        assert isinstance(signal, SpeakerSignal)
        assert signal.speaker_name == "Jerome Powell"
        assert signal.ticker == "EURUSD=X"
        mock_session.commit.assert_called_once()

    def test_persist_false(
        self, powell_item, hawkish_interpretation, full_market_context
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = hawkish_interpretation

        service = SpeakerAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with patch("nlp_x.Speaker.from_name") as mock_from:
            mock_from.return_value = Speaker(
                name="Jerome Powell", role="Fed Chair",
                primarily_impacts="USD", weight=1.0,
            )
            interp, signal = analyze_speaker_text(
                item=powell_item,
                market_context=full_market_context,
                ticker="EURUSD=X",
                llm=mock_llm,
                persist=False,
                analysis_service=service,
            )
        assert isinstance(signal, SpeakerSignal)

    def test_empty_ticker_raises(self, powell_item, full_market_context):
        with pytest.raises(ValueError, match="ticker"):
            analyze_speaker_text(
                item=powell_item,
                market_context=full_market_context,
                ticker="",
                llm=MagicMock(),
            )