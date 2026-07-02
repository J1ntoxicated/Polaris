"""Bar-path ``trade_eligible`` ENFORCEMENT gap — close the #41-class defer seam.

DEMO/PAPER · AGGRESSIVE bias preserved · flow_not_block · 9-stack ban untouched.
No sizing multiplier is touched here — this is a data-quality CORRECTNESS overlay
at the bar-pipeline entry seam, mirroring the existing ``okx_unsettleable_set`` /
``okx_unsettleable_entry_defers`` pattern (test_okx_bar_settleable_gate.py) for the
EntranceJudge's ``trade_eligible`` flag.

Root cause this proves fixed: ``EntranceJudge`` (``entrance.py``) sets
``watchlist_focus.trade_eligible=0`` for a name that fails the venue liquidity
floor and/or the opportunity-score floor (``get_focus_targets(eligible_only=True)``
reads this — the tick-engine TRADE set). The BAR pipeline dispatch
(``_production_tick._run_tick``) calls ``get_focus_targets`` WITHOUT
``eligible_only`` (the WATCH set — everything) and never re-checks the flag before
emitting an order, so a name EntranceJudge already marked ``trade_eligible=0``
(illiquid / thin / wide-spread) still reached the OKX/Alpaca order path.

flow_not_block: the signal is ALREADY emitted+persisted before this check; only
the ENTRY (order submission) is deferred. The name stays WATCHED/SIGNALED/
streamed. A held position is force-seated by ``get_focus_targets`` regardless of
eligibility, so an open position is NEVER touched by this gate (exits run via the
recalc loop, not this entry seam).
"""

from __future__ import annotations

import sqlite3

from polaris.scripts._production_layers import trade_ineligible_set
from polaris.storage.schema import init_db


def _seed_focus(conn: sqlite3.Connection, cycle_ts: int = 1000) -> None:
    rows = [
        # (venue, symbol, trade_eligible, focus_rank)
        ("alpaca", "GVH", 0, 1),     # illiquid microcap → ineligible
        ("alpaca", "BIVI", 0, 2),    # illiquid microcap → ineligible
        ("alpaca", "PRGS", 1, 3),    # large-cap → eligible
        ("okx", "BTC-USDT", 1, 4),   # eligible
    ]
    for venue, symbol, eligible, rank in rows:
        conn.execute(
            "INSERT INTO watchlist_focus "
            "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket, "
            " trade_eligible) VALUES (?,?,?,1.0,?,'core',?)",
            (cycle_ts, venue, symbol, rank, eligible),
        )
    # Legacy/un-judged row: column DEFAULT is 1 (NOT NULL) — omit the column
    # entirely to exercise the DEFAULT path (equivalent to a pre-Increment-1 row).
    conn.execute(
        "INSERT INTO watchlist_focus "
        "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket) "
        "VALUES (?,?,?,1.0,?,'core')",
        (cycle_ts, "okx", "THIN-USDT", 5),
    )


def test_trade_ineligible_set_excludes_only_flagged_rows() -> None:
    conn = init_db(":memory:")
    _seed_focus(conn)

    ineligible = trade_ineligible_set(conn)

    assert ("alpaca", "GVH") in ineligible
    assert ("alpaca", "BIVI") in ineligible
    assert ("alpaca", "PRGS") not in ineligible
    assert ("okx", "BTC-USDT") not in ineligible
    # Legacy/un-judged row (column DEFAULT 1) reads as eligible — flow_not_block.
    assert ("okx", "THIN-USDT") not in ineligible
    conn.close()


def test_trade_ineligible_set_uses_latest_cycle_only() -> None:
    conn = init_db(":memory:")
    _seed_focus(conn, cycle_ts=1000)
    # A later cycle re-judges GVH as eligible (score recovered) — the stale
    # earlier-cycle ineligible row must NOT leak forward.
    conn.execute(
        "INSERT INTO watchlist_focus "
        "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket, "
        " trade_eligible) VALUES (2000,'alpaca','GVH',1.0,1,'core',1)"
    )
    ineligible = trade_ineligible_set(conn)
    assert ("alpaca", "GVH") not in ineligible
    conn.close()


def test_trade_ineligible_set_empty_when_no_focus_rows() -> None:
    conn = init_db(":memory:")
    assert trade_ineligible_set(conn) == set()
    conn.close()


# ---------------------------------------------------------------------------
# Source-level lint: the defer is actually WIRED at the bar-path entry seam,
# mirroring the okx_unsettleable defer (emit-before/defer-after, no block).
# ---------------------------------------------------------------------------


def test_trade_ineligible_defer_is_wired_into_run_tick() -> None:
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    assert "trade_ineligible_set" in src, (
        "trade_eligible bar-path defer must be wired into _run_tick — otherwise "
        "EntranceJudge's ineligibility verdict never reaches the bar order path."
    )
    assert "trade_ineligible_entry_defers" in src, (
        "the defer must be counted (telemetry), mirroring "
        "okx_unsettleable_entry_defers."
    )


def test_trade_ineligible_defer_happens_after_signal_persist() -> None:
    """The defer ``continue`` must sit AFTER ``persist_emitted_signal`` (emit
    first, defer only the order) — never before, or the WATCH-side visibility
    (dashboard/streamed signal) would be silently blocked too (a real block,
    not flow_not_block)."""
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    persist_idx = src.index("persist_emitted_signal(")
    defer_idx = src.index("trade_ineligible_entry_defers")
    okx_defer_idx = src.index("okx_unsettleable_entry_defers", persist_idx)
    assert persist_idx < defer_idx, (
        "trade_ineligible defer must be wired AFTER persist_emitted_signal "
        "(emit-before/defer-after — flow_not_block)."
    )
    # Must sit alongside (adjacent to) the existing okx_unsettleable defer, not
    # duplicated ahead of the emit — both are post-persist entry-only defers.
    assert persist_idx < okx_defer_idx
