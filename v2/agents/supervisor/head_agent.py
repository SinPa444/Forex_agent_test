"""
agents/supervisor/head_agent.py
================================
The Head Agent (Supervisor) for the Multi-Agent system.

Before waking up the expensive Fundamental, Technical, and Risk agents,
the Supervisor assesses the market regime and event calendar to issue an
Execution Plan, optimizing resource allocation and token usage.

Phase 5 (token optimization):
  - plan ساختن به‌صورت پیش‌فرض RULE-BASED است (بدون LLM) — چارچوب تصمیم
    همان سه قاعده‌ای است که قبلاً در پرامپت LLM بود، فقط deterministic شده.
  - اگر USE_LLM_PLANNER=True یا create_plan(use_llm=True) صدا زده شود،
    مسیر LLM قدیمی به‌عنوان fallback حفظ شده است.
  - مهاجرت talipp → pandas-ta-classic در get_quick_trend.
"""

from __future__ import annotations

import logging
import pandas as pd
from typing import Optional, Literal
from datetime import datetime, timedelta

import yfinance as yf
import pandas_ta_classic as pta  # noqa: F401 — رجیستر df.ta

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field

from core.database import SessionLocal, EventHistoryDB
from core.llm_utils import invoke_with_retry, _strip_json_markdown

logger = logging.getLogger(__name__)

# ===========================================================================
# CONFIG
# ===========================================================================

# پیش‌فرض Phase 5: پلن rule-based است. True فقط برای A/B test یا fallback.
USE_LLM_PLANNER: bool = False


# ===========================================================================
# Pydantic Models
# ===========================================================================

class ExecutionPlan(BaseModel):
    """The execution plan issued by the Head Agent."""
    activate_events: bool = Field(description="Whether to run the economic events pipeline")
    activate_news: bool = Field(description="Whether to run the RSS news pipeline")
    activate_speakers: bool = Field(description="Whether to run the speaker pipeline")
    activate_technical: bool = Field(description="Whether to run the technical agent")
    technical_timeframe: str = Field(description="Suggested timeframe for technical analysis (e.g., 'H1', 'H4', 'D1')")
    market_regime: str = Field(description="Assessed market regime (e.g., 'Trending', 'Ranging', 'Volatile-News')")
    reasoning: str = Field(description="1-2 sentences explaining the resource allocation decision")


# ===========================================================================
# Quick Market Regime Detector (No LLM, pure math for speed)
# ===========================================================================

def _last_val(out) -> Optional[float]:
    """آخرین مقدار معتبر از خروجی pandas-ta (امن در برابر DataFrame/NaN)."""
    if out is None:
        return None
    if isinstance(out, pd.DataFrame):
        if out.empty:
            return None
        out = out.iloc[:, 0]
    val = out.iloc[-1]
    return float(val) if pd.notna(val) else None


def get_quick_trend(ticker: str) -> str:
    """A fast check of EMA 50/200 and ADX to determine trend regime."""
    try:
        data = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
        if data is None or data.empty:
            return "Unknown"
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        df = data.copy()
        df.columns = [c.lower() for c in df.columns]

        if len(df) < 50:
            return "Unknown"

        # ADX Check (ADX < 25 means Ranging)
        adx_df = df.ta.adx(length=14)
        adx_val = 0.0
        if adx_df is not None and not adx_df.empty:
            adx_col = [c for c in adx_df.columns if c.startswith("ADX")]
            if adx_col:
                adx_val = _last_val(adx_df[adx_col[0]]) or 0.0

        if adx_val < 25:
            return "Ranging"

        ema50 = _last_val(df.ta.ema(length=50))
        ema200 = _last_val(df.ta.ema(length=200))
        price = float(df["close"].dropna().iloc[-1])

        if ema50 is not None and ema200 is not None:
            if ema50 > ema200 and price > ema50:
                return "Uptrend"
            if ema50 < ema200 and price < ema50:
                return "Downtrend"

        return "Trending"  # ADX بالا ولی EMA200 در دسترس نیست یا mix است
    except Exception as e:
        logger.warning(f"Quick trend check failed: {e}")
        return "Unknown"


def has_high_impact_events(currency: str) -> bool:
    """Checks DB for high-impact events in the next 24 hours."""
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff = now + timedelta(hours=24)
        count = session.query(EventHistoryDB).filter(
            EventHistoryDB.currency == currency,
            EventHistoryDB.impact == "High",
            EventHistoryDB.date >= now,
            EventHistoryDB.date <= cutoff
        ).count()
        return count > 0
    except Exception:
        return False
    finally:
        session.close()


# ===========================================================================
# Rule-Based Planner (No LLM — deterministic version of the old prompt rules)
# ===========================================================================

def create_plan_rule_based(currency: str, ticker: str) -> ExecutionPlan:
    """
    نسخه قطعی و بدون LLM از همان Decision Framework پرامپت قدیمی:

    1. NEWS DRIVEN: ایونت High impact در ۲۴ ساعت آینده → همه پایپ‌لاین‌ها +
       Tech روی H4 (فیلتر نویز) — regime = Volatile-News
    2. TREND DRIVEN: Uptrend/Downtrend بدون خبر → Tech روی H4 اولویت دارد،
       News فقط برای context فعال است، Events خاموش
    3. RANGE BOUND: Ranging/Unknown بدون خبر → Events خاموش، News فعال،
       Tech روی H1 برای range trading

    Speakers همیشه False است (پایپ‌لاین آفلاین).
    """
    trend_status = get_quick_trend(ticker)
    news_24h = has_high_impact_events(currency)

    if news_24h:
        return ExecutionPlan(
            activate_events=True,
            activate_news=True,
            activate_speakers=False,
            activate_technical=True,
            technical_timeframe="H4",
            market_regime="Volatile-News",
            reasoning=(
                f"Rule-based: High-impact events in next 24h for {currency}; "
                f"fundamental mandatory, technical on H4 to filter noise. Trend={trend_status}."
            ),
        )

    if trend_status in ("Uptrend", "Downtrend"):
        return ExecutionPlan(
            activate_events=False,
            activate_news=True,
            activate_speakers=False,
            activate_technical=True,
            technical_timeframe="H4",
            market_regime=trend_status,
            reasoning=(
                f"Rule-based: {trend_status} with no major news; technical leads on H4, "
                "news kept for context."
            ),
        )

    # Ranging / Unknown / Trending-mixed بدون خبر
    return ExecutionPlan(
        activate_events=False,
        activate_news=True,
        activate_speakers=False,
        activate_technical=True,
        technical_timeframe="H1",
        market_regime="Ranging" if trend_status in ("Ranging", "Unknown") else trend_status,
        reasoning=(
            f"Rule-based: market {trend_status} with no major news; "
            "events disabled, news active for narrative, technical on H1."
        ),
    )


# ===========================================================================
# LLM Prompt & Agent Service (fallback path — فقط اگر use_llm=True)
# ===========================================================================

_SUPERVISOR_SYSTEM_PROMPT = """\
You are the Head of a Forex Trading Desk. Your job is to assess market conditions and allocate resources (agents) efficiently.

## Decision Framework
1. NEWS DRIVEN: If there are High Impact events in the next 24h, Fundamental agent is MANDATORY (Events + News + Speakers). Market is "Volatile-News". Technical timeframe should be "H1" or "H4" to filter noise.
2. TREND DRIVEN: If market trend is "Uptrend" or "Downtrend" and no major news: Technical agent is priority. Timeframe "H4". Fundamental can be True just for context, but technicals lead.
3. RANGE BOUND: If market is "Ranging" and no major news: Do NOT disable fundamental completely. Disable Events (since no calendar events), but keep News active to understand market narrative. Focus on Technical on "H1" for range trading.

IMPORTANT SYSTEM RULE:
- Currently, the Speaker pipeline (Twitter/Statements ingestion) is OFFLINE due to missing API. You MUST ALWAYS set `activate_speakers` to False. Do not activate it under any circumstances.

Provide a structured Execution Plan.
"""

class HeadAgent:
    """
    The Supervisor Agent that decides the execution plan.

    Phase 5: پیش‌فرض rule-based (بدون کال LLM). مسیر LLM فقط با
    use_llm=True یا USE_LLM_PLANNER=True فعال می‌شود.
    """

    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.parser = PydanticOutputParser(pydantic_object=ExecutionPlan)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", _SUPERVISOR_SYSTEM_PROMPT),
            ("human", "Currency: {currency}\nTechnical Trend: {trend_status}\nHigh Impact Events in 24h: {has_news}\n\nIssue the execution plan.\n{format_instructions}")
        ]).partial(format_instructions=self.parser.get_format_instructions())

        self.chain = self.prompt | self.llm | RunnableLambda(_strip_json_markdown) | self.parser

    def create_plan(self, currency: str, ticker: str, use_llm: Optional[bool] = None) -> ExecutionPlan:
        """Assesses market and returns the execution plan."""
        if use_llm is None:
            use_llm = USE_LLM_PLANNER

        if not use_llm:
            plan = create_plan_rule_based(currency, ticker)
            logger.info(
                f"[Head Agent] Rule-based plan: Events={plan.activate_events}, News={plan.activate_news}, "
                f"Speakers={plan.activate_speakers}, Tech={plan.activate_technical} ({plan.technical_timeframe})"
            )
            return plan

        logger.info(f"[Head Agent] Creating execution plan for {currency} via LLM...")

        trend_status = get_quick_trend(ticker)
        news_status = has_high_impact_events(currency)

        prompt_inputs = {
            "currency": currency,
            "trend_status": trend_status,
            "has_news": "Yes" if news_status else "No",
        }

        try:
            plan = invoke_with_retry(self.chain, prompt_inputs)
            if not isinstance(plan, ExecutionPlan):
                raise ValueError("LLM returned unexpected type")
            logger.info(f"[Head Agent] Plan issued: Events={plan.activate_events}, News={plan.activate_news}, Speakers={plan.activate_speakers}, Tech={plan.activate_technical} ({plan.technical_timeframe})")
            return plan
        except Exception as exc:
            logger.error(f"[Head Agent] LLM failed: {exc}. Falling back to rule-based planner.")
            return create_plan_rule_based(currency, ticker)

# ===========================================================================
# CLI Smoke Test
# ===========================================================================
if __name__ == "__main__":
    import os
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    print("=" * 60)
    print("Head Agent (Supervisor) Smoke Test — Rule-Based (No LLM)")
    print("=" * 60)

    from core.routing import resolve_asset_route

    ccy = "USD"
    route = resolve_asset_route(ccy)
    if route:
        plan = create_plan_rule_based(ccy, route.ticker)
        print(f"\nExecution Plan for {ccy}:")
        print(f"  Market Regime: {plan.market_regime}")
        print(f"  Activate Events: {plan.activate_events}")
        print(f"  Activate News: {plan.activate_news}")
        print(f"  Activate Speakers: {plan.activate_speakers}")
        print(f"  Activate Technical: {plan.activate_technical}")
        print(f"  Technical Timeframe: {plan.technical_timeframe}")
        print(f"  Reasoning: {plan.reasoning}")
    print("=" * 60)
