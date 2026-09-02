"""
v2/observability — حسابرسی تصمیمات (Phase 1)
============================================
الگوها از AgenticTrading (domain/runs + backtest_decisions + run manifest):

  decision_log  — ثبت هر تصمیم technical در جدول technical_decisions
                  (idempotent با dedup_hash)
  run_manifest  — manifest تکرارپذیری هر اجرا (نسخه موتور، وزن‌ها،
                  hash کد، تنظیمات، زمان شروع) — در ابتدا و در پایان ران نوشته می‌شود

هر دو ماژول «سکوت در خطا» دارند: شکست در ثبت لاگ هرگز پایپ‌لاین اصلی را
نمی‌ریزد (log + continue).
"""
