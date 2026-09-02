"""
tests/technical/test_analyzer.py
================================
تست‌های TechnicalAgent — guard روایت LLM + llm_status + precomputed passthrough.

بدون شبکه/LLM واقعی: FakeListChatModel از langchain_core.
زنجیره دقیقاً production است (PydanticOutputParser + _strip_json_markdown).
"""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.technical.analyzer import TechnicalAgent
from agents.technical.data import market_data
from agents.technical.models import ComponentScores, TechnicalMetrics


def _metrics(score=0.42, confidence=0.6):
    return TechnicalMetrics(
        current_price=1.1050,
        technical_score=score,
        technical_confidence=confidence,
        components=ComponentScores(structure=0.8, trend=0.8),
    )


def _ok_llm(direction_words: str = "bullish buy long"):
    """LLM تقلبی با روایت هم‌راستا."""
    payload = json.dumps({
        "strategy": "Trade the setup in the " + direction_words + " direction.",
        "reasoning": "Deterministic factors align on the " + direction_words + " bias.",
    })
    return FakeListChatModel(responses=[payload, payload])


class TestAnalyzer:
    def test_ok_path(self, monkeypatch):
        monkeypatch.setattr(
            market_data, "fetch_price_history",
            lambda ticker, interval="1d": None,
        )
        agent = TechnicalAgent(_ok_llm("bullish"))
        metrics, report = agent.analyze(
            "TEST=X", timeframe="H1", precomputed_metrics=_metrics(0.42)
        )
        assert report.direction == 1
        assert report.score == 0.42
        assert report.confidence == 0.6
        assert report.llm_status == "ok"
        assert "bullish" in report.strategy.lower()
        # precomputed: fetch واقعی صدا نزده شد (دیتای None fetch → score None می‌شد)
        assert metrics.technical_score == 0.42

    def test_guard_flagged_on_contradiction(self, monkeypatch):
        """
        روایت LLM با سیگنال deterministic تناقض دارد (2 بار) →
        guard_flagged + «Stand aside» (سیگنال deterministic سالم می‌ماند).
        """
        monkeypatch.setattr(
            market_data, "fetch_price_history",
            lambda ticker, interval="1d": None,
        )
        # score=+0.42 (BULLISH) ولی LLM بارها sell/short می‌گوید
        payload = json.dumps({
            "strategy": "Sell aggressively and go short here.",
            "reasoning": "Everything looks bearish, short short short.",
        })
        agent = TechnicalAgent(FakeListChatModel(responses=[payload] * 5))
        metrics, report = agent.analyze(
            "TEST=X", timeframe="H1", precomputed_metrics=_metrics(0.42)
        )
        assert report.direction == 1          # deterministic دست‌نخورده
        assert report.score == 0.42
        assert report.llm_status == "guard_flagged"
        assert report.strategy == "Stand aside"

    def test_fallback_on_llm_error(self, monkeypatch):
        def _boom(_input):
            raise RuntimeError("llm down")

        monkeypatch.setattr(
            market_data, "fetch_price_history",
            lambda ticker, interval="1d": None,
        )
        agent = TechnicalAgent(_boom)
        metrics, report = agent.analyze(
            "TEST=X", timeframe="H1", precomputed_metrics=_metrics(0.42)
        )
        assert report.llm_status == "fallback"
        assert report.strategy == "Stand aside"
        assert "Technical LLM narrative failed" in report.reasoning
        assert report.direction == 1  # سیگنال deterministic حفظ است

    def test_insufficient_data(self, monkeypatch):
        monkeypatch.setattr(
            market_data, "fetch_price_history",
            lambda ticker, interval="1d": None,
        )
        agent = TechnicalAgent(_ok_llm())
        metrics, report = agent.analyze(
            "TEST=X", timeframe="H1",
            precomputed_metrics=TechnicalMetrics(),  # score None
        )
        assert report.direction == 0
        assert report.strategy == "Stand aside"
        assert "Insufficient data" in report.reasoning
        assert report.llm_status == "fallback"

    def test_neutral_direction_for_small_score(self, monkeypatch):
        """
        W2: score=0.12 (بین 0.10 و 0.15) → NEUTRAL (deadband یکپارچه 0.15).
        در نسخه قبل BULLISH می‌شد — اختلاف آگاهانه‌ی Phase 1.
        """
        monkeypatch.setattr(
            market_data, "fetch_price_history",
            lambda ticker, interval="1d": None,
        )
        agent = TechnicalAgent(_ok_llm())
        _, report = agent.analyze(
            "TEST=X", timeframe="H1", precomputed_metrics=_metrics(0.12, 0.5)
        )
        assert report.direction == 0

    def test_prompt_inputs_shape(self, monkeypatch):
        """زنجیره باید inputs کامل prompt را render کند (بدون placeholder باز)."""
        monkeypatch.setattr(
            market_data, "fetch_price_history",
            lambda ticker, interval="1d": None,
        )
        agent = TechnicalAgent(_ok_llm())
        rendered = agent.prompt.invoke({
            "ticker": "TEST=X", "timeframe": "H1", "tech_score": "0.42",
            "tech_confidence": "0.60", "structure": "0.80", "trend": "0.80",
            "smc_loc": "0.00", "mtf_conf": "N/A", "mom": "0.80", "vol": "0.50",
            "liq_sweep": "0.40", "pdh_pdl": "0.60", "ote": "0.00",
            "liq_status": "N/A", "pdh": "1.10000", "pdl": "1.09000",
            "pdh_pdl_status": "N/A", "ote_status": "N/A", "htf_interval": "N/A",
            "htf_trend_status": "N/A", "current_price": "1.1050",
            "trend_status": "Uptrend", "adx_value": "25.0", "chop_value": "30.0",
            "recent_bos": "Bullish BOS", "active_bullish_ob": "1.1000",
            "active_bearish_ob": "None nearby", "active_bull_fvg_low": "N/A",
            "active_bull_fvg_high": "N/A", "active_bear_fvg_low": "N/A",
            "active_bear_fvg_high": "N/A", "rsi": "55.0",
            "macd_histogram": "0.0001", "last_candle_type": "Neutral",
            "format_instructions": "(test)",
        })
        text = rendered.to_string()
        assert "{ticker}" not in text  # همه placeholderها پر شده‌اند
        assert "TEST=X" in text
