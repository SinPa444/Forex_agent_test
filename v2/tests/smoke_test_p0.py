"""
tests/smoke_test_p0.py
======================
Smoke test برای مسير P0 `TechnicalAgent.analyze_structured`.

بدون شبکه و بدون LLM واقعی:
  - `_fetch_price_history` با DataFrame ساده با ۳۰۰ کندل صعودی جایگزین می‌شود.
  - LLM با `RunnableLambda` (خروجی "{}") جایگزین می‌شود؛ در نتیجه مسير
    LLM تا Pydantic parse اجرا می‌شود و اگر پارس نشود،
    `TechnicalInterpretation=None` برمی‌گردد، ولی سیگنال deterministic ساخته می‌شود.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from langchain_core.runnables import RunnableLambda

from agents.technical import technical_analyzer as ta_mod
from agents.technical.technical_analyzer import TechnicalAgent


def _synthetic_df() -> pd.DataFrame:
    n = 300
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = [100.0 + i * 0.05 for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c + 0.3 for c in close],
            "Low": [c - 0.3 for c in close],
            "Close": close,
            "Volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


def main() -> int:
    ta_mod._fetch_price_history = lambda ticker, interval="1d": _synthetic_df()
    agent = TechnicalAgent(llm=RunnableLambda(lambda inputs: "{}"))

    metrics, signal, interp = agent.analyze_structured(
        "EURUSD=X",
        timeframe="D1",
        temporal_context=None,
        mtf_matrix=None,
        mtf_scores_roles=None,
    )

    print("=== P0 Smoke Test: analyze_structured ===")
    print(f"metrics.score       = {metrics.technical_score}")
    print(f"metrics.recent_bos  = {metrics.recent_bos}")
    print(f"metrics.recent_choch= {metrics.recent_choch}")
    print(f"signal.symbol       = {signal.symbol}")
    print(f"signal.direction    = {signal.direction}")
    print(f"signal.direction_v  = {signal.direction_value}")
    print(f"signal.score        = {signal.score}")
    print(f"signal.confidence   = {signal.confidence}")
    print(f"signal.regime       = {signal.market_regime}")
    print(f"signal.trend        = {signal.trend}")
    print(f"signal.structure    = {signal.market_structure}")
    print(f"signal.smc_context  = {signal.smc_context}")
    print(f"signal.volatility   = {signal.volatility}")
    print(f"signal.reasoning    = {signal.reasoning_summary}")
    print(f"signal.evidence     = {len(signal.supporting_evidence)}")
    print(f"signal.risks        = {len(signal.risks)}")
    print(f"interp (LLM)        = {interp}")
    print("=== SMOKE OK ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
