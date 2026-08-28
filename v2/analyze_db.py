# analyze_db.py
"""
analyze_db.py
=============
تحلیل کامل دیتای موجود در economic_events_history برای
تصمیم‌گیری در مورد upgrade کردن data_fetcher.py.

سؤالات کلیدی:
  1. چقدر داده داریم؟
  2. کدام eventها بیشترین record را دارند؟
  3. چند درصد رکوردها هم actual هم forecast دارند؟
  4. توزیع زمانی چطور است؟
  5. تنوع titleها چقدر است؟ (برای canonical matching)
  6. توزیع بر اساس currency چطور است؟
  7. std سورپرایزها برای eventهای مهم چقدر است؟
"""

from __future__ import annotations

from sqlalchemy import func, text

import numpy as np
from sqlalchemy import func

from core.database import EventHistoryDB, SessionLocal


# ===========================================================================
# تحلیل ۱ — آمار کلی DB
# ===========================================================================

def analyze_general_stats():
    print("=" * 70)
    print("  تحلیل ۱ — آمار کلی DB")
    print("=" * 70)

    session = SessionLocal()
    try:
        total = session.query(EventHistoryDB).count()
        with_actual = session.query(EventHistoryDB).filter(
            EventHistoryDB.actual.isnot(None)
        ).count()
        with_forecast = session.query(EventHistoryDB).filter(
            EventHistoryDB.forecast.isnot(None)
        ).count()
        with_both = session.query(EventHistoryDB).filter(
            EventHistoryDB.actual.isnot(None),
            EventHistoryDB.forecast.isnot(None),
        ).count()
        with_detail = session.query(EventHistoryDB).filter(
            EventHistoryDB.detail_text.isnot(None)
        ).count()

        print(f"  کل رکوردها           : {total:>8,}")
        print(f"  دارای actual         : {with_actual:>8,}  ({100*with_actual/total:.1f}%)")
        print(f"  دارای forecast       : {with_forecast:>8,}  ({100*with_forecast/total:.1f}%)")
        print(f"  دارای هر دو          : {with_both:>8,}  ({100*with_both/total:.1f}%)")
        print(f"  دارای detail         : {with_detail:>8,}  ({100*with_detail/total:.1f}%)")
        print()
        print(f"  ⭐ کلیدی: {with_both:,} رکورد برای historical_std قابل استفاده هستند")
    finally:
        session.close()


# ===========================================================================
# تحلیل ۲ — توزیع زمانی
# ===========================================================================

def analyze_time_distribution():
    print()
    print("=" * 70)
    print("  تحلیل ۲ — توزیع زمانی")
    print("=" * 70)

    session = SessionLocal()
    try:
        min_date = session.query(func.min(EventHistoryDB.date)).scalar()
        max_date = session.query(func.max(EventHistoryDB.date)).scalar()

        print(f"  قدیمی‌ترین تاریخ : {min_date}")
        print(f"  جدیدترین تاریخ  : {max_date}")
        if min_date and max_date:
            years = (max_date - min_date).days / 365.25
            print(f"  بازه کل         : {years:.1f} سال")

        # توزیع per year
        print()
        print("  توزیع رکوردها در هر سال (با actual+forecast):")
        print(f"  {'سال':>6} | {'تعداد':>8}")
        print("  " + "-" * 20)

        results = session.execute(
            text(
                "SELECT strftime('%Y', date) as year, COUNT(*) "
                "FROM economic_events_history "
                "WHERE actual IS NOT NULL AND forecast IS NOT NULL "
                "AND date IS NOT NULL "
                "GROUP BY year ORDER BY year"
            )
        ).fetchall()

        for year, count in results:
            print(f"  {year:>6} | {count:>8,}")
    finally:
        session.close()


# ===========================================================================
# تحلیل ۳ — توزیع بر اساس currency
# ===========================================================================

def analyze_currency_distribution():
    print()
    print("=" * 70)
    print("  تحلیل ۳ — توزیع بر اساس Currency")
    print("=" * 70)

    session = SessionLocal()
    try:
        results = (
            session.query(
                EventHistoryDB.currency,
                func.count(EventHistoryDB.id),
            )
            .filter(
                EventHistoryDB.actual.isnot(None),
                EventHistoryDB.forecast.isnot(None),
            )
            .group_by(EventHistoryDB.currency)
            .order_by(func.count(EventHistoryDB.id).desc())
            .all()
        )

        print(f"  {'Currency':<10} | {'رکوردهای usable':>15}")
        print("  " + "-" * 30)
        for currency, count in results:
            print(f"  {currency:<10} | {count:>15,}")
    finally:
        session.close()


# ===========================================================================
# تحلیل ۴ — مهم‌ترین eventها (با actual+forecast)
# ===========================================================================

def analyze_top_events():
    print()
    print("=" * 70)
    print("  تحلیل ۴ — Top 30 Event (بر اساس تعداد رکوردهای usable)")
    print("=" * 70)

    session = SessionLocal()
    try:
        results = (
            session.query(
                EventHistoryDB.title,
                EventHistoryDB.currency,
                func.count(EventHistoryDB.id).label("cnt"),
            )
            .filter(
                EventHistoryDB.actual.isnot(None),
                EventHistoryDB.forecast.isnot(None),
            )
            .group_by(EventHistoryDB.title, EventHistoryDB.currency)
            .order_by(func.count(EventHistoryDB.id).desc())
            .limit(30)
            .all()
        )

        print(f"  {'#':>3} | {'Currency':<6} | {'تعداد':>6} | Title")
        print("  " + "-" * 80)
        for idx, (title, currency, cnt) in enumerate(results, start=1):
            print(f"  {idx:>3} | {currency:<6} | {cnt:>6} | {title[:60]}")
    finally:
        session.close()


# ===========================================================================
# تحلیل ۵ — کلیدی‌ترین event ها (CPI, NFP, GDP, ...)
# ===========================================================================

KEY_EVENT_PATTERNS = {
    "US CPI": {"currency": "USD", "keywords": ["cpi", "consumer price"]},
    "US NFP": {"currency": "USD", "keywords": ["non-farm", "nonfarm", "nfp"]},
    "US GDP": {"currency": "USD", "keywords": ["gdp"]},
    "US Unemployment": {"currency": "USD", "keywords": ["unemployment rate"]},
    "US Retail Sales": {"currency": "USD", "keywords": ["retail sales"]},
    "US ISM Manufacturing": {"currency": "USD", "keywords": ["ism manufacturing"]},
    "US Fed Rate Decision": {"currency": "USD", "keywords": ["federal funds rate", "fomc"]},
    "EU CPI": {"currency": "EUR", "keywords": ["cpi"]},
    "EU GDP": {"currency": "EUR", "keywords": ["gdp"]},
    "ECB Rate Decision": {"currency": "EUR", "keywords": ["main refinancing", "ecb"]},
    "UK CPI": {"currency": "GBP", "keywords": ["cpi"]},
    "UK GDP": {"currency": "GBP", "keywords": ["gdp"]},
    "JP CPI": {"currency": "JPY", "keywords": ["cpi", "national core cpi"]},
}


def analyze_key_events():
    print()
    print("=" * 70)
    print("  تحلیل ۵ — Key Events (با تنوع titleها)")
    print("=" * 70)

    session = SessionLocal()
    try:
        for label, config in KEY_EVENT_PATTERNS.items():
            currency = config["currency"]
            keywords = config["keywords"]

            print()
            print(f"  📊 {label} (currency={currency})")
            print(f"  {'─' * 60}")

            # ترکیب فیلترها با OR
            from sqlalchemy import or_
            keyword_filters = or_(
                *[EventHistoryDB.title.ilike(f"%{kw}%") for kw in keywords]
            )

            # تنوع titleها
            title_variations = (
                session.query(
                    EventHistoryDB.title,
                    func.count(EventHistoryDB.id).label("cnt"),
                )
                .filter(
                    EventHistoryDB.currency == currency,
                    keyword_filters,
                    EventHistoryDB.actual.isnot(None),
                    EventHistoryDB.forecast.isnot(None),
                )
                .group_by(EventHistoryDB.title)
                .order_by(func.count(EventHistoryDB.id).desc())
                .all()
            )

            if not title_variations:
                print("    ⚠️  هیچ رکوردی یافت نشد")
                continue

            total = sum(cnt for _, cnt in title_variations)
            print(f"    کل رکوردهای usable: {total}")
            print(f"    تنوع title       : {len(title_variations)}")
            print(f"    Title های اصلی   :")
            for title, cnt in title_variations[:5]:
                pct = 100 * cnt / total
                print(f"      • {cnt:>4} ({pct:>5.1f}%) — {title[:55]}")

            # محاسبه std سورپرایز
            surprises = (
                session.query(
                    EventHistoryDB.actual - EventHistoryDB.forecast,
                )
                .filter(
                    EventHistoryDB.currency == currency,
                    keyword_filters,
                    EventHistoryDB.actual.isnot(None),
                    EventHistoryDB.forecast.isnot(None),
                )
                .all()
            )

            values = [s[0] for s in surprises if s[0] is not None]
            if len(values) >= 2:
                std_val = float(np.std(values, ddof=1))
                mean_val = float(np.mean(values))
                min_val = float(np.min(values))
                max_val = float(np.max(values))
                print(f"    std (همه)        : {std_val:.4f}")
                print(f"    mean             : {mean_val:.4f}")
                print(f"    range            : [{min_val:.4f}, {max_val:.4f}]")
    finally:
        session.close()


# ===========================================================================
# تحلیل ۶ — مقایسه std برای windowهای زمانی مختلف
# ===========================================================================

def analyze_window_comparison():
    print()
    print("=" * 70)
    print("  تحلیل ۶ — مقایسه std برای windowهای زمانی مختلف")
    print("=" * 70)
    print("  (برای US CPI به عنوان نمونه)")

    session = SessionLocal()
    try:
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        windows = {
            "1 year": now - timedelta(days=365),
            "3 years": now - timedelta(days=3 * 365),
            "5 years": now - timedelta(days=5 * 365),
            "10 years": now - timedelta(days=10 * 365),
            "All time": None,
        }

        print()
        print(f"  {'Window':<12} | {'count':>6} | {'std':>10} | {'mean':>10} | {'range':>20}")
        print("  " + "-" * 70)

        for label, start_date in windows.items():
            query = session.query(
                EventHistoryDB.actual - EventHistoryDB.forecast
            ).filter(
                EventHistoryDB.currency == "USD",
                EventHistoryDB.title.ilike("%cpi%"),
                EventHistoryDB.actual.isnot(None),
                EventHistoryDB.forecast.isnot(None),
            )

            if start_date is not None:
                query = query.filter(EventHistoryDB.date >= start_date)

            surprises = [s[0] for s in query.all() if s[0] is not None]

            if len(surprises) >= 2:
                std_val = float(np.std(surprises, ddof=1))
                mean_val = float(np.mean(surprises))
                range_str = f"[{min(surprises):.2f}, {max(surprises):.2f}]"
                print(
                    f"  {label:<12} | {len(surprises):>6} | "
                    f"{std_val:>10.4f} | {mean_val:>10.4f} | {range_str:>20}"
                )
            else:
                print(f"  {label:<12} | داده کافی نیست")
    finally:
        session.close()


# ===========================================================================
# تحلیل ۷ — توصیه نهایی
# ===========================================================================

def analyze_recommendations():
    print()
    print("=" * 70)
    print("  تحلیل ۷ — توصیه‌های نهایی")
    print("=" * 70)

    session = SessionLocal()
    try:
        # چند event با ≥ 12 observation در 5 سال اخیر داریم؟
        from datetime import datetime, timedelta
        five_years_ago = datetime.utcnow() - timedelta(days=5 * 365)

        results = (
            session.query(
                EventHistoryDB.title,
                EventHistoryDB.currency,
                func.count(EventHistoryDB.id).label("cnt"),
            )
            .filter(
                EventHistoryDB.actual.isnot(None),
                EventHistoryDB.forecast.isnot(None),
                EventHistoryDB.date >= five_years_ago,
            )
            .group_by(EventHistoryDB.title, EventHistoryDB.currency)
            .having(func.count(EventHistoryDB.id) >= 12)
            .all()
        )

        print(f"\n  ✓ {len(results)} event با ≥ 12 رکورد در ۵ سال اخیر داریم")
        print(f"  ✓ این یعنی برای این تعداد event ها می‌توانیم std معتبر حساب کنیم")

        # eventهایی که داده کافی ندارند
        all_titles = (
            session.query(
                EventHistoryDB.title,
                EventHistoryDB.currency,
                func.count(EventHistoryDB.id).label("cnt"),
            )
            .filter(
                EventHistoryDB.actual.isnot(None),
                EventHistoryDB.forecast.isnot(None),
            )
            .group_by(EventHistoryDB.title, EventHistoryDB.currency)
            .all()
        )

        low_data = [r for r in all_titles if r.cnt < 5]
        print(f"  ⚠ {len(low_data)} event title با کمتر از ۵ رکورد (نیاز به fallback)")

    finally:
        session.close()


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == "__main__":
    analyze_general_stats()
    analyze_time_distribution()
    analyze_currency_distribution()
    analyze_top_events()
    analyze_key_events()
    analyze_window_comparison()
    analyze_recommendations()

    print()
    print("=" * 70)
    print("  تحلیل کامل شد")
    print("=" * 70)