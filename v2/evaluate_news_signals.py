"""
evaluate_news_signals.py
========================
اسکریپت ارزیابی کیفیت سیگنال‌های پایپ‌لاین اخبار (Macro Digest).

این اسکریپت ۳ لایه ارزیابی انجام می‌دهد:
1. ممیزی داده‌ها و متادیتا (درستی فیلدهای aggregated و source_links)
2. ممیزی امتیازدهی (توزیع امتیازها و معامله‌پذیری)
3. بررسی کیفی استدلال‌ها و Hallucination (مطابقت استدلال با تیترها)
"""

import json
import logging
import textwrap

from core.database import SessionLocal, NewsSignalDB

logger = logging.getLogger(__name__)

# ===========================================================================
# Layer 1: Data & Metadata Audit
# ===========================================================================

def audit_news_data_metadata():
    print("\n" + "=" * 80)
    print("  لایه ۱: ممیزی داده‌ها و متادیتا (Data & Metadata Audit)")
    print("=" * 80)
    
    session = SessionLocal()
    try:
        total_signals = session.query(NewsSignalDB).count()
        agg_signals = session.query(NewsSignalDB).filter(NewsSignalDB.is_aggregated == True).all()
        
        if not agg_signals:
            print("  هیچ سیگنال تجمیعی (Digest) در دیتابیس یافت نشد.")
            return

        print(f"  کل سیگنال‌های اخبار     : {total_signals}")
        print(f"  سیگنال‌های تجمیعی (Digest): {len(agg_signals)}")
        print("-" * 80)
        
        missing_links = 0
        missing_payload = 0
        avg_items_per_digest = []
        
        for sig in agg_signals:
            if not sig.source_links_json:
                missing_links += 1
            else:
                links = json.loads(sig.source_links_json)
                avg_items_per_digest.append(len(links))
                
            if not sig.raw_news_payload_json:
                missing_payload += 1
                
        print(f"  رکوردهای بدون source_links_json : {missing_links}")
        print(f"  رکوردهای بدون raw_news_payload  : {missing_payload}")
        
        if avg_items_per_digest:
            avg_count = sum(avg_items_per_digest) / len(avg_items_per_digest)
            print(f"  میانگین تعداد اخبار در هر Digest : {avg_count:.1f}")
            
        if missing_links == 0 and missing_payload == 0:
            print("\n  ✅ متادیتای تجمیع‌سازی به درستی ذخیره شده است.")
        else:
            print("\n  ❌ نقص در ذخیره متادیتای تجمیع‌سازی وجود دارد.")

    finally:
        session.close()


# ===========================================================================
# Layer 2: Scoring Math Audit
# ===========================================================================

def audit_news_scoring_math():
    print("\n" + "=" * 80)
    print("  لایه ۲: ممیزی امتیازدهی (Scoring Math Audit)")
    print("=" * 80)
    
    session = SessionLocal()
    try:
        agg_signals = session.query(NewsSignalDB).filter(NewsSignalDB.is_aggregated == True).all()
        if not agg_signals:
            return
            
        tradable_count = 0
        low_score_count = 0
        scores = []
        
        for sig in agg_signals:
            scores.append(sig.final_score)
            if sig.is_tradable:
                tradable_count += 1
            if abs(sig.final_score) < 0.2:
                low_score_count += 1
                
        total = len(agg_signals)
        print(f"  سیگنال‌های Tradable     : {tradable_count} ({100*tradable_count/total:.1f}%)")
        print(f"  میانگین امتیاز (Score)  : {sum(scores)/total:.3f}")
        print(f"  بیشترین امتیاز         : {max(scores):.3f}")
        print(f"  کمترین امتیاز          : {min(scores):.3f}")
        print(f"  سیگنال‌های خنثی (<0.2) : {low_score_count}")
        
        # در پایپ‌لاین اخبار، چون actual/forecast معمولا None است، surprise صفر است.
        # پس final_score نباید بالاتر از 0.8 برود چون ضریب (1+surprise) نیست.
        # مگر اینکه sentiment خیلی بالا باشد.
        if max(scores) > 0.85:
            print("\n  ⚠️ هشدار: امتیاز بالای 0.85 با surprise صفر غیرعادی است. ممکن است LLM احساسات اغراق‌آمیز داده باشد.")
        else:
            print("\n  ✅ توزیع امتیازها منطقی به نظر می‌رسد.")

    finally:
        session.close()


# ===========================================================================
# Layer 3: Qualitative & Hallucination Check
# ===========================================================================

def qualitative_and_hallucination_check():
    print("\n" + "=" * 80)
    print("  لایه ۳: بررسی کیفی و Hallucination (Qualitative Check)")
    print("=" * 80)
    print("  (بررسی تطابق استدلال LLM با تیترهای اخبار تجمیع شده)")
    
    session = SessionLocal()
    try:
        # بالاترین امتیازات مطلق (صعودی یا نزولی)
        agg_signals = session.query(NewsSignalDB).filter(
            NewsSignalDB.is_aggregated == True
        ).order_by(NewsSignalDB.final_score.desc()).limit(5).all()
        
        if not agg_signals:
            return
            
        for sig in agg_signals:
            print("\n" + "-" * 80)
            print(f"  Ticker: {sig.ticker} | Score: {sig.final_score:.3f} | Direction: {sig.direction}")
            
            # استخراج تیترها از payload
            titles = []
            try:
                payload = json.loads(sig.raw_news_payload_json)
                if isinstance(payload, list):
                    titles = [item.get("title", "") for item in payload]
            except Exception:
                pass
                
            print(f"  اخبار گروه شده ({len(titles)} مورد):")
            for t in titles[:5]: # فقط 5 تیتر اول
                print(f"    - {t}")
            if len(titles) > 5:
                print(f"    ... و {len(titles)-5} خبر دیگر")
                
            print(f"\n  LLM Reasoning:")
            print(textwrap.indent(textwrap.fill(sig.reasoning, width=70), "    "))
            
            # چک دستی Hallucination: آیا LLM عددی را ذکر کرده که در تیترها نیست؟
            # این یک چک ساده است، هوشمندتر کردن آن نیازمند NLP پیشرفته‌تر است
            reasoning_lower = sig.reasoning.lower()
            for word in reasoning_lower.split():
                if any(char.isdigit() for char in word) and "%" in word:
                    # چک می‌کنیم آیا این عدد در هیچ تیتری نبوده است
                    in_title = any(word in t.lower() for t in titles)
                    if not in_title:
                        print(f"\n  ⚠️ احتمال Hallucination: عدد '{word}' در استدلال آمده اما در تیترها نیست!")

    finally:
        session.close()


# ===========================================================================
# Main
# ===========================================================================
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    
    audit_news_data_metadata()
    audit_news_scoring_math()
    qualitative_and_hallucination_check()
    
    print("\n" + "=" * 80)
    print("  ارزیابی اخبار کامل شد.")
    print("=" * 80)