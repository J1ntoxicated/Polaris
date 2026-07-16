"""Real-fee maker-fill shadow measurement (#77 component C).

DEMO/PAPER only. OKX demo bills a FLAT 70 bps (taker == maker), so the maker
edge is INVISIBLE in demo P&L. This shadow records, per maker fill:

  * entry-BASIS — how much better the fill landed vs the touch the bid was
    posted at (a passive post-only fills AT or better than the touch);
  * the real-fee NET via ``real_fee_bps(is_maker=True)`` (OKX spot maker 8 bps),
    the ONLY place the weekend maker edge is measurable before go-live;
  * a fill / pick-off label so the clean-fill vs adverse-selection split is
    countable.

Instrumentation only — never influences the live path; a missing table / None
conn is a no-op (degrade-never-crash).
"""

from __future__ import annotations

import sqlite3

from polaris.core.economics.maker_fill_shadow import (
    compute_entry_basis_bps,
    log_maker_fill,
    maker_net_bps,
)


def _conn() -> sqlite3.Connection:
    from polaris.storage.schema import ALL_DDL

    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    return conn


def test_entry_basis_better_when_fill_below_touch_for_buy() -> None:
    # BUY: a fill price BELOW the posted touch is FAVOURABLE → positive basis bps.
    basis = compute_entry_basis_bps(side="buy", touch_px=100.0, fill_px=99.9)
    assert basis > 0.0
    # Exactly at touch → ~0 basis.
    assert compute_entry_basis_bps(side="buy", touch_px=100.0, fill_px=100.0) == 0.0
    # A worse (higher) fill on a buy → negative basis (adverse).
    assert compute_entry_basis_bps(side="buy", touch_px=100.0, fill_px=100.1) < 0.0


def test_maker_net_uses_real_maker_fee_not_demo() -> None:
    # Net edge in bps = entry basis credit minus the real maker round-trip fee.
    # OKX real maker = 8 bps/leg → 16 bps round trip; demo's 70 bps is NOT used.
    net = maker_net_bps(venue="okx", entry_basis_bps=20.0, gross_move_bps=0.0)
    # 20 bps basis credit - 16 bps real maker round trip = +4 bps.
    assert abs(net - 4.0) < 1e-6


def test_log_maker_fill_persists_row() -> None:
    conn = _conn()
    log_maker_fill(
        conn,
        run_id="run1",
        strategy_id="weekend_thin_book_flush_maker",
        venue="okx",
        symbol="BTC-USDT",
        side="buy",
        touch_px=100.0,
        fill_px=99.9,
        outcome="clean_fill",
        reposts=2,
    )
    row = conn.execute(
        "SELECT strategy_id, venue, symbol, entry_basis_bps, real_maker_net_bps, "
        "outcome, reposts FROM maker_fill_shadow"
    ).fetchone()
    assert row is not None
    assert row[0] == "weekend_thin_book_flush_maker"
    assert row[1] == "okx"
    assert row[3] > 0.0  # favourable basis recorded
    assert row[5] == "clean_fill"
    assert row[6] == 2


def test_log_maker_fill_none_conn_is_noop() -> None:
    # degrade-never-crash: a None conn must not raise.
    log_maker_fill(
        None, run_id="r", strategy_id="s", venue="okx", symbol="X",
        side="buy", touch_px=1.0, fill_px=1.0, outcome="clean_fill", reposts=0,
    )


def test_log_maker_fill_routes_through_db_writer_when_wired() -> None:
    # storage-split (round 4 fix): maker_fill_shadow is marketdata-domain —
    # a wired db_writer (state.md_db_writer) receives the job instead of the
    # caller's own conn.execute (mirrors log_price_through_entry's pattern).
    conn = _conn()
    submitted: list[str] = []

    class _FakeWriter:
        def submit(self, fn: object, *, label: str = "") -> None:
            fn(conn)  # type: ignore[operator]
            submitted.append(label)

    log_maker_fill(
        conn, run_id="r", strategy_id="weekend_thin_book_flush_maker",
        venue="okx", symbol="BTC-USDT", side="buy", touch_px=100.0,
        fill_px=99.9, outcome="clean_fill", reposts=1,
        db_writer=_FakeWriter(),  # type: ignore[arg-type]
    )
    assert submitted == ["maker_fill_shadow"]
    assert conn.execute(
        "SELECT COUNT(*) FROM maker_fill_shadow"
    ).fetchone()[0] == 1
