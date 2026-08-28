"""
risk_manager.py
===============
Phase 3 Risk Management Agent.

The final decision-maker agent. Takes Fundamental and Technical reports,
evaluates confluence, and produces a structured Trade Plan (or rejection).

Phase 5 (token optimization):
  - تصمیم ریسک به‌صورت پیش‌فرض RULE-BASED است (بدون LLM) — همان Decision
    Framework پرامپت قدیمی، deterministic پیاده شده:
      * تعارض مستقیم Fund/Tech → REJECTED
      * هم‌راستا + R:R کافی → APPROVED با پلن deterministic از S/R
      * تکنیکال «صبر برای pullback» → WAIT
      * حافظه معاملات ضعیف (WR<40%) → سخت‌گیرانه‌تر (R:R 1:2 و گرایش به WAIT)
  - مسیر LLM فقط با use_llm=True یا USE_LLM_RISK=True فعال می‌شود.

Design principles:
  - Independent reasoning for complex conflict resolution
  - Outputs strictly structured trade parameters (Entry, SL, TP)
  - Prioritizes capital preservation over signal execution
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field, field_validator

from core.llm_utils import invoke_with_retry, _strip_json_markdown

logger = logging.getLogger(__name__)

# ===========================================================================
# CONFIG
# ===========================================================================

# پیش‌فرض Phase 5: تصمیم rule-based است. True فقط برای A/B test یا fallback.
USE_LLM_RISK: bool = False

# حداقل Risk/Reward قابل قبول (با حافظه ضعیف سخت‌گیرانه‌تر می‌شود)
MIN_RR_DEFAULT: float = 1.5
MIN_RR_STRICT: float = 2.0

# بافر امنیت SL به نسبت فاصله تا S/R (زیر/بالای سطح، درصد قیمت)
SL_BUFFER_PCT: float = 0.001  # 0.1%

# ===========================================================================
# Pydantic Models
# ===========================================================================

class TradePlan(BaseModel):
    """Structured trade execution plan."""
    entry_zone: str = Field(description="Specific price zone for entry, e.g., '1.1540-1.1545' or 'Market Price'")
    stop_loss: float = Field(description="Exact Stop Loss price")
    take_profit: float = Field(description="Exact Take Profit price")
    risk_reward_ratio: str = Field(description="Calculated R:R ratio, e.g., '1:2.5'")

    @field_validator("stop_loss", "take_profit")
    @classmethod
    def prices_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Prices must be positive")
        return v

class RiskDecision(BaseModel):
    """Final decision from the Risk Manager."""
    decision: Literal["APPROVED", "REJECTED", "WAIT"]
    reasoning: str = Field(description="3-4 sentences explaining the confluence analysis and final decision")
    trade_plan: Optional[TradePlan] = None

# ===========================================================================
# Rule-Based Decision Engine (No LLM)
# ===========================================================================

def _structure_trade(
    direction: int,
    current_price: float,
    nearest_support: Optional[float],
    nearest_resistance: Optional[float],
    min_rr: float,
) -> tuple[Optional[TradePlan], str]:
    """
    ساخت پلن deterministic از سطوح S/R.

    Long:  SL زیر Support (با بافر)، TP روی Resistance
    Short: SL بالای Resistance (با بافر)، TP روی Support

    Returns:
        (TradePlan یا None، متن توضیح)
    """
    if direction == 1:
        if nearest_support is None or nearest_resistance is None:
            return None, "Long setup lacks both S/R levels for structuring."
        entry = current_price
        sl = nearest_support * (1 - SL_BUFFER_PCT)
        tp = nearest_resistance
        risk = entry - sl
        reward = tp - entry
    else:  # direction == -1
        if nearest_support is None or nearest_resistance is None:
            return None, "Short setup lacks both S/R levels for structuring."
        entry = current_price
        sl = nearest_resistance * (1 + SL_BUFFER_PCT)
        tp = nearest_support
        risk = sl - entry
        reward = entry - tp

    if risk <= 0:
        return None, "Invalid risk geometry (entry already beyond SL level)."

    rr = reward / risk
    rr_str = f"1:{rr:.1f}"

    if reward <= 0 or rr < min_rr:
        return None, f"R:R {rr_str} below minimum 1:{min_rr} — poor trade geometry."

    plan = TradePlan(
        entry_zone=f"{entry:.5f} (Market)",
        stop_loss=round(sl, 5),
        take_profit=round(tp, 5),
        risk_reward_ratio=rr_str,
    )
    return plan, f"Structured {('LONG' if direction == 1 else 'SHORT')} at market with R:R {rr_str}."


def evaluate_rule_based(
    fund_direction: int,
    fund_score: float,
    fund_confidence: float,
    fund_tradable: bool,
    fund_evaluated: bool,
    tech_direction: int,
    tech_confidence: float,
    tech_strategy: str,
    current_price: float,
    nearest_support: Optional[float],
    nearest_resistance: Optional[float],
    mem_total: int = 0,
    mem_wins: int = 0,
    mem_losses: int = 0,
    mem_win_rate: float = 0.0,
) -> RiskDecision:
    """
    نسخه deterministic از Decision Framework پرامپت قدیمی:

    1. تعارض مستقیم Fund/Tech → REJECTED
    2. Fund ارزیابی نشده → اتکا به Tech (اگر directional)
    3. Fund directional + Tech neutral → APPROVED با احتیاط (اگر R:R ok)
    4. هم‌راستا → APPROVED (اگر R:R ok)
    5. استراتژی تکنیکال «صبر» → WAIT
    6. حافظه ضعیف (WR<40% با حداقل ۵ معامله) → R:R سخت‌گیرانه 1:2
    """
    # --- حافظه معاملات: سخت‌گیری پویا ---
    min_rr = MIN_RR_DEFAULT
    memory_note = ""
    if mem_total >= 5 and mem_win_rate < 40.0:
        min_rr = MIN_RR_STRICT
        memory_note = (
            f" Poor track record (WR {mem_win_rate:.0f}% over {mem_total} trades) "
            f"— tightened rules: min R:R 1:{MIN_RR_STRICT}."
        )
    elif mem_total >= 5 and mem_win_rate > 60.0:
        memory_note = f" Good track record (WR {mem_win_rate:.0f}%) — standard rules."

    # --- سیگنال «صبر» در استراتژی تکنیکال ---
    wait_keywords = ("wait", "pullback", "stand aside", "retracement", "patience")
    tech_says_wait = any(k in (tech_strategy or "").lower() for k in wait_keywords)

    # --- ۱. تعارض مستقیم ---
    if (
        fund_evaluated
        and fund_direction != 0
        and tech_direction != 0
        and fund_direction != tech_direction
    ):
        return RiskDecision(
            decision="REJECTED",
            reasoning=(
                f"Direct conflict: Fundamental is {'bullish' if fund_direction == 1 else 'bearish'} "
                f"(score {fund_score:+.2f}) while Technical is "
                f"{'bullish' if tech_direction == 1 else 'bearish'}. "
                f"Capital preservation takes priority — no trade against conflicting signals."
                + memory_note
            ),
            trade_plan=None,
        )

    # --- تعیین جهت غالب ---
    # اگر Fund ارزیابی شده و directional است، Fund مبناست؛ وگرنه Tech.
    if fund_evaluated and fund_direction != 0:
        direction = fund_direction
        driver = "fundamental"
    elif tech_direction != 0:
        direction = tech_direction
        driver = "technical"
    else:
        return RiskDecision(
            decision="WAIT",
            reasoning=(
                "Both fundamental and technical signals are neutral — "
                "no directional edge. Standing aside." + memory_note
            ),
            trade_plan=None,
        )

    # --- ۲. استراتژی تکنیکال می‌گوید صبر کن ---
    if tech_says_wait and tech_direction == 0:
        return RiskDecision(
            decision="WAIT",
            reasoning=(
                f"{driver.capitalize()} bias is {'bullish' if direction == 1 else 'bearish'} "
                f"but the technical strategy advises waiting ({tech_strategy[:80]}). "
                "Waiting for a better entry zone rather than chasing price." + memory_note
            ),
            trade_plan=None,
        )

    # --- ۳. ساخت پلن و چک R:R ---
    plan, plan_note = _structure_trade(
        direction, current_price, nearest_support, nearest_resistance, min_rr
    )

    if plan is None:
        # هندسه معامله خوب نیست → WAIT (سیگنال هست ولی ورود الان بد است)
        return RiskDecision(
            decision="WAIT",
            reasoning=(
                f"Directional {'bullish' if direction == 1 else 'bearish'} bias from {driver} "
                f"(fund dir={fund_direction}, tech dir={tech_direction}), but {plan_note} "
                "Waiting for price to reach a zone with acceptable geometry." + memory_note
            ),
            trade_plan=None,
        )

    # --- ۴. تایید نهایی با درجه اطمینان ---
    if fund_evaluated and fund_direction != 0 and tech_direction == 0:
        conf_note = "Fundamental-directional with neutral technicals — approved with caution. "
    elif not fund_evaluated:
        conf_note = "Fundamental not evaluated — approval based on technicals alone. "
    else:
        conf_note = "Fundamental and technical are aligned — high-probability confluence. "

    return RiskDecision(
        decision="APPROVED",
        reasoning=(
            conf_note + plan_note + memory_note
        ),
        trade_plan=plan,
    )


# ===========================================================================
# LLM Prompt (fallback path — فقط اگر use_llm=True)
# ===========================================================================

_RISK_SYSTEM_PROMPT = """\
You are a strict and disciplined Risk Manager and Trade Structurer for a forex trading desk.
Your job is to review the Fundamental Analysis and Technical Analysis, resolve any conflicts, and decide whether to execute the trade.

## Decision Framework
1. CONFLUENCE CHECK:
   - If Fundamental and Technical directions align (e.g., both Bullish), this is a high-probability setup. APPROVE.
   - If Fundamental reasoning says "Fundamental analysis not run.", treat fundamental as NOT EVALUATED. In this case, rely entirely on Technical. If Technical is directional and at a good S/R zone, you MAY APPROVE.
   - If Fundamental is directional and Technical is Neutral, you may APPROVE but with caution.
   - If Fundamental and Technical are in direct conflict (e.g., Fund Bullish, Tech Bearish), you MUST REJECT. Capital preservation is priority.
2. TRADE STRUCTURING (If APPROVED):
   - Use the provided Technical Support/Resistance to define Entry, Stop Loss (SL), and Take Profit (TP).
   - For Longs (Buy): SL must be safely below the Support. TP must be at or near the Resistance.
   - For Shorts (Sell): SL must be safely above the Resistance. TP must be at or near the Support.
   - Ensure a minimum Risk/Reward ratio of 1:1.5. If the distance to S/R doesn't allow 1:1.5, REJECT due to poor R:R.
3. WAIT CONDITION:
   - If technicals suggest waiting for a pullback or breakout, and fundamentals are aligned, set decision to WAIT.

## SPECIAL CASE: Mean Reversion in Ranging Markets
- If the Technical strategy indicates a Ranging market AND price is at a Support (Bullish OB/FVG) or Resistance (Bearish OB/FVG), you MAY APPROVE a counter-trend (Mean Reversion) trade even if fundamental direction is neutral (0).
- In this case, target the opposite end of the range.
- Ensure SL is strictly outside the OB/FVG zone.

## Strict Output Rules
- If decision is REJECTED or WAIT, `trade_plan` MUST be null.
- If decision is APPROVED, `trade_plan` MUST be populated with exact numbers based on the provided Technical Data.
- Do not invent prices not supported by the provided Technical Support/Resistance data.

## SPECIAL CASE: Memory & Past Performance
- You will be provided with the Recent Trade Memory for this currency.
- If the system has executed trades in the past and has a track record:
  - If Win Rate is high (>60%): You can be slightly more lenient with entry confirmations.
  - If Win Rate is poor (<40%) or there are consecutive losses: You MUST tighten your risk rules. Demand higher confluence, require stricter R:R (e.g., 1:2 instead of 1:1.5), or default to WAIT to prevent repeating mistakes.
"""

_RISK_HUMAN_TEMPLATE = """\
## Fundamental Analysis Report
Direction: {fund_direction} (1=Bullish, -1=Bearish, 0=Neutral)
Score: {fund_score:.2f} | Confidence: {fund_confidence:.2f} | Tradable: {fund_tradable}
Reasoning: {fund_reasoning}

## Technical Analysis Report
Direction: {tech_direction} (1=Bullish, -1=Bearish, 0=Neutral)
Confidence: {tech_confidence:.2f}
Strategy: {tech_strategy}
Reasoning: {tech_reasoning}

## Technical Levels (for Trade Structuring)
Current Price: {current_price}
Nearest Support: {nearest_support}
Nearest Resistance: {nearest_resistance}

## Recent Trade Memory (Past Performance)
Total Recent Trades: {mem_total} | Wins: {mem_wins} | Losses: {mem_losses} | Win Rate: {mem_win_rate:.1f}%

## Task
Evaluate the confluence between Fundamental and Technical. Make a final Risk Decision.
If approved, structure the trade plan strictly using the Technical Levels.
{format_instructions}
"""

# ===========================================================================
# Agent Service
# ===========================================================================

class RiskManagerAgent:
    """
    Synthesizes Fundamental and Technical reports into a final trade decision.

    Phase 5: پیش‌فرض rule-based (بدون کال LLM). مسیر LLM فقط با
    use_llm=True یا USE_LLM_RISK=True فعال می‌شود.
    """

    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.parser = PydanticOutputParser(pydantic_object=RiskDecision)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", _RISK_SYSTEM_PROMPT),
            ("human", _RISK_HUMAN_TEMPLATE)
        ]).partial(format_instructions=self.parser.get_format_instructions())

        self.chain = self.prompt | self.llm | RunnableLambda(_strip_json_markdown) | self.parser

    def evaluate(
        self,
        fund_direction: int,
        fund_score: float,
        fund_confidence: float,
        fund_tradable: bool,
        fund_reasoning: str,
        tech_direction: int,
        tech_confidence: float,
        tech_strategy: str,
        tech_reasoning: str,
        current_price: Optional[float],
        nearest_support: Optional[float],
        nearest_resistance: Optional[float],
        mem_total: int = 0,
        mem_wins: int = 0,
        mem_losses: int = 0,
        mem_win_rate: float = 0.0,
        use_llm: Optional[bool] = None,
    ) -> RiskDecision:
        """Evaluates the signals and returns a final risk decision."""
        logger.info(f"[Risk Agent] Evaluating confluence: Fund Dir={fund_direction}, Tech Dir={tech_direction}")

        # Hard code rejection if critical data is missing for trade structuring
        if current_price is None or (nearest_support is None and nearest_resistance is None):
            logger.warning("[Risk Agent] Missing critical price data for structuring. Auto-rejecting.")
            return RiskDecision(
                decision="REJECTED",
                reasoning="Cannot structure a trade plan due to missing Support/Resistance or Current Price data.",
                trade_plan=None
            )

        fund_evaluated = fund_reasoning != "Fundamental analysis not run."

        if use_llm is None:
            use_llm = USE_LLM_RISK

        # ============================
        # مسیر Rule-Based (پیش‌فرض)
        # ============================
        if not use_llm:
            decision = evaluate_rule_based(
                fund_direction=fund_direction,
                fund_score=fund_score,
                fund_confidence=fund_confidence,
                fund_tradable=fund_tradable,
                fund_evaluated=fund_evaluated,
                tech_direction=tech_direction,
                tech_confidence=tech_confidence,
                tech_strategy=tech_strategy,
                current_price=current_price,
                nearest_support=nearest_support,
                nearest_resistance=nearest_resistance,
                mem_total=mem_total,
                mem_wins=mem_wins,
                mem_losses=mem_losses,
                mem_win_rate=mem_win_rate,
            )
            logger.info(f"[Risk Agent] Rule-based decision: {decision.decision}")
            return decision

        # ============================
        # مسیر LLM (fallback / opt-in)
        # ============================
        prompt_inputs = {
            "fund_direction": fund_direction,
            "fund_score": fund_score,
            "fund_confidence": fund_confidence,
            "fund_tradable": fund_tradable,
            "fund_reasoning": fund_reasoning,
            "tech_direction": tech_direction,
            "tech_confidence": tech_confidence,
            "tech_strategy": tech_strategy,
            "tech_reasoning": tech_reasoning,
            "current_price": f"{current_price:.4f}",
            "nearest_support": f"{nearest_support:.4f}" if nearest_support else "None",
            "nearest_resistance": f"{nearest_resistance:.4f}" if nearest_resistance else "None",
            "mem_total": mem_total,
            "mem_wins": mem_wins,
            "mem_losses": mem_losses,
            "mem_win_rate": mem_win_rate
        }

        try:
            decision = invoke_with_retry(self.chain, prompt_inputs)
            if not isinstance(decision, RiskDecision):
                raise ValueError("LLM returned unexpected type")

            logger.info(f"[Risk Agent] Decision: {decision.decision}")
            return decision

        except Exception as exc:
            logger.error(f"[Risk Agent] LLM evaluation failed: {exc} — falling back to rule-based")
            return evaluate_rule_based(
                fund_direction=fund_direction,
                fund_score=fund_score,
                fund_confidence=fund_confidence,
                fund_tradable=fund_tradable,
                fund_evaluated=fund_evaluated,
                tech_direction=tech_direction,
                tech_confidence=tech_confidence,
                tech_strategy=tech_strategy,
                current_price=current_price,
                nearest_support=nearest_support,
                nearest_resistance=nearest_resistance,
                mem_total=mem_total,
                mem_wins=mem_wins,
                mem_losses=mem_losses,
                mem_win_rate=mem_win_rate,
            )

# ===========================================================================
# Phase 7: Strategy Engine — برگه استراتژی چند-افقی (deterministic)
# ===========================================================================

class StrategyPlan(BaseModel):
    """یک پلن معاملاتی روی یک افق — فعال، در انتظار، یا نامعتبر."""
    timeframe: str
    horizon_role: str                       # HTF / MTF / LTF
    horizon_label: str                      # SWING / INTRADAY / SCALP
    status: Literal["ACTIVE", "PENDING", "INVALID"]
    direction: int = 0                      # 1 / -1 / 0
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    rr_ratio: Optional[float] = None
    invalidation_price: Optional[float] = None
    counter_bias: bool = False
    ttl_hours: float = 24.0
    reasoning: str = ""


class StrategySheet(BaseModel):
    """خروجی نهایی Phase 7 per ارز — نقشه کامل افق‌ها."""
    currency: str
    generated_at: str
    htf_bias: int = 0
    htf_bias_label: str = "Neutral"
    plans: list[StrategyPlan] = Field(default_factory=list)

    def active_plans(self) -> list[StrategyPlan]:
        return [p for p in self.plans if p.status == "ACTIVE"]

    def pending_plans(self) -> list[StrategyPlan]:
        return [p for p in self.plans if p.status == "PENDING"]


# پیکربندی افق‌ها: کدام تایم‌فریم، چه TTL، چه بافر SL
HORIZON_CONFIG: dict[str, dict] = {
    "SWING":    {"timeframes": ["H4", "H2"],   "ttl_hours": 120.0, "sl_buffer_pct": 0.003},
    "INTRADAY": {"timeframes": ["H1"],         "ttl_hours": 24.0,  "sl_buffer_pct": 0.0015},
    "SCALP":    {"timeframes": ["M15", "M30"], "ttl_hours": 4.0,   "sl_buffer_pct": 0.0008},
}

# شرط معامله خلاف bias اصلی
COUNTER_BIAS_MIN_SCORE: float = 0.4
COUNTER_BIAS_MIN_RR: float = 2.0


def _structure_horizon_plan(
    direction: int,
    current_price: float,
    support: Optional[float],
    resistance: Optional[float],
    min_rr: float,
    sl_buffer_pct: float,
) -> tuple[str, Optional[dict], str]:
    """
    ساخت هندسه معامله برای یک افق. خروجی: (status, params, note)

    ACTIVE:  ورود در مارکت — R:R الان کافی است
    PENDING: ورود شرطی روی زون S/R — R:R از بدترین پرشدن (لبه زون) محاسبه می‌شود
    INVALID: هندسه مرده
    """
    if direction == 1:
        if support is None or resistance is None:
            return "INVALID", None, "no S/R levels"
        # --- ACTIVE ---
        sl_act = support * (1 - sl_buffer_pct)
        risk = current_price - sl_act
        reward = resistance - current_price
        if risk > 0 and reward > 0 and reward / risk >= min_rr:
            return "ACTIVE", {
                "entry_low": current_price, "entry_high": current_price,
                "stop_loss": round(sl_act, 5), "take_profit": round(resistance, 5),
                "rr_ratio": round(reward / risk, 2), "invalidation_price": round(sl_act, 5),
            }, f"market entry ok, R:R {reward / risk:.1f}"
        # --- PENDING (ورود روی حمایت) ---
        entry_high = support * 1.0005
        entry_low = support * 0.9995
        sl_pen = support * (1 - sl_buffer_pct)
        risk_p = entry_high - sl_pen          # بدترین پرشدن = لبه بالای زون
        reward_p = resistance - entry_high
        if risk_p > 0 and reward_p > 0 and reward_p / risk_p >= min_rr:
            return "PENDING", {
                "entry_low": round(entry_low, 5), "entry_high": round(entry_high, 5),
                "stop_loss": round(sl_pen, 5), "take_profit": round(resistance, 5),
                "rr_ratio": round(reward_p / risk_p, 2), "invalidation_price": round(sl_pen, 5),
            }, f"pending long at support {support:.5f}, R:R {reward_p / risk_p:.1f}"
        return "INVALID", None, f"poor geometry (S={support:.5f} R={resistance:.5f} P={current_price:.5f})"

    if direction == -1:
        if support is None or resistance is None:
            return "INVALID", None, "no S/R levels"
        # --- ACTIVE ---
        sl_act = resistance * (1 + sl_buffer_pct)
        risk = sl_act - current_price
        reward = current_price - support
        if risk > 0 and reward > 0 and reward / risk >= min_rr:
            return "ACTIVE", {
                "entry_low": current_price, "entry_high": current_price,
                "stop_loss": round(sl_act, 5), "take_profit": round(support, 5),
                "rr_ratio": round(reward / risk, 2), "invalidation_price": round(sl_act, 5),
            }, f"market entry ok, R:R {reward / risk:.1f}"
        # --- PENDING (ورود روی مقاومت) ---
        entry_low = resistance * 0.9995
        entry_high = resistance * 1.0005
        sl_pen = resistance * (1 + sl_buffer_pct)
        risk_p = sl_pen - entry_low
        reward_p = entry_low - support
        if risk_p > 0 and reward_p > 0 and reward_p / risk_p >= min_rr:
            return "PENDING", {
                "entry_low": round(entry_low, 5), "entry_high": round(entry_high, 5),
                "stop_loss": round(sl_pen, 5), "take_profit": round(support, 5),
                "rr_ratio": round(reward_p / risk_p, 2), "invalidation_price": round(sl_pen, 5),
            }, f"pending short at resistance {resistance:.5f}, R:R {reward_p / risk_p:.1f}"
        return "INVALID", None, f"poor geometry (S={support:.5f} R={resistance:.5f} P={current_price:.5f})"

    return "INVALID", None, "no directional edge"


def build_strategy_sheet(
    currency: str,
    mtf_matrix,
    mem_total: int = 0,
    mem_wins: int = 0,
    mem_losses: int = 0,
    mem_win_rate: float = 0.0,
    recent_losses: Optional[list[dict]] = None,
    fund_dir: int = 0,
    fund_score: float = 0.0,
) -> StrategySheet:
    """
    Strategy Engine — خروجی deterministic برگه استراتژی از روی MTFMatrix.

    قواعد:
      - جهت هر افق از امتیاز همان تایم‌فریم می‌آید
      - خلاف bias اصلی (HTF) فقط با سیگنال قوی (|score|≥0.4) و R:R≥2 مجاز است
        و با تگ counter_bias (نیم‌سایز) می‌آید
      - وتوی فاندامنتال: اگر جهت پلن با جهت فاندامنتال (fund_dir) در تضاد باشد،
        همان رژیم ضدبایاس اعمال می‌شود (آستانه امتیاز + R:R≥2 + تگ نیم‌سایز)
      - حافظه ضعیف (WR<40% با ≥۵ معامله، یا ≥۳ باخت در ۵ معامله اخیر)
        آستانه R:R را ۰٫۵ بالا می‌برد
    """
    sheet = StrategySheet(
        currency=currency,
        generated_at=datetime.utcnow().isoformat(),
        htf_bias=mtf_matrix.htf_bias,
        htf_bias_label=mtf_matrix.htf_bias_label,
    )

    # --- حافظه: سخت‌گیری پویا ---
    min_rr_boost = 0.0
    memory_notes = []
    if mem_total >= 5 and mem_win_rate < 40.0:
        min_rr_boost += 0.5
        memory_notes.append(f"poor track record (WR {mem_win_rate:.0f}%)")
    if recent_losses and len(recent_losses) >= 3:
        min_rr_boost += 0.5
        memory_notes.append(f"{len(recent_losses)} recent losses on this currency")
        # حافظه معنایی: دلیل باخت‌ها در برگه می‌آید تا انسان/LLM الگو را ببیند
        for loss in recent_losses[:3]:
            memory_notes.append(f"loss#{loss.get('id')}: {(loss.get('reasoning') or '')[:100]}")

    for horizon_label, cfg in HORIZON_CONFIG.items():
        # قوی‌ترین تایم‌فریم معتبر این افق — بر اساس |score| × confidence
        # (نه «اولین معتبر» — وگرنه H4ِ خنثی، H2ِ قوی را از چشم می‌اندازد)
        candidates = [
            mtf_matrix.scores[tf]
            for tf in cfg["timeframes"]
            if tf in mtf_matrix.scores and mtf_matrix.scores[tf].valid
        ]
        if not candidates:
            continue
        tf_score = max(candidates, key=lambda s: abs(s.score) * s.confidence)

        min_rr = MIN_RR_DEFAULT + min_rr_boost
        notes = list(memory_notes)

        if tf_score.direction == 0:
            sheet.plans.append(StrategyPlan(
                timeframe=tf_score.timeframe, horizon_role=tf_score.role,
                horizon_label=horizon_label, status="INVALID", direction=0,
                ttl_hours=cfg["ttl_hours"],
                reasoning=f"no directional edge on {tf_score.timeframe} (score {tf_score.score:+.2f})",
            ))
            continue

        counter_htf = (
            mtf_matrix.htf_bias != 0 and tf_score.direction != mtf_matrix.htf_bias
        )
        counter_fund = (
            fund_dir != 0 and tf_score.direction != fund_dir
        )
        counter_bias = counter_htf or counter_fund
        if counter_bias:
            min_rr = max(min_rr, COUNTER_BIAS_MIN_RR)
            if counter_htf:
                against = f"HTF {mtf_matrix.htf_bias_label}"
            else:
                against = f"fundamental (dir={fund_dir}, score={fund_score:+.2f})"
            if abs(tf_score.score) < COUNTER_BIAS_MIN_SCORE:
                sheet.plans.append(StrategyPlan(
                    timeframe=tf_score.timeframe, horizon_role=tf_score.role,
                    horizon_label=horizon_label, status="INVALID", direction=tf_score.direction,
                    ttl_hours=cfg["ttl_hours"], counter_bias=True,
                    reasoning=(
                        f"counter-bias signal too weak (|score|={abs(tf_score.score):.2f} "
                        f"< {COUNTER_BIAS_MIN_SCORE}) vs {against}"
                    ),
                ))
                continue
            notes.append(f"COUNTER-BIAS vs {against} — half size")

        status, params, note = _structure_horizon_plan(
            direction=tf_score.direction,
            current_price=tf_score.price,
            support=tf_score.nearest_support,
            resistance=tf_score.nearest_resistance,
            min_rr=min_rr,
            sl_buffer_pct=cfg["sl_buffer_pct"],
        )

        reasoning = note + (" | " + "; ".join(notes) if notes else "")
        sheet.plans.append(StrategyPlan(
            timeframe=tf_score.timeframe, horizon_role=tf_score.role,
            horizon_label=horizon_label, status=status, direction=tf_score.direction,
            entry_low=params["entry_low"] if params else None,
            entry_high=params["entry_high"] if params else None,
            stop_loss=params["stop_loss"] if params else None,
            take_profit=params["take_profit"] if params else None,
            rr_ratio=params["rr_ratio"] if params else None,
            invalidation_price=params["invalidation_price"] if params else None,
            counter_bias=counter_bias, ttl_hours=cfg["ttl_hours"],
            reasoning=reasoning,
        ))

    return sheet


def render_strategy_sheet_text(sheet: StrategySheet) -> str:
    """رندر متنی برگه استراتژی — deterministic، بدون LLM."""
    lines = [
        f"{'=' * 56}",
        f"STRATEGY SHEET: {sheet.currency} | HTF bias: {sheet.htf_bias_label}",
        f"generated: {sheet.generated_at}",
        f"{'=' * 56}",
    ]
    for p in sheet.plans:
        head = f"[{p.horizon_label:>8} {p.timeframe}] {p.status}"
        if p.status == "INVALID":
            lines.append(f"{head} — {p.reasoning}")
            continue
        direction_fa = "LONG" if p.direction == 1 else "SHORT"
        tag = " (COUNTER-BIAS, half size)" if p.counter_bias else ""
        if p.status == "ACTIVE":
            body = (f"{direction_fa} NOW @ {p.entry_high} | SL {p.stop_loss} | "
                    f"TP {p.take_profit} | R:R 1:{p.rr_ratio}{tag}")
        else:
            body = (f"{direction_fa} IF price enters [{p.entry_low} - {p.entry_high}] | "
                    f"SL {p.stop_loss} | TP {p.take_profit} | R:R 1:{p.rr_ratio} | "
                    f"invalid below/above {p.invalidation_price}{tag}")
        lines.append(f"{head} — {body}")
        lines.append(f"{'':>12} {p.reasoning}")
    if not sheet.plans:
        lines.append("  no valid timeframes — no plans")
    return "\n".join(lines)


# ===========================================================================
# CLI Smoke Test (rule-based, no LLM needed)
# ===========================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

    print("=" * 60)
    print("Risk Manager — Rule-Based Smoke Test (No LLM)")
    print("=" * 60)

    # Scenario 1: Aligned Bullish with good geometry (reward 40 pips vs risk 21.5)
    print("\n>>> Scenario 1: Aligned Bullish (Fund & Tech both Bullish, R:R≈1.9)")
    d1 = evaluate_rule_based(
        fund_direction=1, fund_score=0.65, fund_confidence=0.75, fund_tradable=True,
        fund_evaluated=True,
        tech_direction=1, tech_confidence=0.80,
        tech_strategy="Look for longs on pullback to support",
        current_price=1.1540, nearest_support=1.1530, nearest_resistance=1.1580,
    )
    print(f"Decision: {d1.decision} | {d1.reasoning[:120]}")
    if d1.trade_plan:
        print(f"Plan: Entry={d1.trade_plan.entry_zone} SL={d1.trade_plan.stop_loss} TP={d1.trade_plan.take_profit} RR={d1.trade_plan.risk_reward_ratio}")

    # Scenario 2: Conflict
    print("\n>>> Scenario 2: Conflict (Fund Bullish, Tech Bearish)")
    d2 = evaluate_rule_based(
        fund_direction=1, fund_score=0.45, fund_confidence=0.60, fund_tradable=True,
        fund_evaluated=True,
        tech_direction=-1, tech_confidence=0.70,
        tech_strategy="Short on rejection at resistance",
        current_price=1.1555, nearest_support=1.1530, nearest_resistance=1.1560,
    )
    print(f"Decision: {d2.decision} | {d2.reasoning[:120]}")

    # Scenario 3: Aligned but poor R:R
    print("\n>>> Scenario 3: Aligned but poor R:R (resistance too close)")
    d3 = evaluate_rule_based(
        fund_direction=1, fund_score=0.65, fund_confidence=0.75, fund_tradable=True,
        fund_evaluated=True,
        tech_direction=1, tech_confidence=0.80,
        tech_strategy="Long at market",
        current_price=1.1558, nearest_support=1.1530, nearest_resistance=1.1560,
    )
    print(f"Decision: {d3.decision} | {d3.reasoning[:120]}")

    # Scenario 4: Bad memory tightens rules — R:R 1.6 would pass normally, fails at strict 1:2
    print("\n>>> Scenario 4: Poor track record (WR 30%) tightens R:R to 1:2")
    d4 = evaluate_rule_based(
        fund_direction=1, fund_score=0.65, fund_confidence=0.75, fund_tradable=True,
        fund_evaluated=True,
        tech_direction=1, tech_confidence=0.80,
        tech_strategy="Long at market",
        current_price=1.1540, nearest_support=1.1530, nearest_resistance=1.1575,
        mem_total=10, mem_wins=3, mem_losses=7, mem_win_rate=30.0,
    )
    print(f"Decision: {d4.decision} | {d4.reasoning[:140]}")
    # همان هندسه با حافظه خوب → APPROVED
    d4b = evaluate_rule_based(
        fund_direction=1, fund_score=0.65, fund_confidence=0.75, fund_tradable=True,
        fund_evaluated=True,
        tech_direction=1, tech_confidence=0.80,
        tech_strategy="Long at market",
        current_price=1.1540, nearest_support=1.1530, nearest_resistance=1.1575,
        mem_total=10, mem_wins=7, mem_losses=3, mem_win_rate=70.0,
    )
    print(f"Same geometry with good memory (WR 70%): {d4b.decision}")

    # Scenario 5: Both neutral
    print("\n>>> Scenario 5: Both neutral")
    d5 = evaluate_rule_based(
        fund_direction=0, fund_score=0.05, fund_confidence=0.50, fund_tradable=False,
        fund_evaluated=True,
        tech_direction=0, tech_confidence=0.60,
        tech_strategy="Stand aside",
        current_price=1.1540, nearest_support=1.1530, nearest_resistance=1.1560,
    )
    print(f"Decision: {d5.decision} | {d5.reasoning[:120]}")
