"""
observability/run_manifest.py
=============================
Manifest تکرارپذیری هر اجرا (Phase 1 — الگوی run_manifest از AgenticTrading).

هر ران پایپ‌لاین یک `run_id` می‌گیرد و manifest زیر در ابتدای ران نوشته
می‌شود و در پایان به‌روزرسانی می‌شود:

  backtest/results/runs/<run_id>/manifest.json

محتوا: run_id، زمان شروع/پایان، نسخه موتور + hash وزن‌ها + hash کد
(technical package)، نسخه پایتون، پکیج‌های کلیدی، و پارامترهای ران
(currencies/timeframes/...).

هدف: هر نتیجه‌ی ثبت‌شده (decision log / backtest) باید به رانی قابل پیگیری
باشد که دقیقاً چه کدی و با چه پیکربندی‌ای اجرا شده است.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

RUNS_DIR = Path(__file__).resolve().parent.parent / "backtest" / "results" / "runs"


_alea_counter = 0


def new_run_id() -> str:
    """run_id خوانا: زمان UTC + ۴ کاراکتر alea (تک‌نویس؛ کافیه برای یک ماشین)."""
    global _alea_counter
    _alea_counter += 1
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    alea = hashlib.sha1(f"{_alea_counter}".encode()).hexdigest()[:4]
    return f"run-{ts}-{alea}"


def code_hash(package_path: Path) -> str:
    """
    sha1ِ همه فایل‌های .py یک بسته (به ترتیب نام فایل) — شناسه‌ی کوتاه کد.
    هر تغییر در کد technical package → hash جدید.
    """
    h = hashlib.sha1()
    for py_file in sorted(package_path.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        rel = py_file.relative_to(package_path).as_posix()
        h.update(rel.encode("utf-8"))
        try:
            h.update(py_file.read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:16]


def key_package_versions() -> Dict[str, str]:
    """نسخه پکیج‌های کلیدی (برای تکرارپذیری) — بدون خطا در نبودشان."""
    import importlib.metadata as md

    names = [
        "pandas", "numpy", "pandas-ta-classic", "smartmoneyconcepts",
        "langchain-core", "langgraph", "SQLAlchemy", "pydantic", "yfinance",
    ]
    out: Dict[str, str] = {}
    for name in names:
        try:
            out[name] = md.version(name)
        except md.PackageNotFoundError:
            out[name] = "missing"
    return out


def start_manifest(
    *,
    run_id: str,
    engine_version: str,
    weights_hash: str,
    technical_code_hash: str,
    params: Dict[str, Any],
    runs_dir: Optional[Path] = None,
) -> Path:
    """
    نوشتن manifest ابتدای ران. مسیر فایل manifest برمی‌گردد.
    """
    base = runs_dir or RUNS_DIR
    run_dir = base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "engine_version": engine_version,
        "weights_hash": weights_hash,
        "technical_code_hash": technical_code_hash,
        "python": platform.python_version(),
        "packages": key_package_versions(),
        "params": params,
    }
    path = run_dir / "manifest.json"
    try:
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("[RunManifest] started %s → %s", run_id, path)
    except OSError as exc:
        logger.warning("[RunManifest] failed to write start manifest: %s", exc)
    return path


def update_manifest(manifest_path: Path, **fields: Any) -> None:
    """افزودن/به‌روزرسانی فیلدها در manifest (مثلاً verdict هر نماد)."""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data.update(fields)
        manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[RunManifest] failed to update %s: %s", manifest_path, exc)


def finish_manifest(manifest_path: Path, status: str = "completed") -> None:
    """به‌روزرسانی manifest پایان ران (started_at حفظ می‌شود)."""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["finished_at"] = datetime.now(timezone.utc).isoformat()
        data["status"] = status
        manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("[RunManifest] finished %s (status=%s)", data.get("run_id"), status)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[RunManifest] failed to finalize manifest %s: %s", manifest_path, exc)
