"""pts-classes group F follow-up — probe cap consumer gate (read-only).

Spec source: task header (group F followup) — G5 PROVE branch
(``compute_size``) must actually CONSUME the probe controls the 07:30
reranker computes, not just leave ``probe_slot_assignment``/``probe_fee_24h``
unread:

  1. Concurrent probe cap 3/2/1 per track — consume ``slot_active`` from the
     LATEST ``probe_slot_assignment`` snapshot. Snapshot absent/stale ->
     fallback = count currently-open probe positions for the track directly
     (cap must stay alive even before the reranker has ever run).
  2. 24h probe fee cap (6/4/2 x F_track_cap) — ``strategy_class.probe_fee_24h``
     over cap -> shadow-route (same shadow swap ``compute_size`` already uses
     for admission-fail / bottom-cell / anti-edge).
  3. Any cap-caused shadow route is falsifiable via a
     ``[pts-classes] probe_cap`` log line naming ``reason=slots|fee_24h``.

Read-only consumption — this module performs no hot-path write (accrual stays
owned by ``probe_fee.accrue_probe_fee`` / the close-hook WIRE path; snapshot
writes stay owned by ``tools.ops.probe_reranker``).
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from polaris.core.sizing.probe_cap import probe_cap_check

NOW = 1_780_000_000


def _seed_snapshot(
    conn: sqlite3.Connection,
    *,
    run_ts: int,
    track: str,
    venue: str,
    strategy_id: str,
    slot_active: bool,
    reason: str = "SLOT_ACTIVE",
) -> None:
    conn.execute(
        "INSERT INTO probe_slot_assignment (run_ts, track, venue, strategy_id, "
        "rank, slot_active, reason) VALUES (?, ?, ?, ?, 1, ?, ?)",
        (run_ts, track, venue, strategy_id, int(slot_active), reason),
    )


def _seed_strategy_class_fee(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, probe_fee_24h: float
) -> None:
    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, probe_fee_24h) "
        "VALUES (?, ?, 'PROVE', ?) "
        "ON CONFLICT(venue, strategy_id) DO UPDATE SET probe_fee_24h = excluded.probe_fee_24h",
        (venue, strategy_id, probe_fee_24h),
    )


def _seed_open_position(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, symbol: str, position_id: str
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, 'long', 1.0, 'open', ?)",
        (position_id, venue, symbol, strategy_id, strategy_id, strategy_id, NOW),
    )


# ---------------------------------------------------------------------------
# ① concurrent-slot consumption — snapshot present
# ---------------------------------------------------------------------------


def test_snapshot_slot_active_passes(memdb: sqlite3.Connection) -> None:
    _seed_snapshot(
        memdb, run_ts=NOW - 100, track="A", venue="okx", strategy_id="s1", slot_active=True,
    )
    result = probe_cap_check(
        memdb, track="A", venue="okx", strategy_id="s1",
        probe_fee_24h_usd=0.0, f_track_cap_usd=100.0, now_ts=NOW,
    )
    assert result.admitted is True
    assert result.reason == ""


def test_snapshot_slot_inactive_shadow_routes(memdb: sqlite3.Connection) -> None:
    _seed_snapshot(
        memdb, run_ts=NOW - 100, track="A", venue="okx", strategy_id="s1",
        slot_active=False, reason="CONCURRENCY_CAP_EXHAUSTED",
    )
    result = probe_cap_check(
        memdb, track="A", venue="okx", strategy_id="s1",
        probe_fee_24h_usd=0.0, f_track_cap_usd=100.0, now_ts=NOW,
    )
    assert result.admitted is False
    assert result.reason == "slots"


def test_snapshot_uses_latest_run_ts_only(memdb: sqlite3.Connection) -> None:
    """An older snapshot said inactive, the LATEST run_ts says active -> the
    latest wins (this cycle's routing decision, not a stale one)."""
    _seed_snapshot(
        memdb, run_ts=NOW - 200, track="A", venue="okx", strategy_id="s1", slot_active=False,
    )
    _seed_snapshot(
        memdb, run_ts=NOW - 50, track="A", venue="okx", strategy_id="s1", slot_active=True,
    )
    result = probe_cap_check(
        memdb, track="A", venue="okx", strategy_id="s1",
        probe_fee_24h_usd=0.0, f_track_cap_usd=100.0, now_ts=NOW,
    )
    assert result.admitted is True


# ---------------------------------------------------------------------------
# ① fallback — snapshot absent or stale -> direct open-probe-position count
# ---------------------------------------------------------------------------


def test_no_snapshot_fallback_counts_open_positions_under_cap(memdb: sqlite3.Connection) -> None:
    """Track A cap = 3 concurrent probes. Only 1 currently open -> admitted."""
    _seed_strategy_class_fee(memdb, venue="okx", strategy_id="s1", probe_fee_24h=0.0)
    _seed_open_position(memdb, venue="okx", strategy_id="s1", symbol="BTC-USDT", position_id="p1")
    result = probe_cap_check(
        memdb, track="A", venue="okx", strategy_id="s1",
        probe_fee_24h_usd=0.0, f_track_cap_usd=100.0, now_ts=NOW,
    )
    assert result.admitted is True
    assert result.used_fallback is True


def test_no_snapshot_fallback_counts_open_positions_over_cap_shadow_routes(
    memdb: sqlite3.Connection,
) -> None:
    """Track C cap = 1 concurrent probe. 1 already open across the track ->
    a NEW candidate is shadow-routed even though the reranker never ran."""
    _seed_strategy_class_fee(memdb, venue="alpaca", strategy_id="s_other", probe_fee_24h=0.0)
    _seed_open_position(
        memdb, venue="alpaca", strategy_id="s_other", symbol="AAPL", position_id="p1",
    )
    result = probe_cap_check(
        memdb, track="C", venue="alpaca", strategy_id="s_new",
        probe_fee_24h_usd=0.0, f_track_cap_usd=100.0, now_ts=NOW,
    )
    assert result.admitted is False
    assert result.reason == "slots"
    assert result.used_fallback is True


def test_stale_snapshot_falls_back_to_open_position_count(memdb: sqlite3.Connection) -> None:
    """A snapshot exists but is older than the staleness window -> treated as
    absent (fallback path), never trusted as still-current."""
    _seed_snapshot(
        memdb, run_ts=NOW - 1_000_000, track="A", venue="okx", strategy_id="s1", slot_active=False,
    )
    result = probe_cap_check(
        memdb, track="A", venue="okx", strategy_id="s1",
        probe_fee_24h_usd=0.0, f_track_cap_usd=100.0, now_ts=NOW,
    )
    # No open positions at all -> fallback count = 0 < cap -> admitted, even
    # though the STALE snapshot itself said inactive.
    assert result.admitted is True
    assert result.used_fallback is True


# ---------------------------------------------------------------------------
# ② 24h probe fee cap (6/4/2 x F_track_cap)
# ---------------------------------------------------------------------------


def test_fee_cap_within_budget_passes(memdb: sqlite3.Connection) -> None:
    _seed_snapshot(
        memdb, run_ts=NOW - 100, track="B", venue="capital", strategy_id="s1", slot_active=True,
    )
    result = probe_cap_check(
        memdb, track="B", venue="capital", strategy_id="s1",
        probe_fee_24h_usd=10.0, f_track_cap_usd=100.0, now_ts=NOW,  # budget = 4x100=400
    )
    assert result.admitted is True


def test_fee_cap_exceeded_shadow_routes(memdb: sqlite3.Connection) -> None:
    _seed_snapshot(
        memdb, run_ts=NOW - 100, track="B", venue="capital", strategy_id="s1", slot_active=True,
    )
    result = probe_cap_check(
        memdb, track="B", venue="capital", strategy_id="s1",
        probe_fee_24h_usd=500.0, f_track_cap_usd=100.0, now_ts=NOW,  # budget = 4x100=400
    )
    assert result.admitted is False
    assert result.reason == "fee_24h"


def test_slots_reason_wins_over_fee_when_both_exceeded(memdb: sqlite3.Connection) -> None:
    _seed_snapshot(
        memdb, run_ts=NOW - 100, track="C", venue="alpaca", strategy_id="s1",
        slot_active=False, reason="CONCURRENCY_CAP_EXHAUSTED",
    )
    result = probe_cap_check(
        memdb, track="C", venue="alpaca", strategy_id="s1",
        probe_fee_24h_usd=999.0, f_track_cap_usd=1.0, now_ts=NOW,
    )
    assert result.admitted is False
    assert result.reason == "slots"


# ---------------------------------------------------------------------------
# ③ telemetry — falsifiable log line on cap-caused shadow routing
# ---------------------------------------------------------------------------


def test_slots_exhausted_logs_pts_classes_probe_cap_tag(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_snapshot(
        memdb, run_ts=NOW - 100, track="A", venue="okx", strategy_id="s1",
        slot_active=False, reason="CONCURRENCY_CAP_EXHAUSTED",
    )
    with caplog.at_level(logging.INFO):
        probe_cap_check(
            memdb, track="A", venue="okx", strategy_id="s1",
            probe_fee_24h_usd=0.0, f_track_cap_usd=100.0, now_ts=NOW,
        )
    assert any(
        "[pts-classes]" in r.message and "probe_cap" in r.message and "reason=slots" in r.message
        for r in caplog.records
    )


def test_fee_cap_exhausted_logs_pts_classes_probe_cap_tag(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_snapshot(
        memdb, run_ts=NOW - 100, track="B", venue="capital", strategy_id="s1", slot_active=True,
    )
    with caplog.at_level(logging.INFO):
        probe_cap_check(
            memdb, track="B", venue="capital", strategy_id="s1",
            probe_fee_24h_usd=500.0, f_track_cap_usd=100.0, now_ts=NOW,
        )
    assert any(
        "[pts-classes]" in r.message and "probe_cap" in r.message and "reason=fee_24h" in r.message
        for r in caplog.records
    )


def test_admitted_case_does_not_log_probe_cap_tag(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_snapshot(
        memdb, run_ts=NOW - 100, track="A", venue="okx", strategy_id="s1", slot_active=True,
    )
    with caplog.at_level(logging.INFO):
        probe_cap_check(
            memdb, track="A", venue="okx", strategy_id="s1",
            probe_fee_24h_usd=0.0, f_track_cap_usd=100.0, now_ts=NOW,
        )
    assert not any("probe_cap" in r.message for r in caplog.records)
