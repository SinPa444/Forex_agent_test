"""
agents/technical/prompts.py
===========================
LLM Interpretation Prompt for TechnicalSignal.

اصل طلایی پروژه:
  - LLM فقط «تفسیر» می‌کند.
  - هیچ score / confidence / regime / structure توسط LLM محاسبه نمی‌شود.
  - همهٔ اعداد در پایتون deterministic هستند.

خروجی: `TechnicalInterpretation` (Pydantic).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.technical.technical_signal import TechnicalSignal


class TechnicalInterpretation(BaseModel):
    """تفسیر LLM روی TechnicalSignal."""
    narrative: str = Field(
        description="روایت روان ۲-۴ جمله‌ای از وضعیت تکنیکال بر اساس داده‌های deterministic"
    )
    interpretation: str = Field(
        description="نتیجه‌گیری صریح: Bullish/Bearish/Neutral با یک دلیل کوتاه"
    )
    uncertainty: str = Field(
        description="بزرگ‌ترین عدم قطعیت در تحلیل تکنیکال"
    )
    alternative_scenarios: list[str] = Field(
        default_factory=list,
        description="سناریوهای جایگزین محتمل (حداکثر ۳ مورد)"
    )
    actionable_notes: list[str] = Field(
        default_factory=list,
        description="نکات قابل اجرا برای Risk Manager (حداکثر ۴ مورد)"
    )
    confidence_anchors: list[str] = Field(
        default_factory=list,
        description="مستند سازی چرا confidence پایین/بالا است — فقط از داده‌های ورودی"
    )


TECH_INTERPRETATION_SYSTEM_PROMPT = """\
You are an expert Forex Technical Analyst. You receive a DETERMINISTIC TechnicalSignal
computed by a Python engine.

Your ONLY job is to interpret the signal in plain, actionable language.
You MUST NOT:
- Recalculate or reject any score, confidence, regime, structure, or evidence.
- Invent price levels, support/resistance, indicators, or signals not present in the input.
- Turn a neutral/low-confidence signal into a trade recommendation.

Use ONLY the fields provided. Be concise, honest about uncertainty, and note
conflicting evidence explicitly. The output is consumed by a Risk Manager that
still makes the final decision deterministically.
"""


def build_technical_narrative_prompt(signal: TechnicalSignal) -> str:
    """Prompt ورودی انسان برای LLM narrative."""
    payload = signal.to_dict()
    # JSON-like dump for stable prompt
    import json
    return (
        f"## Deterministic TechnicalSignal\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "Based ONLY on the structured signal above, produce your interpretation.\n"
        "Do not compute or modify any numeric value."
    )
