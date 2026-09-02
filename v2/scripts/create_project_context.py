
# === AGENTS.md ===
from sympy import python


content = r"""
# AGENTS.md — AI Coding Agent Entry Point

## What This Project Is

A production-grade, multi-agent AI system for forex and macro market analysis. It ingests economic events, RSS news, and speaker statements; enriches them with quantitative data; analyzes them via LLM + deterministic scoring; and produces structured trading signals with entry/SL/TP plans. A memory system tracks trade outcomes and feeds them back into risk decisions.

## Repository Structure

```
core/                 — Database (SQLAlchemy), Pydantic schemas, LLM utils, routing
ingestion/            — RSS feeds, FF scraper/bridge, historical importer, speaker seeder
agents/
  fundamental/        — Phase 1 & 2 pipelines, data fetcher, phase2 LangGraph
  technical/          — 7-factor deterministic technical agent
  risk/               — Risk manager agent
  supervisor/         — Head agent (supervisor)
orchestration/        — CLI entry points, trade evaluator
data/                 — Mock data (speaker statements)
tests/                — Unit tests and evaluation scripts
runs/                 — Audit artifacts (auto-generated)
ext_ff_scraper/       — External FF scraper repo (cloned separately)
```

## What to Read First

1. This file (AGENTS.md)
2. `.ai/overview.md` — high-level project purpose
3. `.ai/architecture.md` — technical architecture, data flows, state models
4. `.ai/code_map.md` — navigation map for features → source paths
5. `.ai/project_state.md` — what is implemented vs broken vs planned
6. `.ai/components/*.md` — deep technical detail per component
7. `.ai/decisions.md` — architectural decisions and their reasoning
8. Actual source code for any module you intend to modify

## Source-of-Truth Rules

1. **Source code is the final source of truth.** Documentation may be outdated.
2. Never assume documentation is correct without verifying against code.
3. If documentation conflicts with code, code wins. Update the documentation.
4. Never make broad destructive rewrites without understanding dependencies.
5. Preserve architectural invariants (see below).
6. Update relevant `.ai/` context files after significant architectural changes.
7. Record important decisions in `.ai/decisions.md`.
8. Update `.ai/current_task.md` and session logs when appropriate.

## Architectural Invariants

1. Signal direction is always currency-native (1 = currency strengthens). Instrument-view translation only for display via `core/routing.py`.
2. LLM never produces final numeric scores. Python deterministic math computes all scores.
3. Forward-only DB migration (`ALTER TABLE ADD COLUMN`, `nullable=True`).
4. `session_scope()` for all new DB call sites.
5. `None` means "not evaluated"; `0.0` means "evaluated and neutral". Never conflate.
6. All LLM calls use `invoke_with_retry()` + `_strip_json_markdown()` from `core/llm_utils.py`.
7. Arvan GLM-5.2 wraps JSON in markdown blocks; stripping is mandatory.
8. Speakers pipeline is force-disabled (API not integrated).
9. Phase 0 ingestion runs before Head Agent to ensure DB freshness.

## Development Rules

1. Use `core/models.py` as the Pydantic schema source of truth.
2. Use `core/database.py` ORM models; do not write raw SQL.
3. Keep LLM interpretation separate from deterministic scoring.
4. Keep modules LangGraph-ready (stateless functions convertible to graph nodes).
5. Prefer additive changes over destructive rewrites.
6. `talipp` OHLCV requires positional args: `OHLCV(o, h, l, c, v)`.
7. `talipp` ADX returns `ADXVal` object; access `.adx` attribute.
8. `talipp` MACD returns `MACDVal` object; access `.histogram` attribute.
9. `smartmoneyconcepts` uses class methods (e.g., `smc.fvg(df)`), not instances.

## Testing Rules

1. Only `tests/test_event_scorer.py` exists as a unit test. It tests `EventSignalScorer` math.
2. `tests/evaluate_signals.py` and `tests/evaluate_news_signals.py` are evaluation scripts, not unit tests.
3. No tests exist for Phase 2, Phase 3, Technical Agent, Risk Manager, or Head Agent.
4. Run tests: `python -m pytest tests/test_event_scorer.py -v`

## How to Run

```bash
# Initialize database
python -c "from core.database import init_db; init_db()"

# Seed speakers
python -m ingestion.seed_speakers

# Phase 1 (single pipeline)
python -m orchestration.run --mode events --llm-provider arvan
python -m orchestration.run --mode news --llm-provider arvan
python -m orchestration.run --mode speakers --llm-provider arvan

# Phase 2 (fundamental aggregation)
python -m orchestration.run_phase2 --currencies USD EUR --llm-provider arvan

# Phase 3 (full multi-agent system)
python -m orchestration.run_phase3 --currencies USD EUR --llm-provider arvan

# Trade memory evaluator (background)
python -m orchestration.trade_evaluator
```

## Configuration

- `.env` file with: `ARVAN_BASE_URL`, `ARVAN_API_KEY`, `OPENROUTER_API_KEY` (optional)
- `ext_ff_scraper/` repo cloned at project root
- SQLite DB auto-created at `core/crypto_agent.db`

## Documentation Hierarchy

```
AGENTS.md (this file)
├── .ai/overview.md
├── .ai/architecture.md
├── .ai/code_map.md
├── .ai/project_state.md
├── .ai/current_task.md
├── .ai/decisions.md
├── .ai/roadmap.md
├── .ai/components/
│   ├── fundamental.md
│   ├── technical.md
│   ├── risk.md
│   └── orchestration.md
└── .ai/sessions/
    └── README.md
```
"""

with open("AGENTS.md", "w") as f:
    f.write(content)

# === .ai/overview.md ===
content = r"""
# Project Overview

## Purpose

A multi-agent AI platform for forex and macro market analysis. It fuses fundamental analysis (events, news, speakers), technical analysis (Smart Money Concepts, trend, momentum), and risk management to produce explainable, high-probability trade signals. A memory system learns from past trade outcomes.

## Major Capabilities

- Live Forex Factory calendar scraping with actual values via HTML bridge
- RSS news aggregation from 16+ feeds with currency detection and deduplication
- Historical economic event import from CSV (2007–present)
- LLM-powered event/news/speaker interpretation with strict prompt rules
- Deterministic scoring (separate from LLM reasoning)
- LangGraph per-currency and cross-asset consistency graphs
- Head Agent for dynamic resource allocation and timeframe selection
- 7-factor deterministic technical scoring (Structure, SMC Location, Trend, MTF, Momentum, Volatility, Price Action)
- Risk Manager Agent with confluence detection, trade structuring, and memory injection
- Trade memory DB with silent evaluator for Win/Loss tracking
- Temporal context engine (market sessions, weekends, holidays)

## Technology Stack

- Python 3.11+
- SQLAlchemy ORM + SQLite (WAL mode)
- Pydantic v2 (schemas + output parsing)
- LangChain + LangGraph (orchestration)
- yfinance (market data)
- talipp (indicators: RSI, MACD, EMA, ADX)
- smartmoneyconcepts (OB, FVG, BOS/CHOCH)
- feedparser + requests (RSS)
- BeautifulSoup4 (body enrichment)
- python-dotenv (config)
- LLM Providers: Arvan AI (GLM-5.2), OpenRouter, Ollama

## External Services

- Forex Factory (HTML scraping via `ext_ff_scraper`)
- yfinance / Yahoo Finance API (price data)
- Arvan Cloud AI (GLM-5.2 LLM)
- OpenRouter (multi-model LLM)
- RSS feeds: ForexLive, DailyFX, Investing.com, FXStreet, MarketWatch, central banks, Google News

## Inputs

- Forex Factory calendar HTML (via `ext_ff_scraper`)
- RSS feed XML (16+ feeds)
- yfinance OHLCV data
- Historical CSV events
- Speaker statements (mock JSON)

## Outputs

- SQLite tables: event_signals, news_signals, trading_signals, composite_signals, trade_outcomes
- Terminal reports (signals, detailed analyses, global summary)
- Audit JSON artifacts in `runs/`

## Major Subsystems

1. **Ingestion** (`ingestion/`): FF scraper, RSS loader, historical importer, speaker seeder
2. **Fundamental Agent** (`agents/fundamental/`): Phase 1 pipelines + Phase 2 LangGraph aggregation
3. **Technical Agent** (`agents/technical/`): 7-factor deterministic scorer
4. **Risk Manager** (`agents/risk/`): Confluence, trade structuring, memory
5. **Head Agent** (`agents/supervisor/`): Resource allocation, regime detection
6. **Orchestration** (`orchestration/`): CLI entry points, trade evaluator
7. **Core** (`core/`): Database, models, LLM utils, routing

## High-Level Execution Flow

1. Phase 0: Scrape FF calendar + RSS news → SQLite (no LLM)
2. Head Agent: Assess market regime + event calendar → ExecutionPlan
3. Fundamental Agent: Run Phase 2 LangGraph per currency → CompositeSignal
4. Technical Agent: 7-factor deterministic scoring → TechnicalReport
5. Risk Manager: Fuse fundamental + technical + memory → RiskDecision
6. If APPROVED: Register in TradeOutcomeDB
7. Cross-asset graph: Validate inverse correlations → Global summary

## Important Constraints

- LLM never produces scores; Python deterministic math does
- Signal direction is currency-native; translation for display only
- Forward-only DB migration
- Speakers pipeline force-disabled (no API)
- Arvan GLM-5.2 requires markdown JSON stripping
"""

with open(".ai/overview.md", "w") as f:
    f.write(content)

# === .ai/architecture.md ===
content = r"""
# Architecture

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    PHASE 0: INGESTION                           │
│  FF HTML Scraper (3x retry) → economic_events_history (DB)     │
│  RSS Loader (16+ feeds) → raw_news_items (DB)                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   HEAD AGENT (Supervisor)                        │
│  Quick Trend (EMA 50/200 + ADX) + DB Event Check (24h)         │
│  → LLM → ExecutionPlan (toggles + timeframe)                   │
│  Force: activate_speakers = False                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
           ┌───────────────┴───────────────┐
           ▼                               ▼
┌─────────────────────┐         ┌─────────────────────┐
│ FUNDAMENTAL AGENT   │         │ TECHNICAL AGENT     │
│ (Phase 2 LangGraph) │         │ (7-Factor Scorer)   │
│                     │         │                     │
│ Event Node          │         │ Structure (25%)     │
│   → nlp_event       │         │ SMC Location (20%)  │
│   → event_signals   │         │ Trend (18%)         │
│ News Node           │         │ MTF (12%, =0.0)     │
│   → nlp_news digest │         │ Momentum (10%)      │
│   → news_signals    │         │ Volatility (10%)    │
│ Speaker Node        │         │ Price Action (5%)   │
│   → nlp_x (disabled)│         │                     │
│ Aggregator Node     │         │ LLM: strategy only  │
│   → CompositeSignal │         │ Python: score       │
└─────────┬───────────┘         └──────────┬──────────┘
          │                                │
          └────────────┬───────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                   RISK MANAGER AGENT                             │
│  Confluence Check + Memory Stats + Trade Structuring            │
│  → RiskDecision (APPROVED/REJECTED/WAIT)                       │
│  If APPROVED → TradeOutcomeDB (PENDING)                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                CROSS-ASSET CONSISTENCY GRAPH                     │
│  Inverse pair anomaly detection + penalty                       │
│  Detailed per-currency LLM reports                              │
│  Global macro executive summary                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Execution Phases

### Phase 0 — Ingestion (`orchestration/run_phase3.py::run_ingestion_phase`)

Entry: `run_ingestion_phase()`
1. FF HTML scraper: 3 retries with 5s delay. `ff_bridge.fetch_and_store_ff_data(start_date, end_date)` → `economic_events_history`
2. RSS loader: `FeedLoader().load_all_collect()` → dedup hash → `insert_raw_news_item_if_new()` → `raw_news_items`

Failure: FF failure logged, proceeds with existing DB data. RSS failure logged, proceeds.

### Phase 1 — Fundamental Pipelines (inside Phase 2 graph nodes)

Three independent pipelines, each: LLM interpretation → deterministic scoring → DB persistence.

**Event Pipeline** (`agents/fundamental/nlp_event.py`)
- Entry: `analyze_economic_event(event_input, market_context, ticker, llm)`
- LLM: `EventAnalysisService` chain (prompt | llm | strip_markdown | PydanticOutputParser)
- Deterministic: `EventSignalScorer.score()` — `sentiment * impact_weight * (1+surprise) * data_quality * quant_alignment * std_reliability_mult`
- Persistence: `EventSignalRepository.save()` → `EventSignalDB`
- Post-LLM enforcement: `_enforce_market_context_values()` corrects surprise/volatility drift

**News Pipeline** (`agents/fundamental/nlp_news.py`)
- Entry: `analyze_news_digest(currency, news_items, market_context, ticker, llm)`
- LLM: `NewsDigestAnalysisService` chain
- Deterministic: `NewsDigestScorer.score()` — `sentiment * category_weight * (1+surprise) * data_quality`
- Persistence: `NewsSignalDB` with `is_aggregated=True`

**Speaker Pipeline** (`agents/fundamental/nlp_x.py`)
- Entry: `analyze_speaker_text(item, market_context, ticker, llm)`
- LLM: `SpeakerAnalysisService` chain (prompt | llm | strip_markdown | PydanticOutputParser)
- Deterministic: `SpeakerSignalScorer.score()` — `sentiment * speaker_weight * stmt_type_weight * (1+surprise) * data_quality * alignment`
- Persistence: `SpeakerSignalRepository.save()` → `TradingSignalDB`
- Status: Force-disabled in Phase 3

### Phase 2 — Fundamental Aggregation (`agents/fundamental/phase2_graph.py`)

**Per-Currency Graph** (`build_phase2_graph()`)
```
START → fetch_event → fetch_news → fetch_speaker → aggregate → END
```

Nodes:
- `fetch_and_analyze_event_node`: reads DB for High/Medium events in 48h, analyzes each
- `fetch_and_analyze_news_node`: loads RSS, filters by currency, Macro Digest (10 items)
- `fetch_latest_speaker_signal_node`: reads TradingSignalDB for last 24h (disabled)
- `aggregate_signals_node`: impact-weighted average, confluence rules, temporal filter

Aggregator logic:
1. Event aggregation: weighted average of event scores by impact_weight
2. Confluence: Aligned → score×1.1, conf+0.10. Conflicting → score×0.7, conf-0.20, tradable=False
3. Temporal filter: weekend/holiday → is_tradable=False
4. Output: `CompositeSignal`

Pipeline toggles in state: `enable_events`, `enable_news`, `enable_speakers`

**Cross-Asset Graph** (`build_cross_asset_graph()`)
```
START → cross_check → detailed_reports → global_summary → END
```

Nodes:
- `cross_asset_consistency_node`: checks inverse pairs (USD/CAD, USD/XAU, USD/OIL). If same direction → score×0.8, conf-0.15
- `generate_detailed_currency_reports_node`: per-currency LLM macro analysis
- `generate_global_summary_node`: global LLM executive summary

### Phase 3 — Multi-Agent (`orchestration/run_phase3.py`)

Per-currency loop:
1. Head Agent: `create_plan(currency, ticker)` → `ExecutionPlan`
2. Force `activate_speakers = False`
3. Fundamental: if any sub-flag active, invoke `fund_app.invoke(state_input)`
4. Technical: if `activate_technical`, `tech_agent.analyze(ticker, timeframe, temporal_context)`
5. Risk: `risk_agent.evaluate(...)` with memory stats from `get_trade_memory_stats(currency)`
6. If APPROVED: register in `TradeOutcomeDB` with PENDING status
7. Build `CompositeSignal` with tech_driven fallback if fundamental not run

Score semantics in `run_phase3.py`:
```python
fund_status = "not_evaluated"  # or "evaluated"
final_dir = tech_report.direction if tech_report else (fund_sig.direction if fund_sig else 0)
final_score = fund_score_val if fund_status == "evaluated" else (tech_report.score if tech_report else 0.0)
```

## State Objects

### PipelineState (Phase 2 per-currency graph)
```python
class PipelineState(TypedDict, total=False):
    currency: str
    llm: BaseChatModel
    temporal_context: dict
    enable_events: bool
    enable_news: bool
    enable_speakers: bool
    event_signals: List[EventSignal]
    news_signal: Optional[NewsSignal]
    speaker_signal: Optional[dict]
    composite_signal: Optional[CompositeSignal]
    final_report: Optional[str]
```

### GlobalPipelineState (cross-asset graph)
```python
class GlobalPipelineState(TypedDict, total=False):
    llm: BaseChatModel
    temporal_context: dict
    composite_signals: dict[str, CompositeSignal]
    detailed_reports: dict[str, str]
    global_report: Optional[str]
```

### Key Pydantic Schemas
- `CompositeSignal`: currency, direction, final_score [-1,1], confidence [0,1], is_tradable, confluence_status, components_used, reasoning
- `TechnicalMetrics`: current_price, technical_score [-1,1], technical_confidence [0,1], ComponentScores, raw SMC/indicator data
- `TechnicalReport`: direction, score [-1,1], confidence [0,1], strategy, reasoning
- `RiskDecision`: decision (APPROVED/REJECTED/WAIT), reasoning, trade_plan (optional)
- `TradePlan`: entry_zone (string), stop_loss, take_profit, risk_reward_ratio
- `ExecutionPlan`: activate_events, activate_news, activate_speakers, activate_technical, technical_timeframe, market_regime, reasoning

## LLM Flow

All LLM calls follow this pattern:
```
ChatPromptTemplate → LLM → RunnableLambda(_strip_json_markdown) → PydanticOutputParser
```
Wrapped in `invoke_with_retry(chain, inputs, max_retries=2)` with exponential backoff.

Post-LLM enforcement: `_enforce_market_context_values()` corrects `surprise_factor` and `expected_volatility` if LLM drifts from MarketContext values.

## Database Flow

Write paths:
- FF scraper → `economic_events_history` (upsert by title+currency+date)
- RSS loader → `raw_news_items` (insert if new by dedup_hash)
- Event analysis → `event_signals`
- News analysis → `news_signals` (is_aggregated=True for digest)
- Speaker analysis → `trading_signals`
- Phase 2 aggregator → `composite_signals`
- Risk APPROVED → `trade_outcomes` (PENDING)
- Trade evaluator → `trade_outcomes` (WIN/LOSS/EXPIRED)

Read paths:
- Head Agent: `economic_events_history` (24h forward High Impact count)
- Phase 2 Event Node: `economic_events_history` (48h backward High/Medium)
- Phase 2 Speaker Node: `trading_signals` (24h backward)
- Memory: `trade_outcomes` (win/loss stats by currency)
- Historical std: `economic_events_history` (canonical matching)

## Deterministic vs LLM Boundary

| Component | LLM Responsibility | Deterministic Responsibility |
|-----------|-------------------|------------------------------|
| Event | reasoning, direction, sentiment, surprise_interp | final_score, confidence, is_tradable, half_life |
| News | reasoning, direction, sentiment, theme | final_score, confidence, is_tradable |
| Speaker | reasoning, direction, sentiment, policy_signal | final_score, confidence, is_tradable |
| Technical | strategy narrative, reasoning | score, confidence, all 7 component scores |
| Risk | decision reasoning, trade plan structuring | hard rejection on missing data, memory injection |
| Head | execution plan reasoning | trend check (EMA+ADX), event DB query |
| Cross-asset | detailed reports, global summary | inverse pair detection, score penalty |

## Important Abstractions

1. `MarketContext`: carries all quantitative data (ATR, HV, IV, yield_spread, historical_std, calculated_surprise, calculated_volatility). Built by `data_fetcher.py`.
2. `HistoricalStdResult`: carries std value + source metadata (computed_canonical, computed_loose, category_fallback, absolute_fallback, no_data). Used for confidence scoring.
3. `route.asset_route`: maps currency → yfinance ticker + alignment (DIRECT/INVERSE/INDEX). Used for instrument direction translation.
4. `temporal_context`: dict with market_session, is_weekend, is_holiday. Used by aggregator and LLM prompts.
"""

with open(".ai/architecture.md", "w") as f:
    f.write(content)
# === .ai/code_map.md ===
content = r"""
# Code Map

Navigation map for AI agents. Each feature traces from entry point through modules, classes, functions, models, DB tables, and external services.

## Fundamental Event Analysis

→ Entry: `agents/fundamental/nlp_event.py::analyze_economic_event`
→ Modules: `agents/fundamental/nlp_event.py`, `agents/fundamental/data_fetcher.py`
→ Classes: `EventAnalysisService`, `EventSignalScorer`, `EventSignalRepository`
→ Functions: `analyze_economic_event()`, `build_event_brief()`, `fetch_market_context()`, `fetch_historical_surprise_std_full()`
→ Input models: `EventAnalysisInput` (`core/models.py`), `MarketContext` (`core/models.py`)
→ Output models: `EventInterpretationEvent` (`core/models.py`), `EventSignal` (`core/models.py`)
→ DB tables: `event_signals`, `economic_events_history`
→ External: Arvan GLM-5.2 LLM, yfinance, Forex Factory HTML scraper
→ Tests: `tests/test_event_scorer.py` (EventSignalScorer math only)

## Fundamental News Analysis

→ Entry: `agents/fundamental/nlp_news.py::analyze_news_digest`
→ Modules: `agents/fundamental/nlp_news.py`, `agents/fundamental/data_fetcher.py`, `ingestion/rss_feed_loader.py`
→ Classes: `NewsDigestAnalysisService`, `NewsDigestScorer`, `FeedLoader`
→ Functions: `analyze_news_digest()`, `analyze_news_item()`, `fetch_market_context()`
→ Input models: `NewsItem` (`core/models.py`), `NewsDigestInterpretation` (defined in `nlp_news.py`)
→ Output models: `NewsSignal` (`core/models.py`)
→ DB tables: `news_signals`, `raw_news_items`
→ External: Arvan GLM-5.2 LLM, yfinance, RSS feeds
→ Tests: `tests/evaluate_news_signals.py` (evaluation script, not unit test)

## Fundamental Speaker Analysis

→ Entry: `agents/fundamental/nlp_x.py::analyze_speaker_text`
→ Modules: `agents/fundamental/nlp_x.py`, `agents/fundamental/data_fetcher.py`
→ Classes: `SpeakerAnalysisService`, `SpeakerSignalScorer`, `SpeakerSignalRepository`
→ Functions: `analyze_speaker_text()`, `generate_signal()` (legacy)
→ Input models: `SpeakerTextItem` (`core/models.py`), `Speaker` (`core/models.py`)
→ Output models: `SpeakerInterpretation` (`core/models.py`), `SpeakerSignal` (`core/models.py`)
→ DB tables: `trading_signals`, `raw_speaker_items`, `speakers`
→ External: Arvan GLM-5.2 LLM, yfinance
→ Tests: None
→ Note: Force-disabled in Phase 3. E2E uses mock JSON at `data/mock_speaker_statements.json`.

## Phase 2 Aggregation (Per-Currency Graph)

→ Entry: `agents/fundamental/phase2_graph.py::build_phase2_graph`
→ Modules: `agents/fundamental/phase2_graph.py`, `agents/fundamental/nlp_event.py`, `agents/fundamental/nlp_news.py`, `agents/fundamental/nlp_x.py`
→ Classes: `CompositeSignal` (Pydantic, defined in `phase2_graph.py`)
→ Functions: `fetch_and_analyze_event_node()`, `fetch_and_analyze_news_node()`, `fetch_latest_speaker_signal_node()`, `aggregate_signals_node()`, `get_temporal_context()`
→ State: `PipelineState` (TypedDict)
→ Input: currency, llm, temporal_context, pipeline toggles
→ Output: `CompositeSignal`
→ DB tables: reads `economic_events_history`, `trading_signals`; writes `event_signals`, `news_signals`, `composite_signals`
→ External: Arvan GLM-5.2 LLM
→ Tests: None

## Phase 2 Cross-Asset Graph

→ Entry: `agents/fundamental/phase2_graph.py::build_cross_asset_graph`
→ Modules: `agents/fundamental/phase2_graph.py`
→ Functions: `cross_asset_consistency_node()`, `generate_detailed_currency_reports_node()`, `generate_global_summary_node()`
→ State: `GlobalPipelineState` (TypedDict)
→ Input: dict of `CompositeSignal` per currency
→ Output: Updated signals, detailed reports, global report
→ Tests: None

## Technical Analysis

→ Entry: `agents/technical/technical_analyzer.py::TechnicalAgent.analyze`
→ Modules: `agents/technical/technical_analyzer.py`
→ Classes: `TechnicalAgent`, `ComponentScores`, `TechnicalMetrics`, `TechnicalReport`, `LLMStrategy`
→ Functions: `calculate_technical_metrics()`, `_fetch_price_history()`
→ Input: ticker string, timeframe string, temporal_context dict
→ Output: `TechnicalMetrics` (raw data + scores), `TechnicalReport` (direction, score, confidence, strategy, reasoning)
→ DB tables: None
→ External: yfinance, talipp (RSI, MACD, EMA, ADX), smartmoneyconcepts (OB, FVG, BOS)
→ Tests: None

## Risk Management

→ Entry: `agents/risk/risk_manager.py::RiskManagerAgent.evaluate`
→ Modules: `agents/risk/risk_manager.py`
→ Classes: `RiskManagerAgent`, `RiskDecision`, `TradePlan`
→ Functions: `evaluate()`
→ Input: fundamental direction/score/confidence/tradable/reasoning, technical direction/confidence/strategy/reasoning, price levels, memory stats
→ Output: `RiskDecision` (APPROVED/REJECTED/WAIT + optional TradePlan)
→ DB tables: `trade_outcomes` (via orchestrator on APPROVED)
→ External: Arvan GLM-5.2 LLM
→ Tests: None

## Head Agent (Supervisor)

→ Entry: `agents/supervisor/head_agent.py::HeadAgent.create_plan`
→ Modules: `agents/supervisor/head_agent.py`
→ Classes: `HeadAgent`, `ExecutionPlan`
→ Functions: `create_plan()`, `get_quick_trend()`, `has_high_impact_events()`
→ Input: currency string, ticker string
→ Output: `ExecutionPlan`
→ DB tables: reads `economic_events_history` (24h forward High Impact)
→ External: yfinance (1y data for EMA/ADX), Arvan GLM-5.2 LLM
→ Tests: None

## Phase 3 Orchestration

→ Entry: `orchestration/run_phase3.py::main`
→ Modules: `orchestration/run_phase3.py`, all agent modules, `ingestion/ff_bridge.py`, `ingestion/rss_feed_loader.py`
→ Functions: `main()`, `run_ingestion_phase()`, `build_llm()`
→ Input: CLI args (--currencies, --llm-provider, --llm-model)
→ Output: Terminal reports, DB writes to all signal tables + trade_outcomes
→ DB tables: All signal tables, raw_news_items, economic_events_history, trade_outcomes
→ External: FF scraper, RSS feeds, yfinance, Arvan GLM-5.2
→ Tests: None

## Trade Memory Evaluator

→ Entry: `orchestration/trade_evaluator.py::evaluate_pending_trades`
→ Modules: `orchestration/trade_evaluator.py`
→ Functions: `evaluate_pending_trades()`
→ Input: None (reads DB)
→ Output: Updates `trade_outcomes` status to WIN/LOSS/EXPIRED
→ DB tables: `trade_outcomes`
→ External: yfinance (5m interval current price)
→ Tests: None

## Phase 1 Unified CLI

→ Entry: `orchestration/run.py::main`
→ Modules: `orchestration/run.py`, `agents/fundamental/e2e_pipeline.py`, `agents/fundamental/e2e_news_pipeline.py`, `agents/fundamental/e2e_speaker_pipeline.py`
→ Functions: `main()` routes to sub-pipelines based on --mode
→ Tests: None

## Database Layer

→ Entry: `core/database.py::init_db`
→ Modules: `core/database.py`
→ ORM Models: `SpeakerDB`, `EventHistoryDB`, `TradingSignalDB`, `NewsSignalDB`, `EventSignalDB`, `RawNewsItemDB`, `CompositeSignalDB`, `RawSpeakerItemDB`, `TradeOutcomeDB`
→ Functions: `init_db()`, `session_scope()`, `query_by_time_range()`, `get_latest_*_signal()`, `insert_*_if_new()`, `cleanup_old_data()`, `get_trade_memory_stats()`
→ DB: SQLite at `core/crypto_agent.db`, WAL mode

## Market Data Enrichment

→ Entry: `agents/fundamental/data_fetcher.py::fetch_market_context`
→ Modules: `agents/fundamental/data_fetcher.py`
→ Functions: `fetch_market_context()`, `fetch_historical_surprise_std_full()`, `calculate_surprise_factor()`, `calculate_volatility_multiplier()`, `_canonicalize_event_title()`, `_filter_outliers_iqr()`
→ Input: ticker, currency, event metadata, actual/forecast/previous values
→ Output: `MarketContext`, `HistoricalStdResult`
→ DB tables: reads `economic_events_history` for historical std
→ External: yfinance (ATR, HV, IV, yield spread)
→ Tests: None

## LLM Utilities

→ Entry: `core/llm_utils.py::invoke_with_retry`
→ Modules: `core/llm_utils.py`
→ Functions: `invoke_with_retry()`, `_strip_json_markdown()`
→ Tests: None
"""

with open(".ai/code_map.md", "w") as f:
    f.write(content)

# === .ai/project_state.md ===
content = r"""
# Project State

## IMPLEMENTED

### Core Infrastructure
- SQLite database with WAL mode, 9 ORM tables, forward-only migrations
- Pydantic v2 schemas with direction_matches_sentiment validators
- LLM utilities: retry with exponential backoff, markdown JSON stripping
- Currency routing with DIRECT/INVERSE/INDEX alignment translation
- Session management via `session_scope()` context manager

### Ingestion
- Forex Factory HTML scraping via `ext_ff_scraper` with retry (3x, 5s delay)
- RSS feed loading from 16+ feeds with currency detection (scoring-based), deduplication (SHA256 hash), and enrichment (body scraping)
- Historical CSV import with batch commits (500 rows)
- Speaker seeding (7 tiers, 35+ speakers)

### Fundamental Pipelines (Phase 1)
- Event analysis: LLM interpretation + deterministic scoring + DB persistence
- News analysis: single item + Macro Digest (10 items per currency, 1 LLM call)
- Speaker analysis: LLM interpretation + deterministic scoring + DB persistence
- All pipelines: retry on parse failure, markdown stripping, post-LLM enforcement of market context values
- E2E orchestrators for all three pipelines with CLI args, filtering, routing, audit artifacts

### Fundamental Aggregation (Phase 2)
- Per-currency LangGraph: Event → News → Speaker → Aggregator
- Impact-weighted event aggregation
- Confluence rules: Aligned (score×1.1, conf+0.10), Conflicting (score×0.7, conf-0.20, tradable=False)
- Temporal risk filter: weekend/holiday blocks tradability
- Cross-asset consistency graph: inverse pair anomaly detection (USD/CAD, USD/XAU, USD/OIL)
- Global LLM executive summary
- Pipeline toggle flags (enable_events, enable_news, enable_speakers)

### Multi-Agent System (Phase 3)
- Head Agent: ADX-based regime detection (ADX<25=Ranging), EMA trend check, 24h event DB query, LLM execution plan
- Technical Agent: 7-factor deterministic scoring (Structure 25%, SMC Location 20%, Trend 18%, MTF 12%=0.0, Momentum 10%, Volatility 10%, Price Action 5%). LLM for narrative only.
- Risk Manager: confluence check, trade structuring (Entry/SL/TP), mean reversion special case, memory injection
- Trade memory: TradeOutcomeDB with PENDING/WIN/LOSS/EXPIRED states
- Silent evaluator: checks PENDING trades against yfinance price, 48h expiry
- Phase 0 ingestion before Head Agent
- None vs 0.0 score semantics in orchestrator

### Project Structure
- Layered packages: core/, ingestion/, agents/{fundamental,technical,risk,supervisor}/, orchestration/
- All imports use package-relative paths

## PARTIALLY_IMPLEMENTED

- Cross-asset direction: routing layer built with alignment metadata; persisted direction is currency-native (by design). No "fix" needed — this is intentional.
- MTF Confluence factor in Technical Agent: hardcoded to 0.0. Weight (12%) still applied but contributes nothing.
- RSS loading in Phase 3: Phase 0 loads RSS to `raw_news_items` DB; Phase 2 News Node loads RSS fresh again from feeds (duplicate network calls, not from DB).
- Speaker pipeline: core `nlp_x.py` complete and functional, E2E script works with mock JSON, but force-disabled in Phase 3 because no real API integrated.

## PLANNED

- `live_watcher.py`: Event-driven execution loop (monitor price every 15 min, wake orchestrator on entry zone hit)
- Telegram/Discord notification on APPROVED signals
- APScheduler integration for automated scheduling
- Real speaker API integration (Tweety or similar)
- MTF confluence implementation (simultaneous H4/H1/M15 analysis)
- Strategy engine (Trend Following vs Mean Reversion vs Breakout selection)
- Position sizing based on ATR stop distance
- Portfolio exposure management
- Backtest calibration of factor weights

## DISABLED

- Speakers pipeline: `activate_speakers` forced to False in `run_phase3.py` and Head Agent prompt explicitly instructs False
- DailyFX feed: `enabled=False` in `rss_feed_config.py`
- ECB direct feed: `enabled=False` (Google News fallback used)
- RBA direct feed: `enabled=False` (Google News fallback used)

## BROKEN

None currently broken. All pipelines execute without crashes.

## KNOWN BUGS / ISSUES

1. **Historical Std Fallback**: Core CPI y/y, Core CPI m/m, PPI m/m, Core PPI m/m, Jobless Claims all fall back to category/absolute defaults because historical DB lacks matching canonical keys. Warning logs appear on every run.
2. **DX-Y.NYB yfinance**: Dollar Index sometimes returns "possibly delisted; no price data found" depending on market session/timing.
3. **BoJ RSS Feed**: Connection frequently reset (`ConnectionResetError(104)`). Retry mechanism handles it but adds latency.
4. **FF Scraper IncompleteRead**: Forex Factory drops connections mid-transfer. Retry (3x) handles it.
5. **talipp OHLCV**: Keyword args (`h=`, `l=`, `c=`) cause `TypeError`. Must use positional args.
6. **talipp ADX return type**: Returns `ADXVal` object, not float. Must access `.adx`.
7. **talipp MACD return type**: Returns `MACDVal` object. Must access `.histogram`.
8. **smartmoneyconcepts API drift**: Method names/signatures vary between versions. Fallback try/except blocks used throughout.
9. **RSS double-load**: Phase 0 and Phase 2 News Node both fetch RSS feeds independently.

## TECHNICAL DEBT

1. No tests for Phase 2, Phase 3, Technical Agent, Risk Manager, Head Agent
2. No APScheduler; all execution is manual CLI
3. No live watcher; no Telegram notification
4. No position sizing or portfolio management
5. Factor weights in Technical Agent are hand-tuned, not backtested
6. Fusion weights (fundamental vs technical) are not dynamic (no dynamic weighting implemented despite being discussed)
7. Risk Manager uses LLM for trade structuring; no ATR-based SL calculation
8. No correlation exposure tracking
9. Historical DB likely sparse for many event types

## TEST COVERAGE LIMITATIONS

- Only `tests/test_event_scorer.py` tests deterministic math (EventSignalScorer)
- Tests cover: perfect signal scoring, low impact rejection, fallback std penalty, high surprise + weak sentiment penalty, LLM inconsistency penalty, inverse event direction, half-life calculation, data completeness threshold
- NOT tested: Phase 2 aggregator, Phase 3 orchestrator, Technical Agent scoring, Risk Manager, Head Agent, cross-asset graph, trade evaluator, DB operations, LLM chains
- `tests/evaluate_signals.py` and `tests/evaluate_news_signals.py` are evaluation scripts that require DB data, not unit tests

## FRAGILE AREAS

1. `ext_ff_scraper` dependency: external repo with Cloudflare handling; if it breaks, FF ingestion fails
2. `talipp` API: return types and constructor signatures are non-standard; version updates may break
3. `smartmoneyconcepts` API: method names changed between versions; multiple try/except fallbacks
4. Arvan GLM-5.2: wraps JSON in markdown; temperature sensitivity; occasional parse failures requiring retry
5. `yfinance`: DX-Y.NYB reliability; MultiIndex columns require flattening; rate limiting possible

## CURRENT FOCUS

The repository is at a stable Phase 3 state. The most recent work was:
- Implementing trade memory system (TradeOutcomeDB, get_trade_memory_stats, trade_evaluator.py, memory injection)
- Force-disabling speakers pipeline
- Adding retry to FF bridge ingestion
- Fixing None vs 0.0 score semantics
- Restructuring to layered packages
"""

with open(".ai/project_state.md", "w") as f:
    f.write(content)

# === .ai/current_task.md ===
content = r"""
# Current Task

## Objective

Build a live execution system (`live_watcher.py`) with Telegram notification for the Phase 3 multi-agent platform.

## Current Problem

The system currently runs only via manual CLI invocation (`python -m orchestration.run_phase3`). There is no automated monitoring, no price-level trigger mechanism, and no notification delivery. Trades marked WAIT by Risk Manager have defined entry zones but nothing watches for price reaching those zones.

## Work Already Completed

1. Phase 3 multi-agent orchestrator (`orchestration/run_phase3.py`) is stable and functional
2. Trade memory system implemented: `TradeOutcomeDB`, `get_trade_memory_stats()`, `orchestration/trade_evaluator.py`
3. Memory injection into Risk Manager prompt is working
4. Head Agent issues execution plans with pipeline toggles and timeframe selection
5. Technical Agent produces 7-factor deterministic scores
6. Risk Manager produces APPROVED/REJECTED/WAIT with TradePlan (entry_zone, SL, TP)
7. Phase 0 ingestion runs before Head Agent
8. FF scraper has retry mechanism (3x, 5s delay)
9. None vs 0.0 score semantics resolved in orchestrator
10. Speakers pipeline force-disabled
11. Project restructured to layered packages

## Files Being Changed

No files currently being changed. Next development cycle will create:
- `orchestration/live_watcher.py` (new)
- Telegram notification module (new, location TBD)

## Decisions Already Made

1. Live watcher will use Event-Driven architecture: pure Python price check every ~15 min (no LLM), wakes orchestrator only when price approaches entry zone from WAIT trades
2. Telegram notification on APPROVED Risk Manager decision
3. Speakers pipeline remains disabled until real API integrated
4. Factor weights are hand-tuned pending backtest calibration
5. None vs 0.0 distinction is architectural invariant
6. Phase 0 ingestion separates data collection from LLM analysis

## Constraints

1. Cannot run LLM every minute (token cost, API rate limits)
2. Price check must be lightweight (yfinance only, no LLM)
3. Weekend/holiday gap risk must be respected
4. Trade memory DB must be updated by silent evaluator independently
5. ext_ff_scraper dependency must remain external

## Unresolved Issues

1. Dynamic weighting of fundamental vs technical scores not implemented (was discussed but not coded)
2. MTF Confluence factor hardcoded to 0.0
3. Risk Manager SL placement relies on LLM, not ATR-based calculation
4. No position sizing logic
5. RSS double-load (Phase 0 + Phase 2 News Node)
6. Historical DB sparse for many event types

## Exact Next Steps

1. Create `orchestration/live_watcher.py`:
   - Infinite loop, check price every 15 min via yfinance
   - Query `TradeOutcomeDB` for PENDING trades with entry zones
   - If price within entry zone proximity, invoke Phase 3 orchestrator for that currency
   - On APPROVED, send Telegram notification with trade plan details
2. Create Telegram notification module:
   - Format message with currency, direction, entry zone, SL, TP, R:R, reasoning
   - Send via Telegram Bot API
3. Integrate APScheduler for:
   - `run_phase3.py` every 1 hour during market hours
   - `trade_evaluator.py` every 15 min
   - `live_watcher.py` continuous
"""

with open(".ai/current_task.md", "w") as f:
    f.write(content)

# === .ai/decisions.md ===
content = r"""
# Architectural Decisions

## Decision: LLM Interpretation Separate from Deterministic Scoring

**Status**: VERIFIED_FROM_CODE

**Context**: All NLP pipelines (event, news, speaker) and Technical Agent produce scores.

**Reason**: Prevents LLM hallucination from affecting trade-critical math. Enables auditability and backtesting. LLM produces semantic interpretation (direction, sentiment, reasoning); Python computes all numeric scores.

**Consequences**: Two-layer architecture per pipeline. Post-LLM enforcement (`_enforce_market_context_values`) needed to correct drift. Scorers are independently testable.

**Affected Files**: `agents/fundamental/nlp_event.py`, `agents/fundamental/nlp_news.py`, `agents/fundamental/nlp_x.py`, `agents/technical/technical_analyzer.py`

---

## Decision: Currency-Native Direction

**Status**: VERIFIED_FROM_CODE

**Context**: Signal direction stored in DB and passed between agents.

**Reason**: A single currency appears in multiple pairs. Storing native direction (1 = currency strengthens) avoids ambiguity and enables cross-asset analysis. Instrument translation happens only at display via `routing.py`.

**Consequences**: `translate_instrument_direction()` required for display. JPY bullish means USDJPY down. Cross-asset graph can compare USD vs CAD directly.

**Affected Files**: `core/routing.py`, all signal DB tables, `agents/fundamental/phase2_graph.py`

---

## Decision: Forward-Only DB Migration

**Status**: VERIFIED_FROM_CODE

**Context**: SQLite schema evolution.

**Reason**: SQLite has limited ALTER support. Forward-only ensures backward compatibility. All new columns are `nullable=True`.

**Consequences**: `init_db()` runs `create_all` + migration helpers on every startup. Old columns never removed. Migration functions: `migrate_trading_signals_table`, `migrate_economic_events_table`, `migrate_news_signals_table`, `migrate_additional_indexes`.

**Affected Files**: `core/database.py`

---

## Decision: SQLite WAL Mode

**Status**: VERIFIED_FROM_CODE

**Context**: Concurrent DB access (orchestrator + trade evaluator).

**Reason**: WAL allows concurrent reads during writes. `busy_timeout=5000` retries on lock. `synchronous=NORMAL` is safe with WAL.

**Consequences**: `core/crypto_agent.db-wal` and `core/crypto_agent.db-shm` files appear. Pragmas applied via `@event.listens_for(engine, "connect")`.

**Affected Files**: `core/database.py`

---

## Decision: PydanticOutputParser + Markdown Stripping

**Status**: VERIFIED_FROM_CODE

**Context**: Arvan GLM-5.2 wraps JSON in markdown code blocks.

**Reason**: ` ```json ... ``` ` wrapping breaks Pydantic parsing. `_strip_json_markdown` as `RunnableLambda` in chain handles this globally.

**Consequences**: All LLM chains use pattern: `prompt | llm | RunnableLambda(_strip_json_markdown) | PydanticOutputParser`. Retry on parse failure via `invoke_with_retry`.

**Affected Files**: `core/llm_utils.py`, all agent LLM chains

---

## Decision: None vs 0.0 Score Semantics

**Status**: VERIFIED_FROM_CODE

**Context**: When fundamental pipeline is disabled by Head Agent, score should not be 0.0 (which means neutral).

**Reason**: Risk Manager confused "not evaluated" with "neutral", causing incorrect WAIT decisions. Explicit `fund_status` tracking in `run_phase3.py` distinguishes "evaluated" from "not_evaluated".

**Consequences**: `final_score = fund_score_val if fund_status == "evaluated" else (tech_report.score if tech_report else 0.0)`. If fundamental not run, technical score is used. CompositeSignal can be created with tech_driven status.

**Affected Files**: `orchestration/run_phase3.py`, `agents/risk/risk_manager.py`

---

## Decision: Head Agent Dynamic Resource Allocation

**Status**: VERIFIED_FROM_CODE

**Context**: Running all pipelines every time wastes tokens and time.

**Reason**: Head Agent checks ADX/EMA for trend and DB for upcoming events. Issues ExecutionPlan with sub-pipeline toggles and timeframe. Ranging market + no events → skip Events, keep News, focus Technical on H1.

**Consequences**: `enable_events`, `enable_news`, `enable_speakers` flags in PipelineState. Head Agent LLM call adds ~10s but saves multiple LLM calls.

**Affected Files**: `agents/supervisor/head_agent.py`, `orchestration/run_phase3.py`, `agents/fundamental/phase2_graph.py`

---

## Decision: Speakers Pipeline Force-Disabled

**Status**: VERIFIED_FROM_CODE

**Context**: No real Twitter/Statement API integrated. Mock JSON would produce repetitive signals.

**Reason**: `plan.activate_speakers = False` hardcoded in `run_phase3.py` after Head Agent plan. Head Agent prompt explicitly instructs always False.

**Consequences**: Speaker node in Phase 2 graph still exists but returns `{"speaker_signal": None}` when disabled. `nlp_x.py` and `e2e_speaker_pipeline.py` remain functional for future re-enablement.

**Affected Files**: `agents/supervisor/head_agent.py`, `orchestration/run_phase3.py`, `agents/fundamental/phase2_graph.py`

---

## Decision: 7-Factor Deterministic Technical Score

**Status**: VERIFIED_FROM_CODE

**Context**: Technical Agent needs reproducible, auditable, tunable score.

**Reason**: LLM score hallucination is non-reproducible. 7 factors with fixed weights enable backtest calibration. LLM writes strategy narrative only.

**Consequences**: `calculate_technical_metrics()` computes all factors in Python. Factor weights: Structure 25%, SMC Location 20%, Trend 18%, MTF 12% (deferred=0.0), Momentum 10%, Volatility 10%, Price Action 5%. MTF contributes nothing currently.

**Affected Files**: `agents/technical/technical_analyzer.py`

---

## Decision: Trade Memory System

**Status**: VERIFIED_FROM_CODE

**Context**: System should learn from past trade outcomes without LLM fine-tuning.

**Reason**: Context-based learning via DB stats injection into Risk Manager prompt. APPROVED trades registered as PENDING, evaluator checks price, updates to WIN/LOSS/EXPIRED. Win rate stats passed to Risk Manager.

**Consequences**: `TradeOutcomeDB` table. `trade_evaluator.py` runs independently. `get_trade_memory_stats()` queries per-currency stats. Risk Manager prompt includes memory section with performance thresholds.

**Affected Files**: `core/database.py`, `orchestration/trade_evaluator.py`, `agents/risk/risk_manager.py`, `orchestration/run_phase3.py`

---

## Decision: Phase 0 Ingestion Before Head Agent

**Status**: VERIFIED_FROM_CODE

**Context**: Head Agent checks DB for upcoming events to decide pipeline activation.

**Reason**: If DB is stale (e.g., last scraped yesterday), Head Agent may miss today's NFP and incorrectly disable fundamentals. Phase 0 ensures DB freshness.

**Consequences**: `run_ingestion_phase()` runs before Head Agent in `run_phase3.py`. FF scraper with 3x retry + RSS loader. Adds ~15-30s to startup.

**Affected Files**: `orchestration/run_phase3.py`

---

## Decision: FF Bridge Retry Mechanism

**Status**: VERIFIED_FROM_CODE

**Context**: Forex Factory frequently drops connections (`IncompleteRead`).

**Reason**: Without retry, ingestion failure blocks entire pipeline. With retry, system proceeds with existing DB data if all retries fail.

**Consequences**: 3 retries with 5s delay in `run_ingestion_phase()`. On final failure: logs critical, proceeds with existing data.

**Affected Files**: `orchestration/run_phase3.py`

---

## Decision: External FF Scraper Repo

**Status**: VERIFIED_FROM_CODE

**Context**: FF JSON API does not provide `actual` values.

**Reason**: HTML scraping required for actual values. External repo handles Cloudflare/proxy complexity. Accessed via `sys.path.insert(0, os.path.join(ROOT_DIR, 'ext_ff_scraper/src'))`.

**Consequences**: `ext_ff_scraper` must be cloned at project root. `ff_bridge.py` imports from `github.scrapper.src.forexfactory.providers`. Fragile dependency on external repo structure.

**Affected Files**: `ingestion/ff_bridge.py`

---

## Decision: Canonical Event Matching for Historical Std

**Status**: VERIFIED_FROM_CODE

**Context**: Historical surprise std calculation needs to match similar past events.

**Reason**: Simple title matching is too loose. Canonical matching normalizes titles to keys (e.g., "US_CPI_YY") for precise grouping. IQR outlier filtering removes COVID anomalies.

**Consequences**: Multi-tier fallback chain: canonical matching (5y window) → expand window (10y, 15y) → all-time → loose match → category fallback → absolute fallback. Many events still fall back due to sparse historical DB.

**Affected Files**: `agents/fundamental/data_fetcher.py`

---

## Decision: News Macro Digest Optimization

**Status**: VERIFIED_FROM_CODE

**Context**: Analyzing each news item separately is expensive (many LLM calls).

**Reason**: Group up to 10 news items per currency into single LLM call. Produces one aggregated signal per currency.

**Consequences**: `NewsDigestInterpretation` schema defined in `nlp_news.py` (not `models.py`). `NewsDigestScorer` uses highest source_reliability among items. `is_aggregated=True` in `NewsSignalDB`. `source_links_json` stores all source links.

**Affected Files**: `agents/fundamental/nlp_news.py`

---

## Decision: Layered Package Structure

**Status**: VERIFIED_FROM_CODE

**Context**: Project grew from flat files to 20+ modules.

**Reason**: Separation of concerns: core infrastructure, data ingestion, agent logic, orchestration. Enables future microservice extraction.

**Consequences**: All imports use package-relative paths (`from core.database import ...`). Scripts run with `python -m orchestration.run_phase3` from root. `__init__.py` in each package.

**Affected Files**: All source files (import paths updated)
"""

with open(".ai/decisions.md", "w") as f:
    f.write(content)

# === .ai/roadmap.md ===
content = r"""
# Roadmap

Based on existing TODOs, incomplete implementations, disabled functionality, missing tests, and architectural gaps in the codebase.

## P0 — Critical

1. **Live Watcher (`live_watcher.py`)**: Event-driven execution loop. Monitor price every ~15 min via yfinance (no LLM). Query `TradeOutcomeDB` for PENDING trades with entry zones. When price approaches entry zone, wake Phase 3 orchestrator for confirmation. On APPROVED, send notification.
   - Rationale: System currently requires manual CLI invocation. WAIT trades define entry zones but nothing monitors them.

2. **Telegram/Discord Notification**: On APPROVED Risk Manager decision, send formatted message with entry zone, SL, TP, R:R, and reasoning.
   - Rationale: No notification mechanism exists. User must watch terminal output.

3. **APScheduler Integration**: Schedule `run_phase3.py` (hourly during market hours), `trade_evaluator.py` (every 15 min), `live_watcher.py` (continuous).
   - Rationale: All execution is manual CLI. No automated scheduling.

## P1 — Important

4. **Speaker API Integration**: Replace mock JSON in `e2e_speaker_pipeline.py` with real Twitter/Statement API (e.g., Tweety). Re-enable speakers in Head Agent and `run_phase3.py`.
   - Rationale: Speakers force-disabled. `nlp_x.py` is complete and functional but unused.

5. **Tests for Phase 2/3**: Unit tests for Phase 2 aggregator math, Phase 3 technical scorer, Risk Manager gate logic, Head Agent plan generation, cross-asset graph.
   - Rationale: Only `test_event_scorer.py` exists. Critical scoring logic untested.

6. **Historical DB Enrichment**: Import more historical CSV data (Core CPI, PPI, Jobless Claims) to reduce `historical_std` fallback frequency.
   - Rationale: Many events fall back to category/absolute defaults. Historical std reliability affects confidence scoring.

7. **RSS Double-Load Fix**: Phase 2 News Node should read from `raw_news_items` DB (populated by Phase 0) instead of fetching feeds again.
   - Rationale: Redundant network calls. Phase 0 already stores to DB.

## P2 — Useful

8. **Backtest Calibration**: Use accumulated `TradeOutcomeDB` data to tune 7-factor weights and fusion weights. Build calibration layer mapping scores to win probabilities.
   - Rationale: Factor weights are hand-tuned. Need empirical validation.

9. **Dynamic Fusion Weighting**: Head Agent or Risk Manager should dynamically weight fundamental vs technical based on market regime, event proximity, and trading horizon.
   - Rationale: Currently no dynamic weighting implemented despite architectural intent. Scores are simply taken from whichever pipeline ran.

10. **MTF Confluence Implementation**: Simultaneous analysis across H4/H1/M15 for structure alignment scoring. Currently hardcoded to 0.0 (12% weight contributes nothing).
    - Rationale: MTF is a key factor in SMC trading. 12% of technical score is effectively dead weight.

11. **ATR-Based Stop Loss**: Risk Manager should compute SL based on ATR rather than relying on LLM to place outside OB zone.
    - Rationale: LLM-placed SLs may be inconsistent. ATR-based SL is deterministic and standard practice.

12. **Position Sizing**: Based on ATR stop distance and fixed % account risk (e.g., 1%).
    - Rationale: No position sizing logic exists. Trade plan has entry/SL/TP but no size.

## P3 — Future

13. **Strategy Engine**: Separate from Risk Manager. Select Trend Following vs Mean Reversion vs Breakout based on market regime. Adjust factor weights accordingly.
    - Rationale: Currently one-size-fits-all scoring. Different regimes need different factor emphasis.

14. **Portfolio Exposure Management**: Track open positions, correlation exposure, daily drawdown limits.
    - Rationale: No portfolio-level risk management.

15. **Open Position Manager**: After signal sent, monitor price for TP/SL hit without LLM. Update memory DB automatically.
    - Rationale: Currently `trade_evaluator.py` handles this but as separate manual script, not integrated into live loop.

16. **DX-Y.NYB Alternative**: Find more reliable ticker for Dollar Index or use EURUSD inverse as proxy.
    - Rationale: DX-Y.NYB frequently returns no data from yfinance.

17. **Calibration Layer**: Map technical scores to empirical win probabilities using historical data.
    - Rationale: Score 0.8 should mean something measurable (e.g., 72% win probability), not just "high."
"""

with open(".ai/roadmap.md", "w") as f:
    f.write(content)

# === .ai/components/fundamental.md ===
content = r"""
# Fundamental Component

## Responsibility

Analyzes economic events, RSS news, and speaker statements. Produces deterministic directional scores using LLM for semantic interpretation only. Aggregates into `CompositeSignal` with confluence rules and temporal risk filtering.

## Source Paths

- `agents/fundamental/data_fetcher.py`
- `agents/fundamental/nlp_event.py`
- `agents/fundamental/nlp_news.py`
- `agents/fundamental/nlp_x.py`
- `agents/fundamental/e2e_pipeline.py`
- `agents/fundamental/e2e_news_pipeline.py`
- `agents/fundamental/e2e_speaker_pipeline.py`
- `agents/fundamental/phase2_graph.py`

## Entry Points

- `analyze_economic_event()` in `nlp_event.py`
- `analyze_news_digest()` in `nlp_news.py`
- `analyze_speaker_text()` in `nlp_x.py`
- `build_phase2_graph()` in `phase2_graph.py`
- `build_cross_asset_graph()` in `phase2_graph.py`

## Classes

### data_fetcher.py
- `HistoricalStdResult` (dataclass): value, source, observations, window_years, canonical_key, matched_titles, outliers_removed. Method: `is_reliable()`.

### nlp_event.py
- `EventAnalysisService`: LLM chain (prompt | llm | strip_markdown | parser). `_enforce_market_context_values()` corrects drift.
- `EventSignalScorer`: deterministic scoring engine.
- `EventSignalRepository`: persists to `EventSignalDB`.

### nlp_news.py
- `NewsAnalysisService`: single news LLM chain.
- `NewsSignalScorer`: single news deterministic scorer.
- `NewsSignalRepository`: persists to `NewsSignalDB`.
- `NewsDigestAnalysisService`: Macro Digest LLM chain.
- `NewsDigestScorer`: Macro Digest deterministic scorer.

### nlp_x.py
- `SpeakerAnalysisService`: LLM chain (prompt | llm | strip_markdown | parser).
- `SpeakerSignalScorer`: deterministic scoring engine.
- `SpeakerSignalRepository`: persists to `TradingSignalDB`.

### phase2_graph.py
- `CompositeSignal` (Pydantic): currency, direction, final_score, confidence, is_tradable, confluence_status, components_used, reasoning.

## Functions

### data_fetcher.py
- `fetch_market_context(ticker, currency, event_title, actual, forecast, previous, event_currency)` → `MarketContext`
- `fetch_historical_surprise_std_full(event_title, currency, window_years, min_observations, expand_window_if_needed)` → `HistoricalStdResult`
- `calculate_surprise_factor(actual, forecast, historical_std)` → `float`
- `calculate_volatility_multiplier(iv, hv, atr_current, atr_baseline)` → `float`
- `_canonicalize_event_title(title, currency)` → `Optional[str]`
- `_filter_outliers_iqr(values, iqr_multiplier)` → `(filtered_list, outliers_removed_count)`
- `_compute_robust_std(surprises, min_after_filter)` → `(std_value, outliers_removed_count)`

### nlp_event.py
- `analyze_economic_event(event_input, market_context, ticker, llm, prompt, persist, scorer, repository, analysis_service)` → `(EventInterpretationEvent, EventSignal)`
- `build_event_brief(event_input)` → `str`
- `event_input_from_forex_factory(event, detail_text)` → `EventAnalysisInput`
- `event_input_from_db_record(record)` → `EventAnalysisInput`

### nlp_news.py
- `analyze_news_item(news_item, market_context, ticker, llm)` → `(NewsInterpretation, NewsSignal)`
- `analyze_news_digest(currency, news_items, market_context, ticker, llm, persist)` → `(NewsDigestInterpretation, NewsSignal)`
- `news_item_from_feedparser(entry, source, source_reliability, category, currency, impact)` → `NewsItem`

### nlp_x.py
- `analyze_speaker_text(item, market_context, ticker, llm)` → `(SpeakerInterpretation, SpeakerSignal)`
- `generate_signal(interpretation, speaker_weight)` → `Signal` (legacy)
- `_resolve_speaker(speaker_name, weight_override)` → `Speaker`

### phase2_graph.py
- `build_phase2_graph()` → compiled LangGraph
- `build_cross_asset_graph()` → compiled LangGraph
- `get_temporal_context()` → `dict`
- `fetch_and_analyze_event_node(state)` → `dict`
- `fetch_and_analyze_news_node(state)` → `dict`
- `fetch_latest_speaker_signal_node(state)` → `dict`
- `aggregate_signals_node(state)` → `dict`
- `cross_asset_consistency_node(state)` → `dict`
- `generate_detailed_currency_reports_node(state)` → `dict`
- `generate_global_summary_node(state)` → `dict`

## Data Models

### Input Models (from `core/models.py`)
- `MarketContext`: target_asset, event_title, event_category, actual/forecast/previous values, historical_std, HV, ATR, IV, yield_spread, calculated_surprise, calculated_volatility
- `EventAnalysisInput`: title, currency, impact, category, event_date, actual, forecast, previous, raw strings, detail_text
- `NewsItem`: title, summary, published, link, source, source_reliability, category, currency, impact
- `SpeakerTextItem`: text, speaker_name, published, source, source_type, link, speaker_weight_override, external_id, social fields
- `Speaker`: name, role, primarily_impacts, weight. `from_name()` class method loads from DB.

### Interpretation Models (from `core/models.py`)
- `EventInterpretationEvent`: reasoning, asset_class, direction, nlp_sentiment_score, surprise_factor, expected_volatility, cross_assets, surprise_interpretation, momentum_vs_previous, quantitative_alignment, economic_implication, impact_horizon, is_consistent_with_event_type
- `NewsInterpretation`: reasoning, asset_class, direction, nlp_sentiment_score, surprise_factor, expected_volatility, cross_assets, detected_event_category, headline_body_alignment, quantitative_alignment, impact_horizon
- `SpeakerInterpretation`: reasoning, asset_class, direction, nlp_sentiment_score, surprise_factor, expected_volatility, cross_assets, statement_market_alignment, quantitative_alignment, policy_signal_type, impact_horizon, detected_event_category, is_reiteration, guidance_bias

### Output Models (from `core/models.py`)
- `EventSignal`: reasoning, asset_class, direction, final_score, confidence, is_tradable, expected_volatility_level, signal_half_life_mins, cross_asset_signals, event metadata, values, scoring breakdown, interpretation fields, ticker
- `NewsSignal`: similar structure with source, source_reliability, event_category_weight, data_completeness_score, data_quality_factor, headline_body_alignment, quantitative_alignment, ticker
- `SpeakerSignal`: similar with speaker_name, speaker_role, speaker_weight, source, source_type, source_reliability, statement_type_weight, policy_signal_type, impact_horizon

### Phase 2 Models (from `phase2_graph.py`)
- `CompositeSignal`: currency, direction (-1/0/1), final_score [-1,1], confidence [0,1], is_tradable, confluence_status, components_used, reasoning

## Scoring Formulas

### Event (EventSignalScorer)
```
final_score = clamp(sentiment * impact_weight * (1+surprise) * data_quality_factor * quant_alignment * std_reliability_mult, -1, 1)
confidence = (impact_weight*0.30 + |surprise|*0.25 + completeness*0.20 + quant_alignment*0.15 + std_reliability*0.10) - penalties
is_tradable = |score| >= 0.40 AND confidence >= 0.55 AND completeness >= 0.40 AND impact != "Low"
```
Penalties: high surprise + weak sentiment (-0.10), fallback std (-0.05), LLM inconsistency (-0.10)

### News (NewsDigestScorer)
```
final_score = clamp(sentiment * category_weight * (1+surprise) * data_quality_factor, -1, 1)
confidence = (completeness*0.30 + |surprise|*0.25 + source_reliability*0.20 + headline_body_alignment*0.15 + quant_alignment*0.10)
is_tradable = |score| >= 0.35 AND confidence >= 0.55 AND completeness >= 0.45
```

### Speaker (SpeakerSignalScorer)
```
final_score = clamp(sentiment * speaker_weight * stmt_type_weight * (1+surprise) * data_quality * alignment, -1, 1)
is_tradable = |score| >= 0.40 AND confidence >= 0.60 AND completeness >= 0.40
```

### Phase 2 Aggregator Confluence
```
Aligned: score *= 1.1, confidence += 0.10
Conflicting: score *= 0.7, confidence -= 0.20, is_tradable = False
Temporal filter: weekend/holiday → is_tradable = False
Cross-asset anomaly: score *= 0.8, confidence -= 0.15
```

## Inputs

- Economic events from `economic_events_history` DB or Forex Factory live
- RSS news from feeds (ForexLive, FXStreet, MarketWatch, etc.)
- Speaker statements from `raw_speaker_items` DB or mock JSON
- Market data from yfinance (ATR, HV, IV, yield spread)
- Historical events from `economic_events_history` for std calculation

## Outputs

- `EventSignal` → `EventSignalDB`
- `NewsSignal` → `NewsSignalDB` (is_aggregated=True for digest)
- `SpeakerSignal` → `TradingSignalDB`
- `CompositeSignal` → in-memory state (cross-asset graph)
- Audit JSON artifacts in `runs/` directory (Phase 1 E2E pipelines)

## Dependencies

- `core/database.py`, `core/models.py`, `core/llm_utils.py`, `core/routing.py`
- `ingestion/ff_bridge.py`, `ingestion/rss_feed_loader.py`
- `langchain_core`, `langgraph`
- `yfinance`, `numpy`, `pandas`

## Database Interactions

### Reads
- `economic_events_history`: historical std calculation (canonical matching), event loading (48h backward)
- `trading_signals`: speaker signal loading (24h backward)
- `speakers`: speaker weight lookup

### Writes
- `event_signals`: full event signal with raw payloads
- `news_signals`: news signal with is_aggregated flag
- `trading_signals`: speaker signal with full audit trail
- `composite_signals`: Phase 2 aggregated signals
- `raw_news_items`: RSS staging (via E2E pipeline)

## LLM Interactions

### Event Analysis
- Chain: `_EVENT_SYSTEM_PROMPT + examples` + `_EVENT_HUMAN_TEMPLATE` → `EventInterpretationEvent`
- Post-LLM: `_enforce_market_context_values()` corrects surprise_factor and expected_volatility
- Prompt includes: INVERSE EVENTS rules, Central Bank mapping, beat/miss/in_line definitions

### News Digest
- Chain: `_NEWS_DIGEST_SYSTEM_PROMPT` + `_NEWS_DIGEST_HUMAN_TEMPLATE` → `NewsDigestInterpretation`
- Post-LLM: same enforcement
- Prompt includes: synthesize macro view from multiple items

### Speaker Analysis
- Chain: `_SPEAKER_SYSTEM_PROMPT` + `_SPEAKER_HUMAN_TEMPLATE` → `SpeakerInterpretation`
- Post-LLM: same enforcement
- Prompt includes: policy_signal_type, guidance_bias, is_reiteration detection

### Phase 2 Reports
- Detailed per-currency: `_DETAILED_CURRENCY_PROMPT` → free text
- Global summary: `_GLOBAL_SUMMARY_SYSTEM_PROMPT` → free text
- Temporal context injected into all prompts

## Error Handling

- LLM parse failure: `invoke_with_retry()` with 2 retries, exponential backoff (1s, 2s)
- Markdown wrapping: `_strip_json_markdown` in chain
- Market context drift: `_enforce_market_context_values()` post-correction
- Historical std missing: multi-tier fallback (canonical → loose → category → absolute)
- yfinance failure: logged, metrics set to None, data_completeness drops
- DB failure: `SQLAlchemyError` caught, rollback, re-raise in repository layer

## Retry Behavior

- LLM: `invoke_with_retry(chain, inputs, max_retries=2, initial_delay=1.0)` — exponential backoff
- FF scraper: 3 retries with 5s delay (in `run_phase3.py`)
- RSS feeds: `FeedFetcher` with 2 attempts (10s timeout, 25s retry timeout)

## Important Invariants

1. LLM never sets final_score; Python scorer does.
2. `surprise_factor` in interpretation MUST equal `calculated_surprise` in MarketContext (enforced post-LLM).
3. `expected_volatility` in interpretation MUST equal `calculated_volatility` (enforced post-LLM).
4. `direction` must match sign of `nlp_sentiment_score` (Pydantic validator).
5. Signal direction is currency-native.
6. Phase 2 aggregator applies temporal filter AFTER confluence rules.

## Configuration

### Constants (nlp_event.py)
- `EVENT_IMPACT_WEIGHT`: High=1.00, Medium=0.70, Low=0.40, Unknown=0.50
- `EVENT_HALF_LIFE_BASE_MINS`: Interest Rate=240, NFP=180, CPI=150, GDP=120, Employment=90, PMI=60, Default=60
- `HALF_LIFE_IMPACT_MULTIPLIER`: High=1.5, Medium=1.0, Low=0.6, Unknown=0.8
- Tradability thresholds: score>=0.40, conf>=0.55, completeness>=0.40

### Constants (nlp_news.py)
- `SOURCE_RELIABILITY`: ForexLive=0.82, DailyFX=0.80, Forex Factory=0.85, Unknown=0.65
- `EVENT_IMPORTANCE`: Interest Rate=1.00, NFP=1.00, CPI=0.95, GDP=0.85, Housing=0.55, Unknown=0.40
- `HALF_LIFE_BASE_MINS`: 90

### Constants (nlp_x.py)
- `SOURCE_RELIABILITY`: Official Transcript=0.98, Press Conference=0.96, Bloomberg=0.92, X=0.72
- `STATEMENT_TYPE_WEIGHT`: tweet=0.90, statement=1.00, speech=1.10, press_conference=1.10, testimony=1.15
- `SOURCE_TYPE_BASE_MINS`: tweet=45, statement=75, speech=120, press_conference=135

### Constants (data_fetcher.py)
- `HISTORY_PERIOD`: "1y"
- `DEFAULT_WINDOW_YEARS`: 5
- `MAX_WINDOW_YEARS`: 15
- `DEFAULT_MIN_OBSERVATIONS`: 10
- `ABSOLUTE_MIN_OBSERVATIONS`: 5
- `MAX_IV_HV_RATIO`: 3.0
- `MAX_ATR_RATIO`: 3.0
- `CATEGORY_DEFAULT_STD`: per-category defaults (CPI=0.15, NFP=80000, GDP=0.45, etc.)
- `ABSOLUTE_FALLBACK_STD`: 0.15

## Tests

- `tests/test_event_scorer.py`: Tests `EventSignalScorer` math. 8 test cases covering perfect signal, low impact, fallback std, high surprise + weak sentiment, LLM inconsistency, inverse events, half-life, data completeness.
- `tests/evaluate_signals.py`: Evaluation script (not unit test). Logic audit, 24h price backtest, Half-life validation, Cross-asset validation.
- `tests/evaluate_news_signals.py`: Evaluation script. Data metadata audit, scoring math audit, qualitative hallucination check.
- NO tests for Phase 2 graph, aggregator, cross-asset graph, news scorer, speaker scorer.

## Known Issues

1. Historical std fallback for Core CPI, PPI m/m, Jobless Claims (sparse DB)
2. DX-Y.NYB unreliable from yfinance
3. BoJ RSS feed connection resets
4. Speaker pipeline force-disabled
5. RSS double-load (Phase 0 + Phase 2 News Node)

## Extension Points

1. New event type: add canonical key mapping in `_canonicalize_event_title()`, add category to `CATEGORY_DEFAULT_STD`
2. New news source: add to `rss_feed_config.py`, add reliability to `SOURCE_RELIABILITY`
3. New speaker: add to `seed_speakers.py` SPEAKERS list
4. New pipeline toggle: add to `PipelineState`, handle in node function
5. New cross-asset pair: add to `inverse_pairs` in `cross_asset_consistency_node`

## Dangerous Areas

1. `_canonicalize_event_title()`: changing keyword mapping affects historical std matching for ALL events
2. Phase 2 aggregator `apply_confluence()`: changing boost/penalty multipliers affects all composite signals
3. `_enforce_market_context_values()`: removing this allows LLM drift in surprise/volatility
4. `CompositeSignal` schema: used by cross-asset graph, Risk Manager, and orchestrator; changes propagate everywhere
5. Pipeline toggle flags: if all are False, Phase 2 graph produces empty CompositeSignal
"""

with open(".ai/components/fundamental.md", "w") as f:
    f.write(content)

# === .ai/components/technical.md ===
content = r"""
# Technical Component

## Responsibility

Independent technical analysis agent. Fetches price history, calculates 7 deterministic factors, produces technical score and confidence. LLM used ONLY for narrative strategy generation, never for scores.

## Source Paths

- `agents/technical/technical_analyzer.py`

## Entry Points

- `TechnicalAgent.analyze(ticker, timeframe, temporal_context)` → `(TechnicalMetrics, TechnicalReport)`
- `calculate_technical_metrics(ticker, timeframe)` → `TechnicalMetrics`

## Classes

- `TechnicalAgent`: main agent class. `__init__(llm)`, `analyze(ticker, timeframe, temporal_context)`
- `ComponentScores` (Pydantic): structure, trend, smc_location, mtf_confluence, momentum, volatility, price_action (all `Optional[float]`)
- `TechnicalMetrics` (Pydantic): current_price, technical_score, technical_confidence, components (ComponentScores), raw SMC data (trend_status, adx_value, recent_bos, active_bullish_ob, active_bearish_ob, FVG zones, rsi, macd_histogram, last_candle_type)
- `TechnicalReport` (Pydantic): direction, score [-1,1], confidence [0,1], strategy, reasoning
- `LLMStrategy` (Pydantic): strategy, reasoning (LLM output only)

## Functions

- `calculate_technical_metrics(ticker, timeframe)` → `TechnicalMetrics`
- `_fetch_price_history(ticker, interval)` → `Optional[pd.DataFrame]`
- `_clamp(value, low, high)` → `float`

## 7-Factor Scoring

| Factor | Weight | Source | Scoring Logic |
|--------|--------|--------|---------------|
| Structure | 25% | `smc.bos_choch()` | Bullish BOS = +0.8, Bearish BOS = -0.8 |
| SMC Location | 20% | `smc.ob()`, `smc.fvg()` | Cumulative: Bull OB +0.8, Bear OB -0.8, Bull FVG +0.5, Bear FVG -0.5. Clamped [-1,1] |
| Trend | 18% | `talipp.EMA(50)`, `talipp.EMA(200)` | Uptrend (EMA50>EMA200, price>EMA50) = +0.8, Downtrend = -0.8, Ranging = 0.0 |
| MTF Confluence | 12% | Deferred | Always 0.0 |
| Momentum | 10% | `talipp.RSI(14)`, `talipp.MACD(12,26,9)` | MACD>0 & RSI>55 = +0.8, MACD<0 & RSI<45 = -0.8, else 0.0 |
| Volatility | 10% | `talipp.ADX(14)` | ADX>25 = +0.5, ADX<20 = -0.5, else 0.0 |
| Price Action | 5% | Candle pattern detection | Bullish/Bearish Engulfing = ±1.0, Strong Close = ±0.5, Neutral = 0.0 |

Final score: weighted sum clamped to [-1, 1]. Only calculated if Structure or SMC Location is not None.

Direction: score > 0.1 = Bullish (1), score < -0.1 = Bearish (-1), else Neutral (0).

Confidence:
- Data Quality (0.2): trend available (+0.5) + SMC available (+0.5)
- Setup Quality (0.3): OB nearby (+0.5) + FVG nearby (+0.5)
- Component Agreement (0.5): max(pos_count, neg_count) / 3.0 for structure/trend/momentum

## SMC Location Fallback Logic

For OB and FVG, prefers unmitigated zones (MitigatedIndex is NaN). If none found, falls back to mitigated zones. Filters: Bullish zones must have Top < current_price. Bearish zones must have Bottom > current_price.

## Timeframe Mapping

```python
TIMEFRAME_MAP = {"M15": "15m", "H1": "60m", "H4": "4h", "D1": "1d"}
```

Period based on interval:
- 60m / 1h → 1mo
- 15m → 5d
- 4h → 3mo
- else → 1y

## Data Models

### Input
- ticker: string (yfinance symbol)
- timeframe: string (M15, H1, H4, D1)
- temporal_context: dict (market_session, is_weekend, is_holiday)

### Output
- `TechnicalMetrics`: raw data + component scores + final technical_score/technical_confidence
- `TechnicalReport`: direction, score, confidence, strategy (LLM), reasoning (LLM)

## Dependencies

- `smartmoneyconcepts.smc`: swing_highs_lows, bos_choch, ob, fvg
- `talipp.indicators`: RSI, MACD, EMA, ADX
- `talipp.ohlc`: OHLCV (must use positional args: `OHLCV(o, h, l, c, v)`)
- `yfinance`
- `core/llm_utils.py`: invoke_with_retry, _strip_json_markdown
- `langchain_core`

## Database Interactions

None. Technical Agent does not read from or write to any DB table.

## External APIs

- yfinance: OHLCV price data download

## LLM Interactions

### Chain
```
_TECH_SYSTEM_PROMPT + _TECH_HUMAN_TEMPLATE → LLMStrategy (strategy, reasoning only)
```

### Prompt Content
- System: SMC analyst role. Scores already calculated by Python. LLM formulates strategy only.
- Human: Technical score, confidence, all component scores, raw market context (price, trend, BOS, OB, FVG, RSI, MACD, ADX, candle type, temporal context)
- Output: `LLMStrategy` with strategy string and reasoning string

### LLM Responsibility vs Deterministic
- LLM: strategy narrative, reasoning text
- Python: all 7 component scores, final technical_score, technical_confidence, direction

## Error Handling

- yfinance returns None/empty: metrics set to defaults, `technical_score = None`, returns early with "Insufficient data" report
- talipp indicator failure: caught per-indicator, logged as warning, component set to None
- smartmoneyconcepts failure: caught, logged, SMC components set to None
- LLM failure: caught, returns deterministic scores with fallback strategy "Stand aside"

## Retry Behavior

- LLM: `invoke_with_retry(chain, prompt_inputs)` with default max_retries=2
- No retry for yfinance or indicator calculations

## Important Invariants

1. LLM NEVER produces scores. Only `calculate_technical_metrics()` computes scores.
2. `None` means "not evaluated"; `0.0` means "evaluated and neutral".
3. Score only calculated if Structure or SMC Location is not None (requires SMC data).
4. Direction threshold: ±0.1 (scores between -0.1 and 0.1 are Neutral).
5. SMC Location is cumulative (OB + FVG contributions added together, then clamped).
6. ADX requires OHLCV objects with positional args.
7. ADX return value is `ADXVal` object; must access `.adx` attribute.
8. MACD return value is `MACDVal` object; must access `.histogram` attribute.

## Configuration

- `HISTORY_PERIOD`: "1y" (base default, overridden by timeframe)
- `TIMEFRAME_MAP`: maps timeframe codes to yfinance intervals
- No environment variables specific to this component

## Tests

None. No unit tests exist for Technical Agent.

## Known Issues

1. `talipp` OHLCV constructor: keyword args cause TypeError. Must use positional.
2. `talipp` ADX returns `ADXVal` object, not float. Must access `.adx`.
3. `talipp` MACD returns `MACDVal` object. Must access `.histogram`.
4. `smartmoneyconcepts` API drift between versions. Fallback try/except blocks used.
5. MTF Confluence factor deferred (0.0), contributing 12% weight as nothing.
6. `swing_length=5` hardcoded for swing detection; may be too sensitive for some timeframes.
7. yfinance may return MultiIndex columns; flattening handled but must be maintained.

## Extension Points

1. Add new factor: add to `ComponentScores`, add weight to `weights` dict, implement calculation in `calculate_technical_metrics()`
2. Change timeframe: add to `TIMEFRAME_MAP`, adjust period mapping
3. Implement MTF: replace `components.mtf_confluence = 0.0` with actual multi-timeframe analysis
4. Add new SMC concept: extend `smartmoneyconcepts` calls, add to loc_score calculation

## Dangerous Areas

1. Factor weights in `weights` dict: changing weights affects ALL technical scores; must be backtested
2. Direction threshold (±0.1): changing this affects how many signals are directional vs neutral
3. SMC Location fallback logic: changing unmitigated→mitigated fallback affects which zones are presented to LLM
4. `swing_length` parameter: affects BOS/OB/FVG detection sensitivity
5. `OHLCV` constructor: must remain positional; keyword args cause TypeError in talipp
6. `.adx` and `.histogram` attribute access: talipp return types are objects, not floats
"""

with open(".ai/components/technical.md", "w") as f:
    f.write(content)

# === .ai/components/risk.md ===
content = r"""
# Risk Component

## Responsibility

Final decision-maker. Fuses Fundamental and Technical reports, resolves conflicts, checks trade memory, structures trade plan (Entry/SL/TP), and issues final decision (APPROVED/REJECTED/WAIT).

## Source Paths

- `agents/risk/risk_manager.py`

## Entry Points

- `RiskManagerAgent.evaluate(...)` → `RiskDecision`

## Classes

- `RiskManagerAgent`: main agent class. `__init__(llm)`, `evaluate(...)`
- `TradePlan` (Pydantic): entry_zone (string), stop_loss (float), take_profit (float), risk_reward_ratio (string)
- `RiskDecision` (Pydantic): decision (Literal["APPROVED", "REJECTED", "WAIT"]), reasoning (string), trade_plan (Optional[TradePlan])

## Functions

- `RiskManagerAgent.evaluate(fund_direction, fund_score, fund_confidence, fund_tradable, fund_reasoning, tech_direction, tech_confidence, tech_strategy, tech_reasoning, current_price, nearest_support, nearest_resistance, mem_total, mem_wins, mem_losses, mem_win_rate)` → `RiskDecision`

## Data Models

### Input Parameters
- Fundamental: direction (int), score (float), confidence (float), tradable (bool), reasoning (str)
- Technical: direction (int), confidence (float), strategy (str), reasoning (str)
- Price levels: current_price (Optional[float]), nearest_support (Optional[float]), nearest_resistance (Optional[float])
- Memory: mem_total (int), mem_wins (int), mem_losses (int), mem_win_rate (float)

### Output
- `RiskDecision`: decision, reasoning, trade_plan (optional TradePlan with entry_zone, stop_loss, take_profit, risk_reward_ratio)

## Decision Framework (from prompt)

1. **Confluence Check**:
   - Aligned (same direction) → APPROVE
   - One directional + other neutral → cautious APPROVE
   - Direct conflict → REJECT (capital preservation)
   - Fund reasoning says "not run" → treat as NOT EVALUATED, rely on Technical
2. **Trade Structuring** (if APPROVED):
   - Longs: SL below support, TP at/near resistance
   - Shorts: SL above resistance, TP at/near support
   - Min R:R 1:1.5; if not achievable → REJECT
3. **WAIT Condition**: Technical says wait for pullback/breakout + fundamentals aligned → WAIT
4. **Mean Reversion Special Case**: Ranging market + price at OB/FVG → may APPROVE counter-trend even with neutral fundamentals. Target opposite end of range. SL outside OB/FVG.
5. **Memory Check**: Poor win rate (<40%) or consecutive losses → tighten rules, demand higher confluence or WAIT.

## Hard-Coded Rejection

```python
if current_price is None or (nearest_support is None and nearest_resistance is None):
    return RiskDecision(decision="REJECTED", reasoning="Cannot structure trade plan due to missing S/R or price data.", trade_plan=None)
```

This runs BEFORE LLM invocation. If price data is missing, no LLM call is made.

## Inputs

- Fundamental report from Phase 2 (or "not run" status)
- Technical report from Technical Agent
- Price levels: current_price from Technical Agent metrics, nearest_support = Bullish OB, nearest_resistance = Bearish OB
- Memory stats from `get_trade_memory_stats(currency)` in `core/database.py`

## Outputs

- `RiskDecision` → consumed by `orchestration/run_phase3.py`
- If APPROVED with TradePlan → registered in `TradeOutcomeDB` by orchestrator

## Dependencies

- `core/llm_utils.py`: invoke_with_retry, _strip_json_markdown
- `langchain_core`

## Database Interactions

### Direct
None. Risk Manager does not directly read/write DB.

### Indirect (via orchestrator)
- Reads: `get_trade_memory_stats(currency)` called by `run_phase3.py`, results passed as parameters
- Writes: On APPROVED, `run_phase3.py` registers trade in `TradeOutcomeDB`

## External APIs

None directly. LLM call via LangChain.

## LLM Interactions

### Chain
```
_RISK_SYSTEM_PROMPT + _RISK_HUMAN_TEMPLATE → RiskDecision
```

### Prompt Content
- System: Risk manager role. Decision framework (confluence, structuring, WAIT, mean reversion, memory). Output rules.
- Human: Fundamental report (direction, score, confidence, tradable, reasoning), Technical report (direction, confidence, strategy, reasoning), Price levels, Memory stats (total, wins, losses, win rate)
- Output: `RiskDecision` with decision, reasoning, and optional TradePlan

### LLM Responsibility vs Deterministic
- LLM: decision reasoning, trade plan structuring (entry/SL/TP placement based on S/R levels)
- Python: hard rejection on missing data (before LLM), memory stats preparation (by orchestrator)

## Error Handling

- Missing price data: hard rejection before LLM call
- LLM failure: returns REJECTED with error message in reasoning
- LLM parse failure: `invoke_with_retry` with 2 retries

## Retry Behavior

- LLM: `invoke_with_retry(chain, prompt_inputs)` with default max_retries=2

## Important Invariants

1. Hard rejection on missing price data runs BEFORE LLM — no token wasted on unstructurable trades
2. If decision is REJECTED or WAIT, trade_plan MUST be null
3. If decision is APPROVED, trade_plan MUST be populated
4. `stop_loss` and `take_profit` must be positive (Pydantic validator)
5. Memory stats are per-currency, not global
6. nearest_support and nearest_resistance are Order Block levels from Technical Agent, not FVG zones

## Configuration

No configurable constants in this component. All thresholds are in the LLM prompt text.

## Tests

None. No unit tests exist for Risk Manager.

## Known Issues

1. nearest_support/resistance uses OB Top/Bottom only; does not consider FVG zones for trade structuring
2. No ATR-based stop loss calculation; relies on LLM to place SL outside OB zone
3. No position sizing logic
4. No portfolio exposure/correlation checks
5. Entry zone is a string (e.g., "1.1500-1.1510"); parsed by orchestrator for DB registration
6. LLM may not always achieve min R:R 1:1.5; no deterministic check enforces this post-LLM

## Extension Points

1. Add ATR-based SL: pass ATR value from Technical Agent, compute SL deterministically
2. Add position sizing: pass account size and risk %, compute lot size
3. Add portfolio checks: query open positions from DB, check correlation exposure
4. Add post-LLM R:R validation: deterministic check on trade_plan before accepting APPROVED
5. Add dynamic fusion weighting: weight fundamental vs technical scores before passing to LLM

## Dangerous Areas

1. Hard rejection logic: changing the `if current_price is None` check affects when LLM is invoked
2. Memory thresholds in prompt: changing <40% win rate threshold affects risk behavior
3. Mean reversion special case: allowing counter-trend trades can increase risk if misapplied
4. LLM-placed SL/TP: no deterministic validation; LLM may place invalid levels
5. TradePlan.entry_zone is a string: parsing logic in `run_phase3.py` splits on "-"; format changes break parsing
"""

with open(".ai/components/risk.md", "w") as f:
    f.write(content)

# === .ai/components/orchestration.md ===
content = r"""
# Orchestration Component

## Responsibility

CLI entry points and background scripts that tie all agents together. Manages Phase 0 ingestion, per-currency multi-agent loops, cross-asset validation, and trade memory evaluation.

## Source Paths

- `orchestration/run.py`
- `orchestration/run_phase2.py`
- `orchestration/run_phase3.py`
- `orchestration/trade_evaluator.py`

## Entry Points

- `run.py::main()` — Phase 1 unified CLI
- `run_phase2.py::main()` — Phase 2 fundamental aggregation CLI
- `run_phase3.py::main()` — Phase 3 multi-agent orchestrator (main entry point)
- `trade_evaluator.py::evaluate_pending_trades()` — Silent trade memory evaluator

## Classes

No classes defined in orchestration modules. All use functions and imported classes.

## Functions

### run.py
- `main()` → int (exit code)

### run_phase2.py
- `main()` → int (exit code)
- `build_llm(provider, model)` → `BaseChatModel`

### run_phase3.py
- `main()` → int (exit code)
- `run_ingestion_phase()` → None
- `build_llm(provider, model)` → `BaseChatModel`

### trade_evaluator.py
- `evaluate_pending_trades()` → None

## Data Models

### run.py
- Uses `PipelineConfig` dataclass from `agents/fundamental/e2e_pipeline.py`
- Uses `NewsPipelineConfig` from `agents/fundamental/e2e_news_pipeline.py`
- Uses `SpeakerPipelineConfig` from `agents/fundamental/e2e_speaker_pipeline.py`

### run_phase3.py
- Uses `ExecutionPlan` from `agents/supervisor/head_agent.py`
- Uses `CompositeSignal` from `agents/fundamental/phase2_graph.py`
- Uses `TechnicalMetrics`, `TechnicalReport` from `agents/technical/technical_analyzer.py`
- Uses `RiskDecision` from `agents/risk/risk_manager.py`
- Uses `TradeOutcomeDB` from `core/database.py`

## Inputs

### CLI Arguments
- `--currencies`: list of currency codes (default: ["USD"])
- `--llm-provider`: "arvan" | "openrouter" (default: "arvan")
- `--llm-model`: model name string (default: provider default)

### run.py additional
- `--mode`: "news" | "events" | "speakers" | "all" (required)
- Remaining args passed through to sub-pipeline CLIs

### Data Sources
- DB: `economic_events_history`, `raw_news_items`, `TradeOutcomeDB`
- External: yfinance (price), FF HTML scraper, RSS feeds
- LLM: Arvan GLM-5.2 or OpenRouter

## Outputs

### Terminal
- Signals table (currency, direction, score, tradable)
- Detailed per-currency LLM analyses
- Global macro executive summary

### Database
- `event_signals`, `news_signals`, `trading_signals` (Phase 1)
- `composite_signals` (Phase 2)
- `TradeOutcomeDB` (Phase 3 on APPROVED)

### Files
- Audit JSON in `runs/` directory (Phase 1 E2E pipelines)

## Dependencies

- All agent modules (fundamental, technical, risk, supervisor)
- `core/database.py` (including `TradeOutcomeDB`, `get_trade_memory_stats`, `insert_raw_news_item_if_new`, `session_scope`)
- `core/routing.py`
- `ingestion/ff_bridge.py`, `ingestion/rss_feed_loader.py`
- `langgraph`

## Database Interactions

### run_phase3.py
**Writes**:
- `TradeOutcomeDB`: on APPROVED, registers PENDING trade with entry zone, SL, TP, scores, session
- Via Phase 2 graph: `event_signals`, `news_signals`, `composite_signals`

**Reads**:
- `get_trade_memory_stats(currency)`: win/loss stats for Risk Manager
- Via Head Agent: `economic_events_history` (24h forward High Impact)
- Via Phase 2 Event Node: `economic_events_history` (48h backward)
- Via Phase 2 Speaker Node: `trading_signals` (24h backward)

### trade_evaluator.py
**Reads**: `TradeOutcomeDB` where status="PENDING" and created_at >= 7 days ago
**Writes**: Updates status to WIN/LOSS/EXPIRED, sets closed_at, evaluated_price

## External APIs

- FF HTML scraper via `ext_ff_scraper`
- RSS feeds via `FeedLoader`
- yfinance via `trade_evaluator.py` (5m interval for current price)
- Arvan GLM-5.2 / OpenRouter LLM

## LLM Interactions

### run.py
- LLM built via `build_llm()` in sub-pipeline modules
- Passed to `events_main()`, `news_main()`, `speakers_main()`

### run_phase2.py
- LLM built via `build_llm(provider, model)`
- Passed to Phase 2 graph nodes

### run_phase3.py
- LLM built via `build_llm(provider, model)`
- Shared across Head Agent, Fundamental Agent (Phase 2 graph), Technical Agent, Risk Manager, Cross-Asset graph
- Same LLM instance reused for all agents in a single run

### trade_evaluator.py
- No LLM interactions

## Error Handling

### run_phase3.py
- FF scraper failure: caught in `run_ingestion_phase()`, logged, proceeds with existing DB
- Fundamental pipeline failure: caught per-currency, logged, `fund_sig = None`
- Technical pipeline failure: caught per-currency, logged, `tech_metrics, tech_report = None, None`
- Risk Manager failure: caught, logged, defaults to REJECTED
- TradeOutcomeDB registration failure: caught, logged, continues

### trade_evaluator.py
- yfinance failure: caught per-trade, logged, skips trade
- DB commit failure: rollback, logged

## Retry Behavior

### run_phase3.py
- FF scraper: 3 retries with 5s delay in `run_ingestion_phase()`
- LLM retries: handled by `invoke_with_retry()` in each agent

### trade_evaluator.py
- No retry mechanism

## Important Invariants

1. Phase 0 ingestion runs before Head Agent
2. `plan.activate_speakers = False` forced after Head Agent plan
3. `fund_status` explicitly tracks "evaluated" vs "not_evaluated"
4. `final_score` uses fund_score if evaluated, else tech_score, else 0.0
5. `is_tradable = risk_decision.decision == "APPROVED"`
6. If `fund_sig` is None, CompositeSignal created with "tech_driven" status
7. TradeOutcomeDB registration only on APPROVED with TradePlan
8. Entry zone string parsed by splitting on "-"

## Configuration

### LLM Providers
```python
# Arvan
base_url = os.getenv("ARVAN_BASE_URL")
api_key = os.getenv("ARVAN_API_KEY", "not-needed")
model = "GLM-5.2"
temperature = 0.1

# OpenRouter
api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
base_url = "https://openrouter.ai/api/v1"
model = "openai/gpt-4o-mini"
temperature = 0.1
```

### Trade Evaluator
- PENDING window: 7 days backward
- EXPIRED timeout: 48 hours
- Price interval: 5m (yfinance)

## Tests

None. No unit tests exist for orchestration modules.

## Known Issues

1. No APScheduler; all scripts run manually
2. RSS double-load: Phase 0 loads RSS to `raw_news_items` DB, Phase 2 News Node loads RSS fresh from feeds again
3. No `live_watcher.py` for Event-Driven execution
4. No Telegram/Discord notification
5. FF scraper `IncompleteRead` errors handled by retry but root cause (FF rate limiting) not solved
6. `run_phase3.py` duplicates `build_llm()` function (also in `run_phase2.py`)

## Extension Points

1. Add notification: after APPROVED, call notification module before/after TradeOutcomeDB registration
2. Add live watcher: new script that queries TradeOutcomeDB for PENDING trades and monitors price
3. Add APScheduler: wrap `main()` calls in scheduler jobs
4. Add dynamic fusion weighting: between fundamental and technical score fusion, add weighting logic

## Dangerous Areas

1. `fund_status` tracking: if "evaluated" vs "not_evaluated" logic is broken, score semantics break
2. `plan.activate_speakers = False`: removing this force-disable re-enables mock data signals
3. TradeOutcomeDB entry zone parsing: `entry_str.split("-")` assumes format "low-high"; format changes break parsing
4. Phase 0 FF scraper dates: must use current week (Sunday-Saturday); off-by-one in date calc causes FF API errors
5. `build_llm()` duplication: if one copy is updated and the other isn't, provider behavior diverges between Phase 2 and Phase 3
6. Shared LLM instance: same LLM used for all agents; changing temperature/model affects all agents simultaneously
"""

with open(".ai/components/orchestration.md", "w") as f:
    f.write(content)

# === .ai/sessions/README.md ===
content = r"""
# Session Logs

This directory contains logs of AI coding sessions for project continuity.

## Purpose

Enable a new AI coding session (potentially using a different model) to understand what was done in previous sessions, what decisions were made, what problems remain, and what the next steps are — without reading the entire conversation history.

## Format

Each session log should be a Markdown file named `YYYY-MM-DD_short-description.md`.

### Required Structure

```markdown
# Session: YYYY-MM-DD — Short Description

## Objective
What was the goal of this session? Be specific.

## Starting State
What was the state of the project at the start of this session?
Reference `.ai/project_state.md` and `.ai/current_task.md` if applicable.

## Files Inspected
List actual file paths inspected during this session.
- `path/to/file.py` — what was looked at and why

## Files Changed
List actual file paths modified during this session.
- `path/to/file.py` — description of change

## Implementation Details
Key implementation decisions, algorithms, data flows, or logic changes.
Be specific about class/function names and how they interact.

## Decisions Made
- Decision 1 — reason
- Decision 2 — reason
If a decision affects architecture, also record it in `.ai/decisions.md`.

## Tests Run
List tests executed and their results.
- `python -m pytest tests/test_event_scorer.py -v` — passed/failed

## Failures
Any errors, bugs, or unexpected behavior encountered.
- Error message or description — how it was resolved (or not)

## Unresolved Issues
Issues that remain open after this session.
- Issue description — context and what needs to happen next

## Next Steps
Concrete next steps for the following session.
- Step 1
- Step 2
```

## Guidelines

1. Be concise but specific. Include actual file paths, class names, function names.
2. Note any architectural decisions and their reasoning. Cross-reference `.ai/decisions.md`.
3. If a problem was not resolved, explain what was tried and what remains.
4. Do not duplicate content already in `.ai/project_state.md` or `.ai/decisions.md`; reference them instead.
5. Update `.ai/current_task.md` at the end of the session to reflect the new state.
6. If the session involved significant architectural changes, update `.ai/architecture.md` and relevant `.ai/components/*.md` files.
7. Do not invent information. If something is unknown, state it explicitly.
8. The session log should be writable by any AI model, not just the one that did the work.
"""

with open(".ai/sessions/README.md", "w") as f:
    f.write(content)

