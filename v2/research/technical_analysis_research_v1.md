# Technical Analysis Engine — Research & Integration Master Prompt (فاز Research)

> نسخه: Research-v1
> تاریخ تولید: 2026-08-28
> هدف: Reverse Engineering سه ریپازیتوری هدف و Gap Analysis با Technical Module فعلی پروژه.
> وضعیت: **فاز Research — بدون کدنویسی. منتظر تأیید شما برای ورود به فاز Implementation.**

---

## ۱) خلاصه اجرایی

- **Current Project (= v2)** یک Technical Engine بسیار قوی نسبت به هر سه ریپازیتوری در زمینه **Smart Money Concepts (SMC)** و **Multi-Timeframe** دارد: BOS، FVG، Order Block، Liquidity Sweep، OTE، PDH/PDL، Swing Detection، و یک MTF Matrix با ۷ تایم‌فریم + HTF Bias.
- سه ریپازیتوری هدف **هیچ‌کدام SMC/Price-Structure Engine ندارند**:
  - **AgenticTrading**: Classic TA (RSI/MACD/BB/SMA) + LLM decision + полноценный backtest با costs/market rules/pipeline.
  - **TradingAgents**: Classic indicators (SMA/EMA/MACD/RSI/Boll/ATR/VWMA/MFI) + Multi-Agent Debate + Structured Output.
  - **ai-hedge-fund**: Technical Engine اصلاً shipped نیست؛ معماری، Point-in-Time Data، Backtest و Separation of Views vs Positions قوی است.
- قوی‌ترین الهام‌گیری‌ها برای پروژه من:
  1. **Deterministic Risk Clamps / Portfolio Construction** از ai-hedge-fund (Separation: view vs position).
  2. **Backtest + Prompt-Coverage/Cost/Market-Rules** از AgenticTrading.
  3. **Structured Agent Output + Multi-Agent Debate + Reflection/Memory** از TradingAgents.
  4. **Structured Technical Output Contract + Evidence/Confluence List** جدید است (PROPOSED) — هیچ ریپویی این را کامل ندارد.

---

## ۲) تحلیل Current Technical Module (`v2/agents/technical/`)

### ۲.۱ معماری و فایل‌ها

| فایل | نقش | کلاس/تابع اصلی |
|---|---|---|
| `v2/agents/technical/technical_analyzer.py` | موتور deterministic امتیاز ۱۰ فاکتوری + روایت LLM | `TechnicalMetrics`, `ComponentScores`, `LLMStrategy`, `TechnicalReport`, `TechnicalAgent.analyze()` |
| `v2/agents/technical/mtf_scanner.py` | اسکن ۷ TF بدون LLM | `TimeframeScore`, `MTFMatrix`, `scan_timeframes()` |
| `v2/agents/risk/risk_manager.py` | Strategy Engine + Trade Structuring | `StrategyPlan`, `StrategySheet`, `build_strategy_sheet()`, `evaluate_rule_based()` |
| `v2/orchestration/run_phase3.py` | ارکستراسیون + امتیاز نهایی + ذخیره پلن‌ها | `main()` |
| `v2/backtest/` | Walkforward + IS/OOS + costs | `runner.py`, `walkforward.py`, `engine.py`, `config.py` |

### ۲.۲ Data Flow (OBSERVED)

```text
OHLCV (yfinance)
  ↓ `_fetch_price_history(ticker, interval)`
  ↓ H4/H2 resample از 1h (چون yfinance interval 4h/2h ندارد)
  ↓ normalize columns, volume fill=0
  ↓ Indicators (pandas_ta_classic)
  ↓ smartmoneyconcepts (swing_highs_lows / bos_choch / fvg / ob / liquidity / retracements / previous_high_low)
  ↓ 10 Component Scores
  ↓ Weighted Technical Score
  ↓ Technical Confidence
  ↓ LLM Narrative (فقط برای قوی‌ترین TF در MTF Scanner)
  ↓ MTFMatrix + HTF Bias
  ↓ build_strategy_sheet() → ACTIVE/PENDING/INVALID per horizon
  ↓ dispatch Risk Decision → StrategyPlanDB → live_watcher zone-hit
```

### ۲.۳ خروجی‌های فعلی

- `TechnicalMetrics`: `current_price`, `technical_score`, `technical_confidence`, `components` (10 فاکتور)، و یک سری raw context برای LLM.
- `TechnicalReport`: `direction ∈ {1,-1,0}`, `score`, `confidence`, `strategy`, `reasoning`.
- `MTFMatrix`: `scores` (per TF), `htf_bias`, `htf_bias_label`, `valid_timeframes()`, `strongest()`.
- `StrategySheet`: `plans` با `status`، `entry_low/high`, `stop_loss`, `take_profit`, `rr_ratio`, `invalidation_price`, `counter_bias`, `ttl_hours`.
- در `run_phase3`، Technical فقط به `TechnicalReport` و سپس به Strategy Sheet می‌رسد؛ **یک Output Contract غنی‌تر مانند `direction/score/confidence/market_regime/trend/momentum/volatility/smc_context/mtf_alignment/confluence[]` وجود ندارد** (INFERRED).

### ۲.۴ Indicatorها و Featureها (OBSERVED)

| دسته | مورد | پارامترها / منبع |
|---|---|---|
| Trend | `EMA50`, `EMA200` | `df.ta.ema(length=50/200)` |
| Trend | `Supertrend` | `length=10, multiplier=3`؛ استفاده فقط برای confirm/divergence |
| Momentum | `RSI` | `length=14` |
| Momentum | `MACD` historgram | `fast=12, slow=26, signal=9`؛ فقط histogram |
| Regime/Volatility | `ADX` | `length=14` |
| Regime/Volatility | `CHOP` | `length=14`؛ آستانه 38.2/61.8 |
| Price Action | `CDL_*` | 10 کندل: engulfing, hammer, shootingstar, morningstar, eveningstar, piercing, darkcloudcover, 3white, 3black, harami |
| Structure | `swing_highs_lows` | `swing_length=5` |
| Structure | `bos_choch` | `close_break=True`؛ کد فقط `BOS` را استفاده می‌کند |
| SMC | `fvg` | `join_consecutive=False`؛ Bull/Bear + Mitigated |
| SMC | `ob` | `close_mitigation=False`؛ Bull/Bear OB |
| Liquidity | `liquidity` | `swings`؛ Swept در ~۵ کندل اخیر |
| OTE/Fib | `retracements` | بازه 61.8%–78.6% |
| PDH/PDL | `previous_high_low` | time_frame="1D"؛ شکست/نزدیکی |
| MTF | `MTFMatrix` | W1, D1, H4, H2, H1, M30, M15؛ HTF=D1/W1 |

### ۲.۵ Signal Generation (OBSERVED)

وزن‌ها (در `calculate_technical_metrics`):

```text
structure=0.20, smc_location=0.15, trend=0.13, liquidity_sweep=0.12,
mtf_confluence=0.10, ote_zone=0.10, pdh_pdl=0.08, momentum=0.06,
volatility=0.04, price_action=0.02
```

- `technical_score = Σ weight × component` → clamp [-1, +1].
- شرط ارزیابی: حداقل `structure` یا `smc_location` باید ارزیابی شده باشد.
- `direction`: `score > 0.1 → +1`؛ `score < -0.1 → -1`؛否則 0.
- `technical_confidence = data_quality(0.2) + setup_quality(0.3) + agreement(0.5)`:
  - data_quality: trend + smc_location هر کدام 0.5.
  - setup_quality: active OB یا FVG هر کدام 0.5.
  - agreement: توافق علامتِ `structure/trend/momentum` تقسیم بر 3.

### ۲.۶ MTF (OBSERVED)

- `mtf_scanner.scan_timeframes()`: همه ۷ TF را با همان `calculate_technical_metrics` صدا می‌زند.
- `DIRECTION_DEADBAND=0.15`
- `HTF_WEIGHTS = {"D1":0.6, "W1":0.4}`
- `_compute_htf_bias()`: D1/W1 هم‌جهت → همان؛ تضاد → 0 («Mixed»).
- `strongest()`: بیشترین `|score|×confidence` در MTF/LTF؛ فقط این TF با LLM روایت می‌شود.

### ۲.۷ نقاط قوت Current Module

1. **Deterministic SMC**: FVG, OB, Liquidity Sweep در پایتون محاسبه می‌شود؛ LLM آن‌ها را تولید نمی‌کند.
2. **MTF Matrix**: واقعاً multi-timeframe است (W1..M15) و هزینه LLM ثابت است.
3. **No-Lookahead walk-forward**: در `backtest/walkforward.py` فقط داده‌های `[0..t]` به اسکورر داده می‌شود.
4. **Strategy Sheet + Zone Hit**: پلن‌های ACTIVE/PENDING/INVALID + LIVE watcher + Trade Memory.
5. **Language/Context**: طراحی پروژه فارسی + routing کامل ارزها (direct/inverse/index).

### ۲.۸ نقاط ضعف Current Module

1. **ادعای CHoCH در کلاس نیست**: `bos_choch` فراخوانی می‌شود اما کد فقط ستون `BOS` را می‌خواند و `CHoCH` استفاده نمی‌شود.
2. **Confluence Engine صریح نیست**: weight-sum دارد، ولی خروجی `confluence[]`, `supporting_evidence[]`, `contradicting_evidence[]`, `risks[]` ندارد.
3. **Confidence بسیار ساده است**: فقط 3 مؤلفه (data quality / setup / agreement)؛ از MTF agreement، regime، volatility، data quality، conflicting signals، ATR، spread، data staleness (e.g., YF stale guard) استفاده نمی‌کند.
4. **Market Regime Enum وجود ندارد**: فقط `trend_status` + `ADX/CHOP`؛ هیچ enum ثابتی مثل `TRENDING/RANGING/HIGH_VOLATILITY/BREAKOUT/REVERSAL/UNCLEAR` نیست.
5. **Momentum فقط RSI + MACD Histogram**؛ از `-DI/+DI`, `stoch`, `MFI`, `ROC`, `CMF`, `VWAP`, `volume profile` استفاده نمی‌کند.
6. **قیمت/سقف/کف (ATR) در Technical Module نیست**؛ ATR فقط در `MarketContext` بنیادی است. برای slot sizing و stop normalisation مهم است.
7. **Supply/Demand فقط بر اساس OB/FVG**؛ نه از swing lows/highs، نه از liquidity pool ها، نه از displacement.
8. **کدهای پایتون و PR مشخص نیست**: بک‌تست بازیابی شده است اما هیچ آماری از وزن‌ها و sensitivity analysis وجود ندارد.
9. **Test Coverage missing**: پوشه `v2/tests` شامل تست Technical/MTF/Risk نیست (برخلاف ادعای `FILE_MAP.md` که تست‌ها را می‌گوید).
10. **Output Contract**: `TechnicalReport` فقط direction/score/conf/strategy; میدان‌های `market_regime`, `trend`, `momentum`, `volatility`, `smc_context`, `mtf_alignment`, `confluence[]`, `risks[]` وجود ندارد.
11. **Backtest production mismatch**: هرچند walkforward از `calculate_technical_metrics` استفاده می‌کند، اما برای H4/H2 (resample از 1h) پشتیبانی کامل ندارد؛ `preload_history` فقط `4h/60m/15m/1d` را پوشش می‌دهد (INFERRED).

---

## ۳) AgenticTrading — Deep File-Level Analysis

> Repository: `https://github.com/Open-Finance-Lab/AgenticTrading` (cloned at `/tmp/target_repos/AgenticTrading`)
> نکته: ریپوی پیش‌فرض «TauricResearch/AgenticTrading» وجود ندارد؛ ریپوی واقعی `Open-Finance-Lab/AgenticTrading` است.

### ۳.۱ ساختار و فایل‌های Technical

```
dashboard/backend/
├── domain/backtesting/
│   ├── features.py            # TechnicalIndicators (RSI/MACD/BB/SMA)
│   ├── reference_agent.py     # Rule-based deterministic agent
│   ├── portfolio_manager.py   # LLM decision workflow + position sizing
│   ├── engine.py              # Hourly backtest engine
│   ├── market_rules.py        # trading calendar / T+1 / costs
│   ├── metrics.py             # Sharpe etc.
│   └── (baselines/…)          # baselines
├── infrastructure/llm/
│   ├── backtest_harness.py    # System prompt + Anthropic call + JSON parsing
│   ├── validator.py           # Strict JSON/Pydantic validation + PortfolioConstraints
│   ├── pipeline_runner.py     # Sequential sub-agent pipeline + post-trade prompt patching
│   └── prompts.py             # Custom algorithm prompt (Chinese market)
├── infrastructure/market_data/ # Alpaca/ifind/vnpy providers
└── domain/trading/
    ├── portfolio.py           # portfolio state/valuation
    └── execution.py           # order execution, T+1, costs
```

### ۳.۲ Technical Indicators (OBSERVED)

در `features.py`:

| Indicator | پارامتر | استفاده |
|---|---|---|
| RSI | `length=14` | Overbought/Oversold |
| MACD | `fast=12, slow=26, signal=9` | MACD line & signal |
| Bollinger Bands | `length=20, std=2` | upper/lower |
| SMA | `20`, `50` | trend/support/dynamic MR |

- آستانه minimum: `len(df)>=50` برای SMA50.
- Default/padding برای داده‌های کافی نبوده: RSI=50, MACD/SMA/BB=mean.

### ۳.۳ Signal Generation (OBSERVED)

- **Rule-based** (`reference_agent.py`):
  - BUY: `rsi < 30 and price < sma20` and no position.
  - SELL: `rsi > 70 or price > sma50 * 1.02`.
  - Skip if rsi/sma20 NaN/None.
- **LLM-based** (`validator.py`):
  - `SAFE_TRADING_PROMPT`: «at least 2 of >SMA20 / >SMA50 / SMA20>SMA50 / MACD>signal / RSI 35-70 / positive relative strength / recovery from oversold».
  - Strong BUY: at least 4 of those.
  - Avoid BUY if `price < SMA50 and MACD bearish` or `RSI > 80`.
  - SELL: 2 из weakening conditions.
  - Position sizing guidance: ~10% medium, 15-20% strong, max 25% per name.
  - Confidence: BUY/SELL 0.65-0.90, HOLD 0.30-0.60, skip <0.3.
- **Candidate ranking** (`portfolio_manager.py`): `_trend_score()` = price>SMA20, price>SMA50, SMA20>SMA50, MACD>signal, RSI 45-70 bonus, RSI>80 penalty, distance above SMA50. Top-12 + holdings always included.

### ۳.۴ LLM Role

- LLM **تصمیم نهایی buy/sell/hold می‌گیرد**، نه score.
- LLM تنها با `market_snapshot` JSON شامل قیمت، RSI، MACD، SMA20/50، BB کار می‌کند.
- Strict JSON schema (`LLMTradingDecision`): action, symbol, confidence, reasoning, position_size, stop_loss_price, take_profit_price.
- `validator.py` اعمال PortfolioConstraints (cash, max_position_size, max_position_value, min_confidence, max daily trades).
- **H6 Coverage**: در strict LLM، اگر پاسخ LLM parse نشود -> rule-based fallback و count می‌شود؛ budget `2%` از کل steps.

### ۳.۵ Backtest / Risk (OBSERVED — قوی)

- Hourly backtest بر روی DJIA 30.
- Transaction costs (commission, stamp duty, transfer fee, slippage).
- T+1 settle logic.
- `market_rule_calendar` (calender / trading-hours).
- forward-filled price cache برای valuation.
- Post-trade analysis agent + `prompt_patches` (LLM روزانه پرامپت استپ‌ها را بهبود می‌دهد).
- Equally: rule-based fallback, bounds on actions, max order shares.

### ۳.۶ نقاط قوت نسبت به پروژه من

| قابلیت | AgenticTrading | Current |
|---|---|---|
| Strict-LLM coverage tracking | OBSERVED (`llm_decisions`, fallback ratio) | Not Found |
| Prompt auto-patching after post-trade | OBSERVED (`run_post_trade_analysis`) | Not Found |
| Backtest costs + market rules + T+1 | OBSERVED | Partial (simple costs) |
| Multi-symbol portfolio sizing | OBSERVED (trend-score ranking + LLM sizing) | Per-currency strategy |
| Validity/schema of LLM actions | OBSERVED (Pydantic + PortfolioConstraints) | RiskDecision Pydantic |
| LLM decision -> rule-based fallback with coverage | OBSERVED | Technical LLM fallback simple |
| Custom free-form strategy prompt scaffold | OBSERVED (`CUSTOM_STRATEGY_OUTPUT_CONTRACT`) | Partial (`risk prompt`) |

### ۳.۷ نقاط ضعف / Ignore

- **SMC, Market Structure, Multi-TF, Supply/Demand:** Not Found (مناسب برای الهام نیست؛ برای Forex طراحی نشده).
- **ML features:** Not Found.
- Context: DJIA equities، intraday/daily bars، LLM decides direction (برخلاف اصل «LLM فقط تفسیر» در پروژه من — Ignore).

---

## ۴) TradingAgents — Deep File-Level Analysis

> Repository: `https://github.com/TauricResearch/TradingAgents` (cloned at `/tmp/target_repos/TradingAgents`)

### ۴.۱ معماری Multi-Agent (OBSERVED)

LangGraph `StateGraph` (`graph/setup.py`):

```text
START
→ Market Analyst → [tools_market → Market Analyst ... → Msg Clear Market]
→ Sentiment Analyst → tools
→ News Analyst → tools
→ Fundamentals Analyst → tools
→ Bull Researcher ↔ Bear Researcher (debate, max rounds=1)
→ Research Manager
→ Trader
→ Aggressive Analyst ↔ Conservative Analyst ↔ Neutral Analyst (risk debate)
→ Portfolio Manager
→ END
```

- 4 analysts: `market`, `sentiment`, `news`, `fundamentals` (first `market`, then `sentiment`, `news`, `fundamentals`).
- 2 researchers: `Bull Researcher`, `Bear Researcher`.
- 1 manager: `Research Manager`.
- 1 trader: `Trader`.
- 3 debators: `Aggressive`, `Conservative`, `Neutral`.
- 1 PM: `Portfolio Manager`.
- 1 `Msg Clear N` per analyst.

### ۴.۲ Technical Analyst / Market Analyst (OBSERVED)

- `markets/market_analyst.py`:
  - Tools: `get_stock_data`, `get_indicators`, `get_verified_market_snapshot`.
  - System prompt: انتخاب ۸ Indicator مکمل از لیست.
  - «call get_stock_data first... use get_indicators with exact names... treat verified market snapshot as source of truth».
  - Output: detailed markdown report + markdown table.
- `technical_indicators_tools.py` → `route_to_vendor("get_indicators", ...)`.

### ۴.۳ Indicators (OBSERVED)

| Indicator | نوع | جزئیات |
|---|---|---|
| `close_50_sma` | SMA 50 | trend |
| `close_200_sma` | SMA 200 | long-term trend |
| `close_10_ema` | EMA 10 | short-term momentum |
| `macd` | MACD | momentum |
| `macds` | MACD signal | crossing |
| `macdh` | MACD histogram | divergence |
| `rsi` | RSI | overbought/oversold |
| `boll`, `boll_ub`, `boll_lb` | Bollinger Bands | volatility |
| `atr` | ATR | vol-based stop sizing |
| `vwma` | VWMA | volume-weighted avg |
| `mfi` | Money Flow Index | volume momentum |

- Alpha Vantage mapping (`alpha_vantage_indicator.py`) same.
- `stockstats_utils.py` → `stockstats` wrap + `load_ohlcv` (yfinance via stockstats).
- `get_verified_market_snapshot` acts as final source of truth for exact OHLCV/price-level claims.
- `symbol_utils.normalize_symbol` resolves forex/commodities (e.g., XAUUSD+ → GC=F).

### ۴.۴ Signal / Score / Confluence (OBSERVED)

- **No deterministic technical score**; no component score; no `technical_confidence`.
- **Signal = narrative + final rating**
  - `PortfolioRating` = 5-tier: `Buy / Overweight / Hold / Underweight / Sell`.
  - `TraderAction` = `Buy / Hold / Sell`.
  - `SentimentBand` = `Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish`, with `overall_score` (0-10) and `confidence` (`low/medium/high`).
  - `PortfolioDecision` = `rating`, `executive_summary`, `investment_thesis`, `price_target`, `time_horizon`; rendered to Markdown.
  - `SignalProcessor` picks rating via deterministic regex (`rating.py`).

### ۴.۵ Data Quality / Lookahead (OBSERVED — بسیار قوی)

- `stockstats_utils.load_ohlcv()`: cache per symbol, filter `Date <= curr_date`, `_assert_ohlcv_not_stale(max_stale_days=10)`, `_needs_same_day_refresh(TTL=900s)`.
- `filter_financials_by_date()`: remove financial statement columns with fiscal period after curr_date.
- Vendor routing (`interface.py`): explicit `data_vendors` chain, `NoMarketDataError` + sentinel `NO_DATA_AVAILABLE`; optional macro/prediction categories degrade, never fabricate.
- `resolve_instrument_identity`: anchor to real company/sector to prevent wrong narrative (issue #814).

### ۴.۶ Backtesting (PARTIAL — Not a numeric TA backtest)

- CLI is a **single-date simulation analysis**, not a bar-by-bar strategy backtest.
- `Reflector.reflect_on_final_decision`: reviews past decision using raw/alpha return vs benchmark.
- Actual numerical backtest is not part of the core technical path.

### ۴.۷ Prompt Engineering (OBSERVED)

- Market analyst prompt: exhaustive indicator descriptions and usage/tips; required tool-call order; verified snapshot; Markdown table.
- `get_language_instruction()`: output language config.
- Structured output via native provider schemas (OpenAI `json_schema`, Gemini `response_schema`, Anthropic tool-use).
- `SentimentReport.render_sentiment_report`, `render_pm_decision` deterministic header for parsing.

### ۴.۸ نقاط قوت نسبت به پروژه من

| قابلیت | TradingAgents | Current |
|---|---|---|
| Multi-agent debate (bull/bear + risk) | OBSERVED | Not current |
| Structured output enum rating (5-tier) | OBSERVED (`PortfolioRating`) | Technical direction only |
| Vendor routing + failover + no-data sentinel | OBSERVED | Not current |
| Data staleness guard | OBSERVED (`MAX_OHLCV_STALE_DAYS`) | Not current |
| Verification tool for LLM claims | OBSERVED (`get_verified_market_snapshot`) | Not current |
| Memory log + reflection | OBSERVED (`memory.py`, `Reflector`) | Partial (trade memory only) |
| i18n prompt | OBSERVED | Partial |

### ۴.۹ نقاط ضعف / Ignore

- No SMC, no MTF, no BOS/CHoCH/liquidity, no candlestick engine.
- Technical score/confidence numeric ندارد.
- No real bar-by-bar backtest core.
- طراحی برای equities/crypto با decision date؛ برای Forex intraday/SMC design مناسب نیست.

---

## ۵) ai-hedge-fund — Deep File-Level Analysis

> Repository: `https://github.com/virattt/ai-hedge-fund` (cloned at `/tmp/target_repos/ai-hedge-fund`)

### ۵.۱ معماری کل (OBSERVED)

```text
FundSpec (mandate)
  → Fund (strategies + staff)
    → run_cycle:
        data (point-in-time) → analysts/alpha models → blend_signals → apply_limits → build_orders → broker → CycleRecord
```

- `AlphaModel` interface: `name`, `predict(ticker, date, data_client) -> Signal`.
- 2 flavors: `QuantModel` (pure math) and `LLMAgent` (persona over `FundamentalsSnapshot`).
- Shipped models: `pead` (quant) + `buffett`, `munger`, `graham`, `lynch`, `druckenmiller` (LLM personas).
- Pipeline: `pipeline/run_cycle.py`.
- Portfolio construction: `portfolio/construction.py`.
- Risk: `risk/limits.py`.
- Execution: `pipeline/execution.py`.
- Backtesting: `backtesting/engine.py` (per model) + `backtesting/fund.py` (full fund).
- Event study: `event_study/`.

### ۵.۲ Technical Analysis — OBSERVED finding

- **Technical Engine در نسخه فعلی موجود نیست.**
- `signals/base.py` فقط یک helper `_compute_rsi(prices, period=14)` دارد؛ **هیچ‌جا استفاده نشده**.
- `ROADMAP.md`: Momentum, Mean reversion, Market-regime detection همه ⬜ (Not shipped).
- قیمت فقط برای «mark price» و size/rebalance استفاده می‌شود؛ نه برای تولید Signal تکنیکال.

### ۵.۳ Signal → Portfolio Flow (OBSERVED)

```text
AlphaModel.predict()
  → Signal(value in [-1,1], reasoning, metadata)
  → blend_signals()
      conviction_t = Σ(w_m * value_mt) / Σ(w_m)
      weight_t = conviction_t / Σ|convictions| * gross_target
      market_neutral → demean cross-sectionally
  → apply_limits()
      per-ticker cap → gross cap (hard, not negotiable)
  → build_orders()
      target_shares = floor(weight * equity / mark)
  → broker.place_order()
  → CycleRecord (orders, fills, nav, thesis, clamps)
```

### ۵.۴ Backtest (OBSERVED — قوی و Point-in-Time)

- `BacktestEngine.run_alpha()`: per-model, fixed holding, equal-dollar, no lookahead (edge-trigger, re-arm on flat).
- `backtest_fund()`: whole fund in loop over rebalance grid, persistent `SimBroker`, benchmark NAV, metrics (Sharpe, MaxDD, excess return).
- Data client filters by `as_of`/`filing_date`; fundamentals snapshot `build_snapshot` uses **filed** periods not report periods.
- `FundamentalsSnapshot.content_hash` = prompt cache key; `LLMAgent` caches exact prompt+response.

### ۵.۵ Risk (OBSERVED — deterministic)

- `apply_limits` = hard caps per ticker + gross exposure.
- Deterministic; clamp removed exposure → cash, not redistributed.
- This matches my تمایز «View vs Position» — LLM فقط view، not execution.

### ۵.۶ نقاط قوت نسبت به پروژه من

| قابلیت | ai-hedge-fund | Current |
|---|---|---|
| View/Position separation | OBSERVED | Partial |
| Hard deterministic risk clamps | OBSERVED (`RiskLimits`) | Partial (rule-based risk, not hard portfolio cap) |
| Prompt cache (content hash) | OBSERVED (`PromptCache`) | Not current |
| Full-fund backtest + ledger (every thesis/order/fill) | OBSERVED | Partial |
| Point-in-time data discipline (filing_date) | OBSERVED | Partial (fundamental fetcher, not technical) |
| Pluggable alpha model registry | OBSERVED | Not current |

### ۵.۷ نقاط ضعف / Ignore

- Technical Engine: Not Found.
- LLM personas over fundamentals, not SMC / price action.
- No MTF, no SMC, no candlestick, no price structure.

---

## ۶) Comparative Matrix

مواردی که با **OBSERVED** دیده نشده‌اند `Not Found` تعیین شده‌اند.

| قابلیت | Current Project | AgenticTrading | TradingAgents | ai-hedge-fund |
|---|---|---|---|---|
| Trend | OBSERVED (EMA50/200+Supertrend) | OBSERVED (SMA20/50) | OBSERVED (SMA50/200, EMA10) | Not Found shipped |
| Momentum | OBSERVED (RSI+MACD hist) | OBSERVED (RSI+MACD) | OBSERVED (RSI, MACD, MFI) | Not Found shipped |
| Volatility | OBSERVED (ADX, CHOP) | PARTIAL (BB only) | OBSERVED (ATR, BB) | Not Found shipped |
| Price Action | OBSERVED (CDL patterns) | Not Found | Not Found (LLM prose) | Not Found |
| Candlestick | OBSERVED (10 CDL patterns) | Not Found | Not Found | Not Found |
| Market Structure | OBSERVED (swings+BOS) | Not Found | Not Found | Not Found |
| BOS | OBSERVED | Not Found | Not Found | Not Found |
| CHoCH | Not Found (bos_choch but code ignores CHoCH) | Not Found | Not Found | Not Found |
| Liquidity | OBSERVED (liquidity sweep) | Not Found | Not Found | Not Found |
| FVG | OBSERVED | Not Found | Not Found | Not Found |
| Order Block | OBSERVED | Not Found | Not Found | Not Found |
| Supply/Demand | OBSERVED (OB/FVG) | Not Found | Not Found | Not Found |
| Fibonacci | PARTIAL (OTE via retracements) | Not Found | NotImplemented | Not Found |
| OTE | OBSERVED (61.8%–78.6%) | Not Found | Not Found | Not Found |
| Multi-Timeframe | OBSERVED (7 TF + HTF bias) | Not Found | Not Found | Not Found |
| Confluence | OBSERVED (weighted score + Risk decision) | OBSERVED (LLM rules + trend-rank) | OBSERVED (multi-agent debate + PM) | OBSERVED (model blend) |
| Zone Scoring | OBSERVED (smc_location, strategy sheet) | Not Found | Not Found | Not Found |
| Technical Score | OBSERVED ([-1,1]) | Not Found | Not Found | Not Found |
| Backtesting | OBSERVED (walk-forward IS/OOS costs) | OBSERVED (hourly, costs, market rules, strict LLM) | PARTIAL (single-date + reflection) | OBSERVED (per-model + full fund) |
| ML Features | Not Found | Not Found | Not Found | Not Found shipped |
| Signal Confidence | OBSERVED (0-1, 3 component) | OBSERVED (LLM 0-1) | PARTIAL (sentiment low/med/high) | PARTIAL (LLM % scales value) |
| Market Regime | PARTIAL (ADX/CHOP, no enum) | Not Found | Not Found | Not Found shipped |
| Agent Architecture | OBSERVED (Head/Fund/Tech/Risk) | OBSERVED (pipeline runtime) | OBSERVED (debate graph) | OBSERVED (fund/alpha models) |
| LLM Integration | OBSERVED (Tech narrative, Risk optional) | OBSERVED (decision maker) | OBSERVED (analysts + debate + PM) | OBSERVED (fundamental personas) |
| Risk Management | OBSERVED (risk rules + memory + strategy sheet) | OBSERVED (confidence, cash, T+1, costs etc.) | OBSERVED (risk debate + guidance) | OBSERVED (hard limits + clamps) |

---

## ۷) نقاط قوت هر ریپو نسبت به پروژه من

### ۷.۱ AgenticTrading

1. **LLM Decision Coverage / Strict-LLM budget**: `llm_decisions`, `strict_llm_fallbacks`, `STRICT_LLM_MAX_FALLBACK_RATIO`.
2. **Backtest realism**: transaction costs profile, T+1, market-rule calendar, per-turn audit, forward-filled valuation.
3. **Post-trade prompt patching**: روزانه LLM episode را می‌بینید و پرامپت استپ‌های بعدی را patch می‌کند.
4. **Free-form strategy prompt with fixed output contract**: کاربر می‌تواند استراتژی بنویسد ولی JSON execution contract پایدار می‌ماند.
5. **Candidate ranking**: top-12 by trend score + always include holdings (جلوگیری از bias رتبه‌بندی بر اساس |RSI-50|).

### ۷.۲ TradingAgents

1. **Debate Architecture**: Bull/Bear + 3 risk debators.
2. **Structured enum outputs**: `PortfolioRating`, `TraderAction`, `SentimentBand` با render helper.
3. **Data quality guards**: `get_verified_market_snapshot`, `_assert_ohlcv_not_stale`, `MAX_OHLCV_STALE_DAYS`, vendor routing.
4. **Instrument identity anchoring**: جلوگیری از narrative mismatch.
5. **Multi-provider structured output** (json_schema/response_schema/tool-use).
6. **Memory + Reflection**: decision log و reflection با alpha/outcome.

### ۷.۳ ai-hedge-fund

1. **Separation of Views vs Positions**: `AlphaModel` -> `Signal`, portfolio construction owns mechanics.
2. **Deterministic risk clamps**: hard limits, explicit `ClampEvent`.
3. **Prompt cache / content_hash**: identical prompt -> cache hit.
4. **Point-in-time discipline**: filing_date / as_of filtering, `FundamentalsSnapshot`.
5. **Full-fund backtest with ledger**: każdy `CycleRecord` has orders/fills/thesis/nav/clamps.
6. **Clean interface**: pluggable alpha model registry.

---

## ۸) نقاط قوت پروژه من نسبت به ریپوها

| مزیت | توضیح |
|---|---|
| **SMC native** | FVG/OB/Liquidity Sweep/OTE/PDH-PDL real computed; نه فقط متن LLM |
| **MTF Matrix** | 7 TF + HTF bias؛ هیچ ریپویی این را ندارد |
| **Price Action / Candlestick** | 10 CDL patterns؛ TradingAgents/AHF/ATL ندارند |
| **Strategy Engine + Zone-hit watcher** | ACTIVE/PENDING/INVALID + live monitoring + trade memory |
| **No-Lookahead walkforward** | `backtest/walkforward.py` یک اسکورر historical deterministic |
| **Reverse-direction & cross-asset routing** | USD/JPY/XAU/OIL alignment |
| **LLM فقط روایت/تفسیر** | مطابق اصل طلایی؛ deterministic score در Python |
| **Token optimization** | فقط قوی‌ترین TF با LLM، Risk rule-based default |
| **Forex-native Pip / Spread / Slippage model** | `backtest/config.py` |

---

## ۹) Gap Analysis (مهم‌ترین فاصله‌ها)

1. **ثبات وزن‌های 10 فاکتور**: هیچ sensitivity analysis / walk-forward weight optimization وجود ندارد.
2. **Confidence تک‌بعدی**: MTF agreement، regime، conflicting signals، data quality، ATR/vol، stale data در `technical_confidence` نیست.
3. **Confluence/Evidence Contract**: `confluence[]`, `supporting_evidence[]`, `contradicting_evidence[]`, `risks[]` وجود ندارد.
4. **Market Regime Enum**: `TRENDING/RANGING/HIGH_VOLATILITY/LOW_VOLATILITY/BREAKOUT/REVERSAL/UNCLEAR` نیست.
5. **CHoCH استفاده نمی‌شود**؛ بازار target در `smartmoneyconcepts` دارد ولی کد فقط `BOS` می‌خواند.
6. **ATR/volatility در Technical Module نیست**؛ برای position size / stop-distance normalisation لازم است.
7. **Structured Technical Output Contract** مطابق Output Contract شما وجود ندارد (`trend`, `momentum`, `volatility`, `smc_context`, `mtf_alignment`, ...).
8. **Data Quality/Staleness Guard مشخص نیست** (مانند `MAX_OHLCV_STALE_DAYS` در TradingAgents).
9. **Strict decision/fallback metrics** مانند AgenticTrading نیست (coverage/token/cost audit).
10. **Test Coverage technical** موجود نیست.
11. **No Post-trade Prompt-Auto-Patch** (AgenticTrading) — برای Improve LLM narrative.
12. **No Prompt Cache** (ai-hedge-fund) — هر کال LLM برای narrative یک بار هزینه دارد.

---

## ۱۰) Change Plan (پیشنهادی — منتظر تأیید)

### P0 — هسته معماری (باید اجرا شود)

| تغییر | دلیل | منبع الهام | نوع | اولویت | تأثیر |
|---|---|---|---|---|---|
| 1. Create `TechnicalContext` / `TechnicalSignal` with full output contract (`symbol, direction, score, confidence, market_regime, time_horizon, trend, market_structure, momentum, volatility, smc_context, mtf_alignment, confluence[], supporting_evidence[], contradicting_evidence[], risks[], reasoning_summary`) | خروجی فعلی غنی نیست؛ LLM و Risk نیاز به evidence/conflict دارند | TradingAgents structured enum + Prompt Output Contract | ADD | P0 | Explainability + Risk quality |
| 2. Add `MarketRegimeEngine` (`TRENDING/RANGING/HIGH_VOLATILITY/LOW_VOLATILITY/BREAKOUT/REVERSAL/UNCLEAR`) | Regime برای confidence و risk مهم است | ai-hedge-fund planned regime | ADD | P0 | Signal quality + regime-aware risk |
| 3. Add `ConfluenceEngine` (deterministic): weight-based + evidence agreement + MTF agreement + regime + conflicting signals + data quality | Confidence فعلی ساده است | Current architecture extension | EXTEND | P0 | Robust confidence |
| 4. Make `CHoCH` actually used (extract both `BOS` and `CHoCH` from `smc.bos_choch`) | ادعا/کد mismatch | Current + SMC | EXTEND | P0 | Better structure detection |
| 5. Add ATR-based volatility factor (`atr_pct = ATR14/close`, band relative to ATR) | نیاز به position sizing/stop normalisation + regime | TradingAgents (`atr`) + Project | ADD | P0 | Risk + volatility |

### P1 — Backtest / Data Quality

| تغییر | دلیل | منبع الهام | نوع | اولویت | تأثیر |
|---|---|---|---|---|---|
| 6. Weight optimization + walk-forward sensitivity analysis (grid search, IS/OOS, Sharpe/PF, robustness) | وزن‌ها الآن بدون evidence | ai-hedge-fund + AgenticTrading | EXTEND | P1 | Statistical robustness |
| 7. Data staleness guard + data quality factor (`latest bar age`, volume availability, NaN ratio) | جلوگیری از stale data | TradingAgents `_assert_ohlcv_not_stale` | ADD | P1 | Data quality |
| 8. Multi-timeframe backtest support H4/H2 (from 1h) + full 7-TF walkforward | Current walkforward incomplete | Current + AgenticTrading | EXTEND | P1 | MTF validation |
| 9. Add `Confidence` and `evidence` metrics into backtest results (debug/log) | Audit gaps | Current | EXTEND | P1 | Explainability |

### P2 — LLM / Observability

| تغییر | دلیل | منبع الهام | نوع | اولویت | تأثیر |
|---|---|---|---|---|---|
| 10. Prompt cache (content_hash) for narrative LLM | cost reduction | ai-hedge-fund `PromptCache` | ADD | P2 | Cost |
| 11. Post-trade review / prompt-patch (optional) | better LLM narrative | AgenticTrading `post_trade_analysis` | ADD | P2 | Narrative auto-improvement |
| 12. LLM coverage/token/cost audit record | التزام به اصل deterministic | AgenticTrading H6 coverage | ADD | P2 | Auditing |

### ۱۰.۱ چه چیزهایی اضافه شود

- `agents/technical/prompts.py` — سیستم پرامپت structured output.
- `agents/technical/signals.py` / `technical_schema.py` — `TechnicalSignal`, `ConfluenceEvidence`, `MarketRegime`.
- `agents/technical/market_regime.py` — `detect_market_regime()`.
- `agents/technical/confluence.py` — `build_confluence()`.
- `agents/technical/data_quality.py` — staleness guard + data quality factor.
- `core/technical_config.py` — all weights/thresholds as dataclass+pydantic (not hardcoded).
- `tests/test_technical_analyzer*.py`, `tests/test_market_regime*.py`, `tests/test_confluence*.py`.
- `research/` folder (this report).

### ۱۰.۲ چه چیزهایی حذف/جایگزین شود

- `TechnicalReport` فعلی → `TechnicalSignal` جدید (backward-compatible wrapper can stay).
- Hardcoded weights in `calculate_technical_metrics` → config object.
- `technical_confidence` 3-component formula → new `ConfluenceEngine`.
- LLM prompt فقط `LLMStrategy` → full structured interpretation with evidence/risks.

### ۱۰.۳ چه چیزهایی دست‌نخورده بماند

- Deterministic core: `calculate_technical_metrics` (API قابل حفظ).
- `MTFMatrix`, `scan_timeframes`, `build_strategy_sheet`, Strategy Engine.
- `backtest/walkforward.py` no-lookahead design.
- Routing (`core/routing.py`).
- `run_phase3.py` orchestration contracts.
- Design principle: LLM فقط تفسیر، نه محاسبه score.

---

## ۱۱) Recommended Folder & File Architecture (PROPOSED)

```text
v2/
├── core/
│   ├── models.py                          # فعلی + افزودن TechnicalSignal/Schema
│   ├── routing.py                         # بدون تغییر
│   └── technical_config.py                # NEW: weights/thresholds/regimes aliases
├── agents/
│   ├── technical/
│   │   ├── __init__.py
│   │   ├── technical_analyzer.py          # فعلی — deterministic components
│   │   ├── mtf_scanner.py                 # فعلی — MTFMatrix
│   │   ├── indicators.py                  # NEW: isolated indicator wrappers
│   │   ├── price_action.py                # NEW: candlestick + swing labels
│   │   ├── market_structure.py            # NEW: BOS + CHoCH + HL/LH etc.
│   │   ├── smc.py                         # NEW: FVG/OB/liquidity/OTE
│   │   ├── market_regime.py               # NEW: regime detection
│   │   ├── confluence.py                  # NEW: weighted confluence + evidence
│   │   ├── technical_signal.py            # NEW: TechnicalSignal schema + builder
│   │   ├── data_quality.py                # NEW: staleness/data quality
│   │   └── prompts.py                     # NEW: full LLM interpretation prompt
│   ├── risk/
│   │   ├── risk_manager.py                # فعلی — strategy sheet (keep)
│   │   └── portfolio_risk.py              # NEW: hard risk clamps (ai-hedge-fund inspired)
├── backtest/
│   ├── engine.py, runner.py, walkforward.py, config.py  # فعلی + MTF support
├── orchestration/
│   ├── run_phase3.py                      # فعلی + TechnicalSignal integration
│   └── trade_evaluator.py                 # فعلی
├── tests/
│   ├── test_technical_analyzer.py         # NEW
│   ├── test_market_regime.py              # NEW
│   ├── test_confluence.py                 # NEW
│   ├── test_mtf_scanner.py                # NEW
│   └── test_strategy_sheet.py             # NEW
└── research/
    └── technical_analysis_research_v1.md  # this report
```

### ۱۱.۱ مسئولیت هر فایل

| فایل | مسئولیت | کلاس/توابع پیشنهاد شده |
|---|---|---|
| `indicators.py` | محاسبه تمام common indicators با guard/NaN | `compute_rsi`, `compute_macd`, `compute_ema`, `compute_sma`, `compute_atr`, `compute_adx`, `compute_chop`, `compute_bollinger`, `compute_mfi`, `compute_supertrend` |
| `price_action.py` | کندل‌ها + Swing Highs/Lows + HH/HL/LH/LL | `detect_candlestick_patterns`, `detect_swings`, `classify_market_structure` |
| `market_structure.py` | BOS + CHoCH + momentum legs | `detect_bos`, `detect_choch`, `classify_structure` |
| `smc.py` | FVG, OB, Liquidity, OTE, PDH/PDL | `detect_fvg`, `detect_order_blocks`, `detect_liquidity_sweep`, `detect_ote`, `detect_pdh_pdl` |
| `market_regime.py` | regime detection | `detect_market_regime(df, atr, adx, chop) -> MarketRegime` |
| `confluence.py` | weighted evidence + agreement | `build_confluence(components, regime, mtf_alignment, data_quality) -> ConfluenceResult` |
| `technical_signal.py` | final schema + builder | `build_technical_signal(...) -> TechnicalSignal` |
| `data_quality.py` | stale/volume/NaN score | `compute_data_quality(df, curr_date)` |
| `prompts.py` | LLM interpretation | `build_technical_narrative_prompt(signal)` |

---

## ۱۲) Recommended Technical Pipeline (PROPOSED)

```text
OHLCV
→ Data Validation / Data Quality (staleness, NaN, volume, bar age)
→ Feature Engineering (returns, ATR%, range, body/wick ratios)
→ Indicator Engine (RSI/MACD/EMA/SMA/ATR/ADX/CHOP/Boll/MFI/Supertrend)
→ Price Action Engine (CDL + Swing + HH/HL/LH/LL)
→ Market Structure Engine (BOS + CHoCH + leg classification)
→ SMC Engine (FVG/OB/Liquidity/OTE/PDH-PDL)
→ Market Regime Engine (enum)
→ Multi-Timeframe Engine (existing MTF matrix + HTF bias)
→ Confluence Engine (weighted evidence + agreement + conflict)
→ Technical Score (deterministic [-1,1])
→ Technical Confidence (deterministic with MTF/regime/data quality)
→ TechnicalSignal (structured output contract)
→ LLM Technical Interpretation (narrative + uncertainty, never score)
→ Risk / Strategy Sheet
```

### ۱۲.۱ Signal States (PROPOSED)

```text
STRONG_BULLISH
BULLISH
WEAK_BULLISH
NEUTRAL
WEAK_BEARISH
BEARISH
STRONG_BEARISH
```

### ۱۲.۲ Market Regime

```text
TRENDING * RANGING * HIGH_VOLATILITY * LOW_VOLATILITY * BREAKOUT * REVERSAL * UNCLEAR
```

### ۱۲.۳ TechnicalSignal Output Contract (PROPOSED — برای تایید)

```json
{
  "symbol": "EURUSD=X",
  "direction": "BULLISH",
  "score": 0.42,
  "confidence": 0.63,
  "market_regime": "RANGING",
  "time_horizon": "INTRADAY",
  "trend": "BULLISH",
  "market_structure": "BULLISH",
  "momentum": "WEAK_BULLISH",
  "volatility": "LOW",
  "smc_context": "PRICE_AT_DEMAND_OB",
  "mtf_alignment": "BULLISH",
  "confluence": [
    {"name": "structure", "value": 0.8, "weight": 0.20, "kind": "supporting"},
    {"name": "smc_location", "value": 0.5, "weight": 0.15, "kind": "supporting"},
    {"name": "momentum", "value": 0.0, "weight": 0.06, "kind": "neutral"}
  ],
  "supporting_evidence": ["Bullish BOS on H1", "Price at unmitigated demand OB"],
  "contradicting_evidence": ["HTF mixed (D1=+1 vs W1=-1)"],
  "risks": ["Low ADX, choppy market"],
  "reasoning_summary": "Bullish bias with moderate confidence; wait for confirmation near OB."
}
```

### ۱۲.۴ Weight Calibration Approach (PROPOSED — بدون Backtest انتخاب نمی‌شود)

1. **Walk-forward grid search**: `weight_i ∈ [0, 0.35]`، sum=1.
2. Metrics: OOS Sharpe, Profit Factor, MaxDD, WinRate, `n_trades >= 10`.
3. Debiasing check: OOS/IS ratio.
4. **Robustness**: top-N parameter combinations در OOS؛ میانگین/مدین metrics.
5. **Sensitivity analysis**: change each weight ±25% and measure score stability / direction flip rate.
6. **No lookahead**: هر ترکیب فقط از داده‌های `<= t` است.
7. **Per-regime calibration** (advisable but avoid overfit; use collapsed regimes).

---

## ۱۳) Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Overfitting weight optimization | High | walk-forward + OOS + robustness top-N |
| Lookahead via HTF/resample | High | strict `<= t` cursors, stale guard, resample from completed bars |
| LLM hallucinating evidence | Medium | enforce structured `evidence` from deterministic context only |
| Multiple engine state divergence | Medium | keep `calculate_technical_metrics` as canonical; new modules call it |
| Cost/Tokens | Medium | keep MTF LLM only for strongest TF; prompt cache eventually |
| Regime overfit | Medium | collapse regimes + per-regime analysis without hard decisions |
| SMC zone instability | Medium | parameterized swing_length, ATR buffer, zone merging |
| Data source variability (YF) | Medium | staleness guard, volume fill detection, available data not fabricate |

---

## ۱۴) جمع‌بندی و مرحله بعد

- **فاز Research کامل شد:**
  - Current Technical Module analysed deeply.
  - AgenticTrading, TradingAgents, ai-hedge-fund از روی فایل‌های واقعی بررسی شدند.
  - Comparative Matrix، Gap Analysis، Change Plan (P0/P1/P2) و Folder Architecture ارائه شد.
- **هیچ کدی نوشته نشده است.**
- **منتظر تأیید شما هستم** برای:
  1. تصویب Change Plan.
  2. تعیین Scope دقیق (P0 فقط یا P0+P1).
  3. تایید Output Contract.
  4. سپس شروع Phase Implementation و ساخت پوشه `v{version}` + `CHANGELOG.md`.
