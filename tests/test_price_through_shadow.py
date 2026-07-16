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

import pytest

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


def test_exact_touch_without_crossing_is_not_traded_through_on_buy() -> None:
    # OVER-FILL BIAS regression (maker_fill_sim R1): a bar that only TOUCHES
    # the resting bid (low == touch_px) without going strictly below it must
    # NOT count as a fill — a bare touch is a queue-position toss-up, not a
    # guaranteed maker fill.
    outcome = resolve_price_through(
        side="buy", touch_px=99.0, fill_px=100.0,
        forward_highs=[99.5, 100.6], forward_lows=[99.0, 100.1],
        forward_closes=[99.3, 100.4],
    )
    assert outcome.traded_through is False
    assert outcome.price_improve_bps == 0.0


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


# ---------------------------------------------------------------------------
# storage-split (round 4 LOW fix): the entry-path touch lookup
# (``_production_pipeline._fetch_touch_px``) reads quote_ticks, which is
# marketdata-domain.
# ---------------------------------------------------------------------------


def _insert_tick(conn: sqlite3.Connection, *, bid: float, ask: float) -> None:
    conn.execute(
        "INSERT INTO quote_ticks (instrument_id, venue, symbol, ts, bid, ask, "
        "mid, spread_bps, bid_size, ask_size, source) VALUES "
        "('okx:BTC-USDT', 'okx', 'BTC-USDT', 1000, ?, ?, 100.0, 1.0, 1.0, 1.0, 'ws')",
        (bid, ask),
    )


def test_fetch_touch_px_reads_bid_for_buy_and_ask_for_sell() -> None:
    from polaris.scripts._production_pipeline import _fetch_touch_px

    conn = _conn()
    _insert_tick(conn, bid=99.0, ask=101.0)
    assert _fetch_touch_px(conn, venue="okx", symbol="BTC-USDT", side="buy") == 99.0
    assert _fetch_touch_px(conn, venue="okx", symbol="BTC-USDT", side="sell") == 101.0


@pytest.mark.asyncio
async def test_reserve_and_submit_touch_px_resolves_from_md_conn() -> None:
    """quote_ticks is marketdata-domain — the entry-path touch lookup must
    resolve against state.md_conn, not the (post-split, permanently empty)
    trading conn ``reserve_and_submit`` is otherwise driven by. If the fix
    regresses, ``_fetch_touch_px`` reads the empty trading conn -> touch_px
    is None -> NO price_through_shadow row is written at all."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import reserve_and_submit
    from polaris.scripts.production_paper_loop import ProdLoopState
    from polaris.storage.schema import ALL_DDL
    from polaris.strategies.base import RawSignal

    reset_process_fence()
    # autocommit (isolation_level=None), matching the production loop's conn —
    # reserve_and_submit issues its own explicit BEGIN IMMEDIATE below.
    conn = sqlite3.connect(":memory:", isolation_level=None)  # trading, quote_ticks EMPTY
    for stmt in ALL_DDL:
        conn.execute(stmt)
    md_conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        md_conn.execute(stmt)
    _insert_tick(md_conn, bid=99.0, ask=101.0)  # ONLY in md_conn

    sig = RawSignal(
        signal_id="sig-touch", strategy_id="ema_crossover", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_cross_sectional_momo",
    )
    state = ProdLoopState(md_conn=md_conn)
    trade = await reserve_and_submit(
        conn=conn, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=200.0, last_price=100.0, now_ts=1_780_000_000,
    )
    assert trade is not None
    row = conn.execute(
        "SELECT touch_px FROM price_through_shadow WHERE strategy_id = 'ema_crossover'"
    ).fetchone()
    assert row is not None  # a None touch_px would have skipped the row entirely
    assert row[0] == 99.0  # buy → bid, resolved from md_conn
    md_conn.close()
