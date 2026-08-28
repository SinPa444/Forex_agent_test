"""
tests/test_market_structure.py
==============================
P0 tests — Market Structure / CHoCH usage.
"""

from __future__ import annotations

from agents.technical.market_structure import structure_score_for_component


def test_bos_only_keeps_magnitude():
    # رفتار قبلی: فقط BOS → ±0.8
    assert structure_score_for_component(bos=1, choch=0) == 0.8
    assert structure_score_for_component(bos=-1, choch=0) == -0.8


def test_choch_only_is_weaker_than_bos():
    # CHoCH به‌تنهایی تغییر ماهیت است؛ قدرت کمتر از BOS تأییدشده.
    assert structure_score_for_component(bos=0, choch=1) == 0.6
    assert structure_score_for_component(bos=0, choch=-1) == -0.6


def test_inverse_choch_after_bos_flips_structure():
    # CHoCH خلاف جهت BOS قبلی = reversal؛ در جهت CHoCH ولی با نیم‌وزن.
    assert structure_score_for_component(bos=-1, choch=1) == 0.5
    assert structure_score_for_component(bos=1, choch=-1) == -0.5


def test_same_direction_bos_choch_keeps_confidence():
    # همراهی BOS + CHoCH هم‌جهت = ساختار قوی.
    assert structure_score_for_component(bos=1, choch=1) == 0.8
    assert structure_score_for_component(bos=-1, choch=-1) == -0.8


def test_no_structure_returns_none():
    assert structure_score_for_component(bos=0, choch=0) is None
