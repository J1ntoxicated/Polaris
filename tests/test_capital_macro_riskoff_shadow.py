"""capital_macro_riskoff_catalyst SHADOW emit tagger — P3 promotion.

DEMO/PAPER · aggressive · flow_not_block. Unit coverage for
``log_capital_macro_riskoff_shadow`` (pure sidecar writer, mirrors
``tsmom_literature_shadow``'s own test shape) — the strategy is registered but
``dispatch_eligible=False``, so this shadow log is the ONLY observation of its
would-be signal.
"""

from __future__ import annotations

import sqlite3

from polaris.core.pipeline.agents.capital_macro_riskoff_shadow import (
    log_capital_macro_riskoff_shadow,
)
from polaris.storage.schema import ALL_DDL
from polaris.strategies.base import AltDataView, BarView, MarketView

NOW = 1_780_000_000


def _memdb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _bars(n: int = 10) -> list[BarView]:
    return [
        BarView(
            ts=NOW - (n - i) * 3600, open=1900.0, high=1905.0, low=1895.0,
            close=1900.0, volume=100.0,
        )
        for i in range(n)
    ]


def _mv(symbol: str, venue: str, altdata: AltDataView) -> MarketView:
    return MarketView(
        symbol=symbol, venue=venue, timeframe="1H", bars=_bars(),
        last_price=1900.0, spread_bps=4.0, altdata=altdata,
    )


def _row(conn: sqlite3.Connection) -> tuple:
    row = conn.execute(
        "SELECT technical_decision, technical_scalar, technical_flags, "
        "gpt_decision, mismatch FROM gate_shadow_events "
        "WHERE technical_flags = 'capital_macro_riskoff_catalyst_shadow'"
    ).fetchone()
    assert row is not None
    return row


def test_none_conn_is_a_no_op() -> None:
    log_capital_macro_riskoff_shadow(
        None, run_id="r", signal_id=None, venue="capital", symbol="GOLD",
        regime="chop", market_view=_mv("GOLD", "capital", AltDataView()),
        now_ts=NOW,
    )  # must not raise


def test_non_gold_symbol_logs_nothing() -> None:
    conn = _memdb()
    log_capital_macro_riskoff_shadow(
        conn, run_id="r", signal_id=None, venue="capital", symbol="EURUSD",
        regime="chop",
        market_view=_mv("EURUSD", "capital", AltDataView(vix=30.0, hy_spread=600.0)),
        now_ts=NOW,
    )
    count = conn.execute("SELECT COUNT(*) FROM gate_shadow_events").fetchone()[0]
    assert count == 0


def test_non_capital_venue_logs_nothing() -> None:
    conn = _memdb()
    log_capital_macro_riskoff_shadow(
        conn, run_id="r", signal_id=None, venue="okx", symbol="GOLD",
        regime="chop",
        market_view=_mv("GOLD", "okx", AltDataView(vix=30.0, hy_spread=600.0)),
        now_ts=NOW,
    )
    count = conn.execute("SELECT COUNT(*) FROM gate_shadow_events").fetchone()[0]
    assert count == 0


def test_below_threshold_logs_flat() -> None:
    conn = _memdb()
    log_capital_macro_riskoff_shadow(
        conn, run_id="r", signal_id=None, venue="capital", symbol="GOLD",
        regime="chop",
        market_view=_mv("GOLD", "capital", AltDataView(vix=10.0, hy_spread=200.0)),
        now_ts=NOW,
    )
    decision, scalar, flags, gpt_decision, mismatch = _row(conn)
    assert decision == "flat"
    assert scalar == 0.0
    assert gpt_decision == ""
    assert mismatch == 0


def test_above_threshold_logs_the_would_be_long_signal() -> None:
    conn = _memdb()
    log_capital_macro_riskoff_shadow(
        conn, run_id="r", signal_id=None, venue="capital", symbol="GOLD",
        regime="risk_off",
        market_view=_mv("GOLD", "capital", AltDataView(vix=36.0, hy_spread=700.0)),
        now_ts=NOW,
    )
    decision, scalar, flags, _gpt, _mismatch = _row(conn)
    assert decision == "long"
    assert scalar > 0.0
    assert flags == "capital_macro_riskoff_catalyst_shadow"


def test_never_writes_to_positions_or_orders() -> None:
    """Structural behavior-0 check: only gate_shadow_events is touched."""
    conn = _memdb()
    log_capital_macro_riskoff_shadow(
        conn, run_id="r", signal_id=None, venue="capital", symbol="GOLD",
        regime="risk_off",
        market_view=_mv("GOLD", "capital", AltDataView(vix=36.0, hy_spread=700.0)),
        now_ts=NOW,
    )
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
