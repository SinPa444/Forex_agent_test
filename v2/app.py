"""
ui/app.py
=========

Streamlit entry point for the multi-agent forex analysis dashboard.

Run from the project root:
    streamlit run ui/app.py
    streamlit run ui/app.py --server.port 8502   # custom port

Two modes:
  - DB View (default, no LLM): latest composite signals, open trades, memory stats.
  - Live Analysis (button): full Phase 3 pipeline via ui.service.run_live_analysis().
    Results are cached in session_state for CACHE_TTL_MINUTES to protect LLM tokens.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta

import streamlit as st

from ui import config, service
from ui.charts import candlestick_with_smc, fetch_ohlcv, win_loss_bar
from ui.components import (
    render_db_currency_card,
    render_live_currency_card,
    render_status_board,
)

st.set_page_config(
    page_title="FX Multi-Agent Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================================================================
# Sidebar — controls
# ============================================================================

st.sidebar.title("Controls")

all_currencies = service.list_supported_currencies()
selected = st.sidebar.multiselect(
    "Currencies", options=all_currencies, default=["USD", "EUR"],
)

run_ingestion = st.sidebar.checkbox("Run Phase 0 (Ingestion)", value=False)

override_on = st.sidebar.checkbox("Override Timeframe", value=False)
timeframe_override = None
if override_on:
    timeframe_override = st.sidebar.selectbox("Timeframe", config.TIMEFRAME_CHOICES, index=2)

run_clicked = st.sidebar.button("▶ Run Live Analysis", type="primary",
                                disabled=not selected)

st.sidebar.caption(f"LLM provider: {config.LLM_PROVIDER} (server-side)")
st.sidebar.caption(f"Live results cached for {config.CACHE_TTL_MINUTES} min.")


# ============================================================================
# Live run handling (with 15-minute token-protection cache)
# ============================================================================

def _is_cache_fresh(entry: dict) -> bool:
    return datetime.utcnow() - entry["ran_at"] < timedelta(minutes=config.CACHE_TTL_MINUTES)


if run_clicked:
    last = st.session_state.get("last_run")
    if last and _is_cache_fresh(last):
        st.info("Recent analysis cached, showing last results "
                f"(ran {last['ran_at']:%H:%M:%S} UTC).")
    else:
        log_lines: list = []
        log_box = st.empty()
        status_box = st.empty()

        def _sink(msg: str) -> None:
            log_lines.append(msg)
            status_box.caption(msg)
            log_box.code("\n".join(log_lines[-config.LOG_TAIL_LINES:]), language="text")

        status_box.caption("Starting live analysis...")
        with st.spinner("Running multi-agent analysis (this can take several minutes)..."):
            result = service.run_live_analysis(
                selected,
                run_ingestion=run_ingestion,
                timeframe_override=timeframe_override,
                provider=config.LLM_PROVIDER,
                model=config.LLM_MODEL,
                log_sink=_sink,
            )
        st.session_state["last_run"] = {
            "ran_at": result.started_at,
            "params": {"currencies": list(selected),
                       "run_ingestion": run_ingestion,
                       "timeframe_override": timeframe_override},
            "result": result,
        }
        st.session_state["log_lines"] = log_lines
        status_box.caption(f"Run finished at {result.finished_at:%H:%M:%S} UTC.")
        if result.errors:
            for err in result.errors:
                st.warning(err)


# ============================================================================
# Data for rendering
# ============================================================================

live_entry = st.session_state.get("last_run")
live_result = live_entry["result"] if live_entry else None

snapshot = None
snapshot_error = None
try:
    snapshot = service.load_db_snapshot()
except Exception as exc:
    snapshot_error = str(exc)


# ============================================================================
# Tabs
# ============================================================================

tab_dash, tab_macro, tab_history = st.tabs(["Dashboard", "Macro Board", "History / Memory"])


with tab_dash:
    st.subheader("Market Status")
    render_status_board(live_result=live_result, snapshot=snapshot)
    if snapshot_error:
        st.error(f"Database view unavailable: {snapshot_error}")

    if live_result is not None:
        st.caption(f"Showing LIVE analysis from {live_result.started_at:%Y-%m-%d %H:%M:%S} UTC "
                   f"(cached {config.CACHE_TTL_MINUTES} min).")
        for ccy in live_result.currencies:
            render_live_currency_card(live_result.currencies[ccy])

        # Technical chart (levels available after a live run)
        chartable = [c for c, r in live_result.currencies.items() if r.ticker]
        if chartable:
            with st.expander("Price Chart (SMC levels)"):
                c_sel = st.selectbox("Currency", chartable, key="live_chart_ccy")
                res = live_result.currencies[c_sel]
                tf = (res.plan.technical_timeframe if res.plan else "H4")
                df = fetch_ohlcv(res.ticker, tf)
                if df is not None:
                    st.plotly_chart(
                        candlestick_with_smc(df, res.tech_metrics,
                                             title=f"{res.ticker} — {tf}"),
                        use_container_width=True)
                else:
                    st.warning("Price data unavailable for charting.")

    elif snapshot is not None:
        st.caption(f"Showing latest DB signals (fetched {snapshot.fetched_at:%H:%M:%S} UTC). "
                   "Press ▶ Run Live Analysis for a fresh LLM run.")
        shown = 0
        for ccy in selected:
            snap = snapshot.currencies.get(ccy)
            if snap:
                render_db_currency_card(snap)
                shown += 1
        if shown == 0:
            st.info("Select currencies in the sidebar to view their latest DB signals.")


with tab_macro:
    st.subheader("Cross-Asset & Global Macro")
    if live_result is not None and (live_result.global_report or live_result.detailed_reports):
        st.markdown("### Global Macro Executive Summary")
        st.write(live_result.global_report or "No global report.")
        st.markdown("### Detailed Currency Analyses")
        for ccy, report in live_result.detailed_reports.items():
            with st.expander(f"{ccy} Analysis", expanded=False):
                st.write(report)
    else:
        st.info("Macro reports are generated during a live analysis and are not "
                "persisted to the DB. Run a live analysis to see them here.")


with tab_history:
    st.subheader("Trade History & Memory")

    hcol1, hcol2 = st.columns([1, 3])
    hist_ccy = hcol1.selectbox("Currency filter", ["All"] + all_currencies)
    range_label = hcol2.selectbox(
        "Range", ["last_7_days", "last_14_days", "last_30_days", "last_90_days", "all"],
        index=2,
    )

    try:
        history = service.load_trade_history(
            currency=None if hist_ccy == "All" else hist_ccy,
            range_type=range_label,
        )
    except Exception as exc:
        history = []
        st.error(f"Failed to load trade history: {exc}")

    if history:
        st.plotly_chart(win_loss_bar(history), use_container_width=True)
        st.dataframe(
            [{
                "created_at": r["created_at"],
                "currency": r["currency"],
                "direction": r["direction"],
                "entry": f"{r['entry_zone_low']}–{r['entry_zone_high']}",
                "SL": r["stop_loss"],
                "TP": r["take_profit"],
                "status": r["status"],
                "evaluated_price": r["evaluated_price"],
                "fund_score": r["fundamental_score"],
                "tech_score": r["technical_score"],
                "session": r["temporal_session"],
            } for r in history],
            use_container_width=True,
        )
    else:
        st.info("No trade outcomes in this range.")

    with st.expander("Recent Composite Signals"):
        try:
            comps = service.load_composite_history(
                currency=None if hist_ccy == "All" else hist_ccy,
                range_type=range_label,
            )
            if comps:
                st.dataframe(
                    [{
                        "created_at": c["created_at"],
                        "currency": c["currency"],
                        "direction": c["direction"],
                        "score": c["final_score"],
                        "confidence": c["confidence"],
                        "tradable": c["is_tradable"],
                        "confluence": c["confluence_status"],
                    } for c in comps],
                    use_container_width=True,
                )
            else:
                st.caption("No composite signals in this range.")
        except Exception as exc:
            st.error(f"Failed to load composite history: {exc}")

    with st.expander("Technical Logs"):
        logs = st.session_state.get("log_lines", [])
        if logs:
            st.code("\n".join(logs), language="text")
        else:
            st.caption("No live run logs in this session.")
