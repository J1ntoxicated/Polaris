"""probe_decisions → G6/G7 tighten-intent wiring helpers (pure, sidecar reader + synth).

DEMO/PAPER · aggressive · flow_not_block. These pure helpers bridge the probe
TIGHTEN producer (observe-only probe_decisions sidecar) to the G6/G7 deterministic
tighten consumer:

- ``latest_probe_tighten`` reads the freshest OPEN (un-backfilled) probe_decisions
  row for a position and returns its action + trail_mult (NULL in observe mode) — the
  read is fail-open (None on any sqlite error / missing table).
- ``synth_tighten_stop`` synthesises the tighter stop from the position's current
  stop + anchor (peak/trough) using the probe ``trail_mult`` when present, else an
  ATR-N% fallback. ratchet-safe by construction — the result is only ever returned to
  G7's ``can_tighten_exit`` which re-checks the ratchet (loosening rejected).
"""

from __future__ import annotations

import sqlite3

from polaris.core.probes.tighten_intent import (
    PROBE_TIGHTEN_APPLY_ACTIONS,
    latest_probe_tighten,
    mark_probe_tighten_applied,
    synth_tighten_stop,
)
from polaris.core.probes.tuning_log import PROBE_DDL


def _probe_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for stmt in PROBE_DDL:
        conn.execute(stmt)
    return conn


def _insert_decision(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    action: str,
    trail_mult: float | None,
    ts: int,
    realized: float | None = None,
) -> None:
    conn.execute(
        "INSERT INTO probe_decisions "
        "(decision_id, eval_id, ts, run_id, position_id, mode, composite_lean, "
        " action, trail_mult, applied, pnl_r_at_decision, pnl_r_truth, mark_source, "
        " mark_age_ms, exit_state, realized_pnl_r) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"d_{ts}", f"e_{ts}", ts, "run", position_id, "observe", -0.49,
            action, trail_mult, 0, -0.05, -0.05, "bar", 0, "open", realized,
        ),
    )


# --------------------------------------------------------------------------- #
# latest_probe_tighten
# --------------------------------------------------------------------------- #


def test_latest_returns_freshest_tighten() -> None:
    conn = _probe_db()
    _insert_decision(conn, position_id="p1", action="HOLD", trail_mult=None, ts=100)
    _insert_decision(conn, position_id="p1", action="TIGHTEN", trail_mult=None, ts=200)
    got = latest_probe_tighten(conn, position_id="p1")
    assert got is not None
    assert got.action == "TIGHTEN"
    assert got.trail_mult is None
    assert got.ts == 200


def test_latest_ignores_stale_when_newer_is_hold() -> None:
    """Freshest row wins — a newer HOLD after an older TIGHTEN returns HOLD (the probe
    no longer leans adverse)."""
    conn = _probe_db()
    _insert_decision(conn, position_id="p1", action="TIGHTEN", trail_mult=None, ts=100)
    _insert_decision(conn, position_id="p1", action="HOLD", trail_mult=None, ts=200)
    got = latest_probe_tighten(conn, position_id="p1")
    assert got is not None
    assert got.action == "HOLD"


def test_latest_only_open_rows() -> None:
    """A backfilled (closed) row is excluded — only the live OPEN decision counts."""
    conn = _probe_db()
    _insert_decision(
        conn, position_id="p1", action="TIGHTEN", trail_mult=None, ts=100, realized=0.3
    )
    got = latest_probe_tighten(conn, position_id="p1")
    assert got is None


def test_latest_none_on_missing_position() -> None:
    conn = _probe_db()
    assert latest_probe_tighten(conn, position_id="absent") is None


def test_latest_fail_open_on_none_conn() -> None:
    assert latest_probe_tighten(None, position_id="p1") is None


def test_latest_carries_probe_trail_mult() -> None:
    conn = _probe_db()
    _insert_decision(conn, position_id="p1", action="TIGHTEN", trail_mult=1.0, ts=100)
    got = latest_probe_tighten(conn, position_id="p1")
    assert got is not None
    assert got.trail_mult == 1.0


def test_latest_carries_decision_id() -> None:
    """P3 promotion: decision_id is carried so the recalc caller can stamp the
    EXACT source row applied=1 once its tighten actually lands."""
    conn = _probe_db()
    _insert_decision(conn, position_id="p1", action="TIGHTEN", trail_mult=None, ts=100)
    got = latest_probe_tighten(conn, position_id="p1")
    assert got is not None
    assert got.decision_id == "d_100"


# --------------------------------------------------------------------------- #
# PROBE_TIGHTEN_APPLY_ACTIONS (P3 promotion allowlist)
# --------------------------------------------------------------------------- #


def test_apply_actions_allowlist_is_exactly_tighten_and_harvest() -> None:
    """Structural guarantee: WIDEN/HOLD are excluded by omission from an
    ALLOWLIST (2026-07-15 probe performance readout: WIDEN measured -0.115R
    net-negative; TIGHTEN/HARVEST measured net-positive)."""
    assert frozenset({"TIGHTEN", "HARVEST"}) == PROBE_TIGHTEN_APPLY_ACTIONS
    assert "WIDEN" not in PROBE_TIGHTEN_APPLY_ACTIONS
    assert "HOLD" not in PROBE_TIGHTEN_APPLY_ACTIONS


# --------------------------------------------------------------------------- #
# mark_probe_tighten_applied (promotion evidence write-back)
# --------------------------------------------------------------------------- #


def test_mark_applied_flips_the_exact_source_row() -> None:
    conn = _probe_db()
    _insert_decision(conn, position_id="p1", action="TIGHTEN", trail_mult=None, ts=100)
    _insert_decision(conn, position_id="p1", action="TIGHTEN", trail_mult=None, ts=200)
    mark_probe_tighten_applied(conn, decision_id="d_100")
    row_100 = conn.execute(
        "SELECT applied FROM probe_decisions WHERE decision_id = 'd_100'"
    ).fetchone()
    row_200 = conn.execute(
        "SELECT applied FROM probe_decisions WHERE decision_id = 'd_200'"
    ).fetchone()
    assert row_100[0] == 1
    assert row_200[0] == 0  # untouched — only the targeted decision_id flips


def test_mark_applied_fail_open_on_none_conn() -> None:
    mark_probe_tighten_applied(None, decision_id="d_100")  # must not raise


def test_mark_applied_fail_open_on_missing_table() -> None:
    conn = sqlite3.connect(":memory:")  # no PROBE_DDL applied — no such table
    mark_probe_tighten_applied(conn, decision_id="d_100")  # must not raise


# --------------------------------------------------------------------------- #
# synth_tighten_stop
# --------------------------------------------------------------------------- #


def test_synth_long_pulls_stop_up_toward_anchor() -> None:
    """Long tighten: anchor(peak) - trail_mult*ATR is HIGHER than the wide current
    stop → a tighter stop (closer to price). Uses the probe trail_mult when present."""
    stop = synth_tighten_stop(
        side="long",
        anchor_extreme=20050.0,  # peak
        entry_price=20000.0,
        atr_pct=0.001,  # 1 ATR = 20.0 in price
        current_stop_price=19980.0,
        probe_trail_mult=1.0,  # tight: peak - 1*20 = 20030
    )
    assert stop is not None
    assert stop == 20030.0
    assert stop > 19980.0  # tighter than current


def test_synth_short_pulls_stop_down_toward_anchor() -> None:
    stop = synth_tighten_stop(
        side="short",
        anchor_extreme=19950.0,  # trough
        entry_price=20000.0,
        atr_pct=0.001,  # 1 ATR = 20.0
        current_stop_price=20020.0,
        probe_trail_mult=1.0,  # trough + 1*20 = 19970
    )
    assert stop is not None
    assert stop == 19970.0
    assert stop < 20020.0  # tighter than current


def test_synth_fallback_mult_when_probe_null() -> None:
    """trail_mult NULL (observe mode) → ATR-N% fallback multiplier is used."""
    stop = synth_tighten_stop(
        side="long",
        anchor_extreme=20050.0,
        entry_price=20000.0,
        atr_pct=0.001,
        current_stop_price=19980.0,
        probe_trail_mult=None,
    )
    assert stop is not None
    assert stop > 19980.0  # still tighter than the wide current stop


def test_synth_returns_none_when_not_tighter() -> None:
    """If the synthesised stop would LOOSEN (already tighter current stop), return
    None so the caller skips the tighten (ratchet preserved — never loosen)."""
    stop = synth_tighten_stop(
        side="long",
        anchor_extreme=20050.0,
        entry_price=20000.0,
        atr_pct=0.001,
        current_stop_price=20040.0,  # already tighter than peak-1ATR=20030
        probe_trail_mult=1.0,
    )
    assert stop is None
