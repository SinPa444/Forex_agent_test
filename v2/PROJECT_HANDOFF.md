---
last_updated: 2026-08-19
owner: Sina
scope: global
format: AI-to-AI project handoff (CLAUDE.md + HANDOVER.md hybrid)
---

# Project Handoff: Forex AI Fundamental Analysis Platform

Read this file fully before doing anything. It is the current source of truth
unless the user explicitly corrects it. Do not re-litigate decisions listed in
"Decisions made". Continue from "Next steps", do not restart from scratch.

## Goal

A LangGraph multi-agent Forex analysis platform that:
- ingests fundamental data (ForexFactory calendar, RSS news, speakers),
- scans 7 timeframes technically with zero LLM,
- produces conditional strategy plans (entry zones, SL/TP, R:R) per horizon,
- auto-executes them on a paper account with realistic costs,
- and learns from outcomes via a trade-memory feedback loop.

Design principle (golden rule): **the LLM only interprets and narrates. All
scores, triggers, geometry, and money math are deterministic Python.**

## Environment

- Repo: `~/Desktop/Sina/Crypto/test/v3` (v2 exists; v3 is canonical — confirm with user)
- Python 3.11 venv: `~/Desktop/Sina/Crypto/crypto` (`source .../bin/activate`)
- DB: SQLite at `core/crypto_agent.db` (path derived from `core/database.py` location)
- LLM: GLM-5.2 via Arvan cloud gateway (endpoint in logs; key via env)
- OS: Ubuntu (user machine). MetaTrader5 pip package is Windows-only — do not propose it without Wine/VPS.
- Entry points:
  - Pipeline: `python -m orchestration.run_phase3 --currencies USD EUR OIL XAU`
  - Decision watcher: `python -m orchestration.live_watcher --currencies USD EUR OIL XAU --loop --interval 900`
  - Execution loop: `python -m execution.executor_loop --loop --interval 60`
  - Account status: `python -c "from execution.paper_broker import account_summary; print(account_summary())"`

## Architecture

```
core/           database.py (SQLAlchemy models + additive migrations), llm_utils, models
agents/
  fundamental/  phase2_graph (LangGraph: events/news/speakers nodes, digest cache), nlp_*, data_fetcher
  technical/    technical_analyzer (LLM narrative for ONE strongest TF), mtf_scanner (7 TFs, zero LLM)
  risk/         risk_manager (legacy evaluate + Phase-7 Strategy Engine)
  supervisor/   head_agent (rule-based routing plan by default)
ingestion/      ff_bridge (week-by-week FF scrape + retry + incremental save), rss_feed_loader
orchestration/  run_phase3 (main pipeline), live_watcher (15-min decision loop), trade_evaluator
execution/      paper_broker (fill/close/sizing engine), executor_loop (60s position management)
backtest/       (legacy)
```

Three-layer runtime separation:
- **live_watcher (15 min)**: decision — triggers fire the pipeline. Zero LLM in detection.
- **run_phase3**: analysis — fundamental + MTF + strategy sheet. ~5-9 LLM calls per fire (digest cache ON).
- **executor_loop (60 s)**: execution — fills PENDING plans, checks SL/TP/timeout. Zero LLM.

## Development history (phases)

Base project was handed over as an existing LangGraph pipeline. All phases below were
delivered as `Phase_N.zip` packages, each with a `FILE_MAP.md`, sandbox-tested before delivery.

- **Phase 5 — Token optimization.** 17 LLM calls → ~9 first run / ~5 cached. Rule-based
  head-agent and risk-manager defaults; digest hash cache (sha256 of sorted top-10 news links,
  6h TTL, reuse analysis if unchanged); compact format instructions; `--reports` / `--head-llm` /
  `--risk-llm` / `--no-digest-cache` flags.
- **ff_bridge fix.** Whole-range fetch + save-at-end lost 38 weeks of work on one timeout.
  Now: Sunday-aligned weekly windows, 3 attempts with backoff per week, incremental DB save
  per week, failed_weeks reported. Idempotent reruns.
- **Phase 6 — live_watcher.** Event-driven activation replacing 15-min blind polling.
  Triggers: calendar (High in 60 min → sev 1.0; fresh release; Medium → 0.5), news (hash change +
  importance gate with accumulation of sub-threshold items), price (1h move vs ATR; breakout),
  regime change. Severity gates (0.5 single, 0.8 combined), 60-min cooldown (bypassed only by
  fresh releases and zone-hits), 3h max staleness forced re-analysis, USD fan-out map.
  Launches `run_phase3 --skip-ingestion` per triggered currency.
- **Phase 7 — MTF strategy engine.** 7 timeframes (W1/D1/H4/H2/H1/M30/M15) with HTF/MTF/LTF
  roles; per-currency strategy sheet with SWING/INTRADAY/SCALP horizons, statuses
  ACTIVE/PENDING/INVALID, TTLs (120h/24h/4h), SL buffers (0.3%/0.15%/0.08%). Counter-bias rule:
  |score|≥0.4 AND R:R≥2 required, tagged half-size. Memory tightening: +0.5 min-RR if WR<40%
  with ≥5 trades, +0.5 more if ≥3 recent losses; last-3 loss reasons injected into the sheet.
  New `strategy_plans` table; `trade_outcomes` gained timeframe/plan_id/decision_reasoning/
  entry_filled_at via additive migration. Watcher gained zone-hit trigger + in-tick evaluator.
  - **Hotfix 1**: horizon TF selection was "first valid" → now strongest by |score|×confidence.
  - **Hotfix 2 (fundamental veto)**: `build_strategy_sheet(fund_dir, fund_score)` — a plan
    opposing fundamental direction gets the counter-bias regime (or INVALID if weak).
- **Phase 8 — Paper execution engine.** `paper_account` (balance/equity/margin) +
  `paper_positions` tables. Constant-risk sizing: `size = balance×risk% ÷ |entry−SL|`,
  1% base / 0.5% counter-bias. Realistic fills: per-symbol SPREAD_MAP + uniform slippage
  (0–0.5 pip), always adverse. Conservative same-candle rule: if a 1m candle touches both
  SL and TP → SL first. Position TTL = plan's expires_at. Closed positions write WIN/LOSS to
  `trade_outcomes` with plan link + reasoning → memory loop closes.

## Decisions made (do not re-litigate)

- LLM narrates; Python computes. No LLM in watcher/executor detection paths.
- Event-driven activation with severity gates, not blind periodic polling.
- Two speeds by design: 60 s execution loop vs 15 min decision loop. Do not merge them.
- Plan fills do NOT re-fire the pipeline — the trade decision was made at plan creation.
- Migrations are additive only (`_add_missing_columns`, PRAGMA table_info); never drop data.
- Fundamental veto exists and stays; weak counter-directional plans must die.
- No GitHub push (user declined PAT sharing). API keys via env vars only.
- Deliverables: `Phase_N.zip` + `FILE_MAP.md`, Persian final message. If the user can't
  download, paste full file texts in chat on request.

## Known gotchas (hard-won)

- yfinance has NO "4h"/"2h" intervals — H4/H2 are resampled from 1h in `_fetch_price_history`.
- yfinance 1m data: last 7 days only; rate limits exist — executor uses ONE batched
  `yf.download` per tick for all tickers + retry/backoff.
- `SessionLocal` has `autoflush=False` — flush (or exclude mutated ids) before dependent
  queries in the same session, or you get double-counted equity (this bug was found and fixed).
- argparse `nargs="+"` splits on whitespace, not commas: `--currencies USD EUR`, never `USD, EUR`.
- Signal direction is CURRENCY direction, not chart direction: JPY/CHF/CAD tickers are
  USDJPY/USDCHF/USDCAD (inverse). "JPY +1" = USDJPY chart falls.
- XAU uses GC=F (futures), not spot — structural price offset is normal.
- Iran-sandbox: /tmp wiped between turns; reinstall pip deps per session; chmod a+w on
  shell-created dirs before writing from Python.

## Verified live behavior (2026-08-18/19 logs)

- Watcher fired on accumulated news severity 0.70 → USD fan-out analyzed 4 currencies.
- Directional calls were 8/9 correct that day; the single approved trade (NZD short,
  composite short vs bullish technicals 0.34) was the wrong one — exactly the failure mode
  the fundamental veto now kills. Selection layer, not prediction layer, is the weak link.

## Current state

- All phases 5–8 delivered, installed, and running on the user's machine (v3).
- Watcher + executor run as two separate loops on one shared DB.
- Trade memory is nearly empty — the feedback loop needs real paper-trade volume.

## What to avoid

- Do NOT loosen thresholds (min R:R, counter-bias 0.4, severity gates) to force more trades.
  Only with ≥50 paper trades of statistics.
- Do NOT add Low-impact calendar events as fire triggers (approved backlog option: tiny
  accumulative severity weight only — context, not trigger).
- Do NOT let the LLM set numeric scores.
- Do NOT run watcher and executor against different DBs (v2 vs v3 mix-up already happened).

## Backlog / open questions

- Phase 9 (optional): real broker demo bridge (OANDA v20 practice REST works on Linux;
  MT5 needs Windows/Wine). paper_broker was designed so the broker is a swappable implementation.
- Incremental news memory (per-item analysis cache) — deferred; hash cache covers the big cost.
- Add GBP/JPY/AUD/CHF/CAD/NZD to watcher --currencies when user wants wider coverage.
- Answer with data: are "fundamental-opposes-technical" trades systematically losers?

## Next steps

1. Let the system run 1–2 weeks; accumulate ≥50 paper trades.
2. Review `account_summary()` weekly: WR, R:R distribution, plan→fill conversion.
3. Only then tune thresholds with evidence.

## How to work with the next AI

- Respond in Persian (full-width punctuation). Blunt, no filler, no soft closers (user's
  "Absolute Mode" instruction). Questions allowed only when genuinely ambiguous.
- Workflow per phase: analysis report FIRST → user approval → code → sandbox tests →
  `Phase_N.zip` + `FILE_MAP.md`.
- Start with a short summary of your understanding, then continue from "Next steps".
