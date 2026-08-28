# Graph Report - v2  (2026-08-17)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1716 nodes · 3712 edges · 106 communities (96 shown, 10 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 333 edges (avg confidence: 0.53)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- e2e_pipeline.py
- phase2_graph.py
- SpeakerDB
- invoke_with_retry
- e2e_news_pipeline.py
- cache_validation.py
- database.py
- data_fetcher.py
- EventInterpretationEvent
- NewsInterpretation
- HistoricalStdResult
- nlp_news.py
- service.py
- test_utils.py
- NewsSignalDB
- forex_factory_crawler.py
- test_forex_factory_crawler.py
- _canonicalize_event_title
- EventAnalysisInput
- nlp_x.py
- SpeakerTextItem
- scrape_incremental
- ForexFactoryEvent
- NewsItem
- conftest.py
- e2e_speaker_pipeline.py
- translate_instrument_direction
- test_nlp_news.py
- resolve_asset_route
- EventInterpretation
- test_nlp_x.py
- EventHistoryDB
- main.py
- rss_feed_loader.py
- TestEventSchema
- analyze_economic_event
- .score
- NewsPipelineRunResult
- test_nlp_event.py
- http.py
- FeedLoader
- NewsAnalysisService
- analyze_speaker_text
- Speaker
- direction_label
- test_providers.py
- ForexFactoryExportProvider
- historical_events_importer.py
- SpeakerPipelineRunResult
- _add_missing_columns
- FeedConfig
- Session
- app.py
- fetch_forex_factory_events
- _resolve_event_category_weight
- NewsSignalRepository
- _parse_row
- _parse_numeric
- TestCalendarRowParser
- alignment_label
- _resolve_event_category
- fetch_and_analyze_news_node
- test_historical_events_importer.py
- FeedFetcher
- _category_from_canonical_key
- SpeakerSignalRepository
- build_url_for_partial_range
- forexfactory_html.py
- save_event
- FeedLoadResult
- MarketContext
- _normalize_impact
- FeedEnricher
- .normalize_legacy_keys
- NewsSignal
- import_historical_csv
- _infer_category
- _parse_csv_datetime
- DuplicateDetector
- TestExporters
- _resolve_event_impact_weight
- _CalendarTableParser
- _clean_detail_text
- TestDetailParser
- _resolve_statement_type_weight
- TestTradabilityRule
- ff_bridge.py
- UILogHandler
- db_session
- MockResponse
- forexfactory/__init__.py
- ui/__init__.py

## God Nodes (most connected - your core abstractions)
1. `MarketContext` - 75 edges
2. `NewsItem` - 57 edges
3. `EventHistoryDB` - 45 edges
4. `Speaker` - 45 edges
5. `NewsInterpretation` - 40 edges
6. `EventAnalysisInput` - 39 edges
7. `SpeakerTextItem` - 38 edges
8. `SpeakerInterpretation` - 36 edges
9. `ForexFactoryEvent` - 34 edges
10. `resolve_asset_route()` - 26 edges

## Surprising Connections (you probably didn't know these)
- `AnalyzedEvent` --uses--> `EventAnalysisInput`  [INFERRED]
  agents/fundamental/e2e_pipeline.py → core/models.py
- `AnalyzedEvent` --uses--> `EventSignal`  [INFERRED]
  agents/fundamental/e2e_pipeline.py → core/models.py
- `EnrichedEvent` --uses--> `EventAnalysisInput`  [INFERRED]
  agents/fundamental/e2e_pipeline.py → core/models.py
- `EnrichedEvent` --uses--> `MarketContext`  [INFERRED]
  agents/fundamental/e2e_pipeline.py → core/models.py
- `PreparedEvent` --uses--> `EventAnalysisInput`  [INFERRED]
  agents/fundamental/e2e_pipeline.py → core/models.py

## Import Cycles
- None detected.

## Communities (106 total, 10 thin omitted)

### Community 0 - "e2e_pipeline.py"
Cohesion: 0.07
Nodes (52): AnalyzedEvent, _audit_default(), build_llm(), choose_and_prepare_source(), configure_logging(), EnrichedEvent, expand_impact_filter(), FailureRecord (+44 more)

### Community 1 - "phase2_graph.py"
Cohesion: 0.09
Nodes (42): aggregate_signals_node(), build_cross_asset_graph(), build_phase2_graph(), CompositeSignal, cross_asset_consistency_node(), generate_detailed_currency_reports_node(), generate_global_summary_node(), get_temporal_context() (+34 more)

### Community 2 - "SpeakerDB"
Cohesion: 0.08
Nodes (24): جدول speakers: اطلاعات سخنرانان تأثیرگذار بازار. مثال: name="Jerome Powell",…, SpeakerDB, clear_speakers_table(), list_speakers(), print_speakers_table(), Add or update a speaker in the database. Returns: "inserted" or "updated", Seed all speakers from the SPEAKERS registry. Returns: dict with counts:…, نمایش یک speaker برای seed کردن. (+16 more)

### Community 3 - "invoke_with_retry"
Cohesion: 0.07
Nodes (32): BaseChatModel, BaseModel, field_validator, Evaluates the signals and returns a final risk decision., Structured trade execution plan., Final decision from the Risk Manager., RiskDecision, TradePlan (+24 more)

### Community 4 - "e2e_news_pipeline.py"
Cohesion: 0.08
Nodes (37): _audit_default(), build_llm(), compute_dedup_hash(), compute_dedup_key(), EnrichedCurrencyGroup, FeedTierChoice, is_already_persisted(), LLMProvider (+29 more)

### Community 5 - "cache_validation.py"
Cohesion: 0.12
Nodes (32): ArgumentParser, build_parser(), main(), _canonical_date_series(), _canonical_datetime_series(), canonicalize_cache_frame(), format_validation_report(), _missing_key_mask() (+24 more)

### Community 6 - "database.py"
Cohesion: 0.10
Nodes (34): cleanup_all_tables(), cleanup_old_data(), economic_event_exists(), get_latest_economic_event(), get_latest_event_signal(), get_latest_news_signal(), get_latest_record(), get_latest_trading_signal() (+26 more)

### Community 7 - "data_fetcher.py"
Cohesion: 0.10
Nodes (33): calculate_atr(), calculate_historical_volatility(), calculate_surprise_factor(), calculate_volatility_multiplier(), _compute_robust_std(), _download_single(), fetch_implied_volatility(), fetch_market_context() (+25 more)

### Community 8 - "EventInterpretationEvent"
Cohesion: 0.10
Nodes (19): _clamp(), EventSignalScorer, موتور امتیازدهی deterministic برای event signals. فرمول: final_score =…, مشابه nlp_news و nlp_x — ۹ فیلد کلیدی., EventInterpretationEvent, خروجی ساختاریافته LLM برای تحلیل event. تفاوت با NewsInterpretation: - بدون…, test_event_scorer.py ==================== Unit tests for the EventSignalScorer…, Test baseline scoring for a perfect bullish signal. (+11 more)

### Community 9 - "NewsInterpretation"
Cohesion: 0.09
Nodes (11): NewsInterpretation, خروجی ساختاریافته LLM مخصوص تحلیل اخبار RSS. تفاوت‌ها با EventInterpretation: -…, direction=-1 ولی sentiment مثبت → خطا, TestNewsInterpretation, Even with extreme volatility, half_life should be >= 1., Volatility of 0.0 should not cause division by zero., Low quantitative alignment should reduce confidence., TestAlignmentEdgeCases (+3 more)

### Community 10 - "HistoricalStdResult"
Cohesion: 0.09
Nodes (16): fetch_historical_surprise_std(), fetch_historical_surprise_std_full(), HistoricalStdResult, نتیجه محاسبه historical_std با metadata برای audit. source:…, آیا این std بر اساس داده واقعی محاسبه شده؟, محاسبه historical std سورپرایزها از DB با metadata کامل. استراتژی: 1. canonical…, نسخه ساده برای backward compatibility. فقط مقدار std را برمی‌گرداند. برای…, تست‌های واقعی با DB. این‌ها فرض می‌کنند DB seed شده است. (+8 more)

### Community 11 - "nlp_news.py"
Cohesion: 0.13
Nodes (18): build_news_digest_prompt(), build_news_prompt(), _build_prompt_inputs(), _format_optional(), NewsDigestAnalysisService, _parse_published(), Any, BaseChatModel (+10 more)

### Community 12 - "service.py"
Cohesion: 0.11
Nodes (28): BaseException, get_latest_composite_signal(), query_by_time_range(), جدول trade_outcomes: ردیابی سیگنال‌های تاییدشده و نتیجه نهایی آن‌ها. برای سیستم…, Safe, self-closing session lifecycle: open session -> use it -> commit…, Build a filtered, indexed query for `model` restricted to the requested time…, Latest row in composite_signals, optionally filtered (e.g. currency='USD')., session_scope() (+20 more)

### Community 13 - "test_utils.py"
Cohesion: 0.07
Nodes (9): Tests for utility functions., TestCleanHtmlText, TestCompositeKey, TestGenerateRunId, TestIsMarketHours, TestNormalizeDatetime, TestParseDisplayTime, TestParseNumeric (+1 more)

### Community 14 - "NewsSignalDB"
Cohesion: 0.10
Nodes (16): create_news_signals_table(), Create the news_signals table if it does not already exist., NewsSignalDB, جدول trading_signals: سیگنال‌های تولید شده از nlp_x.py. نسخه ارتقایافته با…, جدول news_signals: سیگنال‌های تولید شده از nlp_news.py. اکنون از سیگنال‌های…, TradingSignalDB, audit_news_data_metadata(), audit_news_scoring_math() (+8 more)

### Community 15 - "forex_factory_crawler.py"
Cohesion: 0.12
Nodes (27): _event_passes_filters(), fetch_forex_factory_events_debug(), _fetch_raw_payload(), ForexFactoryFetchSummary, _format_filters_for_summary(), _increment_reason(), _normalize_currency(), _normalize_currency_filter() (+19 more)

### Community 16 - "test_forex_factory_crawler.py"
Cohesion: 0.13
Nodes (26): ذخیره یا آپدیت داده‌های لیست دیکشنری در دیتابیس پروژه., save_events_to_db(), _build_event_from_raw(), _map_title_to_category(), _parse_event_datetime(), _parse_numeric(), Parse Forex Factory numeric strings into float. Examples: "3.8%" -> 3.8 "185K"…, Parse event datetime from API value. Supports: - datetime object - ISO strings… (+18 more)

### Community 17 - "_canonicalize_event_title"
Cohesion: 0.14
Nodes (6): _canonicalize_event_title(), تبدیل title به canonical event key. مثال: "Non-Farm Employment Change", "USD" →…, بدون currency، باید بدون country prefix بسازد., productivity نباید با NFP اشتباه شود., EUR with country override., TestCanonicalization

### Community 18 - "EventAnalysisInput"
Cohesion: 0.14
Nodes (15): build_event_brief(), _build_prompt_inputs(), EventSignalRepository, _format_optional(), Any, ساخت یک brief deterministic از event برای ورودی LLM. این متن: - تفسیر ندارد…, ساخت dict ورودی template., ذخیره‌سازی EventSignal در جدول event_signals. (+7 more)

### Community 19 - "nlp_x.py"
Cohesion: 0.13
Nodes (15): _build_prompt_inputs(), _clamp(), compute_alignment_factor(), compute_data_completeness(), compute_data_quality_factor(), _format_optional(), generate_signal(), Any (+7 more)

### Community 20 - "SpeakerTextItem"
Cohesion: 0.08
Nodes (9): field_validator, ورودی استاندارد برای تحلیل متن مبتنی بر سخنران. هم tweet/X post و هم…, خروجی ساختاریافته LLM برای تحلیل متن سخنران. تفاوت‌ها با NewsInterpretation: -…, SpeakerInterpretation, SpeakerTextItem, TestConfidence, TestFinalScore, TestHalfLife (+1 more)

### Community 21 - "scrape_incremental"
Cohesion: 0.16
Nodes (19): ensure_csv_header(), merge_new_data(), normalize_calendar_frame(), DataFrame, Merge new data into the existing DataFrame. For each record in new_df: - If the…, Ensure that the CSV file exists with the proper header., Read the existing CSV data and return a DataFrame with the defined columns., Write final merged data to CSV, overwriting it. (+11 more)

### Community 22 - "ForexFactoryEvent"
Cohesion: 0.13
Nodes (9): ForexFactoryEvent, field_validator, Normalized Forex Factory calendar event., تشخیص اینکه آیا رکورد موجود باید update شود یا نه. Update لازم است اگر: 1.…, Batch save: لیستی از eventها را ذخیره/update/skip می‌کند. از یک session مشترک…, save_events(), _should_update(), TestSaveEvents (+1 more)

### Community 23 - "NewsItem"
Cohesion: 0.13
Nodes (6): news_item_from_feedparser(), Convert a raw feedparser entry dict into a validated NewsItem. feedparser…, NewsItem, نمایش استاندارد یک خبر RSS. فیلدها از feedparser یا Forex Factory API پر…, TestNewsItem, TestMalformedRSSInput

### Community 24 - "conftest.py"
Cohesion: 0.12
Nodes (23): db_session(), mock_page(), mock_scraper(), EventSchema, fixture, Path, Session, Pytest fixtures for Forex Factory scraper tests. (+15 more)

### Community 25 - "e2e_speaker_pipeline.py"
Cohesion: 0.19
Nodes (21): AnalyzedSpeakerItem, build_llm(), compute_dedup_hash(), EnrichedSpeakerItem, is_already_persisted(), LLMProvider, log_section(), parse_args() (+13 more)

### Community 26 - "translate_instrument_direction"
Cohesion: 0.14
Nodes (19): EventSignalDB, جدول event_signals: سیگنال‌های تولید شده از nlp_event.py. این جدول مخصوص تحلیل…, Enum, str, Translate a currency-native direction into instrument-view direction. This is…, Alignment between currency-native direction and instrument-view direction.…, RouteAlignment, translate_instrument_direction() (+11 more)

### Community 27 - "test_nlp_news.py"
Cohesion: 0.13
Nodes (18): NewsSignalScorer, Pure deterministic scoring engine for news-based signals. Converts a (NewsItem,…, empty_market_context(), full_market_context(), inline_news_item(), neutral_interpretation(), partial_market_context(), fixture (+10 more)

### Community 28 - "resolve_asset_route"
Cohesion: 0.11
Nodes (18): ExecutionPlan, get_quick_trend(), has_high_impact_events(), BaseModel, agents/supervisor/head_agent.py ================================ The Head Agent…, Assesses market and returns the execution plan., The execution plan issued by the Head Agent., A fast check of EMA 50/200 and ADX to determine trend regime. (+10 more)

### Community 29 - "EventInterpretation"
Cohesion: 0.16
Nodes (7): EventInterpretation, خروجی ساختاریافته LLM برای تحلیل بیانیه/توییت. قوانین: - direction باید با…, سیگنال نهایی تولید شده توسط scoring engine برای بیانیه/توییت. از…, Signal, TestEventInterpretation, TestSignal, TestBackwardCompatibility

### Community 30 - "test_nlp_x.py"
Cohesion: 0.16
Nodes (13): _resolve_source_reliability(), empty_market_context(), full_market_context(), generic_speaker(), generic_tweet_item(), hawkish_interpretation(), neutral_interpretation(), powell_item() (+5 more)

### Community 31 - "EventHistoryDB"
Cohesion: 0.15
Nodes (12): analyze_currency_distribution(), analyze_general_stats(), analyze_key_events(), analyze_recommendations(), analyze_time_distribution(), analyze_top_events(), analyze_window_comparison(), EventHistoryDB (+4 more)

### Community 32 - "main.py"
Cohesion: 0.16
Nodes (13): build_parser(), _build_provider(), main(), parse_args(), _resolve_dates(), scrape_range_with_details(), ForexFactoryHtmlProvider, یک تست انتها به انتها که یک بازه کوچک را اسکرپ می‌کند و بررسی می‌کند آیا فایل… (+5 more)

### Community 33 - "rss_feed_loader.py"
Cohesion: 0.17
Nodes (15): _contains_macro_keyword(), _detect_currency(), FeedNormalizer, _keyword_in_text(), _parse_entry_datetime(), Any, datetime, حذف HTML tags و normalize whitespace. (+7 more)

### Community 34 - "TestEventSchema"
Cohesion: 0.10
Nodes (5): Tests for Pydantic models., TestEventDetailSchema, TestEventSchema, TestQueryFilters, TestScrapeRunSchema

### Community 35 - "analyze_economic_event"
Cohesion: 0.17
Nodes (11): analyze_economic_event(), build_event_prompt(), EventAnalysisService, BaseChatModel, ChatPromptTemplate, ChatPromptTemplate برای تحلیل event., سرویس تحلیل LLM برای event اقتصادی., تابع orchestration اصلی: event → interpretation → signal → DB. Returns:… (+3 more)

### Community 36 - ".score"
Cohesion: 0.15
Nodes (9): _clamp(), compute_data_completeness(), compute_data_quality_factor(), Clamp a float value between low and high (inclusive)., Calculate data completeness score from MarketContext. Checks the nine key…, Calculate data quality factor from completeness score. Formula: 0.5 + (0.5 *…, Enforce that surprise_factor and expected_volatility match MarketContext. The…, Compute and return a NewsSignal from interpretation + context. All intermediate… (+1 more)

### Community 37 - "NewsPipelineRunResult"
Cohesion: 0.11
Nodes (8): FailureRecord, NewsPipelineRunResult, Exception, A single skip event with stage, item, and reason., A single failure event with stage, item, error, and traceback., Complete state of one pipeline run. Contains all inputs, intermediate results,…, Mark the run as finished., SkipRecord

### Community 38 - "test_nlp_event.py"
Cohesion: 0.18
Nodes (15): اعتبارسنجی ورودی قبل از تحلیل. Returns: (is_valid, rejection_reason), _validate_for_analysis(), cpi_event_input(), cpi_interpretation(), fallback_std_result(), low_impact_event_input(), market_context_full(), neutral_interpretation() (+7 more)

### Community 40 - "http.py"
Cohesion: 0.29
Nodes (14): detect_page_issue(), normalize_text(), body_text(), calendar_rows_present(), classify_http_calendar(), dump_http_debug(), extract_title(), http_debug_artifact_paths() (+6 more)

### Community 41 - "FeedLoader"
Cohesion: 0.12
Nodes (14): بارگذاری RSS feeds و ذخیره اخبار جدید در دیتابیس خام. این مرحله مستقل از LLM…, run_pipeline(), stage_load_feeds(), Base, CompositeSignalDB, insert_raw_news_item_if_new(), جدول raw_news_items: ذخیره اخبار خام کرال شده برای جداسازی زمان کرال و تحلیل.…, جدول composite_signals: سیگنال‌های ترکیبی فاز ۲. این جدول سیگنال‌های ترکیبی… (+6 more)

### Community 42 - "NewsAnalysisService"
Cohesion: 0.23
Nodes (7): analyze_news_item(), NewsAnalysisService, Calls the configured LLM to produce a NewsInterpretation. Accepts a pre-…, Top-level orchestration function: news item → interpretation → signal → DB.…, LLM returns wrong surprise_factor — service should correct it., TestFullPipelineIntegration, TestNewsAnalysisService

### Community 43 - "analyze_speaker_text"
Cohesion: 0.18
Nodes (10): analyze_speaker_text(), build_speaker_prompt(), BaseChatModel, ChatPromptTemplate, ChatPromptTemplate برای تحلیل متن سخنران., سرویس تحلیل LLM برای متن سخنران. LLM را فراخوانی می‌کند و SpeakerInterpretation…, تابع orchestration اصلی: متن سخنران → تفسیر → سیگنال → DB. برای تبدیل به…, SpeakerAnalysisService (+2 more)

### Community 44 - "Speaker"
Cohesion: 0.19
Nodes (8): سخنران را resolve می‌کند: 1. اگر weight_override داده شده → Speaker از DB +…, _resolve_speaker(), اطلاعات یک سخنران تأثیرگذار بازار (رئیس بانک مرکزی، وزیر مالیه، ...). weight…, بارگذاری Speaker از دیتابیس بر اساس نام. اگر پیدا نشد، یک Speaker پیش‌فرض با…, رشته خلاصه پروفایل برای ارسال به LLM., Speaker, TestSpeaker, TestSpeakerLookup

### Community 45 - "direction_label"
Cohesion: 0.21
Nodes (16): direction_label(), Convert a numeric direction to a human-readable label. Args: direction:…, badge(), decision_badge(), direction_badge(), ui/components.py ================ Reusable Streamlit render helpers: status…, Card for one currency from a live AnalysisResult., Card for one currency from the DB snapshot (no live run). (+8 more)

### Community 46 - "test_providers.py"
Cohesion: 0.16
Nodes (11): EconomicCalendarProvider, datetime, datetime, SavedHtmlProvider, test_csv_export_parsing(), test_export_link_discovery(), test_ics_export_parsing(), test_json_export_parsing() (+3 more)

### Community 47 - "ForexFactoryExportProvider"
Cohesion: 0.22
Nodes (7): ProviderError, _desc_url(), _desc_value(), ForexFactoryExportProvider, datetime, _unfold_ics(), RuntimeError

### Community 48 - "historical_events_importer.py"
Cohesion: 0.21
Nodes (16): _apply_update(), _create_new(), _find_existing(), _normalize_datetime_for_db(), ParsedRow, datetime, Session, تبدیل datetime to naive UTC برای ذخیره در SQLite. (+8 more)

### Community 49 - "SpeakerPipelineRunResult"
Cohesion: 0.12
Nodes (8): _audit_default(), FailureRecord, Any, Exception, JSON serializer for complex objects., save_audit_artifact(), SkipRecord, SpeakerPipelineRunResult

### Community 50 - "_add_missing_columns"
Cohesion: 0.13
Nodes (16): _add_missing_columns(), _create_index_if_not_exists(), _get_existing_columns(), migrate_additional_indexes(), migrate_economic_events_table(), migrate_news_signals_table(), migrate_trading_signals_table(), Get set of existing column names for a SQLite table. (+8 more)

### Community 51 - "FeedConfig"
Cohesion: 0.19
Nodes (15): FeedConfig, FeedTier, get_enabled_feeds(), get_feed_by_name(), get_feeds_by_tier(), list_feed_names(), Enum, str (+7 more)

### Community 52 - "Session"
Cohesion: 0.25
Nodes (5): EventSchema, Session, Tests for repository layer., TestEventRepository, TestScrapeRunRepository

### Community 53 - "app.py"
Cohesion: 0.17
Nodes (10): ui/app.py ========= Streamlit entry point for the multi-agent forex analysis…, candlestick_with_smc(), fetch_ohlcv(), DataFrame, ui/charts.py ============ Plotly chart builders for the Streamlit UI (display-…, Grouped bar chart of trade outcomes per currency., Fetch OHLCV history for charting. Returns None on failure., Candlestick chart with OB lines and FVG bands from TechnicalMetrics. `metrics`… (+2 more)

### Community 54 - "fetch_forex_factory_events"
Cohesion: 0.20
Nodes (14): fetch_forex_factory_events(), Backward-compatible helper that returns only the list of events. Use…, MockSession, Exception, test_fetch_all_events_no_filters(), test_fetch_datetime_range(), test_fetch_invalid_item_count(), test_fetch_released_only() (+6 more)

### Community 55 - "_resolve_event_category_weight"
Cohesion: 0.22
Nodes (5): Return source reliability score. Priority: 1. Explicit value provided by caller…, Resolve event category and its importance weight. Returns…, _resolve_event_category_weight(), _resolve_source_reliability(), TestEventCategoryWeight

### Community 56 - "NewsSignalRepository"
Cohesion: 0.23
Nodes (6): NewsSignalRepository, Handles persistence of NewsSignal objects to the news_signals SQLite table.…, Serialize NewsItem to JSON string for raw payload storage., Serialize MarketContext to JSON string for raw payload storage., Persist a NewsSignal to the database. Args: news_item: The original news input.…, TestNewsSignalRepository

### Community 57 - "_parse_row"
Cohesion: 0.23
Nodes (6): _normalize_currency(), _parse_row(), Any, تبدیل یک ردیف CSV به ParsedRow. Raises: ValueError: اگر فیلدهای اجباری ناقص…, TestNormalizeCurrency, TestParseRow

### Community 58 - "_parse_numeric"
Cohesion: 0.27
Nodes (3): _parse_numeric(), Parse عددی شبیه crawler. مثال: "3.8%" → 3.8 "185K" → 185000 "-40K" → -40000…, TestParseNumeric

### Community 59 - "TestCalendarRowParser"
Cohesion: 0.15
Nodes (3): Tests for calendar row parser., TestCalendarPageParser, TestCalendarRowParser

### Community 60 - "alignment_label"
Cohesion: 0.21
Nodes (11): configure_logging(), main(), print_report(), Configure root logger and quiet noisy third-party loggers., Print a structured summary report of the pipeline run., configure_logging(), main(), print_report() (+3 more)

### Community 61 - "_resolve_event_category"
Cohesion: 0.27
Nodes (4): تشخیص category از روی input یا title. برای half-life و logging استفاده می‌شود., _resolve_event_category(), مهم: 'Federal Funds Rate' باید match کند نه 'fed funds'., TestCategoryResolution

### Community 62 - "fetch_and_analyze_news_node"
Cohesion: 0.17
Nodes (12): fetch_and_analyze_event_node(), fetch_and_analyze_news_node(), fetch_latest_speaker_signal_node(), generate_final_summary_node(), _news_recency_key(), PipelineState, Scrapes recent events via ff_bridge, reads ALL High/Medium events from DB,…, کلید مرتب‌سازی اخبار: (زمان انتشار، reliability منبع). برای انتخاب تازه‌ترین… (+4 more)

### Community 63 - "test_historical_events_importer.py"
Cohesion: 0.21
Nodes (10): HistoricalImportResult, print_import_summary(), BaseModel, نمایش summary در terminal., fixture, دیتابیس in-memory برای تست., فایل CSV نمونه با چند ردیف., sample_csv() (+2 more)

### Community 64 - "FeedFetcher"
Cohesion: 0.18
Nodes (7): FeedFetcher, FeedLoadStats, Wrapper روی feedparser با timeout واقعی + retry. استراتژی: - تلاش اول با…, یک feed را fetch می‌کند و parsed object برمی‌گرداند. در خطا یا timeout (پس از…, تلاش برای دانلود یک URL. برمی‌گرداند: - bytes در صورت موفقیت - None در صورت…, یک feed را load می‌کند و stats برمی‌گرداند., آمار بارگذاری یک feed.

### Community 65 - "_category_from_canonical_key"
Cohesion: 0.31
Nodes (3): _category_from_canonical_key(), استخراج category از canonical key. برای fallback به CATEGORY_DEFAULT_STD…, TestCategoryFromCanonical

### Community 66 - "SpeakerSignalRepository"
Cohesion: 0.27
Nodes (5): ذخیره‌سازی SpeakerSignal در جدول trading_signals., SpeakerSignalRepository, سیگنال نهایی تولید شده توسط SpeakerSignalScorer. تفاوت‌ها با NewsSignal: -…, SpeakerSignal, TestSpeakerSignalRepository

### Community 67 - "build_url_for_partial_range"
Cohesion: 0.31
Nodes (6): build_url_for_full_month(), build_url_for_partial_range(), datetime, Builds a param like: ?month=jan.2025, Builds a ForexFactory calendar param of the form: ?range=dec20.2024-dec30.2024, TestUrlBuilders

### Community 68 - "forexfactory_html.py"
Cohesion: 0.38
Nodes (9): normalize_events(), _ff_date(), iter_weeks(), parse_calendar_html(), _parse_day_text(), datetime, discover_export_links(), fetch_url() (+1 more)

### Community 69 - "save_event"
Cohesion: 0.24
Nodes (7): _apply_update(), _find_existing_event(), جستجوی duplicate بر اساس (title + currency + date). اگر date نداشته باشیم، فقط…, فیلدهای قابل تغییر رکورد موجود را update می‌کند., یک event را در دیتابیس ذخیره می‌کند. Returns: "inserted" — رکورد جدید ساخته شد…, save_event(), TestSaveEvent

### Community 70 - "FeedLoadResult"
Cohesion: 0.22
Nodes (8): FeedLoadResult, load_all_news(), _print_summary(), تمام feedهای فعال را بارگذاری می‌کند., یک لیست feed مشخص را بارگذاری می‌کند., نسخه‌ای از load_all که items را هم در result جمع می‌کند. (تابع load_all خود این…, یک shortcut برای استفاده مستقیم بدون نیاز به ساخت loader., نتیجه نهایی بارگذاری همه feedها.

### Community 71 - "MarketContext"
Cohesion: 0.31
Nodes (5): MarketContext, داده‌های کمّی بازار که از data_fetcher.py ساخته می‌شود. شامل ۹ فیلد کلیدی + ۲…, expected_volatility باید به calculated_volatility تبدیل شود., فیلدهای اضافی باید بدون خطا نادیده گرفته شوند., TestMarketContext

### Community 73 - "FeedEnricher"
Cohesion: 0.25
Nodes (5): FeedEnricher, اگر summary یک NewsItem ضعیف باشد و feed نیاز به enrichment داشته باشد، body را…, تشخیص اینکه آیا این item نیاز به enrichment دارد., body را از link استخراج می‌کند. اگر موفق نشد، همان item اصلی را برمی‌گرداند., تلاش برای استخراج main article body با heuristic ساده.

### Community 74 - ".normalize_legacy_keys"
Cohesion: 0.29
Nodes (4): Any, model_validator, سازگاری با کدهای قدیمی که از expected_volatility به جای calculated_volatility…, direction باید با علامت nlp_sentiment_score مطابقت داشته باشد.

### Community 75 - "NewsSignal"
Cohesion: 0.15
Nodes (12): analyze_news_digest(), NewsDigestInterpretation, NewsDigestScorer, BaseModel, model_validator, موتور امتیازدهی قطعی برای سیگنال‌های تجمیعی., Orchestration برای تحلیل تجمیعی اخبار یک ارز., خروجی تحلیل LLM برای یک Digest (تجمیع چند خبر یک ارز). (+4 more)

### Community 76 - "import_historical_csv"
Cohesion: 0.39
Nodes (3): import_historical_csv(), وارد کردن دیتاست تاریخی از CSV به DB. Args: csv_path: مسیر فایل CSV…, TestImportHistoricalCSV

### Community 78 - "_parse_csv_datetime"
Cohesion: 0.39
Nodes (3): _parse_csv_datetime(), Parse CSV datetime با فرمت‌های مختلف. مثال فرمت دیتاست:…, TestParseDatetime

### Community 79 - "DuplicateDetector"
Cohesion: 0.25
Nodes (4): DuplicateDetector, _hash_for_dedup(), تولید یک hash برای duplicate detection., تشخیص duplicate در حافظه بر اساس hash از link/title/source.

### Community 81 - "_resolve_event_impact_weight"
Cohesion: 0.43
Nodes (3): تبدیل impact label به weight عددی., _resolve_event_impact_weight(), TestImpactWeight

### Community 83 - "_clean_detail_text"
Cohesion: 0.43
Nodes (3): _clean_detail_text(), تمیز کردن detail text. خط‌های اضافی را حذف می‌کند ولی ساختار اصلی را حفظ می‌کند., TestCleanDetailText

### Community 87 - "ff_bridge.py"
Cohesion: 0.50
Nodes (4): fetch_and_store_ff_data(), datetime, ff_bridge.py ============ اسکریپت پل برای اتصال ریپازیتوری forexfactory-scraper…, اجرای اسکرپر و ذخیره در دیتابیس. توجه: این پروایدر از urllib استفاده می‌کند و…

### Community 88 - "UILogHandler"
Cohesion: 0.40
Nodes (3): LogRecord, Temporary logging handler that forwards backend log records to the UI. Attached…, UILogHandler

### Community 89 - "db_session"
Cohesion: 0.40
Nodes (5): db_session(), fixture, In-memory SQLite for testing., sample_event(), sample_event_no_actual()

## Knowledge Gaps
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ForexFactoryHtmlProvider` connect `main.py` to `forexfactory_html.py`, `test_providers.py`, `ForexFactoryExportProvider`, `_CalendarTableParser`, `scrape_incremental`, `ff_bridge.py`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Why does `EventHistoryDB` connect `EventHistoryDB` to `e2e_pipeline.py`, `phase2_graph.py`, `save_event`, `database.py`, `FeedLoader`, `import_historical_csv`, `NewsSignalDB`, `forex_factory_crawler.py`, `test_forex_factory_crawler.py`, `historical_events_importer.py`, `ForexFactoryEvent`, `ff_bridge.py`, `resolve_asset_route`, `fetch_and_analyze_news_node`, `test_historical_events_importer.py`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Why does `ProviderError` connect `ForexFactoryExportProvider` to `main.py`, `forexfactory_html.py`, `http.py`, `test_providers.py`, `ff_bridge.py`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 33 inferred relationships involving `MarketContext` (e.g. with `fetch_market_context()` and `_log_context_summary()`) actually correct?**
  _`MarketContext` has 33 INFERRED edges - model-reasoned connections that need verification._
- **Are the 31 inferred relationships involving `NewsItem` (e.g. with `compute_dedup_key()` and `EnrichedCurrencyGroup`) actually correct?**
  _`NewsItem` has 31 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `EventHistoryDB` (e.g. with `load_db_records()` and `fetch_and_analyze_event_node()`) actually correct?**
  _`EventHistoryDB` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `Speaker` (e.g. with `AnalyzedSpeakerItem` and `EnrichedSpeakerItem`) actually correct?**
  _`Speaker` has 18 INFERRED edges - model-reasoned connections that need verification._