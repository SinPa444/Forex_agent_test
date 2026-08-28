# models.py

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# =====================================================================
# ۱. MarketContext — داده‌های کمّی بازار
# =====================================================================


class MarketContext(BaseModel):
    """
    داده‌های کمّی بازار که از data_fetcher.py ساخته می‌شود.

    شامل ۹ فیلد کلیدی + ۲ فیلد محاسباتی:
      - actual/forecast/previous: از Forex Factory
      - historical_std: از تاریخچه سورپرایزها در DB
      - historical_volatility: از بازده لگاریتمی (yfinance)
      - atr_current/atr_baseline: از ATR 3 و 14 روزه (yfinance)
      - implied_volatility: از شاخص‌های VIX/EVZ/GVZ/OVX (yfinance)
      - yield_spread: از بازده اوراق قرضه (yfinance)
      - calculated_surprise: z-score نرمالایز شده (محاسباتی)
      - calculated_volatility: ضریب نوسان ترکیبی (محاسباتی)

    data_completeness_score در nlp_news.py بر اساس ۹ فیلد اول
    محاسبه می‌شود.
    """

    target_asset: str = Field(
        ...,
        description="نماد دارایی هدف — مثل EURUSD=X یا DX-Y.NYB",
    )
    event_title: Optional[str] = Field(
        default=None,
        description="عنوان رویداد اقتصادی — مثل US CPI YoY",
    )
    event_category: Optional[str] = Field(
        default=None,
        description="دسته‌بندی رویداد — مثل CPI, GDP, Employment",
    )

    # مقادیر اقتصادی (از Forex Factory)
    actual_value: Optional[float] = Field(default=None)
    forecast_value: Optional[float] = Field(default=None)
    previous_value: Optional[float] = Field(default=None)

    # نوسان تاریخی
    historical_std: Optional[float] = Field(
        default=None,
        description="انحراف معیار سورپرایزهای گذشته",
    )
    historical_volatility: Optional[float] = Field(
        default=None,
        description="نوسان تاریخی سالانه‌شده (log-return based)",
    )

    # ATR
    atr_current: Optional[float] = Field(
        default=None,
        description="ATR سه‌روزه — نوسان کوتاه‌مدت فعلی",
    )
    atr_baseline: Optional[float] = Field(
        default=None,
        description="ATR چهارده‌روزه — نوسان پایه/عادی",
    )

    # نوسان ضمنی و بازده
    implied_volatility: Optional[float] = Field(
        default=None,
        description="شاخص نوسان ضمنی (VIX, EVZ, GVZ, ...)",
    )
    yield_spread: Optional[float] = Field(
        default=None,
        description="اسپرد بازده اوراق (10Y - 2Y)",
    )

    # نرخ‌های بهره (اختیاری — برای تحلیل‌های پیشرفته)
    base_rate: Optional[float] = Field(
        default=None,
        description="نرخ بهره بانک مرکزی ارز پایه",
    )
    quote_rate: Optional[float] = Field(
        default=None,
        description="نرخ بهره بانک مرکزی ارز مظنه",
    )

    # مقادیر محاسباتی (data_fetcher.py محاسبه می‌کند)
    calculated_surprise: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="فاکتور سورپرایز نرمالایز شده (0=بدون سورپرایز, 1=حداکثر)",
    )
    calculated_volatility: float = Field(
        default=1.0,
        ge=0.0,
        description="ضریب نوسان ترکیبی (1.0=عادی, >1.5=بالا, <0.5=پایین)",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_keys(cls, data: Any) -> Any:
        """
        سازگاری با کدهای قدیمی که از expected_volatility
        به جای calculated_volatility استفاده می‌کنند.
        """
        if isinstance(data, dict):
            data = dict(data)
            if "calculated_volatility" not in data and "expected_volatility" in data:
                data["calculated_volatility"] = data.pop("expected_volatility")
        return data

    model_config = {"extra": "ignore"}


# =====================================================================
# ۲. Speaker — اطلاعات سخنرانان (برای nlp_x.py)
# =====================================================================


class Speaker(BaseModel):
    """
    اطلاعات یک سخنران تأثیرگذار بازار (رئیس بانک مرکزی، وزیر مالیه، ...).

    weight مشخص می‌کند حرف‌های این شخص چقدر بازار را تکان می‌دهد:
      1.0 = رئیس فدرال رزرو
      0.8 = رئیس ECB
      0.5 = ناشناس / عمومی
    """

    name: str = Field(
        ...,
        min_length=1,
        description="نام کامل سخنران",
    )
    role: str = Field(
        default="Unknown / General Market Commentator",
        description="عنوان رسمی — مثل Fed Chair, ECB President",
    )
    primarily_impacts: str = Field(
        default="Unknown",
        description="دارایی اصلی تحت تأثیر — مثل USD, EUR, OIL",
    )
    weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="وزن تأثیر بازار (0.0 تا 1.0)",
    )

    @classmethod
    def from_name(cls, name: str) -> Speaker:
        """
        بارگذاری Speaker از دیتابیس بر اساس نام.
        اگر پیدا نشد، یک Speaker پیش‌فرض با weight=0.5 برمی‌گرداند.
        """
        from core.database import SessionLocal, SpeakerDB

        db = SessionLocal()
        try:
            record = (
                db.query(SpeakerDB).filter(SpeakerDB.name.ilike(f"%{name}%")).first()
            )

            if record:
                return cls(
                    name=record.name,
                    role=record.role,
                    primarily_impacts=record.primary_asset,
                    weight=record.weight,
                )
            else:
                return cls(
                    name=name,
                    role="Unknown / General Market Commentator",
                    primarily_impacts="Unknown",
                    weight=0.5,
                )
        finally:
            db.close()

    @property
    def profile_string(self) -> str:
        """رشته خلاصه پروفایل برای ارسال به LLM."""
        return (
            f"Name: {self.name}, "
            f"Role: {self.role}, "
            f"Primarily Impacts: {self.primarily_impacts}, "
            f"Weight: {self.weight}"
        )


# =====================================================================
# ۳. EventInterpretation — خروجی LLM برای توییت/بیانیه (nlp_x)
# =====================================================================


class EventInterpretation(BaseModel):
    """
    خروجی ساختاریافته LLM برای تحلیل بیانیه/توییت.

    قوانین:
      - direction باید با علامت nlp_sentiment_score مطابقت داشته باشد
      - surprise_factor باید دقیقاً برابر calculated_surprise باشد
      - expected_volatility باید دقیقاً برابر calculated_volatility باشد
    """

    reasoning: str = Field(
        ...,
        description="استدلال اقتصادی گام‌به‌گام",
    )
    asset_class: str = Field(
        ...,
        description="کلاس دارایی اصلی — مثل USD, EUR, XAU, OIL",
    )
    direction: int = Field(
        ...,
        description="جهت: 1=صعودی, -1=نزولی, 0=خنثی",
    )
    nlp_sentiment_score: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="امتیاز احساسات (-1.0 تا +1.0)",
    )
    surprise_factor: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="باید دقیقاً برابر calculated_surprise باشد",
    )
    expected_volatility: float = Field(
        ...,
        ge=0.0,
        description="باید دقیقاً برابر calculated_volatility باشد",
    )
    cross_assets: Dict[str, int] = Field(
        default_factory=dict,
        description="تأثیر متقاطع — مثل {'XAU': -1, 'SPX': -1}",
    )

    @field_validator("direction")
    @classmethod
    def direction_must_be_valid(cls, v: int) -> int:
        if v not in (-1, 0, 1):
            raise ValueError(f"direction باید -1, 0 یا 1 باشد؛ مقدار {v} نامعتبر است")
        return v


# =====================================================================
# ۴. Signal — سیگنال نهایی توییت/بیانیه (nlp_x)
# =====================================================================


class Signal(BaseModel):
    """
    سیگنال نهایی تولید شده توسط scoring engine برای بیانیه/توییت.

    از EventInterpretation + speaker_weight محاسبه می‌شود.
    """

    reasoning: str
    asset_class: str
    direction: int
    final_score: float = Field(..., ge=-1.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_tradable: bool
    expected_volatility_level: str  # "Low", "Normal", "High"
    signal_half_life_mins: int = Field(..., ge=1)
    cross_asset_signals: Dict[str, float] = Field(default_factory=dict)

    @field_validator("expected_volatility_level")
    @classmethod
    def vol_level_valid(cls, v: str) -> str:
        allowed = {"Low", "Normal", "High"}
        if v not in allowed:
            raise ValueError(f"باید یکی از {allowed} باشد")
        return v


# =====================================================================
# ۵. NewsItem — ورودی خبر RSS (برای nlp_news.py)
# =====================================================================


class NewsItem(BaseModel):
    """
    نمایش استاندارد یک خبر RSS.

    فیلدها از feedparser یا Forex Factory API پر می‌شوند.
    source_reliability اگر None باشد، از نگاشت پیش‌فرض استفاده می‌شود.
    """

    title: str = Field(
        ...,
        min_length=1,
        description="عنوان خبر",
    )
    summary: str = Field(
        default="",
        description="متن خلاصه / بدنه خبر",
    )
    published: Optional[datetime] = Field(
        default=None,
        description="publish time (UTC)",
    )
    link: Optional[str] = Field(
        default=None,
        description="source link",
    )
    source: str = Field(
        default="Unknown",
        description="source name like: ForexLive, DailyFX, Forex Factory",
    )
    source_reliability: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="امتیاز اعتبار منبع (0.0-1.0). None = از نگاشت پیش‌فرض",
    )
    category: Optional[str] = Field(
        default=None,
        description="دسته‌بندی رویداد — مثل CPI, GDP",
    )
    currency: Optional[str] = Field(
        default=None,
        description="ارز اصلی متأثر — مثل USD, EUR",
    )
    impact: Optional[str] = Field(
        default=None,
        description="سطح تأثیر — مثل High, Medium, Low",
    )

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("عنوان نمی‌تواند خالی باشد")
        return stripped

    @field_validator("summary")
    @classmethod
    def clean_summary(cls, v: str) -> str:
        return v.strip()

    model_config = {"str_strip_whitespace": True}


# =====================================================================
# ۶. NewsInterpretation — خروجی LLM برای تحلیل خبر (nlp_news)
# =====================================================================


class NewsInterpretation(BaseModel):
    """
    خروجی ساختاریافته LLM مخصوص تحلیل اخبار RSS.

    تفاوت‌ها با EventInterpretation:
      - بدون speaker — اخبار ویرایشی هستند
      - headline_body_alignment: تطابق عنوان و متن
      - quantitative_alignment: تطابق متن با داده‌های عددی
      - impact_horizon: افق زمانی تأثیر
      - detected_event_category: دسته‌بندی شناسایی شده از متن

    قوانین:
      - direction باید با علامت nlp_sentiment_score مطابقت داشته باشد
      - surprise_factor = market_context.calculated_surprise (بدون تغییر)
      - expected_volatility = market_context.calculated_volatility (بدون تغییر)
    """

    reasoning: str = Field(
        ...,
        description="استدلال اقتصادی گام‌به‌گام",
    )
    asset_class: str = Field(
        ...,
        description="کلاس دارایی اصلی — مثل FX, Commodity, Equity",
    )
    direction: int = Field(
        ...,
        description="جهت: +1 (صعودی), -1 (نزولی), 0 (خنثی)",
    )
    nlp_sentiment_score: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="امتیاز احساسات (-1.0 تا +1.0)",
    )
    surprise_factor: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="باید دقیقاً برابر calculated_surprise باشد",
    )
    expected_volatility: float = Field(
        ...,
        ge=0.0,
        description="باید دقیقاً برابر calculated_volatility باشد",
    )
    cross_assets: Dict[str, int] = Field(
        default_factory=dict,
        description="تأثیرات متقاطع — مثل {'XAU': -1, 'SPX': -1}",
    )
    detected_event_category: str = Field(
        ...,
        description="دسته‌بندی اقتصادی شناسایی شده از متن خبر",
    )
    headline_body_alignment: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="تطابق عنوان با بدنه خبر (0=تناقض, 1=کاملاً هماهنگ)",
    )
    quantitative_alignment: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="تطابق تفسیر متنی با داده‌های عددی",
    )
    impact_horizon: str = Field(
        ...,
        description="افق زمانی: immediate, intraday, multi-session",
    )

    @field_validator("direction")
    @classmethod
    def direction_must_be_valid(cls, v: int) -> int:
        if v not in (-1, 0, 1):
            raise ValueError(f"direction باید -1, 0 یا 1 باشد")
        return v

    @field_validator("impact_horizon")
    @classmethod
    def impact_horizon_valid(cls, v: str) -> str:
        allowed = {"immediate", "intraday", "multi-session"}
        if v.lower() not in allowed:
            raise ValueError(f"impact_horizon باید یکی از {allowed} باشد")
        return v.lower()

    @field_validator("cross_assets")
    @classmethod
    def cross_assets_valid(cls, v: Dict[str, int]) -> Dict[str, int]:
        for asset, direction in v.items():
            if direction not in (-1, 0, 1):
                raise ValueError(f"cross_assets[{asset!r}] باید -1, 0 یا 1 باشد")
        return v

    @model_validator(mode="after")
    def direction_matches_sentiment(self) -> NewsInterpretation:
        """direction باید با علامت nlp_sentiment_score مطابقت داشته باشد."""
        score = self.nlp_sentiment_score
        direction = self.direction
        tolerance = 0.05

        if score > tolerance and direction != 1:
            raise ValueError(f"sentiment مثبت ({score:.3f}) اما direction={direction}")
        if score < -tolerance and direction != -1:
            raise ValueError(f"sentiment منفی ({score:.3f}) اما direction={direction}")
        if abs(score) <= tolerance and direction != 0:
            raise ValueError(f"sentiment خنثی ({score:.3f}) اما direction={direction}")
        return self


# =====================================================================
# ۷. NewsSignal — سیگنال نهایی خبر (nlp_news)
# =====================================================================


class NewsSignal(BaseModel):
    """
    سیگنال نهایی تولید شده توسط NewsSignalScorer.

    تفاوت‌ها با Signal (توییت/بیانیه):
      - بدون speaker_weight — از event_category_weight استفاده می‌شود
      - source_reliability: اعتبار منبع خبری
      - data_completeness_score: کامل بودن داده‌های MarketContext
      - data_quality_factor: ضریب کیفیت داده
      - headline_body_alignment: تطابق عنوان/متن
      - quantitative_alignment: تطابق متن/داده
    """

    reasoning: str
    asset_class: str
    direction: int
    final_score: float = Field(..., ge=-1.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_tradable: bool
    expected_volatility_level: str  # "Low", "Normal", "High"
    signal_half_life_mins: int = Field(..., ge=1)
    cross_asset_signals: Dict[str, float] = Field(default_factory=dict)

    # فیلدهای اختصاصی خبر (بدون معادل در Signal)
    source: str
    source_reliability: float = Field(..., ge=0.0, le=1.0)
    event_category_weight: float = Field(..., ge=0.0, le=1.0)
    data_completeness_score: float = Field(..., ge=0.0, le=1.0)
    data_quality_factor: float = Field(..., ge=0.5, le=1.0)
    headline_body_alignment: float = Field(..., ge=0.0, le=1.0)
    quantitative_alignment: float = Field(..., ge=0.0, le=1.0)
    ticker: str

    @field_validator("expected_volatility_level")
    @classmethod
    def vol_level_valid(cls, v: str) -> str:
        allowed = {"Low", "Normal", "High"}
        if v not in allowed:
            raise ValueError(f"باید یکی از {allowed} باشد")
        return v


# =====================================================================
# ۸. SpeakerTextItem — ورودی متن سخنران (برای nlp_x.py)
# =====================================================================


class SpeakerTextItem(BaseModel):
    """
    ورودی استاندارد برای تحلیل متن مبتنی بر سخنران.

    هم tweet/X post و هم statement/speech/interview/testimony را پوشش می‌دهد.
    فیلدهای اجتماعی (retweet_count, ...) اختیاری هستند و فقط برای
    audit trail و آینده ذخیره می‌شوند — در scoring اثری ندارند.
    """

    # --- فیلدهای اصلی (الزامی) ---
    text: str = Field(
        ...,
        min_length=1,
        description="متن بیانیه / توییت / سخنرانی",
    )
    speaker_name: str = Field(
        ...,
        min_length=1,
        description="نام کامل سخنران — مثل Jerome Powell",
    )

    # --- متادیتای زمانی و منبع ---
    published: Optional[datetime] = Field(
        default=None,
        description="زمان انتشار (UTC)",
    )
    source: str = Field(
        default="Unknown",
        description="نام منبع — مثل X, Bloomberg, Reuters, Official Transcript",
    )
    source_type: str = Field(
        default="unknown",
        description=(
            "نوع بیانیه: tweet, headline_quote, interview, statement, "
            "speech, press_conference, testimony, official_transcript, unknown"
        ),
    )
    link: Optional[str] = Field(
        default=None,
        description="لینک منبع",
    )

    # --- override وزن سخنران ---
    speaker_weight_override: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="اگر تنظیم شود، وزن سخنران را override می‌کند",
    )

    # --- فیلدهای tweety (اختیاری) ---
    external_id: Optional[str] = Field(
        default=None,
        description="شناسه خارجی — مثل tweet_id",
    )
    author_username: Optional[str] = Field(
        default=None,
        description="نام کاربری نویسنده در پلتفرم",
    )
    author_display_name: Optional[str] = Field(
        default=None,
        description="نام نمایشی نویسنده",
    )
    retweet_count: Optional[int] = Field(default=None, ge=0)
    like_count: Optional[int] = Field(default=None, ge=0)
    reply_count: Optional[int] = Field(default=None, ge=0)
    is_quote: Optional[bool] = Field(default=None)
    is_reply: Optional[bool] = Field(default=None)

    # --- hint ها برای routing ---
    currency_hint: Optional[str] = Field(
        default=None,
        description="اشاره ارزی — مثل USD, EUR",
    )
    event_title_hint: Optional[str] = Field(
        default=None,
        description="اشاره رویداد — مثل FOMC, CPI",
    )

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("text نمی‌تواند خالی باشد")
        return stripped

    @field_validator("speaker_name")
    @classmethod
    def speaker_name_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("speaker_name نمی‌تواند خالی باشد")
        return stripped

    @field_validator("source_type")
    @classmethod
    def source_type_valid(cls, v: str) -> str:
        allowed = {
            "tweet",
            "headline_quote",
            "interview",
            "statement",
            "speech",
            "press_conference",
            "testimony",
            "official_transcript",
            "unknown",
        }
        if v.lower() not in allowed:
            raise ValueError(f"source_type باید یکی از {allowed} باشد")
        return v.lower()

    model_config = {"str_strip_whitespace": True}


# =====================================================================
# ۹. SpeakerInterpretation — خروجی LLM برای تحلیل سخنران (nlp_x)
# =====================================================================


class SpeakerInterpretation(BaseModel):
    """
    خروجی ساختاریافته LLM برای تحلیل متن سخنران.

    تفاوت‌ها با NewsInterpretation:
      - statement_market_alignment به جای headline_body_alignment
      - policy_signal_type: نوع سیگنال سیاستی
      - guidance_bias: تمایل سیاستی
      - is_reiteration: آیا تکرار حرف قبلی است
      - بدون detected_event_category (از سخنران مشخص می‌شود)

    قوانین:
      - direction باید با علامت nlp_sentiment_score مطابقت داشته باشد
      - surprise_factor = market_context.calculated_surprise (بدون تغییر)
      - expected_volatility = market_context.calculated_volatility (بدون تغییر)
    """

    reasoning: str = Field(
        ...,
        description="استدلال اقتصادی گام‌به‌گام",
    )
    asset_class: str = Field(
        ...,
        description="کلاس دارایی اصلی — مثل USD, EUR, XAU, OIL",
    )
    direction: int = Field(
        ...,
        description="جهت: +1 (صعودی), -1 (نزولی), 0 (خنثی)",
    )
    nlp_sentiment_score: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="امتیاز احساسات (-1.0 تا +1.0)",
    )
    surprise_factor: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="باید دقیقاً برابر calculated_surprise باشد",
    )
    expected_volatility: float = Field(
        ...,
        ge=0.0,
        description="باید دقیقاً برابر calculated_volatility باشد",
    )
    cross_assets: Dict[str, int] = Field(
        default_factory=dict,
        description="تأثیرات متقاطع — مثل {'XAU': -1, 'SPX': -1}",
    )

    # فیلدهای اختصاصی سخنران
    statement_market_alignment: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "تطابق بیانیه با وضعیت بازار. "
            "اگر پاول بگوید 'بازار کار قوی است' ولی داده‌ها ضعیف باشند، "
            "این مقدار پایین می‌آید."
        ),
    )
    quantitative_alignment: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="تطابق تفسیر متنی با داده‌های عددی MarketContext",
    )
    policy_signal_type: str = Field(
        ...,
        description=(
            "نوع سیگنال سیاستی: "
            "forward_guidance / data_reaction / policy_commitment / commentary"
        ),
    )
    impact_horizon: str = Field(
        ...,
        description="افق زمانی: immediate / intraday / multi-session",
    )
    detected_event_category: str = Field(
        ...,
        description="دسته‌بندی اقتصادی شناسایی شده از متن",
    )
    is_reiteration: bool = Field(
        ...,
        description="آیا این تکرار حرف قبلی است (True) یا اطلاعات جدید (False)",
    )
    guidance_bias: str = Field(
        ...,
        description=(
            "تمایل سیاستی: "
            "hawkish / dovish / neutral / risk_on / risk_off / "
            "inflationary / recessionary"
        ),
    )

    @field_validator("direction")
    @classmethod
    def direction_must_be_valid(cls, v: int) -> int:
        if v not in (-1, 0, 1):
            raise ValueError(f"direction باید -1, 0 یا 1 باشد")
        return v

    @field_validator("impact_horizon")
    @classmethod
    def impact_horizon_valid(cls, v: str) -> str:
        allowed = {"immediate", "intraday", "multi-session"}
        if v.lower() not in allowed:
            raise ValueError(f"impact_horizon باید یکی از {allowed} باشد")
        return v.lower()

    @field_validator("policy_signal_type")
    @classmethod
    def policy_signal_type_valid(cls, v: str) -> str:
        allowed = {
            "forward_guidance",
            "data_reaction",
            "policy_commitment",
            "commentary",
        }
        if v.lower() not in allowed:
            raise ValueError(f"policy_signal_type باید یکی از {allowed} باشد")
        return v.lower()

    @field_validator("guidance_bias")
    @classmethod
    def guidance_bias_valid(cls, v: str) -> str:
        allowed = {
            "hawkish",
            "dovish",
            "neutral",
            "risk_on",
            "risk_off",
            "inflationary",
            "recessionary",
        }
        if v.lower() not in allowed:
            raise ValueError(f"guidance_bias باید یکی از {allowed} باشد")
        return v.lower()

    @field_validator("cross_assets")
    @classmethod
    def cross_assets_valid(cls, v: Dict[str, int]) -> Dict[str, int]:
        for asset, direction in v.items():
            if direction not in (-1, 0, 1):
                raise ValueError(f"cross_assets[{asset!r}] باید -1, 0 یا 1 باشد")
        return v

    @model_validator(mode="after")
    def direction_matches_sentiment(self) -> SpeakerInterpretation:
        """direction باید با علامت nlp_sentiment_score مطابقت داشته باشد."""
        score = self.nlp_sentiment_score
        direction = self.direction
        tolerance = 0.05

        if score > tolerance and direction != 1:
            raise ValueError(f"sentiment مثبت ({score:.3f}) اما direction={direction}")
        if score < -tolerance and direction != -1:
            raise ValueError(f"sentiment منفی ({score:.3f}) اما direction={direction}")
        if abs(score) <= tolerance and direction != 0:
            raise ValueError(f"sentiment خنثی ({score:.3f}) اما direction={direction}")
        return self


# =====================================================================
# ۱۰. SpeakerSignal — سیگنال نهایی سخنران (nlp_x)
# =====================================================================


class SpeakerSignal(BaseModel):
    """
    سیگنال نهایی تولید شده توسط SpeakerSignalScorer.

    تفاوت‌ها با NewsSignal:
      - speaker_name, speaker_role, speaker_weight
      - statement_type_weight به جای event_category_weight
      - statement_market_alignment به جای headline_body_alignment
      - policy_signal_type, impact_horizon
    """

    reasoning: str
    asset_class: str
    direction: int
    final_score: float = Field(..., ge=-1.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_tradable: bool
    expected_volatility_level: str
    signal_half_life_mins: int = Field(..., ge=1)
    cross_asset_signals: Dict[str, float] = Field(default_factory=dict)

    # فیلدهای اختصاصی سخنران
    speaker_name: str
    speaker_role: str
    speaker_weight: float = Field(..., ge=0.0, le=1.0)
    source: str
    source_type: str
    source_reliability: float = Field(..., ge=0.0, le=1.0)
    statement_type_weight: float = Field(..., ge=0.0)
    data_completeness_score: float = Field(..., ge=0.0, le=1.0)
    data_quality_factor: float = Field(..., ge=0.6, le=1.0)
    statement_market_alignment: float = Field(..., ge=0.0, le=1.0)
    quantitative_alignment: float = Field(..., ge=0.0, le=1.0)
    ticker: str
    policy_signal_type: str
    impact_horizon: str

    @field_validator("expected_volatility_level")
    @classmethod
    def vol_level_valid(cls, v: str) -> str:
        allowed = {"Low", "Normal", "High"}
        if v not in allowed:
            raise ValueError(f"باید یکی از {allowed} باشد")
        return v


# =====================================================================
# ۱۱. EventAnalysisInput — ورودی تحلیل event اقتصادی (nlp_event.py)
# =====================================================================


class EventAnalysisInput(BaseModel):
    """
    ورودی استاندارد برای تحلیل event اقتصادی منتشرشده.

    می‌تواند از ForexFactoryEvent یا EventHistoryDB ساخته شود.
    actual الزامی است — فقط released events قابل تحلیل هستند.
    """

    title: str = Field(..., min_length=1, description="عنوان event، مثل 'CPI y/y'")
    currency: str = Field(..., min_length=1, description="ارز event، مثل 'USD'")
    impact: str = Field(
        default="Unknown", description="High / Medium / Low / Non-Economic / Unknown"
    )
    category: Optional[str] = Field(default=None, description="دسته‌بندی، مثل 'CPI'")
    event_date: Optional[datetime] = Field(default=None, description="زمان release")

    # مقادیر numeric (parsed)
    actual: float = Field(..., description="مقدار واقعی منتشرشده (الزامی)")
    forecast: Optional[float] = Field(default=None)
    previous: Optional[float] = Field(default=None)

    # نسخه‌های raw string (برای brief readable)
    raw_actual_str: Optional[str] = Field(default=None, description="مثل '3.8%'")
    raw_forecast_str: Optional[str] = Field(default=None)
    raw_previous_str: Optional[str] = Field(default=None)

    # متادیتای اختیاری
    detail_text: Optional[str] = Field(
        default=None,
        description="توضیحات تفصیلی event از historical CSV (Usual Effect، FF Notes، ...)",
    )
    source: str = Field(default="Forex Factory")
    external_id: Optional[str] = Field(default=None)

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("title نمی‌تواند خالی باشد")
        return stripped

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.strip().upper()

    model_config = {"str_strip_whitespace": True}


# =====================================================================
# ۱۲. EventInterpretation — خروجی LLM برای تحلیل event (nlp_event.py)
# =====================================================================


class EventInterpretationEvent(BaseModel):
    """
    خروجی ساختاریافته LLM برای تحلیل event.

    تفاوت با NewsInterpretation:
      - بدون headline_body_alignment (متن منبع نداریم)
      - با surprise_interpretation صریح (beat/miss/in_line)
      - با momentum_vs_previous (accelerating/decelerating/stable)
      - با economic_implication (hawkish/dovish/inflationary/...)
      - با is_consistent_with_event_type (sanity check)
    """

    reasoning: str = Field(..., description="استدلال اقتصادی")
    asset_class: str = Field(..., description="مثل 'USD', 'EUR'")
    direction: int = Field(..., description="+1 / -1 / 0")
    nlp_sentiment_score: float = Field(..., ge=-1.0, le=1.0)

    surprise_factor: float = Field(
        ..., ge=0.0, le=1.0, description="echo calculated_surprise"
    )
    expected_volatility: float = Field(
        ..., ge=0.0, description="echo calculated_volatility"
    )

    cross_assets: Dict[str, int] = Field(default_factory=dict)

    # event-specific
    surprise_interpretation: str = Field(..., description="beat / miss / in_line")
    momentum_vs_previous: str = Field(
        ..., description="accelerating / decelerating / stable / unknown"
    )
    quantitative_alignment: float = Field(..., ge=0.0, le=1.0)
    economic_implication: str = Field(
        ...,
        description=(
            "hawkish / dovish / neutral / risk_on / risk_off / "
            "inflationary / recessionary"
        ),
    )
    impact_horizon: str = Field(..., description="immediate / intraday / multi-session")
    is_consistent_with_event_type: bool = Field(
        ...,
        description="آیا تفسیر با قانون معمول event مطابقت دارد؟ (مثل NFP beat = bullish USD)",
    )

    @field_validator("direction")
    @classmethod
    def direction_valid(cls, v: int) -> int:
        if v not in (-1, 0, 1):
            raise ValueError(f"direction باید -1, 0 یا 1 باشد")
        return v

    @field_validator("surprise_interpretation")
    @classmethod
    def surprise_interp_valid(cls, v: str) -> str:
        allowed = {"beat", "miss", "in_line"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"surprise_interpretation باید یکی از {allowed} باشد")
        return v_lower

    @field_validator("momentum_vs_previous")
    @classmethod
    def momentum_valid(cls, v: str) -> str:
        allowed = {"accelerating", "decelerating", "stable", "unknown"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"momentum_vs_previous باید یکی از {allowed} باشد")
        return v_lower

    @field_validator("economic_implication")
    @classmethod
    def implication_valid(cls, v: str) -> str:
        synonym_map = {
            "growth": "risk_on",
            "expansion": "risk_on",
            "recovery": "risk_on",
            "strong_growth": "risk_on",
            "bullish_growth": "risk_on",
            "slowdown": "recessionary",
            "weakness": "recessionary",
            "contraction": "recessionary",
            "shrinking": "recessionary",
            "tightening": "hawkish",
            "easing": "dovish",
            "disinflationary": "dovish",
        }

        allowed = {
            "hawkish",
            "dovish",
            "neutral",
            "risk_on",
            "risk_off",
            "inflationary",
            "recessionary",
        }

        v_lower = v.lower().strip()
        v_lower = synonym_map.get(v_lower, v_lower)

        if v_lower not in allowed:
            raise ValueError(f"economic_implication باید یکی از {allowed} باشد")
        return v_lower

    @field_validator("impact_horizon")
    @classmethod
    def horizon_valid(cls, v: str) -> str:
        allowed = {"immediate", "intraday", "multi-session"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"impact_horizon باید یکی از {allowed} باشد")
        return v_lower

    @field_validator("cross_assets")
    @classmethod
    def cross_assets_valid(cls, v: Dict[str, int]) -> Dict[str, int]:
        for asset, direction in v.items():
            if direction not in (-1, 0, 1):
                raise ValueError(f"cross_assets[{asset!r}] باید -1, 0 یا 1 باشد")
        return v

    @model_validator(mode="after")
    def direction_matches_sentiment(self) -> EventInterpretationEvent:
        score = self.nlp_sentiment_score
        direction = self.direction
        tolerance = 0.05

        if score > tolerance and direction != 1:
            raise ValueError(f"sentiment مثبت ({score:.3f}) اما direction={direction}")
        if score < -tolerance and direction != -1:
            raise ValueError(f"sentiment منفی ({score:.3f}) اما direction={direction}")
        if abs(score) <= tolerance and direction != 0:
            raise ValueError(f"sentiment خنثی ({score:.3f}) اما direction={direction}")
        return self


# =====================================================================
# ۱۳. EventSignal — سیگنال نهایی event (nlp_event.py)
# =====================================================================


class EventSignal(BaseModel):
    """
    سیگنال نهایی event-driven.

    تفاوت با NewsSignal:
      - بدون source_reliability (همه از calendar)
      - با event_impact_weight (High > Medium > Low)
      - با historical_std_reliability tracking
      - با surprise_interpretation و momentum_vs_previous صریح
    """

    # هسته signal
    reasoning: str
    asset_class: str
    direction: int
    final_score: float = Field(..., ge=-1.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_tradable: bool
    expected_volatility_level: str
    signal_half_life_mins: int = Field(..., ge=1)
    cross_asset_signals: Dict[str, float] = Field(default_factory=dict)

    # event metadata
    event_title: str
    event_currency: str
    event_impact: str
    event_category: Optional[str] = None
    event_date: Optional[datetime] = None

    # values
    actual: float
    forecast: Optional[float] = None
    previous: Optional[float] = None
    surprise_value: Optional[float] = None

    # scoring breakdown
    event_impact_weight: float = Field(..., ge=0.0, le=1.0)
    data_quality_factor: float = Field(..., ge=0.6, le=1.0)
    quantitative_alignment: float = Field(..., ge=0.0, le=1.0)
    historical_std_used: float
    historical_std_source: str
    historical_std_reliable: bool

    # event interpretation
    surprise_interpretation: str
    momentum_vs_previous: str
    economic_implication: str
    impact_horizon: str

    ticker: str

    @field_validator("expected_volatility_level")
    @classmethod
    def vol_level_valid(cls, v: str) -> str:
        allowed = {"Low", "Normal", "High"}
        if v not in allowed:
            raise ValueError(f"باید یکی از {allowed} باشد")
        return v
