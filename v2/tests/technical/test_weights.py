"""
tests/technical/test_weights.py
===============================
تست‌های لایه confluence — وزن‌ها، تجمیع score، confidence.

این تست‌ها قرارداد Phase 1 را نگه می‌دارند:
  - مجموع وزن‌ها = 1.0
  - weights_hash پایدار و حساس به تغییر وزن
  - aggregate_score: rule «structure یا smc_location موجود نباشد → None»
  - فرمول confidence دقیقاً نسخه قبل
"""

from __future__ import annotations

from agents.technical.confluence.confidence import compute_confidence
from agents.technical.confluence.scorer import aggregate_score
from agents.technical.confluence.weights import (
    ENGINE_VERSION, WEIGHTS, verify_weights_sum, weights_hash,
)
from agents.technical.models import ComponentScores


class TestWeights:
    def test_sum_is_one(self):
        assert verify_weights_sum() is True

    def test_ten_factors(self):
        assert len(WEIGHTS) == 10
        expected = {
            "structure": 0.20, "smc_location": 0.15, "trend": 0.13,
            "liquidity_sweep": 0.12, "mtf_confluence": 0.10, "ote_zone": 0.10,
            "pdh_pdl": 0.08, "momentum": 0.06, "volatility": 0.04,
            "price_action": 0.02,
        }
        assert WEIGHTS == expected  # مقادیر نسخه قبل — تغییر فقط با backtest

    def test_engine_version_semver(self):
        parts = ENGINE_VERSION.split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts)

    def test_weights_hash_stable(self):
        h1, h2 = weights_hash(), weights_hash()
        assert h1 == h2
        assert len(h1) == 12

    def test_weights_hash_changes_with_weights(self, monkeypatch):
        import agents.technical.confluence.weights as w
        base = w.weights_hash()
        monkeypatch.setitem(w.WEIGHTS, "structure", 0.21)
        assert w.weights_hash() != base


class TestAggregateScore:
    def test_all_none_except_structure(self):
        c = ComponentScores(structure=0.8)
        # semantics نسخه قبل: structure موجود → score محاسبه می‌شود (بقیه 0)
        assert aggregate_score(c) == 0.20 * 0.8

    def test_all_none_is_none(self):
        c = ComponentScores()
        assert aggregate_score(c) is None

    def test_smc_location_alone_counts(self):
        c = ComponentScores(smc_location=-0.5)
        assert abs(aggregate_score(c) - (-0.15 * 0.5)) < 1e-12

    def test_none_treated_as_zero(self):
        c = ComponentScores(structure=1.0, trend=-1.0)
        expected = 0.20 * 1.0 + 0.13 * -1.0
        assert abs(aggregate_score(c) - expected) < 1e-12

    def test_clamp_to_bounds(self):
        c = ComponentScores(
            structure=1.0, smc_location=1.0, trend=1.0, liquidity_sweep=1.0,
            mtf_confluence=1.0, ote_zone=1.0, pdh_pdl=1.0, momentum=1.0,
            volatility=1.0, price_action=1.0,
        )
        assert aggregate_score(c) == 1.0
        c2 = c.model_copy(update={k: -1.0 for k in WEIGHTS})
        assert aggregate_score(c2) == -1.0


class TestConfidence:
    def test_max_confidence(self):
        c = ComponentScores(structure=1.0, trend=1.0, momentum=1.0, smc_location=1.0)
        conf = compute_confidence(c, has_ob=True, has_fvg=True)
        # data_q=1.0×0.2 + setup_q=1.0×0.3 + agreement=3/3×0.5 = 0.2+0.3+0.5
        assert abs(conf - 1.0) < 1e-9

    def test_zero_when_nothing(self):
        c = ComponentScores()
        assert compute_confidence(c, has_ob=False, has_fvg=False) == 0.0

    def test_agreement_partial(self):
        # structure>0, trend<0, momentum>0 → max(pos=2, neg=1)/3 × 0.5
        # data_q: trend موجود → 0.5 × 0.2 (smc نیست)
        c = ComponentScores(structure=0.5, trend=-0.5, momentum=0.5)
        conf = compute_confidence(c, has_ob=False, has_fvg=False)
        expected = 0.5 * 0.2 + (2 / 3) * 0.5
        assert abs(conf - expected) < 1e-9
