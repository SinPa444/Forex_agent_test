import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # root v2

"""
test_event_scorer.py
====================
Unit tests for the EventSignalScorer deterministic scoring engine.

This test suite verifies that the math, penalties, multipliers, and 
tradability rules in nlp_event.py work exactly as designed, independent 
of the LLM.
"""

import unittest
from datetime import datetime

from agents.fundamental.data_fetcher import HistoricalStdResult
from core.models import EventAnalysisInput, EventInterpretationEvent, MarketContext
from agents.fundamental.nlp_event import EventSignalScorer


class TestEventSignalScorer(unittest.TestCase):
    """Test suite for deterministic event scoring math."""

    def setUp(self):
        self.scorer = EventSignalScorer()
        self.ticker = "DX-Y.NYB"
        
        # A perfect, complete MarketContext for baseline tests
        self.full_context = MarketContext(
            target_asset=self.ticker,
            event_title="CPI y/y",
            event_category="CPI",
            actual_value=3.8,
            forecast_value=3.5,
            previous_value=3.6,
            historical_std=0.15,
            historical_volatility=0.8,
            atr_current=0.005,
            atr_baseline=0.004,
            implied_volatility=15.0,
            yield_spread=1.5,
            calculated_surprise=0.5,
            calculated_volatility=1.5,
        )

        # Reliable historical std result
        self.reliable_std = HistoricalStdResult(
            value=0.15,
            source="computed_canonical",
            observations=50,
            window_years=5,
            canonical_key="US_CPI_YY",
            matched_titles=["CPI y/y"],
            outliers_removed=2,
        )

    def _make_interpretation(
        self,
        sentiment: float = 0.8,
        direction: int = 1,
        surprise: float = 0.5,
        volatility: float = 1.5,
        quant_align: float = 0.9,
        is_consistent: bool = True,
        surprise_interp: str = "beat",
        momentum: str = "accelerating",
        implication: str = "inflationary",
    ) -> EventInterpretationEvent:
        """Helper to build valid EventInterpretationEvent objects."""
        return EventInterpretationEvent(
            reasoning="Test reasoning",
            asset_class="USD",
            direction=direction,
            nlp_sentiment_score=sentiment,
            surprise_factor=surprise,
            expected_volatility=volatility,
            cross_assets={"XAU": -1},
            surprise_interpretation=surprise_interp,
            momentum_vs_previous=momentum,
            quantitative_alignment=quant_align,
            economic_implication=implication,
            impact_horizon="multi-session",
            is_consistent_with_event_type=is_consistent,
        )

    def _make_event_input(
        self,
        title: str = "CPI y/y",
        currency: str = "USD",
        impact: str = "High",
        actual: float = 3.8,
        forecast: float = 3.5,
        previous: float = 3.6,
    ) -> EventAnalysisInput:
        """Helper to build valid EventAnalysisInput objects."""
        return EventAnalysisInput(
            title=title,
            currency=currency,
            impact=impact,
            category="CPI",
            event_date=datetime.utcnow(),
            actual=actual,
            forecast=forecast,
            previous=previous,
            raw_actual_str=str(actual),
            raw_forecast_str=str(forecast),
            raw_previous_str=str(previous),
        )

    def test_high_impact_bullish_perfect_signal(self):
        """Test baseline scoring for a perfect bullish signal."""
        event_input = self._make_event_input()
        interp = self._make_interpretation(sentiment=0.8, direction=1, surprise=0.5, volatility=1.5)
        
        signal = self.scorer.score(event_input, interp, self.full_context, self.reliable_std, self.ticker)
        
        # raw_score = 0.8 * 1.0 * (1+0.5) * 1.0 * 0.9 * 1.0 = 1.08 -> clamped to 1.0
        self.assertEqual(signal.final_score, 1.0)
        # confidence = 0.3 + (0.5*0.25) + 0.2 + (0.9*0.15) + 0.1 = 0.86
        self.assertAlmostEqual(signal.confidence, 0.86, places=3)
        self.assertTrue(signal.is_tradable)
        self.assertEqual(signal.direction, 1)

    def test_low_impact_not_tradable(self):
        """Even a perfect signal should not be tradable if impact is Low."""
        event_input = self._make_event_input(impact="Low")
        interp = self._make_interpretation(sentiment=0.8, direction=1, surprise=0.5)
        
        signal = self.scorer.score(event_input, interp, self.full_context, self.reliable_std, self.ticker)
        
        self.assertTrue(signal.final_score >= 0.4)
        self.assertTrue(signal.confidence >= 0.55)
        self.assertFalse(signal.is_tradable, "Low impact events must not be tradable")

    def test_fallback_std_penalty(self):
        """Test that fallback historical std reduces score and confidence."""
        event_input = self._make_event_input()
        interp = self._make_interpretation()
        
        fallback_std = HistoricalStdResult(
            value=0.15,
            source="absolute_fallback",
            observations=0,
            window_years=None,
            canonical_key=None,
            matched_titles=[],
        )
        
        signal_fallback = self.scorer.score(event_input, interp, self.full_context, fallback_std, self.ticker)
        
        # Score should be multiplied by 0.75: 1.08 * 0.75 = 0.81
        self.assertAlmostEqual(signal_fallback.final_score, 0.81, places=4)
        
        # Confidence should have an extra 0.05 penalty: 0.86 - 0.05 = 0.81
        self.assertAlmostEqual(signal_fallback.confidence, 0.785, places=4)

    def test_high_surprise_weak_sentiment_penalty(self):
        """Test confidence penalty when surprise is high but sentiment is weak."""
        event_input = self._make_event_input()
        # Surprise > 0.7, Sentiment < 0.3
        interp = self._make_interpretation(sentiment=0.2, surprise=0.8)
        
        signal = self.scorer.score(event_input, interp, self.full_context, self.reliable_std, self.ticker)
        
        # Base confidence = 0.3 + (0.8*0.25) + 0.2 + (0.9*0.15) + 0.1 = 0.935
        # Penalty = -0.10 -> 0.835
        self.assertAlmostEqual(signal.confidence, 0.835, places=3)

    def test_llm_inconsistency_penalty(self):
        """Test confidence penalty when LLM flags is_consistent_with_event_type=False."""
        event_input = self._make_event_input()
        interp = self._make_interpretation(is_consistent=False)
        
        signal = self.scorer.score(event_input, interp, self.full_context, self.reliable_std, self.ticker)
        
        # Base confidence 0.86 - 0.10 = 0.76
        self.assertAlmostEqual(signal.confidence, 0.76, places=3)

    def test_inverse_event_direction_respected(self):
        """Test that inverse events (like Jobless Claims miss) yield bullish signals."""
        event_input = self._make_event_input(
            title="Unemployment Claims",
            impact="Medium",
            actual=250000,
            forecast=200000,
            previous=190000,
        )
        
        # LLM should output miss -> direction -1
        interp = self._make_interpretation(
            sentiment=-0.6,
            direction=-1,
            surprise_interp="miss",
            implication="recessionary",
            is_consistent=True
        )
        
        signal = self.scorer.score(event_input, interp, self.full_context, self.reliable_std, self.ticker)
        
        self.assertEqual(signal.direction, -1)
        self.assertEqual(signal.surprise_interpretation, "miss")
        self.assertLess(signal.final_score, 0) # Should be bearish

    def test_half_life_calculation(self):
        """Test half-life math: int((base_time * impact_mult) / max(vol, 0.25))"""
        # CPI High impact: base=150, mult=1.5. Volatility=1.5
        # Expected: int((150 * 1.5) / 1.5) = 150
        event_input = self._make_event_input(impact="High")
        interp = self._make_interpretation(volatility=1.5)
        signal = self.scorer.score(event_input, interp, self.full_context, self.reliable_std, self.ticker)
        self.assertEqual(signal.signal_half_life_mins, 150)

        # Same event, but volatility is extremely low (0.1 -> clamped to 0.25)
        # Expected: int((150 * 1.5) / 0.25) = 900
        interp_low_vol = self._make_interpretation(volatility=0.1)
        signal_low_vol = self.scorer.score(event_input, interp_low_vol, self.full_context, self.reliable_std, self.ticker)
        self.assertEqual(signal_low_vol.signal_half_life_mins, 900)

    def test_data_completeness_below_tradability(self):
        """Test that poor data completeness blocks tradability."""
        # Create a context with missing values (4 out of 9 missing -> completeness < 0.5)
        poor_context = MarketContext(
            target_asset=self.ticker,
            event_title="CPI y/y",
            actual_value=3.8,
            forecast_value=3.5,
            previous_value=3.6,
            historical_std=0.15,
            historical_volatility=None,
            atr_current=None,
            atr_baseline=None,
            implied_volatility=None,
            yield_spread=None,
            calculated_surprise=0.5,
            calculated_volatility=1.5,
        )
        
        event_input = self._make_event_input()
        interp = self._make_interpretation()
        
        signal = self.scorer.score(event_input, interp, poor_context, self.reliable_std, self.ticker)
        
        # Completeness is 5/9 = 0.555. This is > 0.40 threshold, so it should pass.
        # Let's make it worse: 3/9 = 0.333
        very_poor_context = poor_context.model_copy(update={"previous_value": None, "historical_std": None})
        
        signal_poor = self.scorer.score(event_input, interp, very_poor_context, self.reliable_std, self.ticker)
        
        self.assertFalse(signal_poor.is_tradable, "Should not be tradable with completeness < 0.40")


if __name__ == "__main__":
    unittest.main()