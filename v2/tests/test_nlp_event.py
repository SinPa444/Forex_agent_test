# tests/test_nlp_event.py
"""
Unit tests for nlp_event.py
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, EventSignalDB
from core.models import (
    EventAnalysisInput,
    EventInterpretation,
    EventSignal,
    MarketContext,
)
from agents.fundamental.data_fetcher import HistoricalStdResult
from agents.fundamental.nlp_event import (
    EVENT_IMPACT_WEIGHT,
    EventAnalysisService,
    EventSignalScorer,
    EventSignalRepository,
    _validate_for_analysis,
    _resolve_event_impact_weight,
    _resolve_event_category,
    analyze_economic_event,
    build_event_brief,
    build_event_prompt,
)


# ===========================================================================
# FIXTURES
# ===========================================================================


@pytest.fixture
def temp_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("nlp_event.SessionLocal", Session)
    return Session


@pytest.fixture
def cpi_event_input():
    return EventAnalysisInput(
        title="CPI y/y",
        currency="USD",
        impact="High",
        category="CPI",
        event_date=datetime(2024, 1, 15, 13, 30),
        actual=3.8,
        forecast=3.5,
        previous=3.6,
        raw_actual_str="3.8%",
        raw_forecast_str="3.5%",
        raw_previous_str="3.6%",
    )


@pytest.fixture
def nfp_inline_event_input():
    return EventAnalysisInput(
        title="Non-Farm Employment Change",
        currency="USD",
        impact="High",
        category="Non-Farm Payrolls",
        actual=185_000,
        forecast=185_000,
        previous=190_000,
    )


@pytest.fixture
def low_impact_event_input():
    return EventAnalysisInput(
        title="Some Minor Indicator",
        currency="GBP",
        impact="Low",
        actual=1.0,
        forecast=1.1,
        previous=1.0,
    )


@pytest.fixture
def market_context_full():
    return MarketContext(
        target_asset="EURUSD=X",
        event_title="CPI y/y",
        actual_value=3.8,
        forecast_value=3.5,
        previous_value=3.6,
        historical_std=0.14,
        historical_volatility=0.12,
        atr_current=0.0045,
        atr_baseline=0.0040,
        implied_volatility=15.2,
        yield_spread=1.85,
        calculated_surprise=0.74,
        calculated_volatility=1.65,
    )


@pytest.fixture
def cpi_interpretation():
    return EventInterpretation(
        reasoning="CPI beat by 0.3pp. Inflationary, bullish USD.",
        asset_class="USD",
        direction=1,
        nlp_sentiment_score=0.75,
        surprise_factor=0.74,
        expected_volatility=1.65,
        cross_assets={"XAU": -1, "SPX": -1},
        surprise_interpretation="beat",
        momentum_vs_previous="accelerating",
        quantitative_alignment=0.95,
        economic_implication="inflationary",
        impact_horizon="multi-session",
        is_consistent_with_event_type=True,
    )


@pytest.fixture
def neutral_interpretation():
    return EventInterpretation(
        reasoning="NFP in line, no signal.",
        asset_class="USD",
        direction=0,
        nlp_sentiment_score=0.02,
        surprise_factor=0.04,
        expected_volatility=0.85,
        cross_assets={},
        surprise_interpretation="in_line",
        momentum_vs_previous="stable",
        quantitative_alignment=0.95,
        economic_implication="neutral",
        impact_horizon="immediate",
        is_consistent_with_event_type=True,
    )


@pytest.fixture
def reliable_std_result():
    return HistoricalStdResult(
        value=0.14,
        source="computed_canonical",
        observations=60,
        window_years=5,
        canonical_key="US_CPI_YY",
        matched_titles=["CPI y/y"],
        outliers_removed=3,
    )


@pytest.fixture
def fallback_std_result():
    return HistoricalStdResult(
        value=0.15,
        source="absolute_fallback",
        observations=0,
        window_years=None,
        canonical_key=None,
        matched_titles=[],
    )


@pytest.fixture
def scorer():
    return EventSignalScorer()


# ===========================================================================
# VALIDATION TESTS
# ===========================================================================


class TestValidation:
    def test_valid_released_event(self, cpi_event_input):
        valid, reason = _validate_for_analysis(cpi_event_input)
        assert valid is True
        assert reason == ""

    def test_non_economic_rejected(self):
        """Non-economic events باید توسط _validate_for_analysis رد شوند."""
        event = EventAnalysisInput(
            title="Bank Holiday",
            currency="USD",
            impact="Non-Economic",
            actual=0.0,
        )
        valid, reason = _validate_for_analysis(event)
        assert valid is False
        assert reason == "non_economic_event"


    def test_non_economic_blocked_in_pipeline(
        self, market_context_full,
    ):
        """Non-economic events باید در analyze_economic_event() exception بدهند."""
        from unittest.mock import MagicMock
        
        event = EventAnalysisInput(
            title="Bank Holiday",
            currency="USD",
            impact="Non-Economic",
            actual=0.0,
        )
        
        with pytest.raises(ValueError, match="non_economic"):
            analyze_economic_event(
                event_input=event,
                market_context=market_context_full,
                ticker="EURUSD=X",
                llm=MagicMock(),
            )


# ===========================================================================
# EVENT BRIEF TESTS
# ===========================================================================


class TestEventBrief:
    def test_basic_brief(self, cpi_event_input):
        brief = build_event_brief(cpi_event_input)
        assert "CPI y/y" in brief
        assert "USD" in brief
        assert "3.8%" in brief
        assert "3.5%" in brief
        assert "BEAT" in brief
        assert "accelerating" not in brief.lower()  # objective, no interpretation
        assert "Increased" in brief  # momentum description

    def test_in_line_brief(self, nfp_inline_event_input):
        brief = build_event_brief(nfp_inline_event_input)
        assert "in line" in brief.lower()

    def test_brief_with_missing_forecast(self):
        event = EventAnalysisInput(
            title="Test Event",
            currency="USD",
            actual=100.0,
            previous=95.0,
        )
        brief = build_event_brief(event)
        assert "not available" in brief.lower()


# ===========================================================================
# IMPACT WEIGHT TESTS
# ===========================================================================


class TestImpactWeight:
    def test_high(self):
        assert _resolve_event_impact_weight("High") == 1.00

    def test_medium(self):
        assert _resolve_event_impact_weight("Medium") == 0.70

    def test_low(self):
        assert _resolve_event_impact_weight("Low") == 0.40

    def test_unknown(self):
        assert _resolve_event_impact_weight("Random") == 0.50


# ===========================================================================
# CATEGORY RESOLUTION TESTS
# ===========================================================================


class TestCategoryResolution:
    def test_explicit_category_kept(self):
        assert _resolve_event_category("CPI", "Some Title") == "CPI"

    def test_infer_cpi(self):
        assert _resolve_event_category(None, "Core CPI m/m") == "CPI"

    def test_infer_nfp(self):
        assert _resolve_event_category(None, "Non-Farm Employment Change") == "Non-Farm Payrolls"

    def test_infer_rate(self):
        assert _resolve_event_category(None, "Federal Funds Rate") == "Interest Rate Decision"

    def test_unknown_falls_to_default(self):
        assert _resolve_event_category(None, "Random Event") == "Default"
        
    def test_infer_federal_funds_rate(self):
        """مهم: 'Federal Funds Rate' باید match کند نه 'fed funds'."""
        assert _resolve_event_category(None, "Federal Funds Rate") == "Interest Rate Decision"

    def test_infer_main_refinancing_rate(self):
        assert _resolve_event_category(None, "Main Refinancing Rate") == "Interest Rate Decision"

    def test_infer_ecb_official_rate(self):
        assert _resolve_event_category(None, "Official Bank Rate") == "Interest Rate Decision"


# ===========================================================================
# SCORING TESTS
# ===========================================================================


class TestEventSignalScorer:
    def test_hot_cpi_strong_signal(
        self, cpi_event_input, cpi_interpretation,
        market_context_full, reliable_std_result, scorer,
    ):
        signal = scorer.score(
            cpi_event_input, cpi_interpretation,
            market_context_full, reliable_std_result, "EURUSD=X",
        )
        assert signal.direction == 1
        assert signal.final_score > 0.5
        assert signal.is_tradable is True
        assert signal.expected_volatility_level == "High"
        assert "XAU" in signal.cross_asset_signals
        assert signal.surprise_interpretation == "beat"
        assert signal.historical_std_reliable is True

    def test_inline_nfp_no_signal(
        self, nfp_inline_event_input, neutral_interpretation,
        market_context_full, reliable_std_result, scorer,
    ):
        signal = scorer.score(
            nfp_inline_event_input, neutral_interpretation,
            market_context_full, reliable_std_result, "DX-Y.NYB",
        )
        assert signal.direction == 0
        assert abs(signal.final_score) < 0.10
        assert signal.is_tradable is False

    def test_low_impact_never_tradable(
        self, low_impact_event_input, cpi_interpretation,
        market_context_full, reliable_std_result, scorer,
    ):
        signal = scorer.score(
            low_impact_event_input, cpi_interpretation,
            market_context_full, reliable_std_result, "GBPUSD=X",
        )
        # Low impact should never be tradable regardless of score
        assert signal.is_tradable is False
        assert signal.event_impact_weight == 0.40

    def test_fallback_std_reduces_confidence(
        self, cpi_event_input, cpi_interpretation,
        market_context_full, fallback_std_result, scorer,
    ):
        signal_with_fallback = scorer.score(
            cpi_event_input, cpi_interpretation,
            market_context_full, fallback_std_result, "EURUSD=X",
        )
        signal_with_real = scorer.score(
            cpi_event_input, cpi_interpretation,
            market_context_full,
            HistoricalStdResult(
                value=0.14, source="computed_canonical",
                observations=60, window_years=5,
                canonical_key="US_CPI_YY", matched_titles=["CPI y/y"],
            ),
            "EURUSD=X",
        )
        assert signal_with_fallback.confidence < signal_with_real.confidence
        assert signal_with_fallback.historical_std_reliable is False

    def test_half_life_high_impact_rate_long(
        self, market_context_full, reliable_std_result, scorer,
    ):
        rate_event = EventAnalysisInput(
            title="Federal Funds Rate",
            currency="USD",
            impact="High",
            category="Interest Rate Decision",
            actual=5.25,
            forecast=5.00,
            previous=5.00,
        )
        rate_interp = EventInterpretation(
            reasoning="Rate hike", asset_class="USD", direction=1,
            nlp_sentiment_score=0.6, surprise_factor=0.5,
            expected_volatility=1.0, cross_assets={},
            surprise_interpretation="beat",
            momentum_vs_previous="accelerating",
            quantitative_alignment=0.9,
            economic_implication="hawkish",
            impact_horizon="multi-session",
            is_consistent_with_event_type=True,
        )
        signal = scorer.score(
            rate_event, rate_interp, market_context_full,
            reliable_std_result, "DX-Y.NYB",
        )
        # 240 base * 1.5 high mult / 1.0 vol = 360 min
        assert signal.signal_half_life_mins >= 300

    def test_cross_asset_propagation(
        self, cpi_event_input, cpi_interpretation,
        market_context_full, reliable_std_result, scorer,
    ):
        signal = scorer.score(
            cpi_event_input, cpi_interpretation,
            market_context_full, reliable_std_result, "EURUSD=X",
        )
        # XAU=-1, score>0 → cross signal should be negative
        assert signal.cross_asset_signals["XAU"] < 0
        assert signal.cross_asset_signals["SPX"] < 0


# ===========================================================================
# LLM ANALYSIS (MOCK)
# ===========================================================================


class TestEventAnalysisService:
    def test_analyze_returns_interpretation(
        self, cpi_event_input, market_context_full, cpi_interpretation,
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = cpi_interpretation

        service = EventAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        brief = build_event_brief(cpi_event_input)
        result = service.analyze(
            cpi_event_input, market_context_full, brief,
            0.14, "computed_canonical",
        )
        assert isinstance(result, EventInterpretation)
        assert result.surprise_interpretation == "beat"

    def test_analyze_corrects_drift(
        self, cpi_event_input, market_context_full, cpi_interpretation,
    ):
        drifted = cpi_interpretation.model_copy(update={"surprise_factor": 0.5})
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = drifted

        service = EventAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        brief = build_event_brief(cpi_event_input)
        result = service.analyze(
            cpi_event_input, market_context_full, brief, 0.14, "computed",
        )
        # should be corrected to market_context value
        assert result.surprise_factor == pytest.approx(0.74, rel=1e-3)

    def test_analyze_raises_on_none(
        self, cpi_event_input, market_context_full,
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = None

        service = EventAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with pytest.raises(ValueError, match="None"):
            service.analyze(
                cpi_event_input, market_context_full, "brief",
                0.14, "computed",
            )


# ===========================================================================
# REPOSITORY TESTS
# ===========================================================================


class TestEventSignalRepository:
    def test_save_success(
        self, cpi_event_input, cpi_interpretation,
        market_context_full, reliable_std_result, temp_db, scorer,
    ):
        signal = scorer.score(
            cpi_event_input, cpi_interpretation,
            market_context_full, reliable_std_result, "EURUSD=X",
        )
        brief = build_event_brief(cpi_event_input)
        repo = EventSignalRepository()
        record_id = repo.save(
            cpi_event_input, signal, market_context_full, brief,
        )
        assert isinstance(record_id, int)
        assert record_id > 0

        # verify in DB
        Session = temp_db
        session = Session()
        try:
            count = session.query(EventSignalDB).count()
            assert count == 1
            record = session.query(EventSignalDB).first()
            assert record.event_title == "CPI y/y"
            assert record.surprise_interpretation == "beat"
        finally:
            session.close()


# ===========================================================================
# FULL PIPELINE (MOCK)
# ===========================================================================


class TestFullPipeline:
    def test_end_to_end(
        self, cpi_event_input, cpi_interpretation,
        market_context_full, reliable_std_result, temp_db,
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = cpi_interpretation

        service = EventAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with patch(
            "nlp_event.fetch_historical_surprise_std_full",
            return_value=reliable_std_result,
        ):
            interp, signal = analyze_economic_event(
                event_input=cpi_event_input,
                market_context=market_context_full,
                ticker="EURUSD=X",
                llm=mock_llm,
                persist=True,
                analysis_service=service,
            )
        assert isinstance(interp, EventInterpretation)
        assert isinstance(signal, EventSignal)
        assert signal.event_title == "CPI y/y"
        assert signal.ticker == "EURUSD=X"

    def test_persist_false(
        self, cpi_event_input, cpi_interpretation,
        market_context_full, reliable_std_result,
    ):
        mock_llm = MagicMock()
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = cpi_interpretation

        service = EventAnalysisService(llm=mock_llm)
        service._chain = mock_chain

        with patch(
            "nlp_event.fetch_historical_surprise_std_full",
            return_value=reliable_std_result,
        ):
            interp, signal = analyze_economic_event(
                event_input=cpi_event_input,
                market_context=market_context_full,
                ticker="EURUSD=X",
                llm=mock_llm,
                persist=False,
                analysis_service=service,
            )
        assert isinstance(signal, EventSignal)

    def test_rejects_empty_ticker(
        self, cpi_event_input, market_context_full,
    ):
        with pytest.raises(ValueError, match="ticker"):
            analyze_economic_event(
                event_input=cpi_event_input,
                market_context=market_context_full,
                ticker="",
                llm=MagicMock(),
            )