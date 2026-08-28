"""
agents/technical/mtf_scanner.py
================================
Phase 7 — لایه اسکن چند تایم‌فریمی (کاملاً deterministic، صفر کال LLM).

هفت تایم‌فریم را با همان calculate_technical_metrics موجود امتیاز می‌کند و
یک MTFMatrix می‌سازد: جهت هر تایم‌فریم + bias کلی از لایه HTF.

نقش‌ها (top-down کلاسیک):
  HTF (W1, D1)      → جهت مجاز (bias)
  MTF (H4, H2)      → ساختار و زون‌ها
  LTF (H1, M30, M15)→ تریگر و اجرا

هزینه: فقط دانلود yfinance. هیچ LLMای در این فایل وجود ندارد.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from agents.technical.technical_analyzer import calculate_technical_metrics

logger = logging.getLogger(__name__)

# ===========================================================================
# CONFIG
# ===========================================================================

DEFAULT_TIMEFRAMES: list[str] = ["W1", "D1", "H4", "H2", "H1", "M30", "M15"]

TIMEFRAME_ROLES: dict[str, str] = {
    "W1": "HTF", "D1": "HTF",
    "H4": "MTF", "H2": "MTF",
    "H1": "LTF", "M30": "LTF", "M15": "LTF",
}

# |score| کمتر از این یعنی خنثی
DIRECTION_DEADBAND: float = 0.15

# وزن D1 و W1 در bias کلی
HTF_WEIGHTS: dict[str, float] = {"D1": 0.6, "W1": 0.4}


# ===========================================================================
# Models
# ===========================================================================

@dataclass
class TimeframeScore:
    """نتیجه یک تایم‌فریم."""
    timeframe: str
    role: str
    valid: bool = False
    direction: int = 0                    # 1 / -1 / 0
    score: float = 0.0                    # [-1, 1]
    confidence: float = 0.0               # [0, 1]
    price: Optional[float] = None
    nearest_support: Optional[float] = None      # OB بولیش فعال
    nearest_resistance: Optional[float] = None   # OB بیرش فعال
    adx: Optional[float] = None
    chop: Optional[float] = None
    trend_status: Optional[str] = None
    error: Optional[str] = None


@dataclass
class MTFMatrix:
    """تصویر کامل یک نماد روی همه تایم‌فریم‌ها."""
    ticker: str
    computed_at: str
    scores: dict[str, TimeframeScore] = field(default_factory=dict)
    htf_bias: int = 0                     # 1=صعودی، -1=نزولی، 0=خنثی/متضاد
    htf_bias_label: str = "Neutral"

    def valid_timeframes(self) -> list[str]:
        return [tf for tf, s in self.scores.items() if s.valid]

    def strongest(self, roles: tuple[str, ...] = ("MTF", "LTF")) -> Optional[TimeframeScore]:
        """قوی‌ترین سیگنال معتبر در نقش‌های داده‌شده (برای narrative LLM)."""
        candidates = [s for s in self.scores.values()
                      if s.valid and s.role in roles and s.direction != 0]
        if not candidates:
            return None
        return max(candidates, key=lambda s: abs(s.score) * s.confidence)


# ===========================================================================
# Core
# ===========================================================================

def _score_one(ticker: str, timeframe: str) -> TimeframeScore:
    role = TIMEFRAME_ROLES[timeframe]
    try:
        m = calculate_technical_metrics(ticker, timeframe=timeframe)
    except Exception as exc:
        logger.warning(f"[MTF] {ticker} {timeframe} failed: {exc}")
        return TimeframeScore(timeframe=timeframe, role=role, error=str(exc))

    if m is None or m.current_price is None or m.technical_score is None:
        return TimeframeScore(timeframe=timeframe, role=role, error="no data")

    score = float(m.technical_score)
    direction = 1 if score > DIRECTION_DEADBAND else (-1 if score < -DIRECTION_DEADBAND else 0)

    return TimeframeScore(
        timeframe=timeframe,
        role=role,
        valid=True,
        direction=direction,
        score=round(score, 4),
        confidence=round(float(m.technical_confidence or 0.0), 4),
        price=float(m.current_price),
        nearest_support=m.active_bullish_ob,
        nearest_resistance=m.active_bearish_ob,
        adx=m.adx_value,
        chop=m.chop_value,
        trend_status=m.trend_status,
    )


def _compute_htf_bias(scores: dict[str, TimeframeScore]) -> tuple[int, str]:
    """
    bias کلی از لایه HTF:
      - D1 و W1 معتبر و هم‌جهت (غیرصفر) → همان جهت
      - متضاد → خنثی (مixed)
      - فقط یکی معتبر → همان یکی
    """
    d1 = scores.get("D1")
    w1 = scores.get("W1")

    dirs = []
    if d1 and d1.valid:
        dirs.append(("D1", d1.direction, HTF_WEIGHTS["D1"]))
    if w1 and w1.valid:
        dirs.append(("W1", w1.direction, HTF_WEIGHTS["W1"]))

    if not dirs:
        return 0, "Neutral (no HTF data)"

    nonzero = [(name, d, w) for name, d, w in dirs if d != 0]
    if len(nonzero) == 2 and nonzero[0][1] != nonzero[1][1]:
        return 0, f"Mixed (D1={nonzero[0][1]:+d} vs W1={nonzero[1][1]:+d})"

    if nonzero:
        d = nonzero[0][1]
        label = "Bullish" if d == 1 else "Bearish"
        names = "+".join(n for n, _, _ in nonzero)
        return d, f"{label} ({names})"

    return 0, "Neutral"


def scan_timeframes(
    ticker: str,
    timeframes: Optional[list[str]] = None,
) -> MTFMatrix:
    """
    اسکن کامل همه تایم‌فریم‌ها برای یک تیکر. صفر کال LLM.
    تایم‌فریم‌های ناموفق skip می‌شوند و ماتریس با بقیه ساخته می‌شود.
    """
    tfs = timeframes or DEFAULT_TIMEFRAMES
    matrix = MTFMatrix(ticker=ticker, computed_at=datetime.utcnow().isoformat())

    for tf in tfs:
        matrix.scores[tf] = _score_one(ticker, tf)
        s = matrix.scores[tf]
        if s.valid:
            logger.info(
                f"[MTF] {ticker} {tf}: dir={s.direction:+d} score={s.score:+.2f} "
                f"conf={s.confidence:.2f}"
            )
        else:
            logger.info(f"[MTF] {ticker} {tf}: invalid ({s.error})")

    matrix.htf_bias, matrix.htf_bias_label = _compute_htf_bias(matrix.scores)
    logger.info(f"[MTF] {ticker} HTF bias: {matrix.htf_bias_label}")
    return matrix


# ===========================================================================
# CLI Smoke Test
# ===========================================================================
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")

    parser = argparse.ArgumentParser(description="MTF Scanner smoke test (no LLM)")
    parser.add_argument("--ticker", default="EURUSD=X")
    parser.add_argument("--timeframes", nargs="+", default=None)
    args = parser.parse_args()

    m = scan_timeframes(args.ticker, args.timeframes)
    print("\n" + "=" * 56)
    print(f"MTF Matrix: {args.ticker} | HTF bias: {m.htf_bias_label}")
    print("=" * 56)
    for tf, s in m.scores.items():
        if s.valid:
            print(f"  {tf:>4} [{s.role}] dir={s.direction:+d} score={s.score:+.2f} "
                  f"conf={s.confidence:.2f} S={s.nearest_support} R={s.nearest_resistance}")
        else:
            print(f"  {tf:>4} [{s.role}] INVALID — {s.error}")
