# debug_violations.py
from core.database import SessionLocal, EventSignalDB

INVERSE_EVENTS_KEYWORDS = ["unemployment rate", "unemployment claims", "jobless claims"]

def is_inverse_event(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in INVERSE_EVENTS_KEYWORDS)

session = SessionLocal()
try:
    signals = session.query(EventSignalDB).all()
    
    print("--- INVERSE RULE VIOLATIONS ---")
    for sig in signals:
        if sig.surprise_value is not None and sig.surprise_value != 0:
            is_inverse = is_inverse_event(sig.event_title)
            if sig.surprise_value > 0:
                expected_dir = -1 if is_inverse else 1
                if sig.direction != expected_dir:
                    print(f"Title: {sig.event_title} | Actual: {sig.actual} | Forecast: {sig.forecast} | SurpriseVal: {sig.surprise_value} | Direction: {sig.direction} (Expected: {expected_dir})")
            elif sig.surprise_value < 0:
                expected_dir = 1 if is_inverse else -1
                if sig.direction != expected_dir:
                    print(f"Title: {sig.event_title} | Actual: {sig.actual} | Forecast: {sig.forecast} | SurpriseVal: {sig.surprise_value} | Direction: {sig.direction} (Expected: {expected_dir})")

    print("\n--- SURPRISE MISMATCH ---")
    for sig in signals:
        if sig.surprise_value is not None:
            if sig.surprise_value > 0.01 and sig.surprise_interpretation != "beat":
                print(f"Title: {sig.event_title} | SurpriseVal: {sig.surprise_value} | Interp: {sig.surprise_interpretation} (Expected: beat)")
            elif sig.surprise_value < -0.01 and sig.surprise_interpretation != "miss":
                print(f"Title: {sig.event_title} | SurpriseVal: {sig.surprise_value} | Interp: {sig.surprise_interpretation} (Expected: miss)")
            elif abs(sig.surprise_value) <= 0.01 and sig.surprise_interpretation != "in_line":
                print(f"Title: {sig.event_title} | SurpriseVal: {sig.surprise_value} | Interp: {sig.surprise_interpretation} (Expected: in_line)")

finally:
    session.close()