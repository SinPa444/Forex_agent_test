"""
agents/technical/llm_guard.py
=============================
LLM Guard — مرز امنیتی برای خروجی LLM (الگوی LLMTradingDecision از AgenticTrading).

قاعده طراحی: LLM فقط «روایت» می‌سازد؛ امتیازها deterministic هستند. Guard
مطمئن می‌شود روایت LLM با سیگنال deterministic **تناقض آشکار** ندارد و
خروجی‌اش به شکل صحیح برگشته.

خروجی: GuardResult(ok, reasons) —
  ok=False  → وضعیت "guard_flagged" در گزارش + log warning
              (Phase 1: با یک retry؛ اگر باز هم شد، روایت deterministic جایگزین می‌شود)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- محدودیت‌های سخت (به‌جای magic numbers) ---
MAX_NARRATIVE_LEN = 500
MIN_NARRATIVE_LEN = 10

# کلمات کلیدی خلاف جهت (هرکدام در روایت = تناقض آشکار)
BULLISH_MARKERS = ("bullish", "buy", "long", "خرید", "صعودی")
BEARISH_MARKERS = ("bearish", "sell", "short", "فروش", "نزولی")


@dataclass
class GuardResult:
    ok: bool = True
    reasons: list[str] = field(default_factory=list)


def validate_narrative(strategy: str, reasoning: str, direction: int) -> GuardResult:
    """
    اعتبارسنجی روایت LLM در برابر سیگنال deterministic.
    direction: +1 (BULLISH) / -1 (BEARISH) / 0 (NEUTRAL)
    """
    result = GuardResult()
    strategy = (strategy or "").strip()
    reasoning = (reasoning or "").strip()

    if not strategy or not reasoning:
        result.ok = False
        result.reasons.append("empty_narrative")
        return result

    if len(strategy) > MAX_NARRATIVE_LEN:
        result.ok = False
        result.reasons.append("strategy_too_long")
    if len(reasoning) > MAX_NARRATIVE_LEN:
        result.ok = False
        result.reasons.append("reasoning_too_long")
    if len(strategy) < MIN_NARRATIVE_LEN or len(reasoning) < MIN_NARRATIVE_LEN:
        result.ok = False
        result.reasons.append("narrative_too_short")

    # --- تناقض جهت: متن روایت vs direction deterministic ---
    text = (strategy + " " + reasoning).lower()
    bullish_hits = sum(1 for m in BULLISH_MARKERS if m in text)
    bearish_hits = sum(1 for m in BEARISH_MARKERS if m in text)

    if direction == 1 and bearish_hits > bullish_hits:
        result.ok = False
        result.reasons.append("narrative_contradicts_bullish_signal")
    elif direction == -1 and bullish_hits > bearish_hits:
        result.ok = False
        result.reasons.append("narrative_contradicts_bearish_signal")

    return result
