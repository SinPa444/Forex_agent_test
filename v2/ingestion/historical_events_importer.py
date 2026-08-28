# historical_events_importer.py
"""
historical_events_importer.py
==============================
وارد کردن دیتاست تاریخی Forex Factory از فایل CSV به دیتابیس.

این ماژول رویدادهای اقتصادی چندساله را از یک CSV که از Forex Factory
صادر شده، می‌خواند، نرمال‌سازی می‌کند و در جدول economic_events_history
ذخیره می‌کند.

تفاوت با forex_factory_crawler.py:
  - crawler.py → live weekly events (بدون detail)
  - importer.py → historical bulk events (با detail_text)

طراحی:
  1. CSV reader با مدیریت خطا
  2. parsing فیلدهای datetime, currency, impact, numeric
  3. normalization مشابه crawler برای consistency
  4. batch save با duplicate detection
  5. progress reporting برای فایل‌های بزرگ
  6. summary نهایی
"""

from __future__ import annotations

import csv
import datetime as dt_module
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from core.database import EventHistoryDB, SessionLocal
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ===========================================================================
# CONSTANTS
# ===========================================================================

IMPACT_NORMALIZATION: dict[str, str] = {
    "high impact expected": "High",
    "medium impact expected": "Medium",
    "low impact expected": "Low",
    "non-economic": "Non-Economic",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
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
    "Bond Auction": ["bond auction", "bond yield"],
    "Bank Holiday": ["bank holiday"],
}

DEFAULT_SOURCE = "Forex Factory (Historical CSV)"

# هر چند ردیف، یک commit بزن (برای performance روی فایل‌های بزرگ)
BATCH_COMMIT_SIZE = 500


# ===========================================================================
# RESULT SCHEMAS
# ===========================================================================

class HistoricalImportResult(BaseModel):
    """
    نتیجه عملیات import.
    """
    csv_path: str
    total_rows: int = 0
    parsed_count: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    invalid: int = 0
    failed: int = 0
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


# ===========================================================================
# PARSING HELPERS
# ===========================================================================

def _parse_csv_datetime(raw: Any) -> Optional[datetime]:
    """
    Parse CSV datetime با فرمت‌های مختلف.

    مثال فرمت دیتاست:
      "2007-01-03T18:30:00+03:30"
    """
    if raw is None:
        return None

    if isinstance(raw, datetime):
        return raw

    if not isinstance(raw, str):
        return None

    clean = raw.strip()
    if not clean:
        return None

    # ISO format اول
    try:
        return datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        pass

    # فرمت‌های جایگزین
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue

    return None


def _parse_numeric(value: Optional[str]) -> Optional[float]:
    """
    Parse عددی شبیه crawler.

    مثال:
      "3.8%" → 3.8
      "185K" → 185000
      "-40K" → -40000
      "1.2M" → 1200000
      "3.94|1.8" → None  (compound value)
    """
    if not value:
        return None

    clean = str(value).strip()
    if clean in ("", "N/A", "—", "-"):
        return None

    # compound values (bond auctions): "3.94|1.8" → None
    if "|" in clean:
        return None

    clean = clean.replace(",", "")
    multiplier = 1.0

    try:
        suffix = clean[-1].upper() if clean else ""
        if suffix == "K":
            multiplier = 1_000
            clean = clean[:-1]
        elif suffix == "M":
            multiplier = 1_000_000
            clean = clean[:-1]
        elif suffix == "B":
            multiplier = 1_000_000_000
            clean = clean[:-1]
        elif suffix == "T":
            multiplier = 1_000_000_000_000
            clean = clean[:-1]

        clean = clean.replace("%", "").strip()
        if not clean or clean in ("-", "+"):
            return None
        return float(clean) * multiplier
    except (ValueError, IndexError):
        return None


def _normalize_currency(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).strip().upper()


def _normalize_impact(raw: Any) -> str:
    if raw is None:
        return "Unknown"

    clean = str(raw).strip()
    if not clean:
        return "Unknown"

    lowered = clean.lower()

    # exact match
    if lowered in IMPACT_NORMALIZATION:
        return IMPACT_NORMALIZATION[lowered]

    # partial match
    for key, normalized in IMPACT_NORMALIZATION.items():
        if key in lowered:
            return normalized

    return clean.title()


def _infer_category(title: str) -> str:
    title_lower = title.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in title_lower for keyword in keywords):
            return category
    return "Unknown"


def _clean_detail_text(raw: Any) -> Optional[str]:
    """
    تمیز کردن detail text.
    خط‌های اضافی را حذف می‌کند ولی ساختار اصلی را حفظ می‌کند.
    """
    if raw is None:
        return None

    clean = str(raw).strip()
    if not clean:
        return None

    # حذف فاصله‌های اضافی
    clean = re.sub(r"\s+", " ", clean)
    return clean if clean else None


def _normalize_datetime_for_db(value: datetime) -> datetime:
    """تبدیل datetime to naive UTC برای ذخیره در SQLite."""
    if value.tzinfo is not None:
        return value.astimezone(dt_module.timezone.utc).replace(tzinfo=None)
    return value


# ===========================================================================
# ROW PARSING
# ===========================================================================

class ParsedRow(BaseModel):
    """نمایش داخلی یک ردیف parse-شده."""
    title: str
    currency: str
    impact: str
    category: str
    date: Optional[datetime]
    actual_str: Optional[str]
    forecast_str: Optional[str]
    previous_str: Optional[str]
    actual_float: Optional[float]
    forecast_float: Optional[float]
    previous_float: Optional[float]
    detail_text: Optional[str]


def _parse_row(row: dict[str, Any]) -> ParsedRow:
    """
    تبدیل یک ردیف CSV به ParsedRow.

    Raises:
        ValueError: اگر فیلدهای اجباری ناقص باشند.
    """
    # title
    title = str(row.get("Event") or "").strip()
    if not title:
        raise ValueError("missing_title")

    # currency
    currency = _normalize_currency(row.get("Currency"))
    if not currency:
        raise ValueError("missing_currency")

    # impact
    impact = _normalize_impact(row.get("Impact"))

    # category
    category = _infer_category(title)

    # date
    event_date = _parse_csv_datetime(row.get("DateTime"))

    # raw string values
    actual_raw = row.get("Actual")
    forecast_raw = row.get("Forecast")
    previous_raw = row.get("Previous")

    actual_str = str(actual_raw).strip() if actual_raw and str(actual_raw).strip() else None
    forecast_str = str(forecast_raw).strip() if forecast_raw and str(forecast_raw).strip() else None
    previous_str = str(previous_raw).strip() if previous_raw and str(previous_raw).strip() else None

    # numeric values
    actual_f = _parse_numeric(actual_str)
    forecast_f = _parse_numeric(forecast_str)
    previous_f = _parse_numeric(previous_str)

    # detail
    detail_text = _clean_detail_text(row.get("Detail"))

    return ParsedRow(
        title=title,
        currency=currency,
        impact=impact,
        category=category,
        date=event_date,
        actual_str=actual_str,
        forecast_str=forecast_str,
        previous_str=previous_str,
        actual_float=actual_f,
        forecast_float=forecast_f,
        previous_float=previous_f,
        detail_text=detail_text,
    )


# ===========================================================================
# DUPLICATE DETECTION & SAVE
# ===========================================================================

def _find_existing(
    session: Session,
    title: str,
    currency: str,
    event_date: Optional[datetime],
) -> Optional[EventHistoryDB]:
    """
    Duplicate detection بر اساس (title + currency + date).
    """
    query = session.query(EventHistoryDB).filter(
        EventHistoryDB.title == title,
        EventHistoryDB.currency == currency,
    )

    if event_date is not None:
        date_naive = _normalize_datetime_for_db(event_date)
        query = query.filter(EventHistoryDB.date == date_naive)
    else:
        # برای ردیف بدون date، فقط آخرین رکورد مشابه
        query = query.order_by(EventHistoryDB.id.desc())

    return query.first()


def _should_update(existing: EventHistoryDB, parsed: ParsedRow) -> bool:
    """
    تشخیص اینکه آیا رکورد موجود باید update شود.
    """
    # اگر actual جدید آمده ولی قبلاً نداشتیم
    if parsed.actual_float is not None and existing.actual is None:
        return True

    # اگر مقادیر عددی تغییر کرده‌اند
    if parsed.actual_float is not None and existing.actual != parsed.actual_float:
        return True
    if parsed.forecast_float is not None and existing.forecast != parsed.forecast_float:
        return True
    if parsed.previous_float is not None and existing.previous != parsed.previous_float:
        return True

    # اگر detail جدید آمده ولی قبلاً نداشتیم
    if parsed.detail_text and not existing.detail_text:
        return True

    return False


def _apply_update(existing: EventHistoryDB, parsed: ParsedRow) -> None:
    """update فیلدهای قابل تغییر."""
    if parsed.actual_float is not None:
        existing.actual = parsed.actual_float
        existing.raw_actual_str = parsed.actual_str

    if parsed.forecast_float is not None:
        existing.forecast = parsed.forecast_float
        existing.raw_forecast_str = parsed.forecast_str

    if parsed.previous_float is not None:
        existing.previous = parsed.previous_float
        existing.raw_previous_str = parsed.previous_str

    if parsed.impact and existing.impact != parsed.impact:
        existing.impact = parsed.impact

    if parsed.category and parsed.category != "Unknown":
        existing.category = parsed.category

    if parsed.detail_text and not existing.detail_text:
        existing.detail_text = parsed.detail_text

    existing.updated_at = dt_module.datetime.utcnow()


def _create_new(parsed: ParsedRow) -> EventHistoryDB:
    """ساخت رکورد جدید EventHistoryDB."""
    date_naive = _normalize_datetime_for_db(parsed.date) if parsed.date else None

    return EventHistoryDB(
        title=parsed.title,
        category=parsed.category,
        currency=parsed.currency,
        impact=parsed.impact,
        date=date_naive,
        actual=parsed.actual_float,
        forecast=parsed.forecast_float,
        previous=parsed.previous_float,
        raw_actual_str=parsed.actual_str,
        raw_forecast_str=parsed.forecast_str,
        raw_previous_str=parsed.previous_str,
        detail_text=parsed.detail_text,
        source=DEFAULT_SOURCE,
        fetched_at=dt_module.datetime.utcnow(),
    )


def _save_parsed_row(
    session: Session,
    parsed: ParsedRow,
) -> str:
    """
    Save یک ردیف.

    Returns:
        "inserted", "updated", or "skipped"
    """
    existing = _find_existing(
        session,
        title=parsed.title,
        currency=parsed.currency,
        event_date=parsed.date,
    )

    if existing is not None:
        if _should_update(existing, parsed):
            _apply_update(existing, parsed)
            return "updated"
        return "skipped"
    else:
        session.add(_create_new(parsed))
        return "inserted"


# ===========================================================================
# MAIN IMPORTER
# ===========================================================================

def import_historical_csv(
    csv_path: str,
    *,
    skip_non_economic: bool = True,
    require_actual_and_forecast: bool = False,
    progress_every: int = 1000,
    max_rows: Optional[int] = None,
) -> HistoricalImportResult:
    """
    وارد کردن دیتاست تاریخی از CSV به DB.

    Args:
        csv_path: مسیر فایل CSV
        skip_non_economic: اگر True باشد، eventهای Bank Holiday و non-economic
                           رد می‌شوند (پیش‌فرض True چون برای historical_std لازم نیستند)
        require_actual_and_forecast: اگر True باشد، فقط ردیف‌هایی که هم actual
                                     هم forecast دارند ذخیره می‌شوند
        progress_every: هر چند ردیف یک log بزن
        max_rows: حداکثر تعداد ردیف برای پردازش (برای تست)

    Returns:
        HistoricalImportResult با آمار کامل
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {csv_path}")

    result = HistoricalImportResult(csv_path=str(path.absolute()))

    logger.info("Starting historical import from: %s", csv_path)
    logger.info(
        "Settings: skip_non_economic=%s require_actual_and_forecast=%s max_rows=%s",
        skip_non_economic, require_actual_and_forecast, max_rows,
    )

    session = SessionLocal()

    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)

            for row_idx, row in enumerate(reader, start=1):
                if max_rows is not None and row_idx > max_rows:
                    logger.info("Reached max_rows=%d limit, stopping.", max_rows)
                    break

                result.total_rows += 1

                # parse
                try:
                    parsed = _parse_row(row)
                    result.parsed_count += 1
                except ValueError as exc:
                    result.invalid += 1
                    reason = str(exc)
                    result.rejection_reasons[reason] = result.rejection_reasons.get(reason, 0) + 1
                    continue
                except Exception as exc:
                    result.invalid += 1
                    result.errors.append(f"row {row_idx}: parse error: {exc}")
                    continue

                # filter: non-economic
                if skip_non_economic and parsed.impact == "Non-Economic":
                    result.skipped += 1
                    result.rejection_reasons["skipped_non_economic"] = (
                        result.rejection_reasons.get("skipped_non_economic", 0) + 1
                    )
                    continue

                # filter: actual+forecast required
                if require_actual_and_forecast:
                    if parsed.actual_float is None or parsed.forecast_float is None:
                        result.skipped += 1
                        result.rejection_reasons["missing_actual_or_forecast"] = (
                            result.rejection_reasons.get("missing_actual_or_forecast", 0) + 1
                        )
                        continue

                # save
                try:
                    status = _save_parsed_row(session, parsed)
                    if status == "inserted":
                        result.inserted += 1
                    elif status == "updated":
                        result.updated += 1
                    elif status == "skipped":
                        result.skipped += 1
                except SQLAlchemyError as exc:
                    result.failed += 1
                    result.errors.append(f"row {row_idx} [{parsed.title}]: {exc}")
                    session.rollback()
                    continue

                # batch commit
                if row_idx % BATCH_COMMIT_SIZE == 0:
                    try:
                        session.commit()
                    except SQLAlchemyError as exc:
                        logger.error("Batch commit failed at row %d: %s", row_idx, exc)
                        session.rollback()
                        raise

                # progress log
                if row_idx % progress_every == 0:
                    logger.info(
                        "Progress: row=%d inserted=%d updated=%d skipped=%d invalid=%d",
                        row_idx, result.inserted, result.updated,
                        result.skipped, result.invalid,
                    )

            # final commit
            try:
                session.commit()
                logger.info("Final commit successful.")
            except SQLAlchemyError as exc:
                logger.error("Final commit failed: %s", exc)
                session.rollback()
                raise

    except FileNotFoundError:
        raise
    except SQLAlchemyError as exc:
        logger.error("DB error during import: %s", exc)
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "Import complete: total=%d parsed=%d inserted=%d updated=%d skipped=%d invalid=%d failed=%d",
        result.total_rows, result.parsed_count, result.inserted,
        result.updated, result.skipped, result.invalid, result.failed,
    )

    return result


def print_import_summary(result: HistoricalImportResult) -> None:
    """نمایش summary در terminal."""
    print("=" * 60)
    print("  Historical CSV Import Summary")
    print("=" * 60)
    print(f"  CSV file       : {result.csv_path}")
    print(f"  Total rows     : {result.total_rows}")
    print(f"  Parsed         : {result.parsed_count}")
    print(f"  Inserted (new) : {result.inserted}")
    print(f"  Updated        : {result.updated}")
    print(f"  Skipped        : {result.skipped}")
    print(f"  Invalid        : {result.invalid}")
    print(f"  Failed         : {result.failed}")
    if result.rejection_reasons:
        print("  Rejection reasons:")
        for reason, count in sorted(result.rejection_reasons.items(), key=lambda x: -x[1]):
            print(f"    - {reason}: {count}")
    if result.errors:
        print(f"  Errors (first 10):")
        for err in result.errors[:10]:
            print(f"    - {err}")
    print("=" * 60)


# ===========================================================================
# CLI
# ===========================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 2:
        print("Usage: python historical_events_importer.py <csv_path> [options]")
        print()
        print("Options:")
        print("  --include-non-economic    شامل eventهای Bank Holiday")
        print("  --require-actual-forecast فقط رکوردهایی که هم actual هم forecast دارند")
        print("  --max-rows N              فقط N ردیف اول را process کن (برای تست)")
        print()
        print("Example:")
        print("  python historical_events_importer.py data/ff_historical.csv")
        print("  python historical_events_importer.py data/ff_historical.csv --max-rows 1000")
        sys.exit(1)

    csv_path = sys.argv[1]
    skip_non_economic = "--include-non-economic" not in sys.argv
    require_actual_and_forecast = "--require-actual-forecast" in sys.argv

    max_rows = None
    if "--max-rows" in sys.argv:
        idx = sys.argv.index("--max-rows")
        try:
            max_rows = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            print("Error: --max-rows requires an integer argument")
            sys.exit(1)

    try:
        result = import_historical_csv(
            csv_path,
            skip_non_economic=skip_non_economic,
            require_actual_and_forecast=require_actual_and_forecast,
            max_rows=max_rows,
        )
        print()
        print_import_summary(result)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.error("Import failed: %s", exc, exc_info=True)
        sys.exit(1)