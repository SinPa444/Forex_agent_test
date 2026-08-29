# forex_factory_crawler.py
"""
Fetch and normalize economic calendar events from Forex Factory.

This module is focused on Phase 1 ingestion and normalization:
- fetch raw weekly calendar JSON
- validate response shape
- normalize fields
- infer event categories
- apply optional filters
- expose debug summary for troubleshooting

It does NOT persist to DB yet.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

FOREX_FACTORY_API = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DEFAULT_TIMEOUT_SECONDS = 15

IMPACT_NORMALIZATION_MAP: dict[str, str] = {
    "high": "High",
    "medium": "Medium",
    "moderate": "Medium",
    "low": "Low",
    "holiday": "Holiday",
    "non-economic": "Non-Economic",
    "none": "Non-Economic",
    "unknown": "Unknown",
}

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Interest Rate Decision": ["interest rate", "rate decision", "fed funds", "base rate"],
    "Non-Farm Payrolls": ["non-farm", "nfp", "nonfarm"],
    "CPI": ["cpi", "consumer price", "inflation rate"],
    "Inflation": ["inflation", "pce", "price index"],
    "GDP": ["gdp", "gross domestic"],
    "Employment": ["employment", "unemployment", "jobless", "jobs"],
    "Retail Sales": ["retail sales", "retail"],
    "PMI": ["pmi", "purchasing managers", "manufacturing index"],
    "Manufacturing": ["manufacturing", "industrial production", "factory"],
    "Trade Balance": ["trade balance", "current account", "trade deficit"],
    "Housing": ["housing", "building permits", "home sales"],
    "Consumer Confidence": ["consumer confidence", "consumer sentiment"],
    "Central Bank Commentary": ["fomc", "fed", "ecb", "boe", "minutes", "statement"],
}


class ForexFactoryEvent(BaseModel):
    """
    Normalized Forex Factory calendar event.
    """

    title: str = Field(..., min_length=1)
    currency: str = Field(..., min_length=1, description="Normalized currency/country code, e.g. USD, EUR")
    impact: str = Field(..., description="Normalized impact label, e.g. High, Medium, Low")
    actual: Optional[str] = Field(default=None)
    forecast: Optional[str] = Field(default=None)
    previous: Optional[str] = Field(default=None)
    date: Optional[datetime] = Field(default=None)
    category: str = Field(default="Unknown")
    raw_payload: Optional[dict[str, Any]] = Field(default=None)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must not be empty")
        return cleaned

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("currency must not be empty")
        return cleaned

    @field_validator("impact")
    @classmethod
    def validate_impact(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            return "Unknown"
        return cleaned

    @staticmethod
    def _has_meaningful_value(value: Optional[str]) -> bool:
        if value is None:
            return False
        return value.strip() not in ("", "N/A", "—", "-")

    @property
    def has_actual(self) -> bool:
        return self._has_meaningful_value(self.actual)

    @property
    def has_forecast(self) -> bool:
        return self._has_meaningful_value(self.forecast)

    @property
    def has_previous(self) -> bool:
        return self._has_meaningful_value(self.previous)

    def actual_float(self) -> Optional[float]:
        return _parse_numeric(self.actual)

    def forecast_float(self) -> Optional[float]:
        return _parse_numeric(self.forecast)

    def previous_float(self) -> Optional[float]:
        return _parse_numeric(self.previous)


class ForexFactoryFetchSummary(BaseModel):
    """
    Debug summary for a fetch run.
    """

    raw_count: int = 0
    parsed_count: int = 0
    accepted_count: int = 0
    filtered_out_count: int = 0
    invalid_count: int = 0
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    filters_applied: dict[str, Any] = Field(default_factory=dict)


class ForexFactoryFetchResult(BaseModel):
    """
    Debug-friendly result object containing events and fetch summary.
    """

    events: list[ForexFactoryEvent] = Field(default_factory=list)
    summary: ForexFactoryFetchSummary


def _parse_numeric(value: Optional[str]) -> Optional[float]:
    """
    Parse Forex Factory numeric strings into float.

    Examples:
      "3.8%" -> 3.8
      "185K" -> 185000
      "1.2M" -> 1200000
      "-0.2" -> -0.2
    """
    if not value:
        return None

    clean = value.strip()
    if clean in ("", "N/A", "—", "-"):
        return None

    clean = clean.replace(",", "")
    multiplier = 1.0

    try:
        suffix = clean[-1].upper()
        if suffix == "K":
            multiplier = 1_000
            clean = clean[:-1]
        elif suffix == "M":
            multiplier = 1_000_000
            clean = clean[:-1]
        elif suffix == "B":
            multiplier = 1_000_000_000
            clean = clean[:-1]

        clean = clean.replace("%", "").strip()
        return float(clean) * multiplier
    except (ValueError, IndexError):
        logger.debug("Could not parse numeric value: %r", value)
        return None


def _parse_event_datetime(raw_date: Any) -> Optional[datetime]:
    """
    Parse event datetime from API value.

    Supports:
      - datetime object
      - ISO strings with timezone
      - basic datetime strings
    """
    if raw_date is None:
        return None

    if isinstance(raw_date, datetime):
        return raw_date

    if not isinstance(raw_date, str):
        logger.debug("Unexpected date type: %s", type(raw_date))
        return None

    clean = raw_date.strip()
    if not clean:
        return None

    try:
        return datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue

    logger.debug("Could not parse event date: %r", raw_date)
    return None


def _normalize_currency(raw_currency: Any) -> str:
    """
    Normalize API country/currency code.
    """
    if raw_currency is None:
        return ""

    return str(raw_currency).strip().upper()


def _normalize_impact(raw_impact: Any) -> str:
    """
    Normalize impact labels to a standard set.
    """
    if raw_impact is None:
        return "Unknown"

    clean = str(raw_impact).strip()
    if not clean:
        return "Unknown"

    lowered = clean.lower()

    for key, normalized in IMPACT_NORMALIZATION_MAP.items():
        if key in lowered:
            return normalized

    # fallback: preserve readable title case
    return clean.title()


def _map_title_to_category(title: str) -> str:
    """
    Infer event category from title keywords.
    """
    title_lower = title.lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in title_lower for keyword in keywords):
            return category

    return "Unknown"


def _increment_reason(summary: ForexFactoryFetchSummary, reason: str) -> None:
    summary.rejection_reasons[reason] = summary.rejection_reasons.get(reason, 0) + 1


def _normalize_datetime_for_comparison(value: datetime) -> datetime:
    """
    Normalize datetime for safe comparisons between aware/naive values.
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _format_filters_for_summary(
    impact_filter: Optional[list[str]],
    currency_filter: Optional[list[str]],
    title_keywords: Optional[list[str]],
    start_datetime: Optional[datetime],
    end_datetime: Optional[datetime],
    released_only: Optional[bool],
    require_forecast: bool,
) -> dict[str, Any]:
    return {
        "impact_filter": impact_filter,
        "currency_filter": currency_filter,
        "title_keywords": title_keywords,
        "start_datetime": start_datetime.isoformat() if start_datetime else None,
        "end_datetime": end_datetime.isoformat() if end_datetime else None,
        "released_only": released_only,
        "require_forecast": require_forecast,
    }


def _normalize_impact_filter(impact_filter: Optional[list[str]]) -> Optional[set[str]]:
    if not impact_filter:
        return None
    return {_normalize_impact(value) for value in impact_filter if value is not None}


def _normalize_currency_filter(currency_filter: Optional[list[str]]) -> Optional[set[str]]:
    if not currency_filter:
        return None
    return {_normalize_currency(value) for value in currency_filter if value is not None and str(value).strip()}


def _normalize_title_keywords(title_keywords: Optional[list[str]]) -> Optional[list[str]]:
    if not title_keywords:
        return None
    return [kw.strip().lower() for kw in title_keywords if kw and kw.strip()]


def _fetch_raw_payload(
    *,
    session: Any = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """
    Fetch raw payload from Forex Factory API and validate top-level shape.
    """
    requester = session or requests

    try:
        response = requester.get(FOREX_FACTORY_API, timeout=timeout)
        response.raise_for_status()
        raw_data = response.json()
    except requests.exceptions.Timeout:
        logger.error("Forex Factory API timeout")
        return []
    except requests.exceptions.HTTPError as exc:
        logger.error("Forex Factory API HTTP error: %s", exc)
        return []
    except requests.exceptions.RequestException as exc:
        logger.error("Forex Factory API connection error: %s", exc)
        return []
    except ValueError as exc:
        logger.error("Forex Factory API invalid JSON: %s", exc)
        return []

    if not isinstance(raw_data, list):
        logger.error(
            "Forex Factory API returned unexpected payload type: %s",
            type(raw_data).__name__,
        )
        return []

    dict_items: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_data):
        if not isinstance(item, dict):
            logger.warning(
                "Skipping non-dict item at index %d: type=%s",
                idx,
                type(item).__name__,
            )
            continue
        dict_items.append(item)

    return dict_items


def _build_event_from_raw(
    raw: dict[str, Any],
    *,
    include_raw: bool = False,
) -> ForexFactoryEvent:
    """
    Convert one raw API item into a normalized ForexFactoryEvent.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("missing_title")

    # IMPORTANT: API uses `country`, not `currency`
    currency = _normalize_currency(raw.get("country") or raw.get("currency"))
    if not currency:
        raise ValueError("missing_currency")

    impact = _normalize_impact(raw.get("impact"))
    event_date = _parse_event_datetime(raw.get("date"))
    category = _map_title_to_category(title)

    return ForexFactoryEvent(
        title=title,
        currency=currency,
        impact=impact,
        actual=raw.get("actual"),
        forecast=raw.get("forecast"),
        previous=raw.get("previous"),
        date=event_date,
        category=category,
        raw_payload=raw if include_raw else None,
    )


def _event_passes_filters(
    event: ForexFactoryEvent,
    *,
    impact_filter: Optional[set[str]],
    currency_filter: Optional[set[str]],
    title_keywords: Optional[list[str]],
    start_datetime: Optional[datetime],
    end_datetime: Optional[datetime],
    released_only: Optional[bool],
    require_forecast: bool,
) -> tuple[bool, Optional[str]]:
    """
    Apply all optional filters to one normalized event.
    """
    if impact_filter is not None and event.impact not in impact_filter:
        return False, "impact_filter"

    if currency_filter is not None and event.currency not in currency_filter:
        return False, "currency_filter"

    if title_keywords is not None:
        title_lower = event.title.lower()
        if not any(keyword in title_lower for keyword in title_keywords):
            return False, "title_keyword_filter"

    if start_datetime is not None:
        if event.date is None:
            return False, "missing_date_for_start_filter"
        if _normalize_datetime_for_comparison(event.date) < _normalize_datetime_for_comparison(start_datetime):
            return False, "start_datetime_filter"

    if end_datetime is not None:
        if event.date is None:
            return False, "missing_date_for_end_filter"
        if _normalize_datetime_for_comparison(event.date) > _normalize_datetime_for_comparison(end_datetime):
            return False, "end_datetime_filter"

    if released_only is True and not event.has_actual:
        return False, "released_only_filter"

    if released_only is False and event.has_actual:
        return False, "upcoming_only_filter"

    if require_forecast and not event.has_forecast:
        return False, "require_forecast_filter"

    return True, None


def fetch_forex_factory_events_debug(
    impact_filter: Optional[list[str]] = None,
    currency_filter: Optional[list[str]] = None,
    *,
    title_keywords: Optional[list[str]] = None,
    start_datetime: Optional[datetime] = None,
    end_datetime: Optional[datetime] = None,
    released_only: Optional[bool] = None,
    require_forecast: bool = False,
    include_raw: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    session: Any = None,
) -> ForexFactoryFetchResult:
    """
    Fetch Forex Factory events with debug summary.

    Default behavior:
      - NO impact filter
      - NO currency filter
      - return ALL valid events

    This is debug-friendly and avoids silently filtering out everything.

    Args:
        impact_filter: Optional list like ["High", "Medium"]
        currency_filter: Optional list like ["USD", "EUR", "GBP"]
        title_keywords: Optional substring filters on title
        start_datetime: Optional lower datetime bound
        end_datetime: Optional upper datetime bound
        released_only:
            - True  -> only released events (has actual)
            - False -> only upcoming/unreleased events
            - None  -> no release-status filter
        require_forecast: If True, only keep events that have forecast
        include_raw: If True, store raw payload inside each event
        timeout: HTTP timeout in seconds
        session: Optional injected HTTP requester for tests

    Returns:
        ForexFactoryFetchResult(events=[...], summary=...)
    """
    normalized_impact_filter = _normalize_impact_filter(impact_filter)
    normalized_currency_filter = _normalize_currency_filter(currency_filter)
    normalized_title_keywords = _normalize_title_keywords(title_keywords)

    raw_events = _fetch_raw_payload(session=session, timeout=timeout)

    summary = ForexFactoryFetchSummary(
        raw_count=len(raw_events),
        filters_applied=_format_filters_for_summary(
            impact_filter=impact_filter,
            currency_filter=currency_filter,
            title_keywords=title_keywords,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            released_only=released_only,
            require_forecast=require_forecast,
        ),
    )

    accepted_events: list[ForexFactoryEvent] = []

    for raw in raw_events:
        try:
            event = _build_event_from_raw(raw, include_raw=include_raw)
            summary.parsed_count += 1
        except ValueError as exc:
            summary.invalid_count += 1
            _increment_reason(summary, str(exc))
            continue

        passed, reject_reason = _event_passes_filters(
            event,
            impact_filter=normalized_impact_filter,
            currency_filter=normalized_currency_filter,
            title_keywords=normalized_title_keywords,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            released_only=released_only,
            require_forecast=require_forecast,
        )

        if not passed:
            summary.filtered_out_count += 1
            if reject_reason:
                _increment_reason(summary, reject_reason)
            continue

        accepted_events.append(event)

    summary.accepted_count = len(accepted_events)

    logger.info(
        "Forex Factory fetch complete: raw=%d parsed=%d accepted=%d filtered=%d invalid=%d",
        summary.raw_count,
        summary.parsed_count,
        summary.accepted_count,
        summary.filtered_out_count,
        summary.invalid_count,
    )

    if summary.rejection_reasons:
        logger.debug("Rejection reasons: %s", summary.rejection_reasons)

    return ForexFactoryFetchResult(events=accepted_events, summary=summary)


def fetch_forex_factory_events(
    impact_filter: Optional[list[str]] = None,
    currency_filter: Optional[list[str]] = None,
    *,
    title_keywords: Optional[list[str]] = None,
    start_datetime: Optional[datetime] = None,
    end_datetime: Optional[datetime] = None,
    released_only: Optional[bool] = None,
    require_forecast: bool = False,
    include_raw: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    session: Any = None,
) -> list[ForexFactoryEvent]:
    """
    Backward-compatible helper that returns only the list of events.

    Use fetch_forex_factory_events_debug(...) if you also want summary info.
    """
    result = fetch_forex_factory_events_debug(
        impact_filter=impact_filter,
        currency_filter=currency_filter,
        title_keywords=title_keywords,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        released_only=released_only,
        require_forecast=require_forecast,
        include_raw=include_raw,
        timeout=timeout,
        session=session,
    )
    return result.events


def print_fetch_summary(result: ForexFactoryFetchResult) -> None:
    """
    Pretty-print debug summary to stdout.
    """
    summary = result.summary
    print("=" * 70)
    print("Forex Factory Fetch Summary")
    print("=" * 70)
    print(f"Raw count         : {summary.raw_count}")
    print(f"Parsed count      : {summary.parsed_count}")
    print(f"Accepted count    : {summary.accepted_count}")
    print(f"Filtered out      : {summary.filtered_out_count}")
    print(f"Invalid count     : {summary.invalid_count}")
    print(f"Filters applied   : {summary.filters_applied}")
    if summary.rejection_reasons:
        print("Rejection reasons :")
        for reason, count in sorted(summary.rejection_reasons.items()):
            print(f"  - {reason}: {count}")
    print("=" * 70)
    
    
# ===========================================================================
# PERSISTENCE LAYER
# ===========================================================================

from core.database import SessionLocal, EventHistoryDB
from sqlalchemy.exc import SQLAlchemyError


class EventPersistenceResult(BaseModel):
    """
    نتیجه عملیات ذخیره‌سازی batch.
    """
    total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


def _find_existing_event(
    session,
    title: str,
    currency: str,
    event_date: Optional[datetime],
) -> Optional[EventHistoryDB]:
    """
    جستجوی duplicate بر اساس (title + currency + date).

    اگر date نداشته باشیم، فقط از (title + currency) استفاده می‌کنیم
    و آخرین رکورد مشابه را برمی‌گردانیم.
    """
    query = session.query(EventHistoryDB).filter(
        EventHistoryDB.title == title,
        EventHistoryDB.currency == currency,
    )

    if event_date is not None:
        # مقایسه تاریخ بدون timezone
        naive_date = event_date.replace(tzinfo=None) if event_date.tzinfo else event_date
        query = query.filter(EventHistoryDB.date == naive_date)
    else:
        # بدون date، آخرین رکورد مشابه
        query = query.order_by(EventHistoryDB.id.desc())

    return query.first()


def _should_update(
    existing: EventHistoryDB,
    event: ForexFactoryEvent,
) -> bool:
    """
    تشخیص اینکه آیا رکورد موجود باید update شود یا نه.

    Update لازم است اگر:
      1. actual جدید آمده ولی قبلاً نداشتیم
      2. forecast تغییر کرده
      3. previous تغییر کرده
      4. impact تغییر کرده
    """
    new_actual = event.actual_float()
    new_forecast = event.forecast_float()
    new_previous = event.previous_float()

    # اگر actual جدید آمده و قبلاً نداشتیم
    if new_actual is not None and existing.actual is None:
        return True

    # اگر مقادیر عددی تغییر کرده‌اند
    if new_actual is not None and existing.actual != new_actual:
        return True
    if new_forecast is not None and existing.forecast != new_forecast:
        return True
    if new_previous is not None and existing.previous != new_previous:
        return True

    # اگر impact تغییر کرده
    if event.impact and existing.impact != event.impact:
        return True

    return False


def _apply_update(
    existing: EventHistoryDB,
    event: ForexFactoryEvent,
) -> None:
    """
    فیلدهای قابل تغییر رکورد موجود را update می‌کند.
    """
    import datetime as dt_module

    new_actual = event.actual_float()
    new_forecast = event.forecast_float()
    new_previous = event.previous_float()

    if new_actual is not None:
        existing.actual = new_actual
        existing.raw_actual_str = event.actual

    if new_forecast is not None:
        existing.forecast = new_forecast
        existing.raw_forecast_str = event.forecast

    if new_previous is not None:
        existing.previous = new_previous
        existing.raw_previous_str = event.previous

    if event.impact:
        existing.impact = event.impact

    if event.category and event.category != "Unknown":
        existing.category = event.category

    existing.updated_at = dt_module.datetime.utcnow()


def save_event(
    event: ForexFactoryEvent,
    *,
    session=None,
) -> str:
    """
    یک event را در دیتابیس ذخیره می‌کند.

    Returns:
        "inserted" — رکورد جدید ساخته شد
        "updated"  — رکورد موجود update شد
        "skipped"  — رکورد موجود بدون تغییر بود
    """
    import datetime as dt_module

    own_session = session is None
    if own_session:
        session = SessionLocal()

    try:
        # Naive datetime for DB storage
        event_date_naive = None
        if event.date is not None:
            event_date_naive = (
                event.date.replace(tzinfo=None)
                if event.date.tzinfo
                else event.date
            )

        existing = _find_existing_event(
            session,
            title=event.title,
            currency=event.currency,
            event_date=event.date,
        )

        if existing is not None:
            if _should_update(existing, event):
                _apply_update(existing, event)
                if own_session:
                    session.commit()
                logger.debug(
                    "Updated event: %s (%s) — actual=%s",
                    event.title, event.currency, event.actual,
                )
                return "updated"
            else:
                logger.debug(
                    "Skipped event (no changes): %s (%s)",
                    event.title, event.currency,
                )
                return "skipped"
        else:
            new_record = EventHistoryDB(
                title=event.title,
                category=event.category,
                currency=event.currency,
                impact=event.impact,
                date=event_date_naive,
                actual=event.actual_float(),
                forecast=event.forecast_float(),
                previous=event.previous_float(),
                raw_actual_str=event.actual if event.has_actual else None,
                raw_forecast_str=event.forecast if event.has_forecast else None,
                raw_previous_str=event.previous if event.has_previous else None,
                source="Forex Factory",
                fetched_at=dt_module.datetime.utcnow(),
            )
            session.add(new_record)
            if own_session:
                session.commit()
            logger.debug(
                "Inserted event: %s (%s) — actual=%s",
                event.title, event.currency, event.actual,
            )
            return "inserted"

    except SQLAlchemyError as exc:
        if own_session:
            session.rollback()
        logger.error("DB error saving event %r: %s", event.title, exc)
        raise
    finally:
        if own_session:
            session.close()


def save_events(
    events: list[ForexFactoryEvent],
) -> EventPersistenceResult:
    """
    Batch save: لیستی از eventها را ذخیره/update/skip می‌کند.

    از یک session مشترک استفاده می‌کند و در انتها یک commit می‌زند.
    اگر یک event خطا بدهد، بقیه ادامه پیدا می‌کنند.

    Returns:
        EventPersistenceResult با آمار کامل
    """
    result = EventPersistenceResult(total=len(events))

    if not events:
        logger.info("No events to save.")
        return result

    session = SessionLocal()
    try:
        for event in events:
            try:
                status = save_event(event, session=session)
                if status == "inserted":
                    result.inserted += 1
                elif status == "updated":
                    result.updated += 1
                elif status == "skipped":
                    result.skipped += 1
            except SQLAlchemyError as exc:
                result.failed += 1
                result.errors.append(f"{event.title}: {exc}")
                # rollback this event but continue with others
                session.rollback()
                continue

        # Commit all successful operations
        session.commit()

        logger.info(
            "Batch save complete: total=%d inserted=%d updated=%d skipped=%d failed=%d",
            result.total,
            result.inserted,
            result.updated,
            result.skipped,
            result.failed,
        )

    except SQLAlchemyError as exc:
        session.rollback()
        logger.error("Batch save failed: %s", exc)
        raise
    finally:
        session.close()

    return result


def fetch_and_save(
    impact_filter: Optional[list[str]] = None,
    currency_filter: Optional[list[str]] = None,
    **kwargs,
) -> tuple[ForexFactoryFetchResult, EventPersistenceResult]:
    """
    Convenience function: fetch + normalize + save در یک فراخوانی.

    Returns:
        (fetch_result, persistence_result)
    """
    fetch_result = fetch_forex_factory_events_debug(
        impact_filter=impact_filter,
        currency_filter=currency_filter,
        **kwargs,
    )

    persistence_result = save_events(fetch_result.events)

    return fetch_result, persistence_result


def print_persistence_summary(result: EventPersistenceResult) -> None:
    """
    Pretty-print persistence summary.
    """
    print("=" * 55)
    print("  Persistence Summary")
    print("=" * 55)
    print(f"  Total events    : {result.total}")
    print(f"  Inserted (new)  : {result.inserted}")
    print(f"  Updated         : {result.updated}")
    print(f"  Skipped (same)  : {result.skipped}")
    print(f"  Failed          : {result.failed}")
    if result.errors:
        print("  Errors:")
        for err in result.errors:
            print(f"    - {err}")
    print("=" * 55)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "fetch"

    if mode == "fetch":
        # فقط fetch و نمایش — بدون ذخیره در DB
        result = fetch_forex_factory_events_debug()
        print_fetch_summary(result)
        print(f"\nFirst 10 events:\n")
        for event in result.events[:10]:
            print(
                f"  {event.currency:>4} | {event.impact:<8} | "
                f"{event.category:<30} | {event.title}"
            )

    elif mode == "save":
        # fetch + save to DB
        print("Fetching events from Forex Factory...")
        fetch_result, persist_result = fetch_and_save()
        print()
        print_fetch_summary(fetch_result)
        print()
        print_persistence_summary(persist_result)

    elif mode == "save-high":
        # fetch + save فقط High impact
        print("Fetching High impact events...")
        fetch_result, persist_result = fetch_and_save(
            impact_filter=["High"],
        )
        print()
        print_fetch_summary(fetch_result)
        print()
        print_persistence_summary(persist_result)

    elif mode == "verify":
        # نمایش رکوردهای موجود در DB
        from core.database import SessionLocal, EventHistoryDB

        session = SessionLocal()
        try:
            total = session.query(EventHistoryDB).count()
            with_actual = session.query(EventHistoryDB).filter(
                EventHistoryDB.actual.isnot(None)
            ).count()
            recent = (
                session.query(EventHistoryDB)
                .order_by(EventHistoryDB.id.desc())
                .limit(10)
                .all()
            )

            print(f"\nDB Statistics:")
            print(f"  Total events: {total}")
            print(f"  With actual : {with_actual}")
            print(f"\nLast 10 events:")
            for r in recent:
                print(
                    f"  #{r.id:>4} | {r.currency or '':>4} | {r.impact or '':<8} | "
                    f"a={r.actual} f={r.forecast} p={r.previous} | "
                    f"{r.title}"
                )
        finally:
            session.close()

    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python forex_factory_crawler.py [fetch|save|save-high|verify]")