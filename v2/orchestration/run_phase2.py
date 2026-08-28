"""
run_phase2.py
=============
Phase 2 Live Orchestrator: Executes in-memory LangGraph pipelines.
Handles per-currency analysis, followed by cross-asset consistency validation.
"""

import argparse
import logging
import os
import sys

from core.database import init_db
from agents.fundamental.phase2_graph import build_phase2_graph, build_cross_asset_graph, get_temporal_context

logger = logging.getLogger('run_phase2')


def build_llm(provider: str, model: str):
    if provider == "arvan":
        from langchain_openai import ChatOpenAI
        base_url = os.getenv("ARVAN_BASE_URL")
        api_key = os.getenv("ARVAN_API_KEY", "not-needed")
        if not base_url:
            raise ValueError("ARVAN_BASE_URL not set in environment.")
        return ChatOpenAI(model=model or "GLM-5.2", temperature=0.1, api_key=api_key, base_url=base_url)
    
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set.")
        return ChatOpenAI(model=model or "openai/gpt-4o-mini", temperature=0.1, api_key=api_key, base_url="https://openrouter.ai/api/v1")
    
    raise ValueError(f"Unsupported provider: {provider}")

def main():
    parser = argparse.ArgumentParser(description="Run Live Phase 2 Graph")
    parser.add_argument("--currencies", nargs="+", default=["USD"], help="Target currency")
    parser.add_argument("--llm-provider", default="arvan", choices=["arvan", "openrouter"])
    parser.add_argument("--llm-model", default="")
    
    parser.add_argument("--no-events", action="store_true", help="Skip event analysis")
    parser.add_argument("--no-news", action="store_true", help="Skip news analysis")
    parser.add_argument("--no-speaker", action="store_true", help="Skip speaker analysis")
    
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    init_db()
    llm = build_llm(args.llm_provider, args.llm_model)
    
    currency_app = build_phase2_graph()
    cross_asset_app = build_cross_asset_graph()

    temporal_ctx = get_temporal_context()
    logger.info(f"Temporal Context: {temporal_ctx['day_of_week']}, {temporal_ctx['market_session']} | Weekend: {temporal_ctx['is_weekend']}")

    composite_signals = {}

    print("\n" + "=" * 70)
    print(f"🚀 PHASE 2 EXECUTING FOR CURRENCIES: {', '.join(args.currencies)}")
    print("=" * 70)

    # Stage 1: Run per-currency graphs
    for ccy in args.currencies:
        print(f"\n--- Analyzing {ccy} ---")
        state_input = {
            "currency": ccy,
            "llm": llm,
            "temporal_context": temporal_ctx,
            
            "enable_events": not args.no_events,
            "enable_news": not args.no_news,
            "enable_speaker": not args.no_speaker
        }

        try:
            result = currency_app.invoke(state_input, {"recursion_limit": 15})
            comp = result.get("composite_signal")
            if comp:
                composite_signals[ccy] = comp
                print(f"  [{ccy}] Initial Status: {comp.confluence_status} | Dir: {comp.direction} | Score: {comp.final_score:+.2f} | Tradable: {comp.is_tradable}")
        except Exception as exc:
            logger.error(f"Failed to process {ccy}: {exc}")

    if not composite_signals:
        print("\nNo signals generated. Exiting.")
        return

    # Stage 2: Run Cross-Asset Consistency Graph
    print("\n" + "=" * 70)
    print("🌐 EXECUTING CROSS-ASSET CONSISTENCY & GLOBAL SUMMARY")
    print("=" * 70)

    global_state_input = {
        "llm": llm,
        "temporal_context": temporal_ctx,
        "composite_signals": composite_signals
    }

    final_result = cross_asset_app.invoke(global_state_input)
    final_signals = final_result.get("composite_signals", composite_signals)
    detailed_reports = final_result.get("detailed_reports", {})
    global_report = final_result.get("global_report", "No report.")

    # Print final adjusted signals
    print("\n" + "-" * 70)
    print("🎯 FINAL ADJUSTED SIGNALS")
    print("-" * 70)
    for ccy, sig in final_signals.items():
        print(
            f"  [{ccy}] Dir: {sig.direction} | Score: {sig.final_score:+.2f} | "
            f"Conf: {sig.confidence:.2f} | Tradable: {sig.is_tradable}"
        )
        if "Cross-asset" in sig.reasoning:
            print(f"       ⚠️ Note: {sig.reasoning.split('Cross-asset')[-1].strip()}")
            
    # Print detailed macro-aware reports per currency
    print("\n" + "-" * 70)
    print("📝 DETAILED CURRENCY ANALYSES (Macro-Aware)")
    print("-" * 70)
    for ccy, report in detailed_reports.items():
        print(f"\n### {ccy} Analysis")
        print(report)
        print("-" * 40)

    # Print global summary
    print("\n" + "-" * 70)
    print("📊 GLOBAL MACRO EXECUTIVE SUMMARY")
    print("-" * 70)
    print(global_report)
    print("=" * 70)

if __name__ == "__main__":
    sys.exit(main())