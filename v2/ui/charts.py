"""
ui/charts.py
============

Plotly chart builders for the Streamlit UI (display-only).

Price history is fetched directly from yfinance for visualization purposes;
this never feeds back into any scoring logic.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger("ui.charts")

# Interval/period mapping mirrors agents/technical/technical_analyzer.py
# (TIMEFRAME_MAP + period rules) so the chart shows what the agent saw.
_INTERVAL_MAP = {"M15": "15m", "H1": "60m", "H4": "4h", "D1": "1d"}


def fetch_ohlcv(ticker: str, timeframe: str = "H4") -> Optional[pd.DataFrame]:
    """Fetch OHLCV history for charting. Returns None on failure."""
    try:
        import yfinance as yf

        interval = _INTERVAL_MAP.get(timeframe, "4h")
        period = "1y"
        if interval in ("60m", "1h"):
            period = "1mo"
        elif interval == "15m":
            period = "5d"
        elif interval == "4h":
            period = "3mo"

        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        # Flatten possible MultiIndex columns (yfinance behavior)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.tail(150)
    except Exception as exc:
        logger.warning("Price history fetch failed for %s: %s", ticker, exc)
        return None


def candlestick_with_smc(df: pd.DataFrame, metrics=None, title: str = ""):
    """Candlestick chart with OB lines and FVG bands from TechnicalMetrics.

    `metrics` may be None (DB view) — then only candles are drawn.
    """
    import plotly.graph_objects as go

    fig = go.Figure(data=[go.Candlestick(
        x=df.index,
        open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
        name="Price",
    )])

    if metrics is not None:
        # Order Blocks → horizontal lines
        if getattr(metrics, "active_bullish_ob", None) is not None:
            fig.add_hline(y=metrics.active_bullish_ob, line_color="#22c55e",
                          line_dash="dot",
                          annotation_text="Bull OB", annotation_position="bottom right")
        if getattr(metrics, "active_bearish_ob", None) is not None:
            fig.add_hline(y=metrics.active_bearish_ob, line_color="#ef4444",
                          line_dash="dot",
                          annotation_text="Bear OB", annotation_position="top right")
        # FVG zones → shaded bands
        if (getattr(metrics, "active_bull_fvg_low", None) is not None
                and getattr(metrics, "active_bull_fvg_high", None) is not None):
            fig.add_hrect(y0=metrics.active_bull_fvg_low, y1=metrics.active_bull_fvg_high,
                          fillcolor="#22c55e", opacity=0.12, line_width=0,
                          annotation_text="Bull FVG", annotation_position="bottom left")
        if (getattr(metrics, "active_bear_fvg_low", None) is not None
                and getattr(metrics, "active_bear_fvg_high", None) is not None):
            fig.add_hrect(y0=metrics.active_bear_fvg_low, y1=metrics.active_bear_fvg_high,
                          fillcolor="#ef4444", opacity=0.12, line_width=0,
                          annotation_text="Bear FVG", annotation_position="top left")
        if getattr(metrics, "current_price", None) is not None:
            fig.add_hline(y=metrics.current_price, line_color="#3b82f6",
                          line_dash="dash", annotation_text="Price",
                          annotation_position="top left")

    fig.update_layout(
        title=title, height=420, margin=dict(l=10, r=10, t=40, b=10),
        template="plotly_dark", xaxis_rangeslider_visible=False,
        legend=dict(orientation="h"),
    )
    return fig


def win_loss_bar(history: list):
    """Grouped bar chart of trade outcomes per currency."""
    import plotly.graph_objects as go

    counts = {}
    for row in history:
        ccy = row["currency"]
        status = row["status"]
        counts.setdefault(ccy, {"WIN": 0, "LOSS": 0, "EXPIRED": 0, "PENDING": 0})
        counts[ccy][status] = counts[ccy].get(status, 0) + 1

    currencies = sorted(counts.keys())
    series = [("WIN", "#22c55e"), ("LOSS", "#ef4444"),
              ("EXPIRED", "#6b7280"), ("PENDING", "#f59e0b")]

    fig = go.Figure()
    for status, color in series:
        fig.add_bar(name=status, x=currencies,
                    y=[counts[c].get(status, 0) for c in currencies],
                    marker_color=color)
    fig.update_layout(
        barmode="group", height=320, template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=10),
        title="Trade Outcomes per Currency",
        legend=dict(orientation="h"),
    )
    return fig
