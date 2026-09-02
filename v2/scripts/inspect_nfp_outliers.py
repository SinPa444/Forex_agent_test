import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # root v2

# inspect_nfp_outliers.py
from core.database import SessionLocal, EventHistoryDB

session = SessionLocal()
try:
    records = session.query(
        EventHistoryDB.date,
        EventHistoryDB.actual,
        EventHistoryDB.forecast,
        EventHistoryDB.raw_actual_str,
        EventHistoryDB.raw_forecast_str,
    ).filter(
        EventHistoryDB.title == "Non-Farm Employment Change",
        EventHistoryDB.currency == "USD",
        EventHistoryDB.actual.isnot(None),
        EventHistoryDB.forecast.isnot(None),
    ).order_by(EventHistoryDB.date).all()

    print(f"Total NFP records: {len(records)}")
    print()
    print("Top 10 largest surprises:")
    print("-" * 80)
    
    surprises = []
    for r in records:
        surprise = r.actual - r.forecast
        surprises.append((surprise, r.date, r.actual, r.forecast, r.raw_actual_str, r.raw_forecast_str))
    
    # Sort by absolute surprise
    surprises.sort(key=lambda x: abs(x[0]), reverse=True)
    
    for s, date, actual, forecast, raw_a, raw_f in surprises[:10]:
        print(f"  {date} | surprise={s:>15,.0f} | actual={actual:>15,.0f} ({raw_a!r}) | forecast={forecast:>15,.0f} ({raw_f!r})")

    print()
    print("Bottom 5 (smallest surprises):")
    surprises.sort(key=lambda x: abs(x[0]))
    for s, date, actual, forecast, raw_a, raw_f in surprises[:5]:
        print(f"  {date} | surprise={s:>15,.0f} | actual={actual:>15,.0f} | forecast={forecast:>15,.0f}")
finally:
    session.close()