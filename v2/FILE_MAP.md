# Phase 7 — Multi-Timeframe Strategy Engine

تکمیل فاز ۷: اسکن کامل تایم‌فریم‌ها + Strategy Sheet (فعال/در انتظار/باطل) + تریگر zone-hit در واچر + ارزیاب داخل تیک.

## ترتیب نصب
Phase 5 → Phase 6 → Phase 7. همه فایل‌ها روی نسخه‌های قبلی کپی می‌شوند (overwrite).
**یک بار** بعد از نصب، `init_db` باید اجرا شود تا جدول/ستون‌های جدید ساخته شوند (کافی است یک بار `run_phase3` یا هر اسکریپتی که `init_db()` را صدا می‌زند اجرا شود — مایگریشن افزایشی است و دیتای قبلی پاک نمی‌شود).

---

## فایل‌ها

### 1. `core/database.py`
- **جدول جدید `strategy_plans`** (StrategyPlanDB):
  - `id, created_at, currency(idx), timeframe, horizon_role(HTF/MTF/LTF), direction`
  - `status(idx)`: PENDING / TRIGGERED / FILLED / INVALIDATED / EXPIRED
  - `entry_low, entry_high, stop_loss, take_profit, rr_ratio, invalidation_price`
  - `reasoning(Text), counter_bias(Integer default 0), expires_at(idx), triggered_at`
- **مایگریشن افزایشی `trade_outcomes`** — `migrate_trade_outcomes_table()` با `_add_missing_columns` (PRAGMA table_info؛ فقط ستون‌های ناقص را ADD می‌کند، دیتا دست نمی‌خورد):
  - `timeframe (String)`, `plan_id (Integer)`, `decision_reasoning (Text)`, `entry_filled_at (DateTime)`
  - در `init_db()` بعد از `migrate_news_signals_table()` ثبت شده؛ اجرای مجدد امن است (idempotent).
- **`get_recent_losing_trades(currency, limit=3)`** — آخرین تریدهای LOSS به ترتیب `closed_at` نزولی؛ خروجی dict شامل `id, created_at, timeframe, direction, reasoning`. اگر `decision_reasoning` نال باشد، خلاصه‌ای بازسازی می‌کند (`dir=... entry=[...] sl=... tp=... fusion=...`).

### 2. `agents/technical/technical_analyzer.py`
- **رفع باگ نهفته H4/H2**: yfinance اینتروال «4h»/«2h» ندارد — قبلاً دانلود H4 همیشه خالی بود. حالا `_RESAMPLE_FROM_1H = {"2h": "2h", "4h": "4h"}`: دانلود 1h با `period="3mo"`، سپس `resample` با تجمیع OHLC (`open=first, high=max, low=min, close=last, volume=sum`).
- **`TIMEFRAME_MAP` گسترش‌یافته**: `M15:15m, M30:30m, H1:60m, H2:2h, H4:4h, D1:1d, W1:1wk`.
- `HTF_MAP` به‌روز شد (M30→60m, H2→4h, W1→1wk)؛ پریود H1 از 1mo به 3mo؛ M30 با period=1mo.

### 3. `agents/technical/mtf_scanner.py` (جدید — بدون LLM)
- `DEFAULT_TIMEFRAMES = ["W1","D1","H4","H2","H1","M30","M15"]`
- `TIMEFRAME_ROLES`: W1/D1 = HTF، H4/H2 = MTF، H1/M30/M15 = LTF
- `DIRECTION_DEADBAND = 0.15`، `HTF_WEIGHTS = {D1: 0.6, W1: 0.4}`
- `TimeframeScore` (dataclass): timeframe, role, valid, direction, score, confidence, price, nearest_support/resistance, adx, chop, trend_status, error
- `MTFMatrix`: `scores` به‌علاوه `htf_bias` و `htf_bias_label`؛ متدها: `valid_timeframes()`، `strongest(roles=("MTF","LTF"))` — بیشترین |score|×confidence
- `_compute_htf_bias`: توافق D1/W1 → همان جهت؛ تضاد → 0 («Mixed»)؛ فقط یکی معتبر → همان
- `scan_timeframes(ticker, timeframes=None)` — ورودی اصلی؛ صفر LLM

### 4. `agents/risk/risk_manager.py` (Strategy Engine — ضمیمه‌شده به نسخه Phase 5)
- **`StrategyPlan`** (pydantic): timeframe, horizon_role, horizon_label, status ∈ {ACTIVE, PENDING, INVALID}، direction، entry_low/high، stop_loss، take_profit، rr_ratio، invalidation_price، counter_bias، ttl_hours، reasoning
- **`StrategySheet`**: currency, generated_at, htf_bias(+label), plans؛ متدهای `active_plans()` / `pending_plans()`
- **`HORIZON_CONFIG`**:
  - SWING ← H4/H2، ttl=120h، بافر SL=0.3%
  - INTRADAY ← H1، ttl=24h، بافر SL=0.15%
  - SCALP ← M15/M30، ttl=4h، بافر SL=0.08%
- **قانون ضدبایاس**: `|score| ≥ 0.4` **و** `R:R ≥ 2` → مجاز با تگ `counter_bias` (نیم‌سایز در اجرا)؛ وگرنه INVALID
- **سفت‌کردن مموری**: اگر `WR < 40%` با `≥5` ترید → `+0.5` به min_rr؛ اگر `≥3` باخت اخیر → `+0.5` دیگر
- `_structure_horizon_plan(...)`: اگر R:R لحظه‌ای کافی است → ACTIVE at market؛ وگرنه PENDING روی ناحیه (long: entry=[support×0.9995, support×1.0005], SL=support×(1−buf)، R:R از بدترین فیل=entry_high؛ short آینه‌ای)
- `build_strategy_sheet(...)` و `render_strategy_sheet_text(sheet)` — خروجی متنی توافق‌شده

### 5. `orchestration/run_phase3.py`
- بخش تکنیکال: به‌جای تحلیل تک‌تایم‌فریم، `scan_timeframes` اجرا می‌شود؛ **LLM نریشن فقط برای تایم‌فریم `mtf_matrix.strongest()`** (بقیه تایم‌فریم‌ها بدون LLM امتیاز می‌گیرند)
- بخش ریسک: اگر `--risk-llm` یا ماتریس None → مسیر قدیمی (legacy evaluate)؛ وگرنه:
  1. `build_strategy_sheet` → چاپ شیت رندرشده
  2. پلن‌های غیر-INVALID در `strategy_plans` ذخیره می‌شوند (`flush` برای id؛ `expires_at = now + ttl`)
  3. `risk_decision` مشتق می‌شود: بهترین ACTIVE → APPROVED + TradePlan؛ فقط PENDING → WAIT («N conditional plan(s) armed»)؛ هیچ‌کدام → REJECTED
- ثبت ترید حالا `timeframe / plan_id / decision_reasoning` را هم می‌نویسد (برای مموری و ارزیاب)

### 6. `orchestration/live_watcher.py` (نسخه Phase 6 + افزودنی‌ها)
- **`check_zone_hit(currency, ticker, now)`**: ردیف‌های PENDING همان ارز را می‌خواند، قیمت را از `_fetch_h1` می‌گیرد:
  - `now > expires_at` → EXPIRED
  - long: `price < invalidation_price` → INVALIDATED (short آینه‌ای)
  - `entry_low ≤ price ≤ entry_high` → TRIGGERED + `Trigger("zone_hit", severity=0.9, bypass_cooldown=True)` → پایپ‌لاین برای همان ارز فایر می‌شود
  - تغییرات وضعیت commit می‌شوند؛ تیک بعد دوباره فایر نمی‌کند
- **`run_trade_evaluator()`**: `orchestration.trade_evaluator.evaluate_pending_trades` را با گارد ImportError/Exception صدا می‌زند
- در `run_tick`: ارزیاب در گام 0.5، zone-hit بعد از `check_price`

---

## تست‌ها (سندباکس — همه پاس)
- DB: 4/4 — مایگریشن روی اسکیمای قدیمی شبیه‌سازی‌شده، ساخت strategy_plans، init_db ایدمپوتنت، get_recent_losing_trades + fallback
- TA: 8/8 — ریسمپل 4h (80→20 کندل)، 2h (80→40)، صحت تجمیع OHLC، TIMEFRAME_MAP، اسکن ۷ تایم‌فریم، deadband، strongest()=H4، تضاد D1/W1 → Mixed
- Risk: 7/7 — SWING PENDING long، INTRADAY ACTIVE نزدیک حمایت، ضدبایاس ضعیف INVALID، سفت‌شدن مموری، دلایل باخت در شیت، رندر متن
- Zone-hit: 3/3 — چرخه 1 TRIGGERED + 1 INVALIDATED + 1 EXPIRED + 1 PENDING؛ عدم فایر مجدد در تیک بعد
- Integration: 3/3 — ایمپورت/سیم‌بندی run_phase3، ارزیاب بدون خطا، مسیر legacy تک‌تایم‌فریم (APPROVED)

## نمونه خروجی شیت استراتژی (از تست)
```
════════ STRATEGY SHEET — EURUSD ════════
HTF bias: BULLISH (D1+W1 agree)

[SWING — H4]  PENDING  LONG
  entry zone: 1.0995 – 1.1006   SL: 1.0967   TP: 1.1500
  R:R: 1.60   invalid below: 1.0950   TTL: 120h
  note: armed at demand zone

[INTRADAY — H1]  ACTIVE  LONG
  entry zone: market   SL: 1.0990   TP: 1.1060
  R:R: 2.10   TTL: 24h

[SCALP — M15]  INVALID
  note: counter-bias score -0.30 below 0.40 threshold
```
