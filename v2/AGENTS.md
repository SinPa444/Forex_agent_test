# AGENTS.md — Forex AI Fundamental Analysis Platform

> سند دانش دائمی پروژه برای ایجنت‌های هوش مصنوعی (Kimi K3 و سایرین).
> این فایل را در ریشه ریپو نگه دارید؛ هر سشن جدید ابتدا این فایل را بخواند.
> آخرین به‌روزرسانی: 2026-08-16

---

## ۱. پروژه چیست؟

پلتفرم چندعاملی (multi-agent) تحلیل بنیادی فارکس مبتنی بر LLM که سیگنال معامله تولید می‌کند.
سه منبع داده بنیادی (رویدادهای اقتصادی، اخبار RSS، سخنرانی‌ها) را با تحلیل تکنیکال SMC و مدیریت ریسک ترکیب می‌کند.

**اصل طراحی طلایی (هرگز نقض نشود):**
> LLM فقط «تفسیر» می‌کند (sentiment، direction، alignments). همه اعداد نهایی — final_score، confidence، half-life — به‌صورت قطعی (deterministic) در پایتون محاسبه می‌شوند. خروجی باید قابل بازتولید و حسابرسی باشد.

---

## ۲. ساختار واقعی پکیج (از روی importها)

```
core/
  database.py          # SQLAlchemy + SQLite، ۹ جدول، مهاجرت افزایشی
  models.py            # همه اسکیمای pydantic (MarketContext، NewsItem، SpeakerSignal، ...)
  routing.py           # نگاشت ارز→تیکر yfinance + تراز DIRECT/INVERSE/INDEX
  llm_utils.py         # invoke_with_retry (بک‌آف نمایی، max 2) + _strip_json_markdown

agents/
  fundamental/
    e2e_pipeline.py          # پایپ‌لاین E2E رویدادها (فاز ۱)
    e2e_news_pipeline.py     # پایپ‌لاین E2E اخبار + Macro Digest (فاز ۱)
    e2e_speaker_pipeline.py  # پایپ‌لاین E2E سخنرانان (فاز ۱)
    phase2_graph.py          # گراف LangGraph فاز ۲ + گراف cross-asset
    data_fetcher.py          # ساخت MarketContext + انحراف معیار تاریخی سورپرایز
    nlp_event.py             # تحلیل LLM رویداد + امتیازدهی قطعی
    nlp_news.py              # تحلیل LLM خبر + Macro Digest
    nlp_x.py                 # تحلیل LLM سخنران
  technical/
    technical_analyzer.py    # امتیازدهنده قطعی SMC با ۷ مؤلفه وزنی
  risk/
    risk_manager.py          # تصمیم نهایی APPROVED/REJECTED/WAIT + TradePlan
  supervisor/
    head_agent.py            # Supervisor؛ صدور ExecutionPlan

ingestion/
  ff_bridge.py                  # پل به scraper HTML خارجی (ext_ff_scraper/src)
  forex_factory_crawler.py      # کراولر API هفتگی JSON فارکس فکتوری
  rss_feed_config.py            # رجیستری فیدها در ۴ رده (tier)
  rss_feed_loader.py            # دانلود/نرمالایز/enrich/dedup فیدها
  historical_events_importer.py # ایمپورت CSV چندساله FF به DB
  seed_speakers.py              # سید ~۴۰ سخنران با وزن

orchestration/
  run_phase3.py          # ارکستراتور فاز ۳ (سیستم چندعاملی کامل)
  trade_evaluator.py     # ارزیاب‌گر بی‌صدا WIN/LOSS/EXPIRED برای cron

run.py                   # CLI یکپارچه فاز ۱ (--mode news|events|speakers|all)
run_phase2.py            # ارکستراتور فاز ۲
```

وابستگی خارجی: ریپوی `ext_ff_scraper/src` (کلاس `ForexFactoryHtmlProvider`) برای scrape با actualها.

---

## ۳. نقاط ورود اجرا

| دستور | کار |
|---|---|
| `python run.py --mode events` | پایپ‌لاین رویدادها (فاز ۱) |
| `python run.py --mode news` | پایپ‌لاین اخبار (فاز ۱) |
| `python run.py --mode speakers` | پایپ‌لاین سخنرانان (فاز ۱) |
| `python run_phase2.py` | ارکستراسیون LangGraph هر ارز + cross-asset |
| `python run_phase3.py` | سیستم چندعاملی کامل: Head Agent → Fundamental/Technical → Risk → Trade Memory |
| `python -m orchestration.trade_evaluator` | ارزیابی معاملات PENDING (برای cron) |
| `python ingestion/forex_factory_crawler.py save` | ذخیره رویدادهای هفته در DB |
| `python ingestion/historical_events_importer.py <csv>` | ایمپورت تاریخچه چندساله |
| `python ingestion/seed_speakers.py seed` | سید سخنرانان |

هر سه پایپ‌لاین فاز ۱ الگوی یکسان دارند: `PipelineConfig` → `PipelineRunResult` (با skip/failure tracking مرحله‌ای) → آرتیفکت حسابرسی JSON در `runs/`.

---

## ۴. قراردادهای معماری (Conventions)

1. **جهت currency-native در DB:** جهت سیگنال همیشه بنیادیِ خود ارز ذخیره می‌شود. ترجمه به جهت ابزار معاملاتی فقط برای نمایش، با `translate_instrument_direction(direction, alignment)` (INVERSE → معکوس علامت). از double-flip جدی پرهیز کن.
2. **تحمیل مقادیر محاسبه‌شده به LLM:** مدل باید `calculated_surprise`/`calculated_volatility` را دقیقاً echo کند؛ `_enforce_market_context_values` انحراف را تصحیح می‌کند.
3. **ولیدیتورهای سخت pydantic:** جهت باید با علامت sentiment مطابقت کند (تلرانس 0.05).
4. **درج idempotent:** همه جدول‌های خام با `dedup_hash` (sha256) و توابع `insert_*_if_new`؛ رویدادها با کلید (title+currency+date).
5. **پاک‌سازی داده فقط صریح:** `cleanup_old_data`/`cleanup_all_tables` هیچ‌وقت خودکار صدا نمی‌شوند.
6. **مهاجرت DB افزایشی:** فقط `ALTER TABLE ADD COLUMN` و `CREATE INDEX IF NOT EXISTS`؛ destructive migration ممنوع.
7. **پراگماهای SQLite:** WAL، synchronous=NORMAL، busy_timeout=5000، foreign_keys=ON، temp_store=MEMORY — هنگام connect.
8. **کامنت‌ها و لاگ‌ها:** فارسی (با اصطلاحات فنی انگلیسی). همین سبک را حفظ کن.

---

## ۵. لایه Routing (core/routing.py)

| ارز/دارایی | تیکر yfinance | تراز |
|---|---|---|
| USD | DX-Y.NYB | INDEX |
| EUR, GBP, AUD, NZD | XXXUSD=X | DIRECT |
| JPY, CHF, CAD, CNY, SEK, NOK | USDXXX=X | INVERSE |
| XAU | GC=F | INDEX |
| OIL | CL=F | INDEX |

`resolve_asset_route(currency)` → `AssetRoute` منجمد (frozen) با `ticker`، `alignment`، `market_context_currency`، `event_currency`.

---

## ۶. MarketContext و انحراف معیار تاریخی (data_fetcher.py)

**MarketContext** ۹ فیلد کلیدی: actual/forecast/previous، historical_std، HV، ATR 3d/14d، IV (^VIX/^GVZ/^OVX)، اسپرد بازده (^TNX−^IRX).

- `calculated_surprise = min(|z|/3, 1)` (z-score نسبت به std تاریخی)
- `calculated_volatility` = میانگین نسبت‌های clamp‌شده (سقف 3.0) IV/HV و ATR

**تطبیق کانونیکال رویداد:** `_canonicalize_event_title` → کلیدهایی مثل `US_CPI_MM`، `DE_CPI_PRELIM_MM` (با اوررایدهای کشوری برای EUR: آلمان/فرانسه/ایتالیا/اسپانیا).

**استراتژی fallback شش‌مرحله‌ای برای std تاریخی:**
پنجره ۵ سال → ۱۰/۱۵ سال → کل تاریخ → ilike شل → `CATEGORY_DEFAULT_STD` → 0.15 مطلق.
فیلتر پرتاب IQR (k=1.5)؛ حداقل ۱۰ مشاهده (حداقل مطلق ۵). خروجی: `HistoricalStdResult` با متادیتای منبع.

---

## ۷. فرمول‌های امتیازدهی قطعی

### رویداد (nlp_event.py)
```
final_score = sentiment × impact_weight × (1+surprise) × data_quality(0.6+0.4×completeness)
              × quant_alignment × std_reliability(1.0 یا 0.75)
confidence  = وزنی 0.30/0.25/0.20/0.15/0.10 با جریمه‌ها
half_life   = base_category × impact_multiplier ÷ max(vol, 0.25)
```
وزن impact: High=1.0 / Medium=0.7 / Low=0.4. نیمه‌عمر پایه: Rate Decision=240min، NFP=180، CPI=150.
قابل‌معامله: score≥0.40، conf≥0.55، completeness≥0.40، impact≠Low.

### خبر (nlp_news.py)
```
final_score = sentiment × category_weight × (1+surprise) × data_quality(0.5+0.5×completeness)
confidence  = completeness 0.30 + surprise 0.25 + reliability 0.20 + hb_align 0.15 + qa 0.10
```
قابلیت‌اطمینان منابع: FF=0.85، ForexLive=0.82، DailyFX=0.80، Unknown=0.65.
**Macro Digest:** تحلیل تجمیعی N خبر یک ارز در یک سیگنال (max reliability، `is_aggregated=True`، `source_links_json`).

### سخنران (nlp_x.py)
```
final_score = sentiment × speaker_weight × stmt_type_weight × (1+surprise)
              × data_quality(0.6-1.0) × alignment_factor(0.5+0.25sma+0.25qa)
```
وزن نوع بیانیه: testimony/official_transcript=1.15، tweet=0.90.

### تکنیکال (technical_analyzer.py) — ۷ مؤلفه وزنی
structure=0.25، smc_location=0.20، trend=0.18، mtf_confluence=0.12 (فعلاً placeholder خنثی)، momentum=0.10، volatility=0.10، price_action=0.05.
ابزارها: talipp (RSI/MACD/EMA/ADX) + smartmoneyconcepts (BOS/CHoCH/FVG/OB). امتیاز نهایی فقط اگر structure یا smc_location ارزیابی شده باشد. LLM فقط روایت استراتژی می‌نویسد.

### تجمیع فاز ۲ (phase2_graph.py)
میانگین وزنی رویدادها (بر اساس impact)؛ پایه سلسله‌مراتبی: event > news > speaker.
`apply_confluence`: هم‌راستا → score×1.1 و conf+0.10؛ متضاد → score×0.7 و conf−0.20.
آستانه tradable: 0.40/0.60. آخرهفته (جمعه≥21 UTC تا یکشنبه≤21) و تعطیلات (25-26 دسامبر، ۱ ژانویه) tradability را مسدود می‌کند.
**Cross-asset:** USD-CAD / USD-XAU / USD-OIL هم‌جهت = ناهنجانی → هر دو: score×0.8 و conf−0.15.

---

## ۸. Head Agent (agents/supervisor/head_agent.py)

دو سیگنال قطعی ورودی: `get_quick_trend` (ADX<25→Ranging؛ وگرنه EMA50/200 → Uptrend/Downtrend/Trending) و `has_high_impact_events` (رویداد High/Medium در ۲۴ ساعت آینده از DB — سطوح در ثابت `MARKET_MOVING_IMPACTS` قابل تنظیم است).
چارچوب سه‌حالته در پرامپت: NEWS DRIVEN (بنیادی اجباری، H1/H4) / TREND DRIVEN (تکنیکال H4 در اولویت) / RANGE BOUND (Events خاموش، News روشن، H1).
**قانون سخت: `activate_speakers` همیشه False** (هم در پرامپت، هم اورراید کد در run_phase3).
Fallback در شکست LLM: اجرای کامل (همه True، H1).

---

## ۹. دیتابیس (core/database.py) — فایل `crypto_agent.db`

| جدول | محتوا |
|---|---|
| `speakers` | سخنرانان (نام یکتا، نقش، دارایی اصلی، وزن، هندل توییتر) |
| `economic_events_history` | تاریخچه رویدادها (actual/forecast/previous + رشته‌های خام) |
| `trading_signals` | سیگنال‌های سخنران با audit trail کامل + JSON خام |
| `news_signals` | سیگنال‌های خبر (شامل is_aggregated و source_links_json برای Digest) |
| `event_signals` | سیگنال‌های رویداد |
| `raw_news_items` | اخبار خام RSS (dedup_hash یکتا) |
| `composite_signals` | سیگنال‌های تجمیعی فاز ۲ |
| `raw_speaker_items` | متن‌های خام سخنران |
| `trade_outcomes` | حافظه معاملات (entry_zone_low/high، SL/TP، وضعیت PENDING/WIN/LOSS/EXPIRED) |

`get_trade_memory_stats(currency)` → win_rate به‌صورت **عدد 0–100** (نه کسر!).
`session_scope()` کانتکست‌منیجر تراکنش است.

---

## ۱۰. LLM و متغیرهای محیطی

- **Arvan Cloud (پیش‌فرض):** مدل `GLM-5.2` — نیازمند `ARVAN_BASE_URL` و `ARVAN_API_KEY`
- **OpenRouter:** پیش‌فرض `openai/gpt-4o-mini` — نیازمند `OPENROUTER_API_KEY`
- **Ollama:** فقط در e2e_pipeline — پیش‌فرض `0xroyce/plutus`

temperature پیش‌فرض 0.1. LangChain (`ChatOpenAI`، `PydanticOutputParser`) + LangGraph (`StateGraph`).
`.env` با python-dotenv لود می‌شود (در صورت نصب).

---

## ۱۱. Gotchaها و مسائل شناخته‌شده

1. **پایپ‌لاین سخنران آفلاین است** — Head Agent همیشه `activate_speakers=False`؛ run_phase3 هم اورراید سخت دارد. تحلیل سخنران فقط از مسیر e2e مستقل با mock JSON قابل اجراست.
2. `get_quick_trend` فقط داده روزانه ۱ ساله می‌بیند (رژیم intraday نه) — محدودیت طراحی، نه باگ.
3. در پرامپت Head Agent سناریوی NEWS DRIVEN ظاهراً Speakers را اجباری می‌داند ولی قانون سخت همان پرامپت آن را لغو می‌کند — تناقض ظاهری، عمدی.
4. تیکرهای IV: USD→^VIX، XAU→^GVZ (fallback ^VIX)، OIL→^OVX (fallback ^VIX).

> **اصلاح‌شده در 2026-08-16:** باگ فرمت win_rate در run_phase3 (مقدار 0–100 با `:.1f%`)، پیاده‌سازی mtf_confluence، تمیزکاری ساختار `analyzed_items` در e2e_news_pipeline (حذف دیتاکلاس‌های مرده EnrichedNewsItem/AnalyzedNewsItem و fallback قدیمی)، پوشش رویدادهای Medium در Head Agent، مرتب‌سازی اخبار بر اساس تازگی + tie-break reliability (`_news_recency_key`) قبل از برش top-N در phase2_graph و سقف max_items در e2e_news_pipeline.

---

## ۱۲. وضعیت فعلی توسعه

- فاز ۱ (سه پایپ‌لاین E2E): کامل و کارکرده.
- فاز ۲ (گراف LangGraph + cross-asset): کامل.
- فاز ۳ (چندعاملی + حافظه معاملات): کامل؛ trade_evaluator برای cron آماده است.
- کارهای باز/کاندید: فعال‌سازی مجدد ingestion سخنران (نیازمند API)، پیاده‌سازی mtf_confluence، اصلاح باگ win_rate، پوشش Medium در چک رویدادهای Head Agent.
