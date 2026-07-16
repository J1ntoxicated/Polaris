"""③ maker_fill_shadow persist wiring (FIX-EXEC, [[weekend_maker_honest_rerun_2026-06-28]]).

DEMO/PAPER only — virtual funds. The persisting ``log_maker_fill`` was NEVER wired
into production (only the entry-time ``_log_maker_fill_basis`` INFO log fired) — so
``maker_fill_shadow`` was empty live despite real maker fills resting at the touch.
This wires the persist into the ENTRY open path (where the conn IS held and the fill
happens exactly once → no double-count): the post-only fill carries its touch / repost
/ outcome on the ``OpenAttempt``, and the open transaction persists ONE row per fill.

Instrumentation only — the persist NEVER touches sizing / the 9-stack / the −1.0R
rail / the trade decision (degrade-never-crash): a None conn or a missing maker-fill
field is a no-op.
"""

from __future__ import annotations

import sqlite3

from polaris.scripts._smoke_roundtrip_shared import OpenAttempt


def _conn() -> sqlite3.Connection:
    from polaris.storage.schema import ALL_DDL

    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    return conn


# ── OpenAttempt carries the maker-fill metadata (default None → byte-identical) ─


def test_open_attempt_maker_fields_default_none() -> None:
    # Every EXISTING caller that builds an OpenAttempt without the maker fields is
    # byte-identical: the new fields default to None / 0.
    a = OpenAttempt(fill=None)
    assert a.maker_touch_px is None
    assert a.maker_reposts == 0
    assert a.maker_outcome is None


def test_open_attempt_carries_maker_fill_metadata() -> None:
    a = OpenAttempt(
        fill=None, maker_touch_px=100.0, maker_reposts=2, maker_outcome="clean_fill"
    )
    assert a.maker_touch_px == 100.0
    assert a.maker_reposts == 2
    assert a.maker_outcome == "clean_fill"


# ── the entry-path persist writes exactly one row per maker fill ───────────────


def test_persist_maker_fill_from_attempt_writes_one_row() -> None:
    from polaris.scripts._production_pipeline import _persist_maker_fill_shadow

    conn = _conn()
    attempt = OpenAttempt(
        fill=None, maker_touch_px=100.0, maker_reposts=1, maker_outcome="clean_fill"
    )
    _persist_maker_fill_shadow(
        conn, attempt=attempt, run_id="run1",
        strategy_id="weekend_thin_book_flush_maker", venue="okx",
        symbol="BTC-USDT", side="long", fill_price=99.9,
    )
    rows = conn.execute(
        "SELECT strategy_id, venue, symbol, side, touch_px, fill_px, "
        "entry_basis_bps, outcome, reposts FROM maker_fill_shadow"
    ).fetchall()
    assert len(rows) == 1  # exactly one row per fill — no double-count
    r = rows[0]
    assert r[0] == "weekend_thin_book_flush_maker"
    assert r[1] == "okx"
    assert r[3] == "buy"  # a long entry is a BUY maker leg
    assert r[4] == 100.0
    assert r[5] == 99.9
    assert r[6] > 0.0  # fill below touch on a buy → favourable basis
    assert r[7] == "clean_fill"
    assert r[8] == 1


def test_persist_maker_fill_noop_without_maker_metadata() -> None:
    # A non-maker (taker / market) fill carries no touch_px → NO row (the persist
    # is maker-only; a taker entry never fabricates a maker shadow row).
    from polaris.scripts._production_pipeline import _persist_maker_fill_shadow

    conn = _conn()
    _persist_maker_fill_shadow(
        conn, attempt=OpenAttempt(fill=None), run_id="r",
        strategy_id="s", venue="okx", symbol="X", side="long", fill_price=10.0,
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM maker_fill_shadow"
    ).fetchone()[0] == 0


def test_persist_maker_fill_none_conn_is_noop() -> None:
    # degrade-never-crash: the fill already happened; a None conn must not raise.
    from polaris.scripts._production_pipeline import _persist_maker_fill_shadow

    _persist_maker_fill_shadow(
        None, attempt=OpenAttempt(fill=None, maker_touch_px=1.0, maker_outcome="x"),
        run_id="r", strategy_id="s", venue="okx", symbol="X", side="long",
        fill_price=1.0,
    )


def test_persist_maker_fill_threads_db_writer_when_wired() -> None:
    # storage-split (round 4 fix): maker_fill_shadow is marketdata-domain —
    # a wired db_writer (state.md_db_writer at the production call site)
    # receives the job instead of the caller's own conn.execute.
    from polaris.scripts._production_pipeline import _persist_maker_fill_shadow

    conn = _conn()
    submitted: list[str] = []

    class _FakeWriter:
        def submit(self, fn: object, *, label: str = "") -> None:
            fn(conn)  # type: ignore[operator]
            submitted.append(label)

    attempt = OpenAttempt(
        fill=None, maker_touch_px=100.0, maker_reposts=1, maker_outcome="clean_fill"
    )
    _persist_maker_fill_shadow(
        conn, attempt=attempt, run_id="run1",
        strategy_id="weekend_thin_book_flush_maker", venue="okx",
        symbol="BTC-USDT", side="long", fill_price=99.9,
        db_writer=_FakeWriter(),  # type: ignore[arg-type]
    )
    assert submitted == ["maker_fill_shadow"]
    assert conn.execute(
        "SELECT COUNT(*) FROM maker_fill_shadow"
    ).fetchone()[0] == 1
