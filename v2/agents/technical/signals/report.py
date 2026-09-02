"""
agents/technical/signals/report.py
==================================
مدل‌های خروجی Technical Agent.

TechnicalReport:
  direction ∈ {1, -1, 0} — سیگنال (از score deterministic با deadband یکپارچه)
  score [-1, 1] — امتیاز (deterministic)
  confidence [0, 1] — اطمینان (deterministic)
  strategy / reasoning — روایت LLM (فقط تفسیر؛ امتیاز نمی‌سازد)

Phase 1 (افزایشی):
  llm_status: ok | guard_flagged | fallback
  llm_guard_flags: دلایل نارسایی LLM Guard (در صورت وجود)

مهم: Field descriptionها بخشی از prompt (format_instructions) هستند —
بدون backtest/تصویب تغییر نکنند.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LLMStrategy(BaseModel):
    """LLM only generates strategy and reasoning, not scores."""
    strategy: str = Field(description="Specific trading strategy based on the deterministic scores")
    reasoning: str = Field(description="3-4 sentences explaining the strategy in context of the scores")


class TechnicalReport(BaseModel):
    """Final output combining Python math and LLM narrative."""
    direction: int = Field(description="1=Bullish, -1=Bearish, 0=Neutral")
    score: float = Field(ge=-1.0, le=1.0, description="Deterministic technical score")
    confidence: float = Field(ge=0.0, le=1.0, description="Reliability of the score")
    strategy: str
    reasoning: str

    # --- Phase 1: فیلد‌های افزایشی (حسابرسی LLM) ---
    llm_status: Optional[str] = None        # ok | guard_flagged | fallback
    llm_guard_flags: Optional[str] = None   # دلایل guard (در صورت وجود)
