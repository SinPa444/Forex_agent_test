# CHANGELOG — P0 (Technical Engine)

> تاریخ: 2026-08-28
> فاز: P0 — Technical Engine ارتقا (پس از تأیید Research + Change Plan)
> هدف: Output Contract کامل TechnicalSignal + Market Regime + Confluence/Confidence + Data Quality + CHoCH + ATR

## خلاصه

P0 بدون شکستن API فعلی پیاده‌سازی شد. `calculate_technical_metrics` و
`scan_timeframes` و `build_strategy_sheet` حفظ شده‌اند؛ فقط وزن‌ها به
`core/technical_config.py` منتقل شدند (مقادیر یکسان). تنها رفتار متغیر طبق
Planned Change «استفادهٔ واقعی از CHoCH» است.

## فایل‌های جدید

| فایل | مسئولیت |
|---|---|
| `core/technical_config.py` | پیکربندی مرکزی وزن‌ها/آستانه‌ها/رژیم‌ها |
| `agents/technical/indicators.py` | ATR، ATR%, EMA, SMA, RSI, MACD (با fallback دستی ATR) |
| `agents/technical/price_action.py` | Swing High/Low + HH/HL/LH/LL |
| `agents/technical/market_structure.py` | استخراج صریح BOS + CHoCH + `structure_score_for_component` |
| `agents/technical/smc.py` | SMC Context (OB/FVG/Liquidity/OTE/PDH-PDL) |
| `agents/technical/market_regime.py` | Market Regime Enum + Detection |
| `agents/technical/data_quality.py` | Staleness/NaN/Volume Quality Score + FUTURE guard |
| `agents/technical/confluence.py` | Weighted Confluence + Evidence + Confidence |
| `agents/technical/technical_signal.py` | TechnicalSignal Output Contract |
| `agents/technical/prompts.py` | LLM Interpretation Prompt |
| `tests/test_technical_signal.py` | تست Output Contract + gate |
| `tests/test_market_regime.py` | تست Regime Detection |
| `tests/test_data_quality.py` | تست Data Quality |
| `tests/test_confluence.py` | تست Confluence |
| `tests/test_market_structure.py` | تست BOS/CHoCH score |
| `tests/smoke_test_p0.py` | Smoke test `analyze_structured` بدون شبکه/LLM واقعی |
| `research/technical_analysis_research_v1.md` | گزارش فاز Research |

## فایل‌های تغییر یافته

### `agents/technical/technical_analyzer.py`

- وزن‌های ۱۰ فاکتور به `core.technical_config.TECHNICAL_WEIGHTS` منتقل شد (مقادیر بدون تغییر).
- **CHoCH واقعاً استفاده شد**: در بلاک Market Structure، هم `BOS` و هم `CHoCH`
  از `smc.bos_choch` استخراج و با `structure_score_for_component()` در فاکتور
  `structure` (وزن ۰.۲۰) اعمال می‌شود.
- `recent_choch` به `TechnicalMetrics` و پرامپت LLM اضافه شد.
- متد جدید `TechnicalAgent.analyze_structured(...)` اضافه شد:
  - خروجی `(TechnicalMetrics, TechnicalSignal, Optional[TechnicalInterpretation])`
  - تک‌کال LLM برای narrative روی `TechnicalSignal` (بدون تغییر score)
  - در شکست LLM، `TechnicalInterpretation=None` و سیگنال deterministic باقی می‌ماند.
- helper جدید `_last_frame_for_signal(...)` برای ATR/Swing/Structure در `TechnicalSignal`.

### `orchestration/run_phase3.py`

- در مسیر Technical با MTF Matrix، از `analyze_structured` استفاده می‌شود.
- برای سازگاری با Risk Manager قدیمی، `TechnicalReport` از `TechnicalSignal` ساخته می‌شود.
- در `reasoning_parts` اطلاعات `TechnicalSignal` (regime/structure/mtf_alignment/risks) اضافه شد.
- متغیر `tech_signal` به‌صورت صریح قبل از چرخه هر ارز init می‌شود.

## Output Contract جدید (TechnicalSignal)

```json
{
  "symbol": "EURUSD=X",
  "direction": "BULLISH",
  "direction_value": 1,
  "score": 0.42,
  "confidence": 0.63,
  "market_regime": "RANGING",
  "time_horizon": "INTRADAY",
  "trend": "BULLISH",
  "market_structure": "BOS_BULLISH",
  "momentum": "WEAK_BULLISH",
  "volatility": "NORMAL",
  "smc_context": "PRICE_AT_DEMAND",
  "mtf_alignment": "MIXED_OR_NEUTRAL",
  "confluence": [],
  "supporting_evidence": [],
  "contradicting_evidence": [],
  "risks": [],
  "reasoning_summary": "score=+0.42 | confidence=0.63 | regime=TRENDING | ..."
}
```

## Market Regime

`TRENDING`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`, `BREAKOUT`, `REVERSAL`, `UNCLEAR`

## Signal States

`STRONG_BULLISH`, `BULLISH`, `WEAK_BULLISH`, `NEUTRAL`, `WEAK_BEARISH`, `BEARISH`, `STRONG_BEARISH`

## نکته‌ها

- `TechnicalSignal.score` همیشه gate اصلی را حفظ می‌کند: اگر
  `metrics.technical_score` برابر `None` باشد (structure/smc ارزیابی نشده)،
  score=0 و direction=NEUTRAL می‌ماند؛ حتی اگر مؤلفه‌های جزئی موجود باشند.
- `DataQualityResult.staleness_status` حالا حالت `FUTURE` هم دارد تا داده‌ای که
  از `reference_date` جدیدتر است به‌عنوان potential lookahead جریمه شود.
- `ATR` در `indicators.py` یک fallback دستی (Wilder) دارد تا تست‌ها بدون
  `pandas_ta_classic` هم اجرا شوند.
- LLM هنوز فقط Interpretation می‌دهد؛ هیچ score/confidence/regime توسط LLM محاسبه نمی‌شود.
- Weight Calibration، Walk-forward Sensitivity، Staleness زنده در Data Layer و Backtest MTF کامل **P1** هستند.
