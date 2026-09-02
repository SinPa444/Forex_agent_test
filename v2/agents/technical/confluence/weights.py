"""
agents/technical/confluence/weights.py
======================================
درست‌کننده (single source of truth) وزن‌ها و نسخه موتور.

قاعده طلایی: هیچ‌جای دیگر از کد مقادیر وزن به‌صورت hardcoded وجود ندارد؛
هر چیزی که score/confluence می‌سازد از این ماژول می‌خواند.

مهم: تغییر این اعداد = تغییر semantics موتور — فقط با backtest A/B مجاز است
(در Phase 1 مقادیر دقیقاً نسخه قبل است و score باید bit-identical بماند).

weights_hash: hash sha1 وزن‌ها — در هر decision log و run manifest ثبت می‌شود
تا هر score ثبت‌شده قابل پیگیری به نسخه‌ی وزن‌های خودش باشد.
"""

from __future__ import annotations

import hashlib
import json

# نسخه موتور (در provenance و decision log ثبت می‌شود)
ENGINE_VERSION = "2.0.0"

# وزن‌های ۱۰ فاکتور — مجموع = 1.0 (مقادیر نسخه قبل)
WEIGHTS: dict[str, float] = {
    "structure": 0.20,
    "smc_location": 0.15,
    "trend": 0.13,
    "liquidity_sweep": 0.12,
    "mtf_confluence": 0.10,
    "ote_zone": 0.10,
    "pdh_pdl": 0.08,
    "momentum": 0.06,
    "volatility": 0.04,
    "price_action": 0.02,
}


def weights_hash() -> str:
    """
    sha1 کاننیکال وزن‌ها (کلید مرتب) — ۱۲ کاراکتر اول.
    هر تغییر وزن → hash جدید → در decision log/manifest ثبت می‌شود.
    """
    canonical = json.dumps(WEIGHTS, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def verify_weights_sum() -> bool:
    """کنترل سلامت: مجموع وزن‌ها باید 1.0 باشد (tolerance 1e-9)."""
    return abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
