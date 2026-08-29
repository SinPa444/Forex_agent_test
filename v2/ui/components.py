"""
ui/components.py
================

Reusable Streamlit render helpers: status board, currency cards, badges.
Presentation only — no business logic lives here.
"""

from __future__ import annotations

import streamlit as st

from core.routing import resolve_asset_route, translate_instrument_direction, direction_label

# Badge colors (dark trading theme)
_GREEN = "#22c55e"
_AMBER = "#f59e0b"
_RED = "#ef4444"
_GRAY = "#6b7280"

_DECISION_COLOR = {"APPROVED": _GREEN, "WAIT": _AMBER, "REJECTED": _RED}
_DIR_COLOR = {1: _GREEN, -1: _RED, 0: _GRAY}


def badge(text: str, color: str) -> str:
    return (
        f'<span style="background-color:{color}22; color:{color}; '
        f'border:1px solid {color}; border-radius:6px; padding:2px 10px; '
        f'font-size:0.85rem; font-weight:600; margin-right:6px;">{text}</span>'
    )


def direction_badge(currency: str, direction: int) -> str:
    """Currency-native direction + instrument-view translation."""
    route = resolve_asset_route(currency)
    label = direction_label(direction)
    html = badge(f"{label} CCY", _DIR_COLOR.get(direction, _GRAY))
    if route:
        inst = translate_instrument_direction(direction, route.alignment)
        html += badge(f"{direction_label(inst)} {route.ticker}", _DIR_COLOR.get(inst, _GRAY))
    return html


def decision_badge(decision: str) -> str:
    return badge(decision, _DECISION_COLOR.get(decision, _GRAY))


def render_status_board(live_result=None, snapshot=None) -> None:
    """Top-of-page board: decision counts + per-currency chips."""
    chips = []
    counts = {"APPROVED": 0, "WAIT": 0, "REJECTED": 0}

    if live_result is not None and live_result.currencies:
        for ccy, res in live_result.currencies.items():
            if res.risk is not None:
                dec = res.risk.decision
                counts[dec] = counts.get(dec, 0) + 1
                score = res.composite.final_score if res.composite else 0.0
                chips.append((ccy, dec, score))
    elif snapshot is not None:
        for ccy, snap in snapshot.currencies.items():
            comp = snap.latest_composite
            if comp:
                dec = "APPROVED" if comp["is_tradable"] else "—"
                if comp["is_tradable"]:
                    counts["APPROVED"] += 1
                chips.append((ccy, dec, comp["final_score"]))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("APPROVED", counts.get("APPROVED", 0))
    c2.metric("WAIT", counts.get("WAIT", 0))
    c3.metric("REJECTED", counts.get("REJECTED", 0))
    c4.metric("Signals", len(chips))

    if chips:
        html = ""
        for ccy, dec, score in chips:
            html += badge(f"{ccy} {score:+.2f}", _DECISION_COLOR.get(dec, _GRAY))
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.info("No signals available yet. Run a live analysis or wait for the next pipeline run.")


def render_trade_plan(trade_plan) -> None:
    """Highlighted trade plan box (Entry / SL / TP / R:R)."""
    st.markdown(
        f"""
        <div style="border:1px solid {_GREEN}; border-radius:10px; padding:14px;
                    background-color:{_GREEN}11; margin:8px 0;">
            <div style="font-weight:700; color:{_GREEN}; margin-bottom:6px;">TRADE PLAN</div>
            <b>Entry Zone:</b> {trade_plan.entry_zone} &nbsp;|&nbsp;
            <b>SL:</b> {trade_plan.stop_loss} &nbsp;|&nbsp;
            <b>TP:</b> {trade_plan.take_profit} &nbsp;|&nbsp;
            <b>R:R</b> {trade_plan.risk_reward_ratio}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_memory_metric(memory: dict) -> None:
    if not memory:
        return
    total = memory.get("total_trades", 0)
    wins = memory.get("wins", 0)
    losses = memory.get("losses", 0)
    wr = memory.get("win_rate", 0.0)
    st.caption(f"Trade Memory: {total} trades | Win Rate {wr:.0f}% ({wins}W/{losses}L)")


def render_live_currency_card(res) -> None:
    """Card for one currency from a live AnalysisResult."""
    title = f"{res.currency} ({res.ticker})" if res.ticker else res.currency
    with st.container(border=True):
        header = f"**{title}**"
        if res.risk is not None:
            header += "  " + decision_badge(res.risk.decision)
        if res.composite is not None:
            header += "  " + direction_badge(res.currency, res.composite.direction)
        st.markdown(header, unsafe_allow_html=True)

        if res.errors:
            for err in res.errors:
                st.warning(err)

        cols = st.columns(3)
        if res.composite is not None:
            cols[0].metric("Final Score", f"{res.composite.final_score:+.2f}")
            cols[1].metric("Confidence", f"{res.composite.confidence:.2f}")
            cols[2].metric("Confluence", res.composite.confluence_status)
        elif res.tech_report is not None:
            cols[0].metric("Tech Score", f"{res.tech_report.score:+.2f}")
            cols[1].metric("Tech Conf", f"{res.tech_report.confidence:.2f}")

        if res.risk is not None and res.risk.trade_plan is not None:
            render_trade_plan(res.risk.trade_plan)

        if res.tech_metrics is None and res.plan is not None and res.plan.activate_technical:
            st.warning("Technical Data Unavailable")

        render_memory_metric(res.memory)

        if res.plan is not None:
            with st.expander("Execution Plan (Head Agent)"):
                st.write(f"Regime: **{res.plan.market_regime}** | "
                         f"Events={res.plan.activate_events} News={res.plan.activate_news} "
                         f"Tech={res.plan.activate_technical} ({res.plan.technical_timeframe})")
                st.write(res.plan.reasoning)

        if res.tech_report is not None:
            with st.expander("Technical Report"):
                st.write(f"**Strategy:** {res.tech_report.strategy}")
                st.write(res.tech_report.reasoning)
                if res.tech_metrics is not None:
                    comp = res.tech_metrics.components
                    st.caption(
                        f"Structure={comp.structure} | SMC Loc={comp.smc_location} | "
                        f"Trend={comp.trend} | Momentum={comp.momentum} | "
                        f"Volatility={comp.volatility} | PA={comp.price_action} | "
                        f"MTF={comp.mtf_confluence}"
                    )
                    st.caption(
                        f"Price={res.tech_metrics.current_price} | ADX={res.tech_metrics.adx_value} | "
                        f"RSI={res.tech_metrics.rsi} | BOS={res.tech_metrics.recent_bos}"
                    )

        if res.risk is not None:
            with st.expander("Risk Manager Reasoning"):
                st.write(res.risk.reasoning)

        if res.composite is not None:
            with st.expander("Fundamental / Combined Reasoning"):
                st.write(res.composite.reasoning)


def render_db_currency_card(snap) -> None:
    """Card for one currency from the DB snapshot (no live run)."""
    comp = snap.latest_composite
    title = f"{snap.currency} ({snap.ticker})" if snap.ticker else snap.currency
    with st.container(border=True):
        header = f"**{title}**"
        if comp:
            header += "  " + badge("TRADABLE", _GREEN) if comp["is_tradable"] else ""
            header += "  " + direction_badge(snap.currency, comp["direction"])
        st.markdown(header, unsafe_allow_html=True)

        if comp:
            cols = st.columns(3)
            cols[0].metric("Final Score", f"{comp['final_score']:+.2f}")
            cols[1].metric("Confidence", f"{comp['confidence']:.2f}")
            cols[2].metric("Confluence", comp["confluence_status"])
            st.caption(f"Last signal: {comp['created_at']}")
            with st.expander("Reasoning"):
                st.write(comp["reasoning"])
        else:
            st.caption("No composite signal in DB for this currency.")

        for trade in snap.open_trades:
            st.markdown(
                f"**Open trade (PENDING):** dir={direction_label(trade['direction'])} | "
                f"Entry {trade['entry_zone_low']}–{trade['entry_zone_high']} | "
                f"SL {trade['stop_loss']} | TP {trade['take_profit']}"
            )

        render_memory_metric(snap.memory)
