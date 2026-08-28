"""
orchestration/run_phase3.py
===========================
Multi-Agent Orchestrator (Phase 3).

Executes the Head Agent to get an execution plan, then runs the active agents
(Fundamental, Technical, Risk) accordingly for each currency.
"""

import argparse
import logging
import os
import sys
import time
import datetime as dt
import hashlib

from core.database import init_db, TradeOutcomeDB, StrategyPlanDB
from agents.fundamental.phase2_graph import build_phase2_graph, build_cross_asset_graph, get_temporal_context
from agents.technical.technical_analyzer import TechnicalAgent, TechnicalReport
from agents.technical.mtf_scanner import scan_timeframes
from agents.risk.risk_manager import (
    RiskManagerAgent, RiskDecision, TradePlan,
    build_strategy_sheet, render_strategy_sheet_text,
    StrategySheet, StrategyPlan,
)
from agents.supervisor.head_agent import HeadAgent
from core.routing import resolve_asset_route
from ingestion import ff_bridge
from ingestion.rss_feed_loader import FeedLoader
from core.database import (
    session_scope, insert_raw_news_item_if_new, get_trade_memory_stats,
    get_recent_losing_trades,
)

logger = logging.getLogger('run_phase3')


def _format_plan_line(p: StrategyPlan) -> str:
    """Format a single strategy plan as a compact actionable line."""
    direction_str = "LONG" if p.direction == 1 else "SHORT"
    tag = " (COUNTER-BIAS, ½ size)" if p.counter_bias else ""
    if p.status == "ACTIVE":
        return (f"  [{p.horizon_label:>8} {p.timeframe}] {direction_str} NOW @ {p.entry_high:.5f} | "
                f"SL {p.stop_loss:.5f} | TP {p.take_profit:.5f} | R:R 1:{p.rr_ratio:.2f}{tag}")
    elif p.status == "PENDING":
        return (f"  [{p.horizon_label:>8} {p.timeframe}] {direction_str} IF price enters "
                f"[{p.entry_low:.5f} - {p.entry_high:.5f}] | "
                f"SL {p.stop_loss:.5f} | TP {p.take_profit:.5f} | R:R 1:{p.rr_ratio:.2f} | "
                f"invalidate {p.invalidation_price:.5f}{tag}")
    else:
        return f"  [{p.horizon_label:>8} {p.timeframe}] INVALID — {p.reasoning}"


def print_final_execution_summary(currency: str, strategy_sheet: StrategySheet) -> None:
    """Print a clean, actionable final summary for one currency."""
    if not strategy_sheet:
        print(f"\n[{currency}] No strategy sheet generated.")
        return

    print(f"\n{'=' * 72}")
    print(f"📋 EXECUTION SUMMARY: {currency} | HTF Bias: {strategy_sheet.htf_bias_label}")
    print(f"{'=' * 72}")

    actives = strategy_sheet.active_plans()
    pendings = strategy_sheet.pending_plans()
    invalids = [p for p in strategy_sheet.plans if p.status == "INVALID"]

    if actives:
        print("✅ ACTIVE — Enter NOW:")
        for p in actives:
            print(_format_plan_line(p))

    if pendings:
        print("⏳ PENDING — Conditional entry:")
        for p in pendings:
            print(_format_plan_line(p))

    if invalids:
        print("❌ INVALID — No trade:")
        for p in invalids:
            print(f"  [{p.horizon_label:>8} {p.timeframe}] {p.reasoning}")

    if not actives and not pendings:
        print("  No actionable plans — stay flat.")

    print(f"{'=' * 72}")


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


def run_ingestion_phase():
    """Phase 0: Crawl events and news to update DB before Head Agent decides."""
    logger.info("🚀 PHASE 0: Running Data Ingestion (No LLM)...")
    
    # 1. Forex Factory Events (Current Week: Sunday to Saturday) with Retry
    today = dt.datetime.utcnow()
    day_since_sunday = (today.weekday() + 1) % 7
    start_date = today - dt.timedelta(days=day_since_sunday)
    end_date = start_date + dt.timedelta(days=6)
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"[Ingestion] Scraping FF calender (Attempt {attempt}/{max_retries}): {start_date.date()} to {end_date.date()}...")
            ff_bridge.fetch_and_store_ff_data(start_date, end_date)
            break  # Success, exit retry loop
        except Exception as e:
            logger.error(f"[Ingestion] FF Bridge attempt {attempt} failed: {e}")
            if attempt < max_retries - 1:
                logger.info("[Ingestion] Retrying in 5 seconds...")
                time.sleep(5)
            else:
                logger.critical("[Ingestion] FF Bridge failed after all retries. Proceeding with existing DB data.")

    # 2. RSS News Feeds
    try:
        logger.info("[Ingestion] Loading RSS feeds...")
        loader = FeedLoader()
        feed_result = loader.load_all_collect()
        
        new_items = 0
        dup_items = 0
        
        with session_scope() as session:
            for item in feed_result.items:
                basis = (item.link or "") + "|" + item.title + "|" + item.source
                h = hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()
                
                fields = {
                    "title": item.title,
                    "summary": item.summary,
                    "link": item.link,
                    "source": item.source,
                    "published_at": item.published,
                    "currency": item.currency,
                    "source_reliability": item.source_reliability,
                    "category": item.category,
                    "impact": item.impact
                }
                
                _, created = insert_raw_news_item_if_new(session, dedup_hash=h, **fields)
                if created:
                    new_items += 1
                else:
                    dup_items += 1
                    
        logger.info(f"[Ingestion] RSS News saved: {new_items} new, {dup_items} duplicates.")
    except Exception as e:
        logger.error(f"[Ingestion] RSS Loader failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Run Multi-Agent Phase 3 System")
    parser.add_argument("--currencies", nargs="+", default=["USD"], help="Target currency")
    parser.add_argument("--llm-provider", default="arvan", choices=["arvan", "openrouter"])
    parser.add_argument("--llm-model", default="")
    # --- Phase 5: Token optimization flags (همه به‌صورت پیش‌فرض اقتصادی) ---
    parser.add_argument("--reports", action="store_true",
                        help="Enable detailed per-currency LLM reports (default: OFF — ~30%% of token cost)")
    parser.add_argument("--head-llm", action="store_true",
                        help="Use LLM for Head Agent planning (default: OFF — rule-based planner)")
    parser.add_argument("--risk-llm", action="store_true",
                        help="Use LLM for Risk Manager decisions (default: OFF — rule-based confluence rules)")
    parser.add_argument("--no-digest-cache", action="store_true",
                        help="Disable news digest caching (force fresh LLM digest analysis)")
    parser.add_argument("--skip-ingestion", action="store_true",
                        help="Skip Phase 0 ingestion (used by live_watcher which already ingested)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    init_db()
    
    if args.skip_ingestion:
        logger.info("Phase 0 ingestion skipped (--skip-ingestion, already done by watcher).")
    else:
        run_ingestion_phase()
    
    llm = build_llm(args.llm_provider, args.llm_model)
    
    # Initialize Agents
    fund_app = build_phase2_graph()
    cross_asset_app = build_cross_asset_graph()
    tech_agent = TechnicalAgent(llm)
    risk_agent = RiskManagerAgent(llm)
    head_agent = HeadAgent(llm)

    temporal_ctx = get_temporal_context()
    logger.info(f"Temporal Context: {temporal_ctx['day_of_week']}, {temporal_ctx['market_session']} | Weekend: {temporal_ctx['is_weekend']}")

    composite_signals = {}
    strategy_sheets: dict[str, StrategySheet] = {}

    print("\n" + "=" * 70)
    print(f"🚀 PHASE 3 MULTI-AGENT EXECUTING FOR: {', '.join(args.currencies)}")
    print(f"💰 Token-optimized mode | Reports: {'ON' if args.reports else 'OFF'} | "
          f"Head LLM: {'ON' if args.head_llm else 'rule-based'} | "
          f"Risk LLM: {'ON' if args.risk_llm else 'rule-based'} | "
          f"Digest cache: {'OFF' if args.no_digest_cache else 'ON'}")
    print("=" * 70)

    # Stage 1: Per-Currency Multi-Agent Loop
    for ccy in args.currencies:
        print(f"\n--- Analyzing {ccy} ---")
        route = resolve_asset_route(ccy)
        if not route:
            logger.warning(f"[{ccy}] No route found. Skipping.")
            continue
            
        # 1. Head Agent Creates Plan (Phase 5: پیش‌فرض rule-based؛ با --head-llm کال LLM)
        logger.info(f"[{ccy}] Consulting Head Agent...")
        plan = head_agent.create_plan(ccy, route.ticker, use_llm=True if args.head_llm else None)
        plan.activate_speakers = False  # Force disable due to offline pipeline
        logger.info(f"[{ccy}] Plan: Events={plan.activate_events}, News={plan.activate_news}, Speakers={plan.activate_speakers}, Tech={plan.activate_technical} ({plan.technical_timeframe})")
        
        fund_sig = None
        tech_metrics, tech_report = None, None
        
                # 2. Fundamental Agent (if activated by any sub-flag)
        run_fund = plan.activate_events or plan.activate_news or plan.activate_speakers
        fund_sig = None
        
        if run_fund:
            logger.info(f"[{ccy}] Running Fundamental Agent (Events={plan.activate_events}, News={plan.activate_news})...")
            state_input = {
                "currency": ccy,
                "llm": llm,
                "temporal_context": temporal_ctx,
                "enable_events": plan.activate_events,
                "enable_news": plan.activate_news,
                "enable_speakers": plan.activate_speakers,
                "use_digest_cache": not args.no_digest_cache,
            }
            try:
                fund_result = fund_app.invoke(state_input, {"recursion_limit": 15})
                fund_sig = fund_result.get("composite_signal")
                if fund_sig:
                    logger.info(f"[{ccy}] Fundamental Status: {fund_sig.confluence_status} | Dir: {fund_sig.direction}")
            except Exception as exc:
                logger.error(f"[{ccy}] Fundamental pipeline failed: {exc}")
        else:
            logger.info(f"[{ccy}] Fundamental Agent fully skipped by Head Agent.")
            
        # 3. Technical Agent — Phase 7: اسکن چند تایم‌فریمی deterministic + narrative فقط برای قوی‌ترین TF
        tech_metrics, tech_report, tech_signal, mtf_matrix = None, None, None, None
        if plan.activate_technical:
            logger.info(f"[{ccy}] Running MTF scan (W1/D1/H4/H2/H1/M30/M15 — zero LLM)...")
            try:
                mtf_matrix = scan_timeframes(route.ticker)
                valid_tfs = mtf_matrix.valid_timeframes()
                logger.info(f"[{ccy}] MTF: {len(valid_tfs)}/7 timeframes valid | HTF bias: {mtf_matrix.htf_bias_label}")

                # narrative LLM فقط برای قوی‌ترین تایم‌فریم (هزینه ثابت: ۱ کال)
                strongest = mtf_matrix.strongest()
                tech_signal = None
                if strongest is not None:
                    logger.info(f"[{ccy}] LLM narrative for strongest TF: {strongest.timeframe} (score {strongest.score:+.2f})")
                    # P0: خروجی ساخت‌یافته TechnicalSignal + تفسیر LLM
                    tech_metrics, tech_signal, tech_interp = tech_agent.analyze_structured(
                        route.ticker, timeframe=strongest.timeframe,
                        temporal_context=temporal_ctx,
                        mtf_matrix=mtf_matrix,
                        mtf_scores_roles={tf: s.role for tf, s in mtf_matrix.scores.items()},
                    )
                    # برای سازگاری با مسیر legacy، از TechnicalSignal یک TechnicalReport می‌سازیم
                    if tech_signal is not None:
                        strategy_text = (
                            tech_interp.interpretation if tech_interp else tech_signal.reasoning_summary
                        )
                        reason_text = (
                            tech_interp.narrative if tech_interp else tech_signal.reasoning_summary
                        )
                        tech_report = TechnicalReport(
                            direction=tech_signal.direction_value,
                            score=tech_signal.score,
                            confidence=tech_signal.confidence,
                            strategy=strategy_text,
                            reasoning=reason_text,
                        )
                    logger.info(f"[{ccy}] Technical ({strongest.timeframe}) Dir: {tech_report.direction if tech_report else 0} "
                                f"| Score: {tech_report.score if tech_report else 0:.2f}")
            except Exception as exc:
                logger.error(f"[{ccy}] MTF/technical pipeline failed: {exc}")
                
        # 4. Risk Manager Agent — Phase 7: Strategy Engine (پیش‌فرض) یا مسیر LLM legacy
        strategy_sheet = None
        active_plan_ids: dict = {}
        if fund_sig or mtf_matrix or tech_report:
            logger.info(f"[{ccy}] Running Risk Manager...")

            # Determine statuses explicitly to avoid 0 confusion
            fund_status = "not_evaluated"
            fund_dir = 0
            fund_score_val = 0.0
            fund_conf = 0.0
            fund_tradable = False
            fund_reason = "Fundamental analysis not run."

            if fund_sig:
                fund_status = "evaluated"
                fund_dir = fund_sig.direction
                fund_score_val = fund_sig.final_score
                fund_conf = fund_sig.confidence
                fund_tradable = fund_sig.is_tradable
                fund_reason = fund_sig.reasoning

            # fetch Trade Memory (آمار + حافظه معنایی)
            mem_stats = get_trade_memory_stats(ccy)
            recent_losses = get_recent_losing_trades(ccy, limit=3)
            # win_rate به‌صورت عدد 0-100 ذخیره می‌شود (نه کسر 0-1)؛ فرمت :.1f درست است
            logger.info(f"[{ccy}] Memory Stats: {mem_stats['wins']}W / {mem_stats['losses']}L (Win Rate: {mem_stats['win_rate']:.1f}%) | recent losses loaded: {len(recent_losses)}")

            if args.risk_llm or mtf_matrix is None:
                # مسیر legacy: تصمیم تکی (LLM با --risk-llm، یا rule-based قدیمی وقتی MTF در دسترس نیست)
                risk_decision = risk_agent.evaluate(
                    fund_direction=fund_dir,
                    fund_score=fund_score_val,
                    fund_confidence=fund_conf,
                    fund_tradable=fund_tradable,
                    fund_reasoning=fund_reason,
                    tech_direction=tech_report.direction if tech_report else 0,
                    tech_confidence=tech_report.confidence if tech_report else 0.0,
                    tech_strategy=tech_report.strategy if tech_report else "No tech data",
                    tech_reasoning=tech_report.reasoning if tech_report else "Technical analysis not run.",
                    current_price=tech_metrics.current_price if tech_metrics else None,
                    nearest_support=tech_metrics.active_bullish_ob if tech_metrics else None,
                    nearest_resistance=tech_metrics.active_bearish_ob if tech_metrics else None,
                    mem_total=mem_stats['total_trades'],
                    mem_wins=mem_stats['wins'],
                    mem_losses=mem_stats['losses'],
                    mem_win_rate=mem_stats['win_rate'],
                    use_llm=True if args.risk_llm else None,
                )
                # حتی در مسیر legacy هم شیت می‌سازیم (خالی) تا در جمع‌بندی نهایی نمایش داده شود
                strategy_sheet = build_strategy_sheet(
                    ccy, mtf_matrix,
                    mem_total=mem_stats['total_trades'], mem_wins=mem_stats['wins'],
                    mem_losses=mem_stats['losses'], mem_win_rate=mem_stats['win_rate'],
                    recent_losses=recent_losses,
                    fund_dir=fund_dir, fund_score=fund_score_val,
                )
            else:
                # مسیر Phase 7: برگه استراتژی چند-افقی deterministic
                # وتوی فاندامنتال: جهت/امتیاز فاندامنتال به موتور پاس می‌شود تا
                # پلنِ مخالف جهت فاندامنتال رژیم ضدبایاس سخت بگیرد
                strategy_sheet = build_strategy_sheet(
                    ccy, mtf_matrix,
                    mem_total=mem_stats['total_trades'], mem_wins=mem_stats['wins'],
                    mem_losses=mem_stats['losses'], mem_win_rate=mem_stats['win_rate'],
                    recent_losses=recent_losses,
                    fund_dir=fund_dir, fund_score=fund_score_val,
                )
                print("\n" + render_strategy_sheet_text(strategy_sheet) + "\n")

            # ذخیره شیت برای جمع‌بندی نهایی
            strategy_sheets[ccy] = strategy_sheet

            # ثبت پلن‌ها در strategy_plans (watcher پلن‌های PENDING را پایش می‌کند)
            try:
                now_utc = dt.datetime.utcnow()
                with session_scope() as session_db:
                    for p in strategy_sheet.plans:
                        if p.status == "INVALID":
                            continue
                        row = StrategyPlanDB(
                            currency=ccy, timeframe=p.timeframe, horizon_role=p.horizon_role,
                            direction=p.direction, status=p.status,
                            entry_low=p.entry_low, entry_high=p.entry_high,
                            stop_loss=p.stop_loss, take_profit=p.take_profit,
                            rr_ratio=p.rr_ratio, invalidation_price=p.invalidation_price,
                            reasoning=p.reasoning, counter_bias=1 if p.counter_bias else 0,
                            expires_at=now_utc + dt.timedelta(hours=p.ttl_hours),
                        )
                        session_db.add(row)
                        session_db.flush()  # برای گرفتن id
                        if p.status == "ACTIVE":
                            active_plan_ids[p.horizon_label] = row.id
                logger.info(
                    f"[{ccy}] Strategy plans saved: "
                    f"{len(strategy_sheet.active_plans())} ACTIVE, {len(strategy_sheet.pending_plans())} PENDING"
                )
            except Exception as db_exc:
                logger.error(f"[{ccy}] Failed to save strategy plans: {db_exc}")

            # استخراج تصمیم نهایی برای composite از روی برگه
            actives = strategy_sheet.active_plans()
            if actives:
                best = max(actives, key=lambda p: p.rr_ratio or 0.0)
                risk_decision = RiskDecision(
                    decision="APPROVED",
                    reasoning=(f"ACTIVE {best.horizon_label} plan on {best.timeframe}: {best.reasoning}"),
                    trade_plan=TradePlan(
                        entry_zone=f"{best.entry_high} (Market)",
                        stop_loss=best.stop_loss,
                        take_profit=best.take_profit,
                        risk_reward_ratio=f"1:{best.rr_ratio}",
                    ),
                )
            elif strategy_sheet.pending_plans():
                risk_decision = RiskDecision(
                    decision="WAIT",
                    reasoning=(f"{len(strategy_sheet.pending_plans())} conditional plan(s) armed and "
                               "registered for watcher zone-hit monitoring. No immediate entry."),
                    trade_plan=None,
                )
            else:
                risk_decision = RiskDecision(
                    decision="REJECTED",
                    reasoning="All horizons invalid (no edge or poor geometry across timeframes).",
                    trade_plan=None,
                )

            logger.info(f"[{ccy}] Risk Decision: {risk_decision.decision}")

            # Build Final Composite for Cross-Asset Graph
            reasoning_parts = []
            if fund_sig:
                reasoning_parts.append(f"[Fundamental]: {fund_sig.reasoning}")
            if tech_report:
                reasoning_parts.append(f"[Technical]: {tech_report.reasoning}")
                if tech_signal is not None:
                    reasoning_parts.append(
                        f"[TechnicalSignal]: regime={tech_signal.market_regime}, "
                        f"structure={tech_signal.market_structure}, "
                        f"mtf_alignment={tech_signal.mtf_alignment}, "
                        f"risks={'; '.join(tech_signal.risks[:3])}"
                    )
            reasoning_parts.append(f"[Risk Manager ({risk_decision.decision})]: {risk_decision.reasoning}")
            if risk_decision.trade_plan:
                tp = risk_decision.trade_plan
                reasoning_parts.append(f"[Trade Plan]: Entry={tp.entry_zone}, SL={tp.stop_loss}, TP={tp.take_profit}")

            # Determine Final Direction and Score
            final_dir = tech_report.direction if tech_report else (fund_sig.direction if fund_sig else 0)
            # If fund is evaluated, use its score. If not, use tech score.
            final_score = fund_score_val if fund_status == "evaluated" else (tech_report.score if tech_report else 0.0)

            is_tradable = risk_decision.decision == "APPROVED"

            # register trade in memory DB if approved
            if is_tradable and risk_decision.trade_plan:
                try:
                    # extract numeric values from entry zone string
                    # فرمت‌های پشتیبانی‌شده: "1.1500-1.1510" یا "1.15400 (Market)" (خروجی rule-based)
                    import re as _re
                    entry_str = risk_decision.trade_plan.entry_zone
                    entry_nums = _re.findall(r"\d+\.?\d*", entry_str)
                    entry_low = float(entry_nums[0]) if entry_nums else None
                    entry_high = float(entry_nums[1]) if len(entry_nums) > 1 else entry_low

                    session = temporal_ctx.get('market_session', "Unknown")

                    # Phase 7: متادیتای افق و لینک به پلن مبدا
                    active_tf, active_plan_id, active_reason = None, None, None
                    if strategy_sheet and strategy_sheet.active_plans():
                        best = max(strategy_sheet.active_plans(), key=lambda p: p.rr_ratio or 0.0)
                        active_tf = best.timeframe
                        active_plan_id = active_plan_ids.get(best.horizon_label)
                        active_reason = f"[{best.horizon_label}/{best.timeframe}] {best.reasoning}"

                    with session_scope() as session_db:
                        new_trade = TradeOutcomeDB(
                            currency=ccy,
                            direction=final_dir,
                            entry_zone_low=entry_low,
                            entry_zone_high=entry_high,
                            stop_loss=risk_decision.trade_plan.stop_loss,
                            take_profit=risk_decision.trade_plan.take_profit,
                            fusion_state="tech_driven" if not fund_sig else "aligned",
                            fundamental_score=fund_score_val if fund_status == "evaluated" else None,
                            technical_score=tech_report.score if tech_report else None,
                            temporal_session=session,
                            status="PENDING",
                            timeframe=active_tf,
                            plan_id=active_plan_id,
                            decision_reasoning=active_reason or risk_decision.reasoning,
                        )
                        session_db.add(new_trade)
                    logger.info(f"[{ccy}] Trade registered in Memory DB for tracking.")
                except Exception as db_exc:
                    logger.info(f"[{ccy}] Failed to register trade in Memory DB: {db_exc}")
            
            # Create/Update Composite Signal
            if not fund_sig:
                from agents.fundamental.phase2_graph import CompositeSignal
                fund_sig = CompositeSignal(
                    currency=ccy, direction=final_dir, final_score=final_score, confidence=fund_conf,
                    is_tradable=is_tradable, confluence_status="tech_driven", components_used=["technical"], 
                    reasoning=" | ".join(reasoning_parts)
                )
            else: 
                fund_sig = fund_sig.model_copy(update={
                    "is_tradable": is_tradable,
                    "reasoning": " | ".join(reasoning_parts)
                })
                
            composite_signals[ccy] = fund_sig
            print(f"  [{ccy}] Final Status: Tradable={is_tradable} | Dir: {final_dir} | Score: {final_score:+.2f}")

    if not composite_signals:
        print("\nNo signals generated. Exiting.")
        return

    # Stage 2: Cross-Asset Consistency Graph & Global Summary
    print("\n" + "=" * 70)
    print("🌐 EXECUTING CROSS-ASSET CONSISTENCY & GLOBAL SUMMARY")
    print("=" * 70)

    global_state_input = {
        "llm": llm,
        "temporal_context": temporal_ctx,
        "composite_signals": composite_signals,
        "enable_reports": args.reports,
    }

    final_result = cross_asset_app.invoke(global_state_input)
    final_signals = final_result.get("composite_signals", composite_signals)
    detailed_reports = final_result.get("detailed_reports", {})
    global_report = final_result.get("global_report", "No report.")

    print("\n" + "-" * 70)
    print("🎯 FINAL ADJUSTED SIGNALS")
    print("-" * 70)
    for ccy, sig in final_signals.items():
        print(f"  [{ccy}] Dir: {sig.direction} | Score: {sig.final_score:+.2f} | Tradable: {sig.is_tradable}")
        
    if args.reports and detailed_reports:
        print("\n" + "-" * 70)
        print("📝 DETAILED CURRENCY ANALYSES")
        print("-" * 70)
        for ccy, report in detailed_reports.items():
            print(f"\n### {ccy} Analysis")
            print(report)
            print("-" * 40)
    else:
        print("\n(Detailed currency reports skipped — enable with --reports)")

    print("\n" + "-" * 70)
    print("📊 GLOBAL MACRO EXECUTIVE SUMMARY")
    print("-" * 70)
    print(global_report)
    print("=" * 70)

    # === FINAL EXECUTION SUMMARY (Actionable Plans) ===
    print("\n" + "=" * 72)
    print("📋 FINAL EXECUTION SUMMARY — ACTIONABLE PLANS PER CURRENCY")
    print("=" * 72)
    for ccy in args.currencies:
        sheet = strategy_sheets.get(ccy)
        print_final_execution_summary(ccy, sheet)

if __name__ == "__main__":
    sys.exit(main())