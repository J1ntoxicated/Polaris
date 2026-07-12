"""Price-through maker-fill shadow (maker_fill_sim R1 2026-07-12 debate).

DEMO/PAPER only — virtual funds. Unlike ``maker_fill_shadow`` (maker-only,
architecturally dead while ``real_roundtrip=False``), this records ONE row per
REAL entry fill (the current all-taker execution) carrying the touch a passive
maker limit would have rested at. Resolution — did price trade through the
touch (price-through-maker) or not (missed-opportunity) — is computed OFFLINE
against forward bars, never at write time (no fabricated outcome).

Instrumentation only — never influences the live entry/exit decision.
"""

from __future__ import annotations

import sqlite3

from polaris.core.economics.price_through_shadow import (
    log_price_through_entry,
    resolve_price_through,
)


def _conn() -> sqlite3.Connection:
    from polaris.storage.schema import ALL_DDL

    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    return conn


# ── resolve_price_through (pure, offline resolution against forward bars) ──


def test_traded_through_when_forward_low_reaches_touch_on_buy() -> None:
    # BUY: touch_px is the bid a passive order rests at (below the taker fill).
    # Price dips to/through it in a later bar → the maker WOULD have filled.
    outcome = resolve_price_through(
        side="buy", touch_px=99.0, fill_px=100.0,
        forward_highs=[100.5, 100.2], forward_lows=[100.1, 98.5],
        forward_closes=[100.3, 99.0],
    )
    assert outcome.traded_through is True
    assert outcome.price_improve_bps > 0.0  # cheaper than the taker fill
    assert outcome.missed_move_bps == 0.0


def test_no_trade_through_records_missed_opportunity_on_buy() -> None:
    # Price never comes down to the resting touch within the forward window —
    # unfilled; the subsequent move away from touch is the opportunity cost.
    outcome = resolve_price_through(
        side="buy", touch_px=99.0, fill_px=100.0,
        forward_highs=[101.0, 102.0], forward_lows=[100.5, 101.5],
        forward_closes=[100.8, 101.8],
    )
    assert outcome.traded_through is False
    assert outcome.price_improve_bps == 0.0
    assert outcome.missed_move_bps > 0.0  # price ran away from the resting bid


def test_sell_side_uses_ask_touch_and_high_for_trade_through() -> None:
    # SELL: touch_px is the ask a passive order rests at (above the taker fill).
    outcome = resolve_price_through(
        side="sell", touch_px=101.0, fill_px=100.0,
        forward_highs=[101.5, 100.2], forward_lows=[99.5, 99.8],
        forward_closes=[100.1, 99.9],
    )
    assert outcome.traded_through is True
    assert outcome.price_improve_bps > 0.0


def test_degenerate_inputs_return_flat_outcome() -> None:
    assert resolve_price_through(
        side="buy", touch_px=0.0, fill_px=100.0,
        forward_highs=[], forward_lows=[], forward_closes=[],
    ) == (False, 0.0, 0.0)
    assert resolve_price_through(
        side="buy", touch_px=99.0, fill_px=100.0,
        forward_highs=[], forward_lows=[], forward_closes=[],
    ) == (False, 0.0, 0.0)


# ── log_price_through_entry (write side, degrade-never-crash) ──────────────


def test_log_price_through_entry_persists_row() -> None:
    conn = _conn()
    log_price_through_entry(
        conn, run_id="run1", strategy_id="strat_a", venue="okx",
        symbol="BTC-USDT", side="buy", fill_px=100.0, touch_px=99.0, now_ts=1000,
    )
    row = conn.execute(
        "SELECT strategy_id, venue, symbol, side, fill_px, touch_px, "
        "current_fee_bps FROM price_through_shadow"
    ).fetchone()
    assert row is not None
    assert row[0] == "strat_a"
    assert row[1] == "okx"
    assert row[3] == "buy"
    assert row[4] == 100.0
    assert row[5] == 99.0
    assert row[6] > 0.0


def test_log_price_through_entry_none_conn_is_noop() -> None:
    log_price_through_entry(
        None, run_id="r", strategy_id="s", venue="okx", symbol="X",
        side="buy", fill_px=1.0, touch_px=1.0, now_ts=1,
    )


def test_log_price_through_entry_skips_without_touch() -> None:
    # No fabricated row when the touch is unknown (0.0 = no quote available).
    conn = _conn()
    log_price_through_entry(
        conn, run_id="r", strategy_id="s", venue="okx", symbol="X",
        side="buy", fill_px=100.0, touch_px=0.0, now_ts=1,
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM price_through_shadow"
    ).fetchone()[0] == 0


def test_log_price_through_entry_routes_through_db_writer_when_wired() -> None:
    # db-writer-reader-split (task rule: new writes go via the queue). A wired
    # db_writer receives the job instead of the caller's own conn.execute.
    conn = _conn()
    submitted: list[tuple[str, tuple[object, ...]]] = []

    class _FakeWriter:
        def submit(self, fn: object, *, label: str = "") -> None:
            # Run the job against our own conn to prove it round-trips the
            # exact same statement the direct-conn path would issue.
            fn(conn)  # type: ignore[operator]
            submitted.append((label, ()))

    log_price_through_entry(
        conn, run_id="r", strategy_id="s", venue="okx", symbol="X",
        side="buy", fill_px=100.0, touch_px=99.0, now_ts=1,
        db_writer=_FakeWriter(),  # type: ignore[arg-type]
    )
    assert len(submitted) == 1
    assert submitted[0][0] == "price_through_shadow"
    assert conn.execute(
        "SELECT COUNT(*) FROM price_through_shadow"
    ).fetchone()[0] == 1
