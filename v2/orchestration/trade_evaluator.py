"""
orchestration/trade_evaluator.py
================================
Silent Evaluator for Trade Memory.

Checks PENDING trades in the database against current market prices.
Updates their status to WIN, LOSS, or EXPIRED based on SL/TP hit.
Designed to be run periodically (e.g., every 15 mins) via cron or background loop.
"""

import logging
import os
import sys
from datetime import datetime, timedelta

# Allow running as script from root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
from core.database import SessionLocal, TradeOutcomeDB
from core.routing import resolve_asset_route, translate_instrument_direction

logger = logging.getLogger('trade_evaluator')

def evaluate_pending_trades():
    """Fetches pending trades and evaluates them against current price."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    logger.info("🧠 Running Trade Memory Evaluator...")

    session = SessionLocal()
    try:
        # Get trades that are still pending and not too old (e.g., max 7 days old)
        cutoff_time = datetime.utcnow() - timedelta(days=7)
        pending_trades = session.query(TradeOutcomeDB).filter(
            TradeOutcomeDB.status == "PENDING",
            TradeOutcomeDB.created_at >= cutoff_time
        ).all()

        if not pending_trades:
            logger.info("No pending trades to evaluate.")
            return

        logger.info(f"Found {len(pending_trades)} pending trades to evaluate.")

        # ── Batch fetch: unique tickers only ──
        ticker_to_trades: dict[str, list] = {}
        for trade in pending_trades:
            route = resolve_asset_route(trade.currency)
            if not route:
                logger.warning(f"Trade {trade.id}: No route for {trade.currency}. Skipping.")
                continue
            ticker_to_trades.setdefault(route.ticker, []).append((trade, route))

        # Fetch prices once per ticker
        price_map: dict[str, float] = {}
        for ticker, trades in ticker_to_trades.items():
            try:
                data = yf.download(ticker, period="1d", interval="5m", progress=False)
                if data.empty:
                    logger.warning(f"No price data for {ticker}. Skipping {len(trades)} trade(s).")
                    continue
                # Fix FutureWarning: use .item() to extract scalar
                price_map[ticker] = float(data['Close'].iloc[-1].item())
            except Exception as e:
                logger.error(f"Price fetch failed for {ticker}: {e}")

        # Evaluate using cached prices
        for ticker, trades in ticker_to_trades.items():
            current_price = price_map.get(ticker)
            if current_price is None:
                continue
            for trade, route in trades:
                # Instrument direction translation
                inst_dir = trade.direction  # 1 = Long, -1 = Short (position direction)

                outcome = None
                if inst_dir == 1:
                    if current_price >= trade.take_profit:
                        outcome = "WIN"
                    elif current_price <= trade.stop_loss:
                        outcome = "LOSS"
                elif inst_dir == -1:
                    if current_price <= trade.take_profit:
                        outcome = "WIN"
                    elif current_price >= trade.stop_loss:
                        outcome = "LOSS"

                # Check expiration (if 48 hours passed and neither hit, mark as EXPIRED)
                if outcome is None and trade.created_at < datetime.utcnow() - timedelta(hours=48):
                    outcome = "EXPIRED"

                if outcome:
                    trade.status = outcome
                    trade.closed_at = datetime.utcnow()
                    trade.evaluated_price = current_price
                    logger.info(f"Trade {trade.id} ({trade.currency} {inst_dir}) evaluated as {outcome} at price {current_price:.4f}")
                else:
                    logger.info(f"Trade {trade.id} ({trade.currency} {inst_dir}) still PENDING at {current_price:.4f}")

        session.commit()
        logger.info("Evaluation cycle complete.")

    except Exception as e:
        session.rollback()
        logger.error(f"Evaluator failed: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    evaluate_pending_trades()