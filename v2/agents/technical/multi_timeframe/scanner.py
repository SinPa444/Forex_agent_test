"""
agents/technical/multi_timeframe/scanner.py
===========================================
اسکنر Multi-Timeframe — 100% deterministic، بدون LLM.

فاز ۷: به‌جای انتخاب دستی یک TF برای هر جفت‌ارز، موتور technical روی ۷ TF
اجرا می‌شود و خروجی یک «matrix» قابل‌تحلیل است.

Phase 1: imports از ماژول‌های جدید (engine/alignment) — منطق بدون تغییر.

Usage:
    from agents.technical.multi_timeframe import scanner as mtf
    m = mtf.scan_timeframes("EURUSD=X")
    print(m.strongest().timeframe)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..data.market_data import TIMEFRAME_MAP
from ..engine import calculate_technical_metrics
from ..models import TechnicalMetrics
from ..signals.technical_signal import DIRECTION_DEADBAND
from .alignment import compute_htf_bias

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

# TFهای پیش‌فرض اسکن (از بلند تا کوتاه)
DEFAULT_TIMEFRAMES = ["W1", "D1", "H4", "H2", "H1", "M30", "M15"]

# نقش هر TF در تحلیل (برای گزارش)
TIMEFRAME_ROLES: dict[str, str] = {
    "W1": "HTF - Context / bias",
    "D1": "HTF - Bias / key levels",
    "H4": "MTF - Setup structure",
    "H2": "MTF - Setup structure",
    "H1": "LTF - Entry timing",
    "M30": "LTF - Entry timing",
    "M15": "LTF - Entry trigger",
}


# ---------------------------------------------------------------------------
# مدل‌های داده
# ---------------------------------------------------------------------------

@dataclass
class TimeframeScore:
    """نتیجه‌ی technical روی یک تایم‌فریم."""
    timeframe: str
    direction: int        # 1 / -1 / 0 (از deadband یکپارچه)
    score: float          # [-1, 1]
    confidence: float     # [0, 1]
    metrics: TechnicalMetrics

    @property
    def label(self) -> str:
        names = {1: "BULLISH", -1: "BEARISH", 0: "NEUTRAL"}
        return f"{names.get(self.direction, 'UNKNOWN')} ({self.score:+.2f}, conf {self.confidence:.2f})"


@dataclass
class MTFMatrix:
    """جمع‌بندی اسکن MTF برای یک نماد."""
    ticker: str
    scores: list[TimeframeScore]
    htf_bias: int = 0
    htf_bias_label: str = "HTF Bias: Mixed"

    def get(self, tf: str) -> Optional[TimeframeScore]:
        for s in self.scores:
            if s.timeframe == tf:
                return s
        return None

    def valid_timeframes(self) -> list[str]:
        """
        TFهایی که score واقعی دارند (fetch + محاسبه موفق).

        Phase 1: اضافه شد — run_phase3 این متد را صدا می‌زد ولی تعریف نداشت
        (W9: AttributeError ساد‌ها کل MTF پایپ‌لاین را بی‌صدا می‌کشت).
        """
        return [s.timeframe for s in self.scores if s.metrics.technical_score is not None]

    def strongest(self) -> TimeframeScore:
        """TF با قوی‌ترین سیگنال: max |score| × confidence."""
        return max(self.scores, key=lambda s: abs(s.score) * s.confidence)

    def aligned(self) -> bool:
        """آیا HTF bias با LTF entry-timeframes (H1/M30/M15) هم‌جهت است؟"""
        if self.htf_bias == 0:
            return False
        ltf_dirs = [s.direction for s in self.scores if s.timeframe in ("H1", "M30", "M15")]
        if not ltf_dirs:
            return False
        return all(d == self.htf_bias for d in ltf_dirs if d != 0) and any(
            d == self.htf_bias for d in ltf_dirs
        )

    def summary(self) -> str:
        lines = [f"MTF Matrix for {self.ticker}"]
        lines.append(f"  HTF Bias: {self.htf_bias_label} (dir={self.htf_bias})")
        for s in self.scores:
            role = TIMEFRAME_ROLES.get(s.timeframe, "")
            lines.append(f"  {s.timeframe:<4} {s.label:<45} [{role}]")
        strong = self.strongest()
        lines.append(
            f"  Strongest: {strong.timeframe} {strong.label} | Aligned: {self.aligned()}"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# اسکن
# ---------------------------------------------------------------------------

def _score_one(ticker: str, tf: str) -> TimeframeScore:
    """اجرای موتور deterministic روی یک TF (fetch داخلی)."""
    interval = TIMEFRAME_MAP.get(tf)
    if not interval:
        raise ValueError(f"unknown timeframe: {tf}")

    metrics = calculate_technical_metrics(ticker, timeframe=tf)
    score = metrics.technical_score
    if score is None:
        return TimeframeScore(
            timeframe=tf, direction=0, score=0.0,
            confidence=metrics.technical_confidence or 0.0, metrics=metrics,
        )
    if score > DIRECTION_DEADBAND:
        direction = 1
    elif score < -DIRECTION_DEADBAND:
        direction = -1
    else:
        direction = 0
    return TimeframeScore(
        timeframe=tf, direction=direction, score=score,
        confidence=metrics.technical_confidence or 0.0, metrics=metrics,
    )


def scan_timeframes(
    ticker: str, timeframes: Optional[list[str]] = None
) -> MTFMatrix:
    """
    اسکن MTF: اجرای calculate_technical_metrics روی هر TF.

    deterministic و بدون LLM. هر TF که fetch/محاسبه‌اش ناموفق باشد
    با score=0 / confidence=0 ثبت می‌شود (سایر TFها ادامه دارند).
    """
    tfs = timeframes or DEFAULT_TIMEFRAMES
    results: list[TimeframeScore] = []

    for tf in tfs:
        try:
            results.append(_score_one(ticker, tf))
        except Exception as exc:
            logger.warning(f"[MTF] {ticker} {tf} scan failed: {exc}")
            results.append(
                TimeframeScore(
                    timeframe=tf, direction=0, score=0.0, confidence=0.0,
                    metrics=TechnicalMetrics(),
                )
            )

    scores_by_tf = {s.timeframe: s.score for s in results}
    htf_dir, htf_label = compute_htf_bias(scores_by_tf)

    return MTFMatrix(
        ticker=ticker,
        scores=results,
        htf_bias=htf_dir,
        htf_bias_label=htf_label,
    )


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Deterministic MTF scanner (no LLM)")
    parser.add_argument("ticker", nargs="?", default="EURUSD=X")
    parser.add_argument(
        "--tfs", default=",".join(DEFAULT_TIMEFRAMES),
        help="comma-separated timeframes",
    )
    args = parser.parse_args()

    tfs = [t.strip().upper() for t in args.tfs.split(",") if t.strip()]
    matrix = scan_timeframes(args.ticker, tfs)
    print(matrix.summary())


if __name__ == "__main__":
    main()
