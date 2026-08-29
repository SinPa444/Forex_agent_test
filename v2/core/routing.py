#!/usr/bin/env python3
"""
routing.py
==========

Shared asset routing layer for the forex AI analysis platform.

This module provides the single source of truth for:
  - How each event currency is routed to a market data ticker
  - How direction semantics translate between currency-native and instrument-view
  - Route alignment metadata (direct / inverse / index)

Both `e2e_pipeline.py` (event analysis) and `e2e_news_pipeline.py` (news analysis)
import from this module. Any future analysis pipeline (speaker, technical, etc.)
should also use this routing layer to ensure consistency.

Design principles:
  - No I/O, no side effects — pure data + pure functions
  - Standalone: only stdlib dependencies
  - Immutable route map (frozen dataclass)
  - Extensible: adding a new currency = adding one entry to CURRENCY_ROUTE_MAP

Future extensions:
  - Persistence of pair-direction (translated) alongside currency-native direction
  - Per-currency ticker preferences by asset class (e.g., XAU proxy for USD)
  - Multi-ticker routing for cross-asset analysis
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ============================================================================
# Enums
# ============================================================================

class RouteAlignment(str, Enum):
    """
    Alignment between currency-native direction and instrument-view direction.

    DIRECT  : currency bullish  → instrument bullish  (e.g., EUR bullish → EURUSD up)
    INVERSE : currency bullish  → instrument bearish  (e.g., JPY bullish → USDJPY down)
    INDEX   : currency bullish  → index bullish       (e.g., USD bullish → DXY up)

    INDEX is treated like DIRECT for direction math, but semantically distinct
    because it represents a proxy index rather than a currency pair.
    """
    DIRECT = "direct"
    INVERSE = "inverse"
    INDEX = "index"


# ============================================================================
# Data class
# ============================================================================

@dataclass(frozen=True)
class AssetRoute:
    """
    Routing metadata from event currency to market context instrument.

    Attributes:
        event_currency: Currency of the event (e.g., "USD", "JPY").
        ticker: yfinance ticker used for MarketContext enrichment.
        market_context_currency: Currency passed to fetch_market_context for
            IV/HV/yield lookup. Usually equal to event_currency, but kept
            separate for future flexibility.
        alignment: How ticker direction relates to currency direction.
        note: Human-readable explanation of the route decision.

    Design note:
        Signal.direction remains currency-native throughout the pipeline.
        The instrument-view direction is derived on-the-fly for reporting
        via translate_instrument_direction(). This keeps the database schema
        stable and allows the pair-direction adapter to evolve independently.
    """
    event_currency: str
    ticker: str
    market_context_currency: str
    alignment: RouteAlignment
    note: str = ""


# ============================================================================
# Route registry
# ============================================================================

CURRENCY_ROUTE_MAP: dict[str, AssetRoute] = {
    "USD": AssetRoute(
        event_currency="USD",
        ticker="DX-Y.NYB",
        market_context_currency="USD",
        alignment=RouteAlignment.INDEX,
        note="Dollar Index proxy aligned with USD strength.",
    ),
    "EUR": AssetRoute(
        event_currency="EUR",
        ticker="EURUSD=X",
        market_context_currency="EUR",
        alignment=RouteAlignment.DIRECT,
        note="EUR strength aligns with EURUSD direction.",
    ),
    "GBP": AssetRoute(
        event_currency="GBP",
        ticker="GBPUSD=X",
        market_context_currency="GBP",
        alignment=RouteAlignment.DIRECT,
        note="GBP strength aligns with GBPUSD direction.",
    ),
    "AUD": AssetRoute(
        event_currency="AUD",
        ticker="AUDUSD=X",
        market_context_currency="AUD",
        alignment=RouteAlignment.DIRECT,
        note="AUD strength aligns with AUDUSD direction.",
    ),
    "NZD": AssetRoute(
        event_currency="NZD",
        ticker="NZDUSD=X",
        market_context_currency="NZD",
        alignment=RouteAlignment.DIRECT,
        note="NZD strength aligns with NZDUSD direction.",
    ),
    "JPY": AssetRoute(
        event_currency="JPY",
        ticker="USDJPY=X",
        market_context_currency="JPY",
        alignment=RouteAlignment.INVERSE,
        note="JPY strength implies USDJPY downside.",
    ),
    "CHF": AssetRoute(
        event_currency="CHF",
        ticker="USDCHF=X",
        market_context_currency="CHF",
        alignment=RouteAlignment.INVERSE,
        note="CHF strength implies USDCHF downside.",
    ),
    "CAD": AssetRoute(
        event_currency="CAD",
        ticker="USDCAD=X",
        market_context_currency="CAD",
        alignment=RouteAlignment.INVERSE,
        note="CAD strength implies USDCAD downside.",
    ),
    "CNY": AssetRoute(
        event_currency="CNY",
        ticker="USDCNY=X",
        market_context_currency="CNY",
        alignment=RouteAlignment.INVERSE,
        note="CNY strength implies USDCNY downside.",
    ),
    "SEK": AssetRoute(
        event_currency="SEK",
        ticker="USDSEK=X",
        market_context_currency="SEK",
        alignment=RouteAlignment.INVERSE,
        note="SEK strength implies USDSEK downside.",
    ),
    "NOK": AssetRoute(
        event_currency="NOK",
        ticker="USDNOK=X",
        market_context_currency="NOK",
        alignment=RouteAlignment.INVERSE,
        note="NOK strength implies USDNOK downside.",
    ),
    "XAU": AssetRoute(
        event_currency="XAU",
        ticker="GC=F",
        market_context_currency="XAU",
        alignment=RouteAlignment.DIRECT,
        note="Gold strength aligns with Gold futures direction.",
    ),
    "OIL": AssetRoute(
        event_currency="OIL",
        ticker="CL=F",
        market_context_currency="OIL",
        alignment=RouteAlignment.DIRECT,
        note="Oil strength aligns with WTI crude direction.",
    )
}


# ============================================================================
# Public API
# ============================================================================

def resolve_asset_route(currency: str) -> Optional[AssetRoute]:
    """
    Look up the AssetRoute for a given currency code.

    Args:
        currency: 3-letter ISO currency code (case-insensitive).

    Returns:
        AssetRoute if the currency is registered, None otherwise.

    Example:
        >>> route = resolve_asset_route("usd")
        >>> route.ticker
        'DX-Y.NYB'
        >>> route.alignment
        <RouteAlignment.INDEX: 'index'>
    """
    if not currency:
        return None
    return CURRENCY_ROUTE_MAP.get(currency.upper())


def translate_instrument_direction(direction: int, alignment: RouteAlignment) -> int:
    """
    Translate a currency-native direction into instrument-view direction.

    This is used ONLY for reporting/display purposes. The database always
    stores currency-native direction.

    Args:
        direction: Currency-native direction (-1, 0, or +1).
        alignment: Route alignment (DIRECT / INVERSE / INDEX).

    Returns:
        Instrument-view direction (-1, 0, or +1).

    Example:
        >>> # JPY bullish (direction=+1) on USDJPY (inverse) → instrument bearish
        >>> translate_instrument_direction(1, RouteAlignment.INVERSE)
        -1
        >>> # EUR bullish (direction=+1) on EURUSD (direct) → instrument bullish
        >>> translate_instrument_direction(1, RouteAlignment.DIRECT)
        1
        >>> # Neutral stays neutral regardless of alignment
        >>> translate_instrument_direction(0, RouteAlignment.INVERSE)
        0
    """
    if direction == 0:
        return 0
    if alignment == RouteAlignment.INVERSE:
        return -direction
    return direction


def direction_label(direction: int) -> str:
    """
    Convert a numeric direction to a human-readable label.

    Args:
        direction: Direction integer (-1, 0, or +1).

    Returns:
        "Bullish" / "Bearish" / "Neutral".
    """
    if direction > 0:
        return "Bullish"
    if direction < 0:
        return "Bearish"
    return "Neutral"


def alignment_label(alignment: RouteAlignment) -> str:
    """
    Convert a RouteAlignment enum to a short display string.

    Args:
        alignment: RouteAlignment enum value.

    Returns:
        "direct" / "inverse" / "index".
    """
    if alignment == RouteAlignment.DIRECT:
        return "direct"
    if alignment == RouteAlignment.INVERSE:
        return "inverse"
    return "index"


def list_supported_currencies() -> list[str]:
    """
    Return the list of all currencies with a configured route.

    Returns:
        Sorted list of currency codes.
    """
    return sorted(CURRENCY_ROUTE_MAP.keys())


def is_currency_supported(currency: str) -> bool:
    """
    Check if a currency has a configured route.

    Args:
        currency: 3-letter ISO currency code (case-insensitive).

    Returns:
        True if the currency is in CURRENCY_ROUTE_MAP.
    """
    if not currency:
        return False
    return currency.upper() in CURRENCY_ROUTE_MAP


# ============================================================================
# CLI smoke test
# ============================================================================

if __name__ == "__main__":
    print("=" * 72)
    print("Routing Layer — Registered Currencies")
    print("=" * 72)
    print(f"Total supported currencies: {len(CURRENCY_ROUTE_MAP)}")
    print()
    print(f"{'CCY':<5}  {'Ticker':<12}  {'Alignment':<10}  Note")
    print("-" * 72)
    for currency in list_supported_currencies():
        route = CURRENCY_ROUTE_MAP[currency]
        print(
            f"{route.event_currency:<5}  {route.ticker:<12}  "
            f"{alignment_label(route.alignment):<10}  {route.note}"
        )
    print("=" * 72)

    # Sanity tests
    print()
    print("Sanity tests:")
    print(f"  JPY bullish → USDJPY view = {direction_label(translate_instrument_direction(1, RouteAlignment.INVERSE))}")
    print(f"  EUR bullish → EURUSD view = {direction_label(translate_instrument_direction(1, RouteAlignment.DIRECT))}")
    print(f"  USD bullish → DXY view    = {direction_label(translate_instrument_direction(1, RouteAlignment.INDEX))}")
    print(f"  Unknown currency route: {resolve_asset_route('XYZ')}")
    print(f"  is_currency_supported('usd'): {is_currency_supported('usd')}")