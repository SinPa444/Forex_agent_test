"""
observability/decision_log.py
=============================
ثبت تصمیمات technical engine در جدول technical_decisions (Phase 1).

قاعده: خطا در ثبت لاگ هرگز نباید پایپ‌لاین اصلی را شکست بدهد —
همه چیز در try/except بسته می‌شود و فقط log می‌شود.

dedup_hash: sha1(ticker|timeframe|run_id|price|score) — اجرای تکراری
همان تحلیل در همان run ردیف دوم نمی‌سازد (idempotency).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def make_dedup_hash(
    ticker: str,
    timeframe: str,
    run_id: str,
    price: Optional[float],
    score: Optional[float],
) -> str:
    """hash یکتا برای idempotency (ticker+timeframe+run+قیمت+score)."""
    canonical = json.dumps(
        {
            "ticker": ticker,
            "timeframe": timeframe,
            "run_id": run_id,
            "price": price,
            "score": score,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def log_technical_decision(
    *,
    session,
    ticker: str,
    timeframe: str,
    run_id: str,
    metrics,
    report,
    engine_version: str,
    weights_hash: str,
    llm_model: Optional[str] = None,
) -> Optional[int]:
    """
    ثبت یک تصمیم technical (metrics + report) در technical_decisions.

    برمی‌گرداند id ردیف ثبت‌شده یا None (در صورت خطا/تکراری بودن).
    """
    try:
        from core.database import insert_technical_decision_if_new

        dedup_hash = make_dedup_hash(
            ticker, timeframe, run_id,
            metrics.current_price,
            metrics.technical_score,
        )

        # --- snapshot کامل داده‌های خام (raw context) برای بازتولید score ---
        raw_context: Dict[str, Any] = {
            "current_price": metrics.current_price,
            "trend_status": metrics.trend_status,
            "adx_value": metrics.adx_value,
            "chop_value": metrics.chop_value,
            "atr": metrics.atr,
            "rsi": metrics.rsi,
            "macd_histogram": metrics.macd_histogram,
            "last_candle_type": metrics.last_candle_type,
            "recent_bos": metrics.recent_bos,
            "structure_raw": metrics.structure_raw,
            "active_bullish_ob": metrics.active_bullish_ob,
            "active_bearish_ob": metrics.active_bearish_ob,
            "active_bull_fvg_low": metrics.active_bull_fvg_low,
            "active_bull_fvg_high": metrics.active_bull_fvg_high,
            "active_bear_fvg_low": metrics.active_bear_fvg_low,
            "active_bear_fvg_high": metrics.active_bear_fvg_high,
            "level_distances": metrics.level_distances,
            "liquidity_sweep_status": metrics.liquidity_sweep_status,
            "previous_day_high": metrics.previous_day_high,
            "previous_day_low": metrics.previous_day_low,
            "pdh_pdl_status": metrics.pdh_pdl_status,
            "current_retracement": metrics.current_retracement,
            "ote_status": metrics.ote_status,
            "htf_interval": metrics.htf_interval,
            "htf_trend_status": metrics.htf_trend_status,
            "data_error": metrics.data_error,
        }

        row, created = insert_technical_decision_if_new(
            session,
            dedup_hash=dedup_hash,
            run_id=run_id,
            currency=ticker,
            timeframe=timeframe,
            engine_version=engine_version,
            weights_hash=weights_hash,
            direction=report.direction,
            score=report.score,
            confidence=report.confidence,
            components_json=metrics.components.model_dump_json(),
            raw_context_json=json.dumps(raw_context, ensure_ascii=False),
            data_quality_json=metrics.data_quality_json,
            provenance_json=metrics.provenance_json,
            llm_model=llm_model,
            llm_status=report.llm_status,
            llm_guard_flags=report.llm_guard_flags,
        )
        if created:
            logger.debug(
                "[DecisionLog] %s %s recorded (id=%s, run=%s)",
                ticker, timeframe, row.id, run_id,
            )
        return row.id
    except Exception as exc:
        # سکوت در خطا — پایپ‌لاین اصلی مهم‌تر از لاگ است
        logger.warning("[DecisionLog] failed to record %s %s: %s", ticker, timeframe, exc)
        return None
