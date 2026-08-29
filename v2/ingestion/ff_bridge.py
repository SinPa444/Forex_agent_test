"""
ff_bridge.py
============
اسکریپت پل برای اتصال ریپازیتوری forexfactory-scraper به دیتابیس پروژه ما.
داده‌های HTML (شامل actual) را می‌گیرد و در economic_events_history آپدیت/درج می‌کند.

فیکس (بوت‌استرپ تاریخی):
  قبلاً کل بازه (مثلاً ۲٫۵ سال) در یک کال provider.fetch_events گرفته می‌شد و
  ذخیره‌سازی فقط در انتها انجام می‌شد؛ در نتیجه یک تایم‌اوت شبکه در هفته ۳۸
  کل ران را نابود می‌کرد (Inserted: 0).
  حالا بازه به پنجره‌های هفتگی (یکشنبه تا شنبه، مطابق هفته‌های FF) شکسته
  می‌شود؛ هر هفته جداگانه با retry گرفته و بلافاصله ذخیره می‌شود. شکست یک
  هفته فقط همان هفته را رد می‌کند و لیست هفته‌های ناموفق در گزارش پایانی
  چاپ می‌شود تا با همان بازه‌ها دوباره ران شود.
"""

import logging
import sys
import time
from datetime import datetime, timedelta
from dateutil.tz import gettz
from typing import Optional
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, 'ext_ff_scraper/src'))

from github.scrapper.src.forexfactory.providers import ForexFactoryHtmlProvider, ProviderError

from core.database import SessionLocal, EventHistoryDB
from ingestion.forex_factory_crawler import _parse_numeric, _map_title_to_category

logger = logging.getLogger(__name__)

# تعداد تلاش برای هر هفته و مکث بین تلاش‌ها (ثانیه؛ به‌صورت تصاعدی)
WEEK_FETCH_MAX_ATTEMPTS: int = 3
WEEK_FETCH_BACKOFF_SEC: float = 5.0


def save_events_to_db(events: list[dict]) -> dict:
    """ذخیره یا آپدیت داده‌های لیست دیکشنری در دیتابیس پروژه."""
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    session = SessionLocal()
    
    try:
        for row in events:
            try:
                title = str(row.get("event", "")).strip()
                currency = str(row.get("currency", "")).strip().upper()
                if not title or not currency:
                    continue
                    
                # پارس کردن تاریخ از فرمت ISO (datetime_utc)
                dt_str = str(row.get("datetime_utc", ""))
                event_date = datetime.fromisoformat(dt_str) if dt_str else None
                
                # مقادیر عددی و رشته‌ای
                actual_str = row.get("actual")
                forecast_str = row.get("forecast")
                previous_str = row.get("previous")
                
                actual_f = _parse_numeric(actual_str) if actual_str else None
                forecast_f = _parse_numeric(forecast_str) if forecast_str else None
                previous_f = _parse_numeric(previous_str) if previous_str else None
                
                impact = str(row.get("impact", "Unknown")).strip().title()
                category = _map_title_to_category(title)
                
                # جستجوی رکورد موجود
                existing = session.query(EventHistoryDB).filter(
                    EventHistoryDB.title == title,
                    EventHistoryDB.currency == currency,
                    EventHistoryDB.date == event_date
                ).first()
                
                if existing:
                    # آپدیت اگر actual جدید داریم یا مقادیر تغییر کرده
                    if actual_f is not None and existing.actual is None:
                        existing.actual = actual_f
                        existing.raw_actual_str = actual_str
                        stats["updated"] += 1
                    elif actual_f is not None and existing.actual != actual_f:
                        existing.actual = actual_f
                        existing.raw_actual_str = actual_str
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1
                        
                    # آپدیت forecast و previous اگر missing باشند
                    if forecast_f is not None and existing.forecast is None:
                        existing.forecast = forecast_f
                        existing.raw_forecast_str = forecast_str
                    if previous_f is not None and existing.previous is None:
                        existing.previous = previous_f
                        existing.raw_previous_str = previous_str
                        
                else:
                    # درج رکورد جدید
                    new_record = EventHistoryDB(
                        title=title,
                        category=category,
                        currency=currency,
                        impact=impact,
                        date=event_date,
                        actual=actual_f,
                        forecast=forecast_f,
                        previous=previous_f,
                        raw_actual_str=actual_str if actual_str else None,
                        raw_forecast_str=forecast_str if forecast_str else None,
                        raw_previous_str=previous_str if previous_str else None,
                        detail_text=row.get("detail_url") if row.get("detail_url") else None,
                        source="Forex Factory (HTML Scraper)",
                        fetched_at=datetime.utcnow()
                    )
                    session.add(new_record)
                    stats["inserted"] += 1
                    
            except Exception as exc:
                session.rollback()
                stats["failed"] += 1
                logger.error(f"DB error for event {title}: {exc}")
                continue
                
        session.commit()
        logger.info(f"DB Save complete: {stats}")
        
    except Exception as exc:
        session.rollback()
        logger.error(f"Fatal error in save_events_to_db: {exc}")
    finally:
        session.close()
        
    return stats


def _iter_week_windows(start_date: datetime, end_date: datetime):
    """
    بازه را به پنجره‌های هفتگی FF (یکشنبه تا شنبه) می‌شکند.
    شروع به عقب تا یکشنبه align می‌شود (چند روز اضافه ابتدای بازه اشکالی ندارد —
    dedup در save_events_to_db جلوی تکراری را می‌گیرد).
    """
    day_since_sunday = (start_date.weekday() + 1) % 7
    cursor = start_date - timedelta(days=day_since_sunday)
    while cursor <= end_date:
        week_end = min(cursor + timedelta(days=6, hours=23, minutes=59), end_date)
        yield cursor, week_end
        cursor = cursor + timedelta(days=7)


def _fetch_week_with_retry(provider, week_start: datetime, week_end: datetime) -> Optional[list]:
    """
    یک هفته را با retry می‌گیرد. موفق → لیست ایونت‌ها؛ شکست نهایی → None.
    خطاهای شبکه (read timeout و غیره) transient هستند و معمولاً در retry بعدی جواب می‌دهند.
    """
    for attempt in range(1, WEEK_FETCH_MAX_ATTEMPTS + 1):
        try:
            return provider.fetch_events(week_start, week_end, "UTC")
        except ProviderError as exc:
            logger.warning(
                f"Week {week_start.date()} attempt {attempt}/{WEEK_FETCH_MAX_ATTEMPTS} "
                f"provider error: {exc.reason}"
            )
        except Exception as exc:
            logger.warning(
                f"Week {week_start.date()} attempt {attempt}/{WEEK_FETCH_MAX_ATTEMPTS} failed: {exc}"
            )
        if attempt < WEEK_FETCH_MAX_ATTEMPTS:
            time.sleep(WEEK_FETCH_BACKOFF_SEC * attempt)
    logger.error(f"Week {week_start.date()} permanently failed after {WEEK_FETCH_MAX_ATTEMPTS} attempts.")
    return None


def fetch_and_store_ff_data(start_date: datetime, end_date: datetime):
    """
    اجرای اسکرپر و ذخیره در دیتابیس — هفته‌به‌هفته با retry و ذخیره افزایشی.
    توجه: این پروایدر از urllib استفاده می‌کند و پروکسی را از Environment Variables می‌خواند.

    رفتار قبلی (یک کال برای کل بازه + ذخیره در انتها) حذف شد؛ حالا شکست شبکه
    در یک هفته فقط همان هفته را رد می‌کند و پیشرفت بقیه هفته‌ها حفظ می‌شود.
    """
    logger.info(f"Starting FF HTML Scraper for {start_date.date()} to {end_date.date()} (week-by-week mode)")

    provider = ForexFactoryHtmlProvider(debug=True, timeout=30)

    total = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    failed_weeks: list[str] = []
    weeks_done = 0

    for week_start, week_end in _iter_week_windows(start_date, end_date):
        events = _fetch_week_with_retry(provider, week_start, week_end)

        if events is None:
            failed_weeks.append(week_start.date().isoformat())
            continue

        if not events:
            logger.info(f"Week {week_start.date()}: no events found.")
            weeks_done += 1
            continue

        stats = save_events_to_db(events)
        for k in total:
            total[k] += stats.get(k, 0)
        weeks_done += 1
        logger.info(
            f"Week {week_start.date()} saved: {len(events)} events "
            f"(inserted={stats['inserted']}, updated={stats['updated']}, skipped={stats['skipped']})"
        )

    logger.info(
        f"Scrape finished: {weeks_done} weeks OK, {len(failed_weeks)} weeks failed. Totals: {total}"
    )

    if failed_weeks:
        total["failed_weeks"] = failed_weeks
        total["error"] = (
            f"{len(failed_weeks)} week(s) failed after retries: {', '.join(failed_weeks)}. "
            "Re-run with --start/--end covering those weeks only."
        )

    return total

# ===========================================================================
# CLI
# ===========================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Forex Factory HTML Scraper Bridge")
    parser.add_argument("--start", type=str, required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=True, help="End date YYYY-MM-DD")

    args = parser.parse_args()

    tz = gettz("UTC")
    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=tz)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=tz)

    stats = fetch_and_store_ff_data(start_dt, end_dt)
    
    print("\n=== Scraper Bridge Report ===")
    print(f"Inserted : {stats.get('inserted', 0)}")
    print(f"Updated  : {stats.get('updated', 0)}")
    print(f"Skipped  : {stats.get('skipped', 0)}")
    print(f"Failed   : {stats.get('failed', 0)}")
    if stats.get("failed_weeks"):
        print(f"Failed weeks ({len(stats['failed_weeks'])}):")
        for w in stats["failed_weeks"]:
            print(f"  - {w}")
    if 'error' in stats:
        print(f"Error    : {stats['error']}")
    print("=============================")
