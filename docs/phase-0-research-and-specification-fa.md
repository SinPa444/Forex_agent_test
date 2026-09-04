# گزارش Phase 0 — Deep Research و Specification

**پروژه:** `SinPa444/Forex_agent_test`
**تاریخ مشاهده:** 2026-09-04 UTC
**وضعیت:** تکمیل‌شده برای بازبینی و approval؛ اجرای Phase 1 مجاز نشده است.

> این سند نتیجهٔ تحقیق مستقل روی live site، کد موجود repository و مستندات رسمی/فنی است. برای جلوگیری از قاطی‌شدن certaintyها، هر نتیجه با یکی از برچسب‌های زیر بیان می‌شود:
>
> - **Verified Fact:** مستقیماً در پاسخ live، صفحهٔ رسمی یا کد موجود مشاهده شده است.
> - **External Evidence:** نتیجه‌ای که از سند رسمی یا مستندات شخص ثالث گرفته شده و باید هنگام implementation با live response دوباره validate شود.
> - **Engineering Inference:** برداشت فنی از Facts/Evidence؛ به‌تنهایی ادعای رفتار سایت نیست.
> - **Recommendation:** تصمیم پیشنهادی برای طراحی crawler.

---

## 1. خلاصهٔ اجرایی

### 1.1 هدف و مرز Phase 0

هدف، طراحی crawler برای یک صفحهٔ Calendar نیست؛ هدف، ساخت یک ingestion system قابل‌ادامه برای surfaceهای عمومی Forex Factory است: discovery، historical backfill، incremental refresh، نگه‌داری raw response، normalization، frontier پایدار، deduplication، change detection، health checks و source-vs-output verification.

در این Phase فقط تحقیق، specification و طراحی candidate انجام شد. موارد زیر عمداً **شروع نشده‌اند**:

- crawler جدید، request scheduler یا database migration؛
- crawl گسترده یا historical backfill؛
- browser automation برای bypass یا حل verification؛
- مقایسهٔ اجرایی Python و TypeScript؛
- source-vs-output benchmark؛
- تغییر در crawlerهای فعلی Calendar.

### 1.2 نتیجهٔ فنی پیشنهادی، مشروط به approval

1. **معماری layered و provider-based** باشد:
   `sitemap/known discovery → public export/feed → low-rate HTTP → browser diagnostic/fallback → immutable raw artifact → deterministic parser → normalized store → validation/dedup/change detection → persistent frontier → exports/verification`.
2. **Python انتخاب پایهٔ فعلی** است، چون repository موجود Python است و Pydantic/SQLAlchemy/SQLite و تست‌های Calendar دارد؛ این انتخاب هنوز benchmark نهایی نیست.
3. **TypeScript/Crawlee رقیب جدی** است، به‌خصوص اگر persistent queue، browser fallback و dataset management در عمل غالب شوند؛ باید با benchmark کوچک و fixtureهای یکسان مقایسه شود.
4. **browser default نیست.** ابتدا export/feed و HTTP کم‌نرخ استفاده می‌شود؛ Playwright فقط برای network/DOM discovery، صفحات واقعاً dynamic یا fallback کنترل‌شده است.
5. **LLM در ingestion قرار نمی‌گیرد.** parsing، identity، pagination، completeness و retry باید deterministic باشند. LLM در آینده فقط روی normalized data یا برای repair proposal محدود و قابل‌تأیید قابل استفاده است.
6. **Trades به‌هیچ‌وجه COT یا institutional positioning نیست.** در مدل، source آن `Trade Explorer` و visibility/privacy آن ثبت می‌شود.
7. برای policy crawler باید توقف سخت روی `403`، `401`، CAPTCHA/verification و هر نشانهٔ access-control، همراه با rate limit پایین، cache، conditional fetch و audit trail وجود داشته باشد.

### 1.3 تصمیم gate

این گزارش deliverable Phase 0 است. تا دریافت approval صریح کاربر، هیچ Phase 1 و هیچ crawler جدیدی اجرا نمی‌شود.

---

## 2. وضعیت repository موجود

### 2.1 آنچه اکنون وجود دارد

| مسیر | مشاهدهٔ مهم | نتیجه برای پروژهٔ جدید |
|---|---|---|
| `v2/ingestion/forex_factory_crawler.py` | Calendar feed هفتگی، Pydantic normalization/filtering و persistence به مدل فعلی | crawler عمومی site نیست؛ raw artifact، frontier و surfaceهای غیرCalendar ندارد |
| `v2/ingestion/ff_bridge.py` | bridge به `ext_ff_scraper/src` و ذخیرهٔ event در DB | برای weekly retry/windowing مفید است، اما schema هدف جامع نیست |
| `v2/tests/test_forex_factory_crawler.py` | unit testهای numeric/date/impact/category/filter/fetch | تست‌های پروژهٔ فعلی‌اند؛ معیار completeness crawler جدید نیستند |
| `v2/github/scrapper/src/forexfactory/` | providerهای Calendar برای export/HTML/saved HTML، normalizer، incremental و page detection | baseline تحقیقاتی مهم است، ولی package جداگانهٔ production-grade عمومی هنوز نیست |
| `v2/github/scrapper/tests/` | تست parser/export/normalizer/URL/cache و یک integration test | تست‌ها وجود دارند اما dependency و test-discovery فعلی مشکل دارد |
| `v2/core/database.py`, `v2/core/models.py` | schema پلتفرم تحلیل بنیادی، از جمله event history | schema canonical crawler، revision و raw provenance را پوشش نمی‌دهد |
| root | `pyproject.toml`، `requirements.txt`، `package.json` و `Makefile` استاندارد در root یافت نشد | setup و dependency boundary پروژهٔ crawler باید جداگانه و پس از approval تعریف شود |

### 2.2 اصل جداسازی

**Engineering Recommendation:** crawler جامع در همان جدول‌های trading-analysis platform ادغام نشود. بهتر است package و storage contract مستقل داشته باشد و در صورت نیاز، بعداً adapter خواندن normalized Calendar/News برای پلتفرم فعلی ساخته شود. این کار از آمیختن raw provenance، access state و domain analytics جلوگیری می‌کند.

---

## 3. Inventory رسمی surfaceها

این جدول inventory فعلی است؛ «مسیر مشاهده‌شده» به معنی route عمومی یا نتیجهٔ رسمی است، نه به معنی آن‌که selector یا network contract آن نهایی شده است.

| Surface | Route/entry point مشاهده‌شده | داده/رفتار قابل مشاهده | وضعیت اطمینان و محدودیت |
|---|---|---|---|
| Calendar landing | `https://www.forexfactory.com/calendar` | تقویم eventها، روزها، time/currency/impact/title و مقادیر actual/forecast/previous در UI | عنوان route live مشاهده شد؛ `fetch_page` DOM کامل نداده است |
| Calendar export | `https://nfs.faireconomy.media/ff_calendar_thisweek.json` و مسیرهای هم‌نام CSV/ICS/XML در code/export discovery | feed هفتگی عمومی | JSON live مشاهده شد؛ count کامل و schema تاریخی هنوز benchmark نشده است |
| Calendar date views | `calendar?week=...` در implementation موجود؛ `calendar?month=...` و `calendar?range=...` در probe | viewهای هفته/ماه/range | routeها پاسخ title دادند؛ semantics و pagination باید با raw/browser probe validate شوند |
| Calendar filters | queryهای `currencies`، `impacts`، `event_types` در probe/code | candidate filtering | **Hypothesis/lead**؛ اثر واقعی filter هنوز verify نشده است |
| Event detail/history | linkهای event و routeهای `/calendar/...` در parser/code و live calendar contract | جزئیات event، تاریخچه actual و احتمالا metadata/linked items | route pattern دیده شده؛ detail schema و historical boundary قطعی نیست |
| News landing | `/news` و `/calendar.forexfactory.com/news` | Hot Stories، Latest Stories، category blocks، comments، Breaking News legend، Search و Submit News | official search/live page evidence؛ DOM/network کامل sparse بود |
| News categories | sitemapهای `sitemap-news-fundamental-analysis.xml`، `technical-analysis`، `forex-industry-news`، `educational-news`، `entertainment-news` | canonical article URLs و `last-modified` | sitemap/category existence مستقیم مشاهده شد؛ همهٔ sitemap indexها هنوز exhaustive audit نشده‌اند |
| News article | `/news/{numeric-id}-{slug}` | title، source، story body/updates، posted time، author، category، impact، comment/view count، linked events/instruments، related stories | چند article از search result و route رسمی مشاهده شد؛ parser باید با raw HTML verify شود |
| Breaking News | breaking item داخل News stream/article، impact ranking | short updates، source handle، timestamps، comments، impact | official guide/search evidence؛ event ordering و retention window unknown |
| News comments/story log | article page و `Story Log` | comment/reply/author/time، append/update history | وجود `Story Log` در official result مشاهده شد؛ access/visibility و pagination نیازمند probe است |
| Forums index/categories | `/forums` و category links | category، subforum، thread counts/last activity | route family و forum/thread evidence مشاهده شد؛ full category inventory pending |
| Forum thread | `/thread/{id}-{slug}?page=N` | title، author، post number/time، body، replies، pagination، post links | route و pagination pattern در external/live search evidence دیده شد؛ page termination باید اندازه‌گیری شود |
| Forum post | `/thread/post/{post_id}` | permalink/anchor به یک post | route pattern در search evidence؛ باید response واقعی و canonical relation verify شود |
| Members/profiles | `https://www.forexfactory.com/{username}` | About، trading since/style/markets/contracts/coding/broker، links، activity، network و در صورت public بودن Trade Explorer | چند profile عمومی از official search مشاهده شد؛ profile slug conflict و privacy boundary مهم است |
| Traders directory | `/traders` و queryهای filter | شمارندهٔ traders، impact ranks، With Trade Explorers، Posted Systems، News Submissions | route و MIRS explanation رسمی مشاهده شد؛ query semantics باید raw verify شود |
| Trade Explorer | `/tradeexplorer` و member profile section | read-only MT4/MT5 analytics، sync، settings، public/member/subscription/buddy/me-only visibility | user guide رسمی؛ دادهٔ private نباید crawl شود و access control نباید bypass شود |
| Trades | `/trades` و `https://calendar.forexfactory.com/trades` | Trade Feed، Live/Demo، impact hurdle، subscriptions، leaderboard، positions | source رسمی صراحتاً Trade Explorer است؛ COT نیست؛ identity/aggregation باید با provenance ثبت شود |
| Market instruments | `/market/{symbol}`؛ نمونه `/market/eurusd`, `/market/vix`, `/market/dxy` | Performance، Chart، Latest Stories، Sessions، Upcoming events، Positions/Position Detail، Quotes | route و componentها مشاهده شد؛ dynamic chart/quotes contract هنوز capture نشده است |
| Market quotes | زیرصفحه/بخش Quotes در Market | broker، bid، ask، spread، commission | component رسمی مشاهده شد؛ quote freshness/endpoint/terms unknown |
| Market macro instruments | نمونه‌های VIX/DXY و SPX/USD، Nikkei225/USD، FTSE100/USD، WTI/USD، Gold/USD، BTC/USD | non-FX/macro instruments | inventory مشاهده شد؛ canonical symbol mapping باید از live list بیاید |
| Brokers | product رسمی User Guide و broker profile route | broker directory/profile، instrument/conditions/metadata | product رسمی verified؛ complete route/schema inventory pending |
| Prop Firms | `/prop-firms/...`؛ نمونهٔ `/prop-firms/united-states` | directory و firm profiles | route live/search evidence دارد؛ صفحهٔ country بسیار بزرگ و chunked بود؛ crawling کامل آن انجام نشد |
| Search | `/search` و search elements در News | search across content، احتمالا filter/pagination | route/title verified؛ query contract و indexing scope ناشناخته است |
| Public exports/sitemaps | official sitemapها و `nfs.faireconomy.media` | discovery و Calendar export | بهترین low-cost source candidate؛ freshness/completeness باید اندازه‌گیری شود |
| Static/product/help | `/userguide`, `/mission`, `/notices`, `/blog`, `/mediakit`, `/products`, `/contact`, `/bug-bounty` | product definitions، terms، policy، help | routeها verified؛ بعضی صفحات با fetch text sparse بودند و منبع schema داده نیستند |

### 3.1 چیزهایی که هنوز ادعا نمی‌کنیم

- هیچ ادعایی دربارهٔ وجود یا پایداری internal JSON/API endpoint بدون capture raw network نداریم.
- `range`، `month`، filter query و `/market/eurusd/1000` فقط route/response candidate هستند؛ semantics آن‌ها finalized نشده است.
- نبودن `actual` در payload نمونهٔ feed به معنی نبودن actual در همهٔ exportها یا Calendar detail نیست.
- search snippet جایگزین raw HTML، DOM کامل یا source comparison نیست.
- صفحات عمومی به معنی «دادهٔ کامل و بدون privacy boundary» نیستند.

---

## 4. یافته‌های live و source contractها

### 4.1 Calendar feed و export

**Verified Fact:** در 2026-09-04 endpoint `https://nfs.faireconomy.media/ff_calendar_thisweek.json` پاسخ JSON داد. در payload مشاهده‌شده رکوردهایی با fieldهای زیر وجود داشتند:

```text
title, country, date, impact, forecast, previous
```

مقادیر `date` در نمونه ISO-8601 با offset بودند؛ نمونه‌ها از بازهٔ 2026-08-30 تا 2026-09-01 شروع می‌شدند. `actual`، event ID و detail URL در رکوردهای مشاهده‌شده وجود نداشت. پاسخ توسط ابزار به دو chunk تقسیم شد و chunk کامل برای شمارش نهایی به workspace ذخیره نشد؛ بنابراین count آن response در این Phase ادعا نمی‌شود.

**Verified Fact:** probe کم‌ریسک به `ff_calendar_nextweek.json` و `ff_calendar_lastweek.json` پاسخ `404 Not Found` داد. این فقط رفتار همان endpointهای آزموده‌شده در تاریخ probe است؛ نباید نتیجه گرفت که historical Calendar عمومی مطلقاً وجود ندارد.

**External Evidence:** در implementation موجود، export discovery برای CSV/JSON/XML/ICS دیده می‌شود و parserها برای هر چهار فرمت test دارند. این evidence نشان می‌دهد export contract candidate ارزش بررسی دارد، نه این‌که تمام URLها همیشه زنده یا complete هستند.

**Engineering Inference:** feed هفتگی برای incremental forecast ingestion مناسب است، اما برای backfill نامحدود و actual/history به‌تنهایی کافی نیست. باید `source_snapshot` و provenance هر provider نگه‌داری شود و Calendar HTML/detail به عنوان provider جدا بماند.

### 4.2 Calendar page و timezone

**Verified Fact:** routeهای Calendar با `week`، `month` و `range` در probe title معتبر Calendar برگرداندند، اما ابزار fetch صفحهٔ dynamic را sparse نشان داد. بنابراین request success به معنی parse success یا اعمال‌شدن filter نیست.

**Verified Fact:** feed timestamp offset صریح دارد و UI Calendar timezone-sensitive است.

**Recommendation:** برای هر datetime سه مقدار جدا ذخیره شود:

- `source_datetime_text` دقیقاً همان متن/ISO source؛
- `source_offset` یا timezone evidence؛
- `occurred_at_utc` برای ordering؛
- و در صورت نیاز `display_datetime` با timezone انتخابی، نه به‌عنوان حقیقت مستقل.

DST و mapping دقیق timezone (`America/New_York` یا setting دیگری) هنوز live verify نشده و قبل از historical backfill باید با چند transition و holiday test شود.

### 4.3 News

**Verified Fact:** صفحهٔ News و official User Guide وجود هفت product اصلی از جمله News را تأیید می‌کنند. live/search evidence در News موارد زیر را نشان داد:

- Hot Stories و Latest Stories؛
- دسته‌های Fundamental Analysis، Technical Analysis، Forex Industry News، Educational News و Entertainment News؛
- Breaking News با impact level؛
- source/handle، timestamp، comments، article body یا updates؛
- `Posted by`، category، impact، view/comment count؛
- Linked events و Instruments؛
- Related Stories و Story Log در بعضی articleها؛
- Search و Submit News در entry point رسمی.

**External Evidence:** sitemapهای رسمی categoryهای News URLهای canonical article و `last-modified` را نشان دادند.

**Engineering Inference:** sitemap برای discovery articleهای canonical و change detection ارزشمندتر از crawl کردن بی‌هدف stream است؛ برای comments، breaking updates و story log باید detail refresh policy مستقل تعریف شود.

### 4.4 Forums

**Verified Fact/External Evidence:** route familyهای forum category، thread با `?page=N` و post permalink در live/search evidence مشاهده شدند. threadها author، post number، joined/status، body، timestamp، reply و permalink دارند.

**Recommendation:** pagination را تا «صفحهٔ معتبر بدون entity جدید» با سقف سخت دنبال کنید؛ به `page=N` حدس‌زده‌شده یا count نمایشی اعتماد نکنید. برای post identity از source post ID و برای thread از source thread ID استفاده کنید؛ title/slug فقط canonical metadata است.

### 4.5 Trades و Trade Explorer

**Verified Fact:** `/trades` به Trade Feed، Live/Demo accounts، impact hurdle، subscriptions، leaderboard و positions اشاره دارد. Official User Guide می‌گوید Trade Explorer فقط read-only است، به MT4/MT5 متصل می‌شود، sync دوره‌ای دارد و visibilityهایی مانند Public، Members Only، Subscriptions Only، Buddies Only و Me Only دارد؛ برخی fieldها نیز permission جداگانه دارند.

**Hard Constraint:** این داده «institutional COT» نیست. مدل باید صریحاً داشته باشد:

```text
source_system = "Forex Factory Trade Explorer"
position_semantics = "member/account-reported or aggregated Trade Explorer data"
visibility = observed_or_declared_visibility
```

نباید field یا reportی با نام `cot_long`، `cot_short` یا `institutional_positioning` ساخته شود مگر از منبع دیگری با definition مستقل.

**Recommendation:** crawler فقط public/authorized surfaceی را بخواند که بدون login و بدون bypass قابل مشاهده است. `Members Only`، `Buddies Only`، `Subscriptions Only` و `Me Only` خارج از public crawl هستند؛ response verification/redirect باید terminal state شود، نه retry loop.

### 4.6 Market

**Verified Fact:** `/market/eurusd`، `/market/vix` و `/market/dxy` در live probe title معتبر داشتند. Market UI/official evidence شامل Performance، Chart، Latest Stories، Sessions، Upcoming events، Positions، Position Detail و Quotes است؛ Quotes broker/bid/ask/spread/commission را نشان می‌دهد.

**Verified Fact:** macro instrument نمونه شامل SPX/USD، Nikkei225/USD، FTSE100/USD، DXY/USD، WTI/USD، Gold/USD، BTC/USD و VIX/USD مشاهده شد.

**Unknown:** chart data، quote freshness، broker source، endpointهای JSON، aggregation semantics و historical depth هنوز capture نشده‌اند. Market positions باید با source=`Trade Explorer` و visibility/aggregation محدود ثبت شوند؛ به عنوان sentiment یا COT نام‌گذاری نشوند.

### 4.7 Brokers و Prop Firms

**Verified Fact:** Brokers یکی از productهای رسمی است و profile/directory surface دارد. Prop Firm country route مشاهده شد، اما response آن بسیار بزرگ/chunked بود و فقط بخش اول بررسی شد.

**Recommendation:** directory index، profile detail و pagination را جدا model کنید. در profileها canonical identity، legal/display name، source URL، observed fields و fetched revision نگه‌داری شود؛ ادعاهای مالی/اعتباری crawler نباید بدون source citation به normalized fact تبدیل شوند.

---

## 5. Policy، ethics و access boundary

### 5.1 شواهد policy

- **Verified Fact:** Terms/Notices دربارهٔ misuse نکردن Services و استفاده از interface/instructions ارائه‌شده هشدار می‌دهد.
- **Verified Fact:** bug bounty، درخواست‌های مخرب و DoS/overwhelming service را خارج از scope می‌داند.
- **Engineering Inference:** crawler باید با request volume پایین، cache، conditional request، backoff، audit trail و kill switch ساخته شود.

### 5.2 قوانین سخت crawler

1. host و path allowlist؛ خارج از scope فقط با approval اضافه شود.
2. concurrency پیش‌فرض پایین، ترجیحاً یک stream کنترل‌شده در هر host؛ jitter و `Retry-After` رعایت شود.
3. `403`، `401`، CAPTCHA، bot-check، security verification، login redirect و access denied، retry بی‌نهایت ندارند و به `blocked/verification_required` تبدیل می‌شوند.
4. هیچ credential، cookie خصوصی، session token، proxy rotation تهاجمی، CAPTCHA solving یا access-control bypass استفاده نشود.
5. browser فقط در صورت ضرورت و بدون دورزدن challenge استفاده شود.
6. پاسخ raw با metadata و hash ذخیره شود، اما secret/header حساس redaction شود.
7. برای pages دارای user content، privacy class و retention policy تعریف شود.
8. پیش از production run، dry-run و budget request داشته باشیم.

---

## 6. Candidate architecture

### 6.1 لایه‌ها

```text
Source Catalog / Allowlist
        ↓
Discovery (sitemap, known routes, feed/export, detail links)
        ↓
Persistent Frontier + Scheduler + Request Budget
        ↓
Provider (export → HTTP → controlled browser diagnostic)
        ↓
Immutable Raw Artifact Store + Fetch Audit
        ↓
Deterministic Parser + Page/Verification Detection
        ↓
Schema Validation + Normalizer + Entity Identity
        ↓
Deduplication + Revision/Change Detection + Parser Health
        ↓
Normalized DB / partitioned exports / verification reports
```

### 6.2 Raw artifact contract

برای هر fetch، حداقل این metadata لازم است:

```text
artifact_id
source_surface
requested_url
final_url
retrieved_at_utc
status_code
content_type
content_encoding
etag
last_modified
retry_after
request_headers_redacted
response_headers_redacted
body_path_or_blob_ref
body_sha256
byte_length
provider_name
parser_version
access_state
```

Raw artifact immutable است؛ parser جدید باید بتواند بدون request مجدد روی raw قبلی replay شود. bodyهای بزرگ نباید بی‌دلیل داخل Git قرار بگیرند.

### 6.3 Entity و revision contract

همهٔ surfaceها یک envelope مشترک داشته باشند:

```text
entity_type
source_name
source_id (nullable but preferred)
canonical_url
observed_at_utc
first_seen_at_utc
last_seen_at_utc
visibility_class
content_hash
normalized_payload
raw_artifact_id
parser_version
record_status
```

identity hierarchy:

1. source-native ID؛
2. canonical URL/route ID؛
3. hash از stable identity fields؛
4. fingerprint title/date/author فقط به‌عنوان fallback و با confidence پایین.

عنوان به‌تنهایی key نیست. تغییر slug یا title نباید entity جدید بسازد اگر source ID ثابت است.

### 6.4 Modelهای typed پیشنهادی

- `calendar_event`, `calendar_event_revision`, `calendar_linked_entity`؛
- `news_article`, `news_update`, `news_comment`, `news_related_story`, `news_linked_event`؛
- `forum_category`, `forum_thread`, `forum_post`؛
- `member_profile`, `trader_directory_entry`, `trade_explorer_snapshot`, `trade_position`؛
- `market_instrument`, `market_session`, `market_quote`, `market_position_aggregate`؛
- `broker`, `prop_firm`؛
- `sitemap_entry`, `frontier_item`, `fetch_attempt`, `parse_result`, `change_event`.

همهٔ مدل‌های user/trade باید `visibility_class`، `access_observed` و `privacy_safe` داشته باشند. `trade_position` باید `source_system=Trade Explorer` داشته باشد و COT vocabulary در schema آن ممنوع باشد.

### 6.5 Durable frontier

هر frontier item:

```text
frontier_id, url, surface, discovery_parent, priority,
state, attempt_count, next_attempt_at, first_seen_at, last_attempt_at,
last_status, last_artifact_id, content_hash, parser_status,
blocked_reason, lease_owner, lease_expires_at
```

stateهای حداقل:

```text
new → leased → fetched → parsed → normalized → verified
                  ↘ retryable_failure
                  ↘ blocked / verification_required
                  ↘ terminal_failure
```

lease و checkpoint باید بعد از process kill قابل resume باشند؛ هیچ queue صرفاً in-memory قابل قبول نیست.

### 6.6 Deterministic parser health

برای هر parser metrics ثبت شود:

- تعداد raw input؛
- HTTP success/blocked/redirect؛
- expected marker presence؛
- entity count؛
- required-field coverage؛
- duplicate ratio؛
- schema validation errors؛
- unexpected zero result؛
- parse version و fixture version.

`HTTP 200 + zero entities` failure/health warning است، نه success خاموش.

---

## 7. Completeness و verification specification

### 7.1 تعریف completeness

«کامل» به معنی download کردن همهٔ URLهای سایت نیست. برای هر surface باید scope و denominator ثبت شود:

```text
completeness = observed_valid_entities / expected_entities_in_declared_scope
```

اگر denominator قابل‌مشاهده نیست، نتیجه باید `coverage_unknown` باشد، نه 100%.

### 7.2 شاخص‌های مشترک

| Metric | تعریف |
|---|---|
| discovery coverage | تعداد candidateهای کشف‌شده نسبت به source catalog/sitemap pages |
| fetch coverage | `fetched_success / scheduled` با blocked و excluded جدا |
| parse coverage | rawهای قابل parse / rawهای successful |
| entity yield | entityهای معتبر به تفکیک page/provider |
| required-field coverage | پرشدن fieldهای required به تفکیک version |
| identity closure | لینک‌های detail کشف‌شده که به entity یا terminal state رسیدند |
| pagination closure | ادامه تا صفحهٔ بدون entity جدید/علامت پایان، با max-page guard |
| revision freshness | زمان مشاهدهٔ آخرین تغییر نسبت به source timestamp/last-modified |
| source parity | اختلاف key set و field values بین source snapshot و normalized output |

### 7.3 روش source-vs-output برای surfaceهای اصلی

#### Calendar

- feed/export raw را immutable ذخیره کن؛
- key پیشنهادی: source ID اگر در detail/HTML موجود است، وگرنه `(date instant, country, normalized title, source week)` با collision report؛
- count، set difference، duplicate، required field و value diff را گزارش کن؛
- feed بدون `actual` را با Calendar detail/HTML یکسان فرض نکن؛ field availability matrix بساز؛
- same-week دوبار fetch شود تا تغییرات forecast/actual به revision تبدیل شود.

#### News

- URLهای sitemap به عنوان expected discovery set؛
- canonical URL، numeric article ID و `lastmod` با crawler output مقایسه شود؛
- برای sample از هر category، title/source/posted/category/impact و linked entities مقایسه شود؛
- comments/Story Log جداگانه با pagination و revision count بررسی شوند.

#### Forums

- category index و thread page sample؛
- thread/post IDs، page count واقعی، first/last post و duplicate identity؛
- حداقل یک thread کوتاه، یک thread چندصفحه‌ای و یک thread با reply/quote fixture لازم است.

#### Market/Trades

- instrument catalog parity؛
- برای quote و position زمان مشاهده، source و visibility گزارش شود؛
- به دلیل dynamic بودن، numeric parity فقط برای یک snapshot و با tolerance/age مشخص معتبر است؛
- aggregated Trade Explorer بدون source snapshot یا visibility metadata «complete» محسوب نشود.

### 7.4 quality gates پیشنهادی

قبل از production:

- zero unexpected verification pages؛
- zero silent parser-zero در صفحات expected؛
- 100% raw/normalized record provenance؛
- idempotent rerun روی fixtureها؛
- resume پس از kill در هر state؛
- retry فقط برای failureهای مجاز؛
- source-vs-output report تولید شود؛
- unknown/blocked به‌صورت عددی و قابل‌مشاهده report شود.

---

## 8. مقایسهٔ Python و TypeScript

| معیار | Python | TypeScript/Crawlee |
|---|---|---|
| fit با repository | **مزیت:** کد، مدل‌ها، Pydantic/SQLAlchemy و تست‌های فعلی Python | نیازمند stack جدا یا مرزبندی جدید |
| HTTP/HTML parsing | requests/httpx/urllib + lxml/BeautifulSoup/Parsel قابل انتخاب | Cheerio و HTTP crawler مناسب |
| persistent frontier | باید با SQLite/Postgres/Redis یا frameworkی مثل Scrapy طراحی شود | **مزیت frameworkی:** RequestQueue، Dataset، retry/failure handler در Crawlee |
| browser fallback | Playwright Python موجود و مستند | Playwright Node + Crawlee integration قوی |
| data validation | Pydantic و ecosystem فعلی | zod/io-ts یا schemaهای دستی |
| deployment | ساده با Python فعلی؛ dependency hygiene لازم است | Node runtime و operational ownership جدا |
| benchmark فعلی | **انجام نشده** | **انجام نشده** |
| ریسک اصلی | ساخت queue و crawler lifecycle به‌صورت custom | duplicate domain model و فاصله از codebase فعلی |

### 8.1 معیار benchmark بعد از approval

با fixtureهای یکسان و بدون فشار به live site:

1. parse throughput برای Calendar JSON/CSV/ICS/HTML؛
2. sitemap pagination/discovery throughput؛
3. resume correctness بعد از kill؛
4. retry/429/Retry-After؛
5. 403/verification detection؛
6. conditional fetch و cache hit؛
7. browser fallback startup/parse cost؛
8. memory و artifact I/O؛
9. duplicate/change detection؛
10. developer/operational complexity.

**Recommendation فعلی:** Python baseline برای Phase 1؛ اگر benchmark نشان دهد persistent queue و browser workload غالب است، TypeScript/Crawlee را به‌صورت informed switch یا service جدا بررسی کنیم. این recommendation نتیجهٔ benchmark نیست.

---

## 9. فازهای پیشنهادی بعدی، بدون شروع آن‌ها

| Phase | خروجی پیشنهادی | gate |
|---|---|---|
| 0 | همین research/spec، inventory، policy، verification plan | همین سند و approval کاربر |
| 1 | vertical slice کم‌ریسک: source catalog، raw artifact، frontier، Calendar feed/export، deterministic parser و fixture tests | source parity و resume روی fixture |
| 2 | Calendar HTML/detail، sitemap discovery، historical/incremental policy، timezone/DST و change detection | live probe محدود و گزارش parity |
| 3 | News sitemap/article/category/breaking/comments با scope و pagination | article sample/category parity |
| 4 | Forums category/thread/post و Traders/profile با privacy boundary | pagination closure و public-only audit |
| 5 | Market/instruments/quotes/sessions و Trades/Trade Explorer، با نام‌گذاری غیر-COT | source/visibility/time freshness audit |
| 6 | Brokers، Prop Firms، Search و static/help inventory | directory completeness و blocked handling |
| 7 | browser fallback/network discovery، benchmark Python/TS، hardening و operational runbook | explicit approval برای production crawl |

این جدول برنامه است، نه اجرای Phase 1. هیچ فایل implementation مربوط به آن فازها در این Phase ایجاد نشده است.

---

## 10. ریسک‌ها و unknownهای باز

| ریسک/unknown | اثر | روش کاهش |
|---|---|---|
| صفحات dynamic در fetch ساده sparse هستند | selector/API اشتباه و false success | raw browser/network probe محدود؛ browser default نشود |
| export فقط current week است | historical incompleteness | sitemap/detail/calendar route feasibility probe و coverage unknown صادقانه |
| actual در feed sample نیست | mismatch بین feed و detail | field availability matrix و multi-provider source provenance |
| timezone/DST دقیق هنوز اثبات نشده | جابه‌جایی event در روز/ساعت | preserve source offset، UTC normalization و DST fixture |
| anti-bot/verification | block یا policy violation | low rate، cache، توقف روی verification، بدون bypass |
| user privacy/Trade Explorer | exposure غیرمجاز | public-only allowlist، visibility metadata، no credential |
| pagination و retention | incomplete threads/comments/stream | termination invariant و sample-based parity |
| quote/chart freshness | valueهای stale یا non-reproducible | observation time، source age و snapshot semantics |
| title/slug changes | duplicate entity یا lost revisions | source ID/canonical identity hierarchy |
| data source drift | parser silent failure | marker/health checks و parser version |
| feed/content live تغییر می‌کند | benchmark غیرقابل‌تکرار | raw artifact fixture و timestamped report |
| dependency setup فعلی ناقص است | تست‌های غیرقابل‌اجرا | بعد از approval setup استاندارد و isolated environment |

---

## 11. Probe و test log همین Phase

### 11.1 Live/official probes

- `fetch_page` روی Calendar landing و queryهای week/month/range/filter: title معتبر، DOM dynamic/sparse.
- `fetch_page` روی `ff_calendar_thisweek.json`: JSON live با fieldهای ذکرشده؛ پاسخ chunked بود.
- `fetch_page` روی `ff_calendar_nextweek.json` و `ff_calendar_lastweek.json`: `404 Not Found`.
- `fetch_page` روی `/news`، `/calendar.forexfactory.com/news` و articleهای News: route/title و بخش‌هایی از content؛ بعضی پاسخ‌ها sparse.
- `fetch_page` روی `/market/eurusd`، `/market/vix`، `/market/dxy` و routeهای candidate: title معتبر؛ dynamic fields کامل استخراج نشدند.
- `fetch_page` روی `/trades`، `/traders` و `/tradeexplorer`/canonical redirect: entry point و access boundary بررسی شد.
- `fetch_page`/`web_search` برای user guide، notices، bug bounty، sitemapهای News، forums، profiles و brokers: inventory و policy evidence جمع شد.

این probes crawl production یا source comparison نیستند و artifact raw آن‌ها در workspace ذخیره نشده است.

### 11.2 Test execution

**Testهای جدید:** در Phase 0 نوشته نشد؛ این فاز specification-only است.

**Testهای موجود:**

1. اجرای `python -m pytest -q v2/github/scrapper/tests v2/tests/test_forex_factory_crawler.py` انجام شد و با `No module named pytest` متوقف شد.
2. اجرای `python -m unittest discover -s v2/github/scrapper/tests -p 'test*.py' -q` انجام شد، اما 8 module به علت dependency/setup شکست خوردند؛ از جمله نبودن `pandas`، `dateutil` و در چند import نبودن package path `src`.
3. `python -m compileall -q v2` با موفقیت انجام شد؛ bytecode تغییرکردهٔ tracked بعد از probe به حالت repository restore شد.

بنابراین در این گزارش هیچ ادعای «تست suite سبز» یا «live crawler موفق» وجود ندارد. محیط dependency فعلی قبل از implementation باید اصلاح و سپس واقعاً test شود.

### 11.3 Source comparison/live crawl

در Phase 0 **انجام نشده است**؛ تنها low-risk discovery probes انجام شدند. هیچ count، recall، precision یا parity عددی برای crawler ادعا نمی‌شود.

---

## 12. ارزیابی کیفیت Phase 0

- **Coverage inventory:** خوب برای surfaceهای اصلی رسمی؛ schema و network contract dynamic هنوز کم‌اطمینان است.
- **Evidence discipline:** Facts، external evidence، inference و recommendation جدا شده‌اند؛ search snippetها به‌عنوان حقیقت raw استفاده نشده‌اند.
- **Completeness definition:** تعریف شده و برای Calendar/News/Forums/Market/Trades روش مقایسه دارد.
- **Safety/policy:** در معماری و acceptance criteria وارد شده است.
- **Technology decision:** recommendation مشروط است؛ benchmark عمداً به فاز بعد موکول شده است.
- **Implementation readiness:** برای ساخت vertical slice بعد از approval کافی است؛ برای claim کردن complete crawler کافی نیست.
- **Known limitation:** responseهای dynamic، raw network contract، historical boundary، DST و live source parity هنوز باقی‌اند.

### پیشنهاد approval

برای شروع Phase 1 فقط این scope را approve کنید:

> «یک vertical slice محدود و قابل‌توقف برای Calendar public feed/export + raw artifact + persistent frontier + deterministic normalization + fixture/source-parity tests، بدون browser bypass و بدون crawl سایر surfaceها.»

پس از آن، طبق قرارداد پروژه، گزارش کامل Phase 1 شامل فایل‌های ایجاد/تغییریافته، testهای واقعاً اجراشده، live crawl محدود، source comparison، known problems و recommendation فاز بعد ارائه خواهد شد و دوباره متوقف می‌شوم.

---

## 13. Files Created / Modified

- **Created:** `docs/phase-0-research-and-specification-fa.md`
- **Modified:** هیچ فایل کد، dependency، schema یا test در Phase 0 تغییر نکرد.
- **Generated artifacts:** هیچ raw crawl، dataset یا artifact حجیم در repository ذخیره نشد.

---

## 14. فهرست شواهد و منابع

تاریخ بررسی منابع live و search: 2026-09-04 UTC، مگر آن‌که خود منبع تاریخ دیگری نشان دهد.

### منابع رسمی Forex Factory

- [User Guide](https://www.forexfactory.com/userguide) — هفت product رسمی، MIRS، Trade Explorer، Forums، Trades، News، Calendar، Market و Brokers؛ visibility و read-only محدودیت‌ها.
- [Notices / Terms](https://www.forexfactory.com/notices) — استفاده از interface/instructions و منع misuse.
- [Bug Bounty](https://www.forexfactory.com/bug-bounty) — scope و خارج‌بودن DoS/overwhelming service.
- [Calendar](https://www.forexfactory.com/calendar) — Calendar landing و route خانوادهٔ Calendar.
- [Current Calendar JSON export](https://nfs.faireconomy.media/ff_calendar_thisweek.json) — public weekly payload؛ schema observed در همین Phase.
- [News](https://www.forexfactory.com/news) و [News calendar host](https://calendar.forexfactory.com/news) — stream، breaking/search/submit/category blocks.
- [Market EURUSD](https://www.forexfactory.com/market/eurusd)، [VIX](https://www.forexfactory.com/market/vix)، [DXY](https://www.forexfactory.com/market/dxy) — instrument entry points و componentهای Market.
- [Trades](https://www.forexfactory.com/trades) و [Calendar Trades](https://calendar.forexfactory.com/trades) — Trade Feed و Trade Explorer attribution.
- [Trade Explorer](https://www.forexfactory.com/tradeexplorer) — canonical public entry/canonical redirect observed.
- [Traders](https://www.forexfactory.com/traders) — directory، MIRS و filter families.
- [Sitemap — Fundamental Analysis](https://www.forexfactory.com/sitemap-news-fundamental-analysis.xml)، [Technical Analysis](https://www.forexfactory.com/sitemap-news-technical-analysis.xml)، [Forex Industry News](https://www.forexfactory.com/sitemap-news-forex-industry-news.xml)، [Educational News](https://www.forexfactory.com/sitemap-news-educational-news.xml)، [Entertainment News](https://www.forexfactory.com/sitemap-news-entertainment-news.xml) — canonical article URLs و last-modified.

### مستندات فنی مورد بررسی

- [Playwright Python Network](https://playwright.dev/python/docs/network) — مشاهده/route/انتظار request و response؛ مناسب discovery و fallback کنترل‌شده.
- [Crawlee Request Storage](https://crawlee.dev/js/docs/guides/request-storage) و [Playwright Crawler](https://crawlee.dev/js/docs/examples/playwright-crawler) — queue/dataset/retry و crawlerهای HTTP/browser.
- [Scrapy](https://docs.scrapy.org/en/latest/) و [AutoThrottle](https://docs.scrapy.org/en/latest/topics/autothrottle.html) — scheduler، dupe filter، pipeline و throttling.
- [curl_cffi](https://curl-cffi.readthedocs.io/en/latest/) — candidate برای TLS/client behavior؛ browser runtime یا تضمین حل challenge نیست.

### Evidence خارجی جست‌وجوشده

Search results برای inventory و نمونه‌های عمومی News، Forums، Profiles و Traders استفاده شدند؛ این نتایج در سند به عنوان External Evidence برچسب خورده‌اند و جایگزین raw live response نیستند.
