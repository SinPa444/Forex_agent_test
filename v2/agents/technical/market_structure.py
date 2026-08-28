"""
agents/technical/market_structure.py
====================================
Market Structure Engine — BOS و CHoCH.

مسئولیت:
  - استخراج Break of Structure (BOS)
  - استخراج Change of Character (CHoCH)
  - ترکیب با Swing Structure برای برچسب نهایی

نکته: کد فعلی `technical_analyzer.py` فقط ستون BOS را می‌خواند؛ این ماژول P0
هر دو ستون BOS و CHoCH را به‌صورت صریح استخراج می‌کند تا گپ شناخته‌شده بسته شود.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

try:
    from smartmoneyconcepts import smc
except ImportError:  # pragma: no cover
    smc = None

from agents.technical.price_action import SwingStructure, detect_swing_structure


@dataclass
class StructureSignal:
    """خروجی ساختار بازار."""
    bos: int = 0              # +1 bullish BOS, -1 bearish BOS, 0 none
    choch: int = 0            # +1 bullish CHoCH, -1 bearish CHoCH, 0 none
    last_bos_desc: str = ""
    last_choch_desc: str = ""
    structure_label: str = "NONE"
    description: str = ""
    valid: bool = False


def _find_column(df: pd.DataFrame, tokens: list[str]) -> Optional[str]:
    for col in df.columns:
        name = str(col).upper()
        if all(tok in name for tok in tokens):
            return col
    return None


def detect_market_structure(
    df: pd.DataFrame,
    swings: Optional[pd.DataFrame] = None,
    swing_length: int = 5,
) -> StructureSignal:
    """استخراج BOS و CHoCH از `smc.bos_choch`."""
    out = StructureSignal()
    if smc is None or df is None or df.empty:
        return out

    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    try:
        if swings is None:
            swings = smc.swing_highs_lows(df, swing_length=swing_length)
        if swings is None or swings.empty:
            return out

        bos_df = smc.bos_choch(df, swings, close_break=True)
        if bos_df is None or bos_df.empty:
            return out

        # --- BOS ---
        bos_col = _find_column(bos_df, ["BOS"])
        if bos_col:
            valid = bos_df[bos_col].dropna().astype(int)
            if not valid.empty:
                last_bos = int(valid.iloc[-1])
                out.bos = 1 if last_bos > 0 else -1
                # توضیح با نزدیک‌ترین شکست
                out.last_bos_desc = "Bullish BOS" if out.bos == 1 else "Bearish BOS"

        # --- CHoCH ---
        choch_col = _find_column(bos_df, ["CHOCH"]) or _find_column(bos_df, ["CHoCH"] or ["CH", "CO"])
        if not choch_col:
            choch_col = next((c for c in bos_df.columns if "choch" in str(c).lower()), None)
        if choch_col:
            valid_c = bos_df[choch_col].dropna().astype(int)
            if not valid_c.empty:
                last_ch = int(valid_c.iloc[-1])
                out.choch = 1 if last_ch > 0 else -1
                out.last_choch_desc = "Bullish CHoCH" if out.choch == 1 else "Bearish CHoCH"

        # --- ترکیب ---
        parts = []
        if out.bos != 0:
            parts.append(out.last_bos_desc)
        if out.choch != 0:
            parts.append(out.last_choch_desc)
        if not parts:
            parts.append("No recent BOS/CHoCH")

        if out.bos != 0 and out.choch != 0:
            # CHoCH معمولاً از BOS مهم‌تر است (تغییر نهاد)
            if out.choch == 1 and out.bos == -1:
                out.structure_label = "CHoCH_BULLISH_AFTER_BEARISH_BOS"
            elif out.choch == -1 and out.bos == 1:
                out.structure_label = "CHoCH_BEARISH_AFTER_BULLISH_BOS"
            else:
                out.structure_label = "BOS+CHoCH"
        elif out.choch != 0:
            out.structure_label = "CHoCH_" + ("BULLISH" if out.choch == 1 else "BEARISH")
        elif out.bos != 0:
            out.structure_label = "BOS_" + ("BULLISH" if out.bos == 1 else "BEARISH")
        else:
            out.structure_label = "NONE"

        out.description = "; ".join(parts)
        out.valid = True
        return out
    except Exception as exc:  # pragma: no cover — defensive
        out.description = f"structure detection failed: {exc}"
        return out


def structure_label_for_signal(
    structure: StructureSignal,
    swing: Optional[SwingStructure] = None,
) -> str:
    """برچسب نهایی ساختار برای TechnicalSignal."""
    parts = []
    if structure.structure_label and structure.structure_label != "NONE":
        parts.append(structure.structure_label)
    if swing and swing.label and swing.label != "UNCLEAR":
        parts.append(swing.label)
    return " | ".join(parts) if parts else "UNCLEAR"


def structure_score_for_component(
    bos: int,
    choch: int,
    magnitude: float = 0.8,
) -> Optional[float]:
    """
    امتیاز deterministic فاکتور structure از روی BOS + CHoCH.

    قانون (P0 — قابل کالیبراسیون در P1):
      - فقط BOS → ±magnitude (همان رفتار قبلی).
      - فقط CHoCH → ±0.6 (تغییر ندارد؛ قدرت کمتر از BOS تأییدشده).
      - BOS + CHoCH هم‌جهت → ±magnitude (تقویت ساختار، بدون cap جدید).
      - BOS + CHoCH مخالف → ±0.5 در جهت CHoCH (reversal؛ هنوز تأیید نشده،
        به همین دلیل کمتر از 0.8).
    """
    bos_dir = 1 if bos > 0 else (-1 if bos < 0 else 0)
    choch_dir = 1 if choch > 0 else (-1 if choch < 0 else 0)

    if bos_dir == 0 and choch_dir == 0:
        return None
    if bos_dir != 0 and choch_dir != 0 and bos_dir != choch_dir:
        # CHoCH خلاف جهت BOS = reversal؛ نیم‌وزن در جهت CHoCH.
        return 0.5 * choch_dir
    if choch_dir != 0 and bos_dir == 0:
        # CHoCH تنها = تغییر ماهیت، قدرت کمتر از BOS تأییدشده.
        return 0.6 * choch_dir
    # BOS (به‌تنهایی یا همراهی CHoCH هم‌جهت) → همان magnitude قبلی.
    return magnitude * bos_dir
