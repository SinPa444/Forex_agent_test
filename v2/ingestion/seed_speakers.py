# seed_speakers.py
"""
seed_speakers.py
================
پر کردن جدول speakers با مهم‌ترین افراد تأثیرگذار بازار فارکس.

استراتژی وزن‌گذاری بر اساس سلسله‌مراتب نفوذ بازاری:
  Tier 1 (1.00) → Top central bank chairs (Fed, ECB, BoJ)
  Tier 2 (0.95) → Major central bank chairs (BoE, BoC, RBA, SNB, RBNZ, PBoC)
  Tier 3 (0.85) → Vice chairs & deputies
  Tier 4 (0.80) → Voting FOMC members, ECB Governing Council
  Tier 5 (0.75) → ECB Executive Board, major finance ministers
  Tier 6 (0.70) → Non-voting regional Fed presidents
  Tier 7 (0.60) → Other officials

اجرا:
  python seed_speakers.py            # add/update speakers
  python seed_speakers.py --list     # نمایش speakers موجود
  python seed_speakers.py --clear    # پاک کردن جدول و seed مجدد
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from core.database import SessionLocal, SpeakerDB

logger = logging.getLogger(__name__)


# ===========================================================================
# DATA MODEL
# ===========================================================================

@dataclass
class SpeakerSeed:
    """نمایش یک speaker برای seed کردن."""
    name: str
    role: str
    primary_asset: str
    weight: float
    twitter_handle: Optional[str] = None


# ===========================================================================
# SPEAKER REGISTRY
# ===========================================================================

SPEAKERS: list[SpeakerSeed] = [
    # =====================================================================
    # TIER 1 — Top central bank chairs (weight=1.00)
    # =====================================================================
    SpeakerSeed(
        name="Jerome Powell",
        role="Federal Reserve Chair",
        primary_asset="USD",
        weight=1.00,
    ),
    SpeakerSeed(
        name="Christine Lagarde",
        role="European Central Bank President",
        primary_asset="EUR",
        weight=1.00,
        twitter_handle="@Lagarde",
    ),
    SpeakerSeed(
        name="Kazuo Ueda",
        role="Bank of Japan Governor",
        primary_asset="JPY",
        weight=1.00,
    ),

    # =====================================================================
    # TIER 2 — Major central bank chairs (weight=0.95)
    # =====================================================================
    SpeakerSeed(
        name="Andrew Bailey",
        role="Bank of England Governor",
        primary_asset="GBP",
        weight=0.95,
    ),
    SpeakerSeed(
        name="Tiff Macklem",
        role="Bank of Canada Governor",
        primary_asset="CAD",
        weight=0.95,
    ),
    SpeakerSeed(
        name="Michele Bullock",
        role="Reserve Bank of Australia Governor",
        primary_asset="AUD",
        weight=0.95,
    ),
    SpeakerSeed(
        name="Martin Schlegel",
        role="Swiss National Bank Chairman",
        primary_asset="CHF",
        weight=0.95,
    ),
    SpeakerSeed(
        name="Adrian Orr",
        role="Reserve Bank of New Zealand Governor",
        primary_asset="NZD",
        weight=0.95,
    ),
    SpeakerSeed(
        name="Pan Gongsheng",
        role="People's Bank of China Governor",
        primary_asset="CNY",
        weight=0.95,
    ),

    # =====================================================================
    # TIER 3 — Vice chairs & deputies (weight=0.85)
    # =====================================================================
    SpeakerSeed(
        name="Philip Jefferson",
        role="Federal Reserve Vice Chair",
        primary_asset="USD",
        weight=0.85,
    ),
    SpeakerSeed(
        name="Michael Barr",
        role="Federal Reserve Vice Chair for Supervision",
        primary_asset="USD",
        weight=0.85,
    ),
    SpeakerSeed(
        name="Luis de Guindos",
        role="ECB Vice President",
        primary_asset="EUR",
        weight=0.85,
    ),
    SpeakerSeed(
        name="Ryozo Himino",
        role="Bank of Japan Deputy Governor",
        primary_asset="JPY",
        weight=0.85,
    ),

    # =====================================================================
    # TIER 4 — Voting FOMC members (weight=0.80)
    # =====================================================================
    SpeakerSeed(
        name="John Williams",
        role="New York Fed President",
        primary_asset="USD",
        weight=0.80,
    ),
    SpeakerSeed(
        name="Christopher Waller",
        role="Federal Reserve Governor",
        primary_asset="USD",
        weight=0.80,
    ),
    SpeakerSeed(
        name="Lisa Cook",
        role="Federal Reserve Governor",
        primary_asset="USD",
        weight=0.80,
    ),
    SpeakerSeed(
        name="Michelle Bowman",
        role="Federal Reserve Governor",
        primary_asset="USD",
        weight=0.80,
    ),
    SpeakerSeed(
        name="Adriana Kugler",
        role="Federal Reserve Governor",
        primary_asset="USD",
        weight=0.80,
    ),

    # =====================================================================
    # TIER 5 — ECB Executive Board / Major officials (weight=0.75)
    # =====================================================================
    SpeakerSeed(
        name="Isabel Schnabel",
        role="ECB Executive Board Member",
        primary_asset="EUR",
        weight=0.75,
    ),
    SpeakerSeed(
        name="Philip Lane",
        role="ECB Chief Economist",
        primary_asset="EUR",
        weight=0.80,
    ),
    SpeakerSeed(
        name="Piero Cipollone",
        role="ECB Executive Board Member",
        primary_asset="EUR",
        weight=0.75,
    ),
    SpeakerSeed(
        name="Joachim Nagel",
        role="Bundesbank President",
        primary_asset="EUR",
        weight=0.75,
    ),
    SpeakerSeed(
        name="François Villeroy de Galhau",
        role="Banque de France Governor",
        primary_asset="EUR",
        weight=0.75,
    ),

    # =====================================================================
    # TIER 6 — Regional Fed presidents (weight=0.70)
    # =====================================================================
    SpeakerSeed(
        name="Austan Goolsbee",
        role="Chicago Fed President",
        primary_asset="USD",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Mary Daly",
        role="San Francisco Fed President",
        primary_asset="USD",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Raphael Bostic",
        role="Atlanta Fed President",
        primary_asset="USD",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Neel Kashkari",
        role="Minneapolis Fed President",
        primary_asset="USD",
        weight=0.70,
        twitter_handle="@neelkashkari",
    ),
    SpeakerSeed(
        name="Loretta Mester",
        role="Cleveland Fed President",
        primary_asset="USD",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Patrick Harker",
        role="Philadelphia Fed President",
        primary_asset="USD",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Thomas Barkin",
        role="Richmond Fed President",
        primary_asset="USD",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Lorie Logan",
        role="Dallas Fed President",
        primary_asset="USD",
        weight=0.70,
    ),

    # =====================================================================
    # TIER 7 — Finance Ministers & Treasury (weight=0.75)
    # =====================================================================
    SpeakerSeed(
        name="Scott Bessent",
        role="US Treasury Secretary",
        primary_asset="USD",
        weight=0.75,
    ),
    SpeakerSeed(
        name="Janet Yellen",
        role="Former US Treasury Secretary",
        primary_asset="USD",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Rachel Reeves",
        role="UK Chancellor of the Exchequer",
        primary_asset="GBP",
        weight=0.75,
    ),
    SpeakerSeed(
        name="Katsunobu Kato",
        role="Japan Finance Minister",
        primary_asset="JPY",
        weight=0.75,
    ),

    # =====================================================================
    # BoE Monetary Policy Committee (weight=0.70)
    # =====================================================================
    SpeakerSeed(
        name="Huw Pill",
        role="Bank of England Chief Economist",
        primary_asset="GBP",
        weight=0.75,
    ),
    SpeakerSeed(
        name="Sarah Breeden",
        role="Bank of England Deputy Governor",
        primary_asset="GBP",
        weight=0.75,
    ),

    # =====================================================================
    # SNB / RBA / BoC officials (weight=0.70)
    # =====================================================================
    SpeakerSeed(
        name="Antoine Martin",
        role="Swiss National Bank Vice Chairman",
        primary_asset="CHF",
        weight=0.80,
    ),
    SpeakerSeed(
        name="Andrea Maechler",
        role="Swiss National Bank Board Member",
        primary_asset="CHF",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Sarah Hunter",
        role="RBA Assistant Governor",
        primary_asset="AUD",
        weight=0.70,
    ),
    SpeakerSeed(
        name="Carolyn Rogers",
        role="Bank of Canada Senior Deputy Governor",
        primary_asset="CAD",
        weight=0.80,
    ),

    # =====================================================================
    # Political figures with market influence (weight=0.85)
    # =====================================================================
    SpeakerSeed(
        name="Donald Trump",
        role="US President",
        primary_asset="USD",
        weight=0.85,
        twitter_handle="@realDonaldTrump",
    ),
]


# ===========================================================================
# SEED OPERATIONS
# ===========================================================================

def upsert_speaker(session, speaker: SpeakerSeed) -> str:
    """
    Add or update a speaker in the database.

    Returns:
        "inserted" or "updated"
    """
    existing = session.query(SpeakerDB).filter(
        SpeakerDB.name == speaker.name
    ).first()

    if existing:
        # update
        existing.role = speaker.role
        existing.primary_asset = speaker.primary_asset
        existing.weight = speaker.weight
        if speaker.twitter_handle:
            existing.twitter_handle = speaker.twitter_handle
        return "updated"
    else:
        # insert
        new_speaker = SpeakerDB(
            name=speaker.name,
            role=speaker.role,
            primary_asset=speaker.primary_asset,
            weight=speaker.weight,
            twitter_handle=speaker.twitter_handle,
        )
        session.add(new_speaker)
        return "inserted"


def seed_all_speakers() -> dict:
    """
    Seed all speakers from the SPEAKERS registry.

    Returns:
        dict with counts: {"inserted": N, "updated": N, "failed": N}
    """
    stats = {"inserted": 0, "updated": 0, "failed": 0}

    session = SessionLocal()
    try:
        for speaker in SPEAKERS:
            try:
                status = upsert_speaker(session, speaker)
                stats[status] += 1
                logger.debug(
                    "%s: %s (weight=%.2f)",
                    status.upper(), speaker.name, speaker.weight,
                )
            except SQLAlchemyError as exc:
                stats["failed"] += 1
                logger.error("Failed to upsert %r: %s", speaker.name, exc)
                session.rollback()
                continue

        session.commit()
        logger.info(
            "Seed complete: inserted=%d updated=%d failed=%d",
            stats["inserted"], stats["updated"], stats["failed"],
        )
    except SQLAlchemyError as exc:
        logger.error("Seed failed: %s", exc)
        session.rollback()
        raise
    finally:
        session.close()

    return stats


def clear_speakers_table() -> int:
    """
    Delete all rows from the speakers table.

    Returns:
        تعداد رکوردهای حذف شده
    """
    session = SessionLocal()
    try:
        count = session.query(SpeakerDB).delete()
        session.commit()
        logger.info("Cleared %d speakers from DB", count)
        return count
    except SQLAlchemyError as exc:
        session.rollback()
        logger.error("Clear failed: %s", exc)
        raise
    finally:
        session.close()


def list_speakers() -> list[SpeakerDB]:
    """List all speakers in DB sorted by weight descending."""
    session = SessionLocal()
    try:
        speakers = (
            session.query(SpeakerDB)
            .order_by(SpeakerDB.weight.desc(), SpeakerDB.name.asc())
            .all()
        )
        return speakers
    finally:
        session.close()


def print_speakers_table(speakers: list[SpeakerDB]) -> None:
    """نمایش speakers به صورت جدول."""
    print("=" * 90)
    print(f"  {'Name':<28} | {'Role':<35} | {'Asset':<6} | {'Weight':>6}")
    print("-" * 90)
    for s in speakers:
        print(
            f"  {s.name:<28} | {s.role[:35]:<35} | {s.primary_asset:<6} | {s.weight:>6.2f}"
        )
    print("=" * 90)
    print(f"  Total: {len(speakers)} speakers")
    print("=" * 90)


# ===========================================================================
# CLI
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if "--list" in sys.argv:
        speakers = list_speakers()
        if not speakers:
            print("No speakers in DB. Run without --list to seed.")
        else:
            print_speakers_table(speakers)
        sys.exit(0)

    if "--clear" in sys.argv:
        confirm = input("⚠️  Clear all speakers and re-seed? (yes/no): ")
        if confirm.lower() != "yes":
            print("Cancelled.")
            sys.exit(0)
        clear_speakers_table()
        print("Speakers cleared.")

    print(f"Seeding {len(SPEAKERS)} speakers into DB...")
    stats = seed_all_speakers()

    print()
    print("=" * 50)
    print(f"  Inserted : {stats['inserted']}")
    print(f"  Updated  : {stats['updated']}")
    print(f"  Failed   : {stats['failed']}")
    print("=" * 50)

    print("\nCurrent speakers in DB:\n")
    speakers = list_speakers()
    print_speakers_table(speakers)