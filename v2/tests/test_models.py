# tests/test_models.py
"""
تست‌های واحد برای models.py — تمام Pydantic schema ها.
"""

import pytest
from datetime import datetime
from pydantic import ValidationError


# =====================================================================
# تست MarketContext
# =====================================================================

class TestMarketContext:
    def test_minimal_creation(self):
        from core.models import MarketContext
        ctx = MarketContext(target_asset="EURUSD=X")
        assert ctx.target_asset == "EURUSD=X"
        assert ctx.calculated_surprise == 0.0
        assert ctx.calculated_volatility == 1.0

    def test_full_creation(self):
        from core.models import MarketContext
        ctx = MarketContext(
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
        assert ctx.actual_value == 3.8
        assert ctx.calculated_surprise == 0.82

    def test_legacy_expected_volatility_key(self):
        """expected_volatility باید به calculated_volatility تبدیل شود."""
        from core.models import MarketContext
        ctx = MarketContext(
            target_asset="DX-Y.NYB",
            expected_volatility=2.5,
        )
        assert ctx.calculated_volatility == 2.5

    def test_extra_fields_ignored(self):
        """فیلدهای اضافی باید بدون خطا نادیده گرفته شوند."""
        from core.models import MarketContext
        ctx = MarketContext(
            target_asset="EURUSD=X",
            some_unknown_field="ignore me",
        )
        assert ctx.target_asset == "EURUSD=X"
        assert not hasattr(ctx, "some_unknown_field")

    def test_none_optional_fields(self):
        from core.models import MarketContext
        ctx = MarketContext(target_asset="GBPUSD=X")
        assert ctx.actual_value is None
        assert ctx.implied_volatility is None
        assert ctx.event_title is None


# =====================================================================
# تست Speaker
# =====================================================================

class TestSpeaker:
    def test_default_speaker(self):
        from core.models import Speaker
        s = Speaker(name="Unknown Person")
        assert s.weight == 0.5
        assert s.role == "Unknown / General Market Commentator"

    def test_custom_speaker(self):
        from core.models import Speaker
        s = Speaker(
            name="Jerome Powell",
            role="Fed Chair",
            primarily_impacts="USD",
            weight=1.0,
        )
        assert s.weight == 1.0
        assert "Powell" in s.profile_string

    def test_weight_bounds(self):
        from core.models import Speaker
        with pytest.raises(ValidationError):
            Speaker(name="Test", weight=1.5)
        with pytest.raises(ValidationError):
            Speaker(name="Test", weight=-0.1)

    def test_empty_name_rejected(self):
        from core.models import Speaker
        with pytest.raises(ValidationError):
            Speaker(name="")

    def test_profile_string_format(self):
        from core.models import Speaker
        s = Speaker(name="Christine Lagarde", role="ECB President",
                    primarily_impacts="EUR", weight=0.95)
        ps = s.profile_string
        assert "Christine Lagarde" in ps
        assert "ECB President" in ps
        assert "EUR" in ps


# =====================================================================
# تست EventInterpretation
# =====================================================================

class TestEventInterpretation:
    def test_valid_bullish(self):
        from core.models import EventInterpretation
        ei = EventInterpretation(
            reasoning="Strong NFP beat",
            asset_class="USD",
            direction=1,
            nlp_sentiment_score=0.8,
            surprise_factor=0.9,
            expected_volatility=1.5,
            cross_assets={"XAU": -1},
        )
        assert ei.direction == 1

    def test_valid_bearish(self):
        from core.models import EventInterpretation
        ei = EventInterpretation(
            reasoning="Weak GDP",
            asset_class="USD",
            direction=-1,
            nlp_sentiment_score=-0.6,
            surprise_factor=0.5,
            expected_volatility=1.2,
        )
        assert ei.direction == -1

    def test_invalid_direction_value(self):
        from core.models import EventInterpretation
        with pytest.raises(ValidationError):
            EventInterpretation(
                reasoning="Test",
                asset_class="USD",
                direction=2,
                nlp_sentiment_score=0.5,
                surprise_factor=0.5,
                expected_volatility=1.0,
            )

    def test_sentiment_out_of_range(self):
        from core.models import EventInterpretation
        with pytest.raises(ValidationError):
            EventInterpretation(
                reasoning="Test",
                asset_class="USD",
                direction=1,
                nlp_sentiment_score=1.5,
                surprise_factor=0.5,
                expected_volatility=1.0,
            )

    def test_empty_cross_assets_default(self):
        from core.models import EventInterpretation
        ei = EventInterpretation(
            reasoning="Test",
            asset_class="USD",
            direction=0,
            nlp_sentiment_score=0.0,
            surprise_factor=0.0,
            expected_volatility=1.0,
        )
        assert ei.cross_assets == {}


# =====================================================================
# تست Signal
# =====================================================================

class TestSignal:
    def test_valid_signal(self):
        from core.models import Signal
        s = Signal(
            reasoning="Test signal",
            asset_class="USD",
            direction=1,
            final_score=0.7,
            confidence=0.8,
            is_tradable=True,
            expected_volatility_level="High",
            signal_half_life_mins=45,
            cross_asset_signals={"XAU": -0.49},
        )
        assert s.is_tradable is True
        assert s.final_score == 0.7

    def test_invalid_vol_level(self):
        from core.models import Signal
        with pytest.raises(ValidationError):
            Signal(
                reasoning="T", asset_class="USD", direction=0,
                final_score=0.0, confidence=0.5, is_tradable=False,
                expected_volatility_level="Extreme",
                signal_half_life_mins=30,
            )

    def test_score_bounds(self):
        from core.models import Signal
        with pytest.raises(ValidationError):
            Signal(
                reasoning="T", asset_class="USD", direction=1,
                final_score=1.5, confidence=0.5, is_tradable=True,
                expected_volatility_level="High",
                signal_half_life_mins=30,
            )

    def test_half_life_minimum(self):
        from core.models import Signal
        with pytest.raises(ValidationError):
            Signal(
                reasoning="T", asset_class="USD", direction=0,
                final_score=0.0, confidence=0.5, is_tradable=False,
                expected_volatility_level="Low",
                signal_half_life_mins=0,
            )


# =====================================================================
# تست NewsItem
# =====================================================================

class TestNewsItem:
    def test_valid_news_item(self):
        from core.models import NewsItem
        item = NewsItem(
            title="US CPI Beats Expectations",
            summary="Inflation came in hot.",
            source="ForexLive",
            category="CPI",
            currency="USD",
            impact="High",
        )
        assert item.source == "ForexLive"

    def test_empty_title_rejected(self):
        from core.models import NewsItem
        with pytest.raises(ValidationError):
            NewsItem(title="", source="ForexLive")

    def test_whitespace_title_rejected(self):
        from core.models import NewsItem
        with pytest.raises(ValidationError):
            NewsItem(title="   ", source="ForexLive")

    def test_default_values(self):
        from core.models import NewsItem
        item = NewsItem(title="Test Headline")
        assert item.summary == ""
        assert item.source == "Unknown"
        assert item.source_reliability is None
        assert item.published is None

    def test_reliability_bounds(self):
        from core.models import NewsItem
        with pytest.raises(ValidationError):
            NewsItem(title="Test", source_reliability=1.5)

    def test_with_datetime(self):
        from core.models import NewsItem
        now = datetime(2024, 6, 15, 12, 0)
        item = NewsItem(title="Test", published=now)
        assert item.published == now


# =====================================================================
# تست NewsInterpretation
# =====================================================================

class TestNewsInterpretation:
    def test_valid_bullish(self):
        from core.models import NewsInterpretation
        ni = NewsInterpretation(
            reasoning="CPI beat → USD bullish",
            asset_class="FX",
            direction=1,
            nlp_sentiment_score=0.75,
            surprise_factor=0.82,
            expected_volatility=1.65,
            cross_assets={"XAU": -1},
            detected_event_category="CPI",
            headline_body_alignment=0.92,
            quantitative_alignment=0.95,
            impact_horizon="multi-session",
        )
        assert ni.direction == 1

    def test_direction_sentiment_mismatch_rejected(self):
        """direction=-1 ولی sentiment مثبت → خطا"""
        from core.models import NewsInterpretation
        with pytest.raises(ValidationError):
            NewsInterpretation(
                reasoning="Test",
                asset_class="FX",
                direction=-1,
                nlp_sentiment_score=0.7,
                surprise_factor=0.5,
                expected_volatility=1.0,
                detected_event_category="CPI",
                headline_body_alignment=0.8,
                quantitative_alignment=0.8,
                impact_horizon="intraday",
            )

    def test_neutral_sentiment_requires_direction_zero(self):
        from core.models import NewsInterpretation
        # sentiment خنثی (0.03 < tolerance=0.05)
        ni = NewsInterpretation(
            reasoning="In-line data",
            asset_class="FX",
            direction=0,
            nlp_sentiment_score=0.03,
            surprise_factor=0.04,
            expected_volatility=0.85,
            detected_event_category="NFP",
            headline_body_alignment=0.90,
            quantitative_alignment=0.90,
            impact_horizon="immediate",
        )
        assert ni.direction == 0

    def test_invalid_impact_horizon(self):
        from core.models import NewsInterpretation
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
                impact_horizon="weekly",  # نامعتبر
            )

    def test_invalid_cross_asset_direction(self):
        from core.models import NewsInterpretation
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
                impact_horizon="intraday",
                cross_assets={"XAU": 5},  # 5 نامعتبره
            )

    def test_alignment_bounds(self):
        from core.models import NewsInterpretation
        with pytest.raises(ValidationError):
            NewsInterpretation(
                reasoning="Test",
                asset_class="FX",
                direction=1,
                nlp_sentiment_score=0.5,
                surprise_factor=0.5,
                expected_volatility=1.0,
                detected_event_category="CPI",
                headline_body_alignment=1.5,  # بالاتر از ۱
                quantitative_alignment=0.8,
                impact_horizon="intraday",
            )


# =====================================================================
# تست NewsSignal
# =====================================================================

class TestNewsSignal:
    def test_valid_news_signal(self):
        from core.models import NewsSignal
        ns = NewsSignal(
            reasoning="Strong CPI beat",
            asset_class="FX",
            direction=1,
            final_score=0.74,
            confidence=0.78,
            is_tradable=True,
            expected_volatility_level="High",
            signal_half_life_mins=98,
            cross_asset_signals={"XAU": -0.518},
            source="ForexLive",
            source_reliability=0.82,
            event_category_weight=0.95,
            data_completeness_score=1.0,
            data_quality_factor=1.0,
            headline_body_alignment=0.92,
            quantitative_alignment=0.95,
            ticker="EURUSD=X",
        )
        assert ns.is_tradable is True
        assert ns.source == "ForexLive"

    def test_data_quality_minimum(self):
        """data_quality_factor نمی‌تواند زیر 0.5 باشد."""
        from core.models import NewsSignal
        with pytest.raises(ValidationError):
            NewsSignal(
                reasoning="T", asset_class="FX", direction=0,
                final_score=0.0, confidence=0.5, is_tradable=False,
                expected_volatility_level="Normal",
                signal_half_life_mins=90,
                source="Unknown", source_reliability=0.65,
                event_category_weight=0.4,
                data_completeness_score=0.0,
                data_quality_factor=0.3,  # کمتر از 0.5
                headline_body_alignment=0.5,
                quantitative_alignment=0.5,
                ticker="DX-Y.NYB",
            )

    def test_invalid_vol_level(self):
        from core.models import NewsSignal
        with pytest.raises(ValidationError):
            NewsSignal(
                reasoning="T", asset_class="FX", direction=0,
                final_score=0.0, confidence=0.5, is_tradable=False,
                expected_volatility_level="Extreme",
                signal_half_life_mins=90,
                source="Unknown", source_reliability=0.65,
                event_category_weight=0.4,
                data_completeness_score=0.5,
                data_quality_factor=0.75,
                headline_body_alignment=0.5,
                quantitative_alignment=0.5,
                ticker="DX-Y.NYB",
            )