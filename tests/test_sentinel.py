"""Sentinel (W1) — deterministic live-audit sidecar tests. DEMO/PAPER only.

Observe-only invariant: Sentinel never writes the live DB (ro URI), never
touches bot behavior. Checks S1/S2/S3/S4/S6 v1 (S5 deferred — no reconcile
output table exists). Time is injected (``now_ts``) for determinism.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

import polaris.scripts.sentinel as sentinel_mod
from polaris.scripts._sentinel_checks import (
    Thresholds,
    check_s1_price_freshness,
    check_s2_exit_fill_parity,
    check_s3_entry_parity_rejects,
    check_s4_stop_ratchet,
    check_s6_feature_availability,
    check_s7_zombie_drain,
    in_session,
)
from polaris.scripts.sentinel import main, open_live_ro, run_once
from polaris.storage.schema import ALL_DDL

# Wed 2026-06-10 18:00 UTC = 14:00 ET — FX in-session, US RTH in-session.
NOW_OPEN = int(dt.datetime(2026, 6, 10, 18, 0, tzinfo=dt.UTC).timestamp())
# Sat 2026-06-13 12:00 UTC — FX weekend-closed, US equity closed.
NOW_WEEKEND = int(dt.datetime(2026, 6, 13, 12, 0, tzinfo=dt.UTC).timestamp())

TH = Thresholds()


@pytest.fixture(autouse=True)
def _isolate_sentinel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """F7: tests must not inherit POLARIS_SENTINEL_* from the outer shell."""
    for key in list(os.environ):
        if key.startswith("POLARIS_SENTINEL_"):
            monkeypatch.delenv(key)


@pytest.fixture
def live_db(tmp_path: Path) -> Path:
    path = tmp_path / "live.sqlite"
    conn = sqlite3.connect(path)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def sentinel_db(tmp_path: Path) -> Path:
    return tmp_path / "sentinel.sqlite"


def _rw(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    return conn


def _insert_tick(
    conn: sqlite3.Connection,
    venue: str,
    symbol: str,
    ts: int,
    bid_size: float = 1.0,
    ask_size: float = 1.0,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO quote_ticks "
        "(instrument_id, venue, symbol, ts, bid, ask, mid, spread_bps, "
        "bid_size, ask_size, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"{venue}:{symbol}", venue, symbol, ts, 100.0, 100.1, 100.05, 1.0,
         bid_size, ask_size, "ws"),
    )


def _insert_inflow(
    conn: sqlite3.Connection,
    venue: str,
    *,
    last_tick_ts: int,
    ticks_600s: int = 100,
    max_flow_size_600s: float = 2.0,
    window_started_at: int,
) -> None:
    """Seed a tick_inflow row (the writer's per-venue rollup S6 now reads)."""
    conn.execute(
        "INSERT OR REPLACE INTO tick_inflow "
        "(venue, last_tick_ts, ticks_600s, max_flow_size_600s, window_started_at) "
        "VALUES (?,?,?,?,?)",
        (venue, last_tick_ts, ticks_600s, max_flow_size_600s, window_started_at),
    )


def _insert_position(
    conn: sqlite3.Connection,
    position_id: str,
    venue: str,
    symbol: str,
    *,
    status: str = "open",
    side: str = "long",
    stop_price: float | None = None,
    opened_ts: int = 0,
    closed_ts: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts, stop_price) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (position_id, venue, symbol, "strat", "strat", "strat", side, 1.0,
         status, opened_ts, closed_ts, stop_price),
    )


def _insert_fill(
    conn: sqlite3.Connection,
    fill_id: str,
    venue: str,
    symbol: str,
    ts_ms: int,
    *,
    is_close: int = 0,
    contribution_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, ts_ms, order_id, is_close, contribution_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, venue, f"{venue}:{symbol}", "strat", "buy", 100.0, 1.0,
         ts_ms, f"ord-{fill_id}", is_close, contribution_id),
    )


def _insert_fault(
    conn: sqlite3.Connection,
    event_id: str,
    event_ts: int,
    *,
    fault_type: str = "reject",
    venue: str = "okx",
    symbol: str = "DEP-USDT",
) -> None:
    conn.execute(
        "INSERT INTO strategy_fault_events (event_id, strategy_id, fault_type, "
        "event_ts, detail_json) VALUES (?,?,?,?,?)",
        (event_id, "strat", fault_type, event_ts,
         json.dumps({"reject_code": "51020", "symbol": symbol, "venue": venue})),
    )


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------


def test_in_session_calendars() -> None:
    assert in_session("always_on", NOW_WEEKEND) is True
    assert in_session("fx_indices_cal", NOW_OPEN) is True
    assert in_session("fx_indices_cal", NOW_WEEKEND) is False
    assert in_session("us_equity_cal", NOW_OPEN) is True
    assert in_session("us_equity_cal", NOW_WEEKEND) is False


# ---------------------------------------------------------------------------
# S1 — price freshness
# ---------------------------------------------------------------------------


def test_s1_fresh_tick_no_finding(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 5)
    conn.close()
    live = open_live_ro(live_db)
    assert check_s1_price_freshness(live, NOW_OPEN, TH) == []
    live.close()


def test_s1_warn_and_critical(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 60)
    _insert_tick(conn, "capital", "US100", NOW_OPEN - 300)
    conn.close()
    live = open_live_ro(live_db)
    found = {f.subject: f for f in check_s1_price_freshness(live, NOW_OPEN, TH)}
    live.close()
    assert found["okx"].severity == "warn"
    assert found["capital"].severity == "critical"
    assert found["capital"].check_id == "S1"


def test_s1_session_closed_skips_judgment(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_tick(conn, "capital", "US100", NOW_WEEKEND - 9999)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_WEEKEND - 9999)
    conn.close()
    live = open_live_ro(live_db)
    found = {f.subject for f in check_s1_price_freshness(live, NOW_WEEKEND, TH)}
    live.close()
    assert "capital" not in found  # fx weekend-closed → skip
    assert "okx" in found  # always_on → judged


# ---------------------------------------------------------------------------
# S2 — exit decision → close fill parity
# ---------------------------------------------------------------------------


def test_s2_closed_without_close_fill_critical(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", status="closed",
                     opened_ts=NOW_OPEN - 4000, closed_ts=NOW_OPEN - 1000)
    conn.close()
    live = open_live_ro(live_db)
    found, state = check_s2_exit_fill_parity(live, NOW_OPEN, TH, {})
    live.close()
    assert len(found) == 1
    assert found[0].severity == "critical"
    assert found[0].subject == "pos1"
    assert "pos1" in state["pending"]  # tracked for re-check beyond lookback


def test_s2_closed_with_close_fill_ok(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", status="closed",
                     opened_ts=NOW_OPEN - 4000, closed_ts=NOW_OPEN - 1000)
    _insert_fill(conn, "f1", "capital", "US100", (NOW_OPEN - 1000) * 1000,
                 is_close=1, contribution_id="pos1")
    conn.close()
    live = open_live_ro(live_db)
    found, state = check_s2_exit_fill_parity(live, NOW_OPEN, TH, {})
    live.close()
    assert found == []
    assert state["pending"] == {}


def test_s2_adjacent_close_fill_does_not_alias(live_db: Path) -> None:
    """F3a: pos2's missing close fill must NOT be masked by pos1's fill on the
    same venue:symbol at the same time — the join is fills.contribution_id ="""
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", status="closed",
                     opened_ts=NOW_OPEN - 4000, closed_ts=NOW_OPEN - 1000)
    _insert_position(conn, "pos2", "capital", "US100", status="closed",
                     opened_ts=NOW_OPEN - 4000, closed_ts=NOW_OPEN - 1000)
    _insert_fill(conn, "f1", "capital", "US100", (NOW_OPEN - 1000) * 1000,
                 is_close=1, contribution_id="pos1")
    conn.close()
    live = open_live_ro(live_db)
    found, _state = check_s2_exit_fill_parity(live, NOW_OPEN, TH, {})
    live.close()
    assert [f.subject for f in found] == ["pos2"]


def test_s2_pending_rechecked_beyond_lookback(live_db: Path) -> None:
    """F3b: a flagged position is re-examined from sentinel_state even after
    its closed_ts left the lookback window — stays flagged until a fill."""
    closed_ts = NOW_OPEN - TH.s2_lookback_sec - 5000  # far outside lookback
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", status="closed",
                     opened_ts=closed_ts - 100, closed_ts=closed_ts)
    conn.close()
    prev = {"pending": {"pos1": {"closed_ts": closed_ts}}}
    live = open_live_ro(live_db)
    found, state = check_s2_exit_fill_parity(live, NOW_OPEN, TH, prev)
    live.close()
    assert [f.subject for f in found] == ["pos1"]
    assert "pos1" in state["pending"]


def test_run_once_s2_active_beyond_lookback_until_fill(
    live_db: Path, sentinel_db: Path,
) -> None:
    """F3b run_once: flagged → out of lookback (must stay active, not the old
    false resolve) → close fill appears → resolves."""
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", status="closed",
                     opened_ts=NOW_OPEN - 4000, closed_ts=NOW_OPEN - 1000)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    later = NOW_OPEN + TH.s2_lookback_sec + 9000  # closed_ts now out of window
    run_once(live_db, sentinel_db, now_ts=later)
    sconn = sqlite3.connect(sentinel_db)
    status, last_ts = sconn.execute(
        "SELECT status, last_ts FROM sentinel_findings "
        "WHERE check_id='S2' AND subject='pos1'"
    ).fetchone()
    sconn.close()
    assert (status, last_ts) == ("active", later)  # old code: false resolve
    conn = _rw(live_db)
    _insert_fill(conn, "f1", "capital", "US100", (NOW_OPEN - 1000) * 1000,
                 is_close=1, contribution_id="pos1")
    conn.close()
    run_once(live_db, sentinel_db, now_ts=later + 45)
    sconn = sqlite3.connect(sentinel_db)
    status2 = sconn.execute(
        "SELECT status FROM sentinel_findings "
        "WHERE check_id='S2' AND subject='pos1'"
    ).fetchone()[0]
    sconn.close()
    assert status2 == "resolved"


# ---------------------------------------------------------------------------
# S3 — entry parity + reject anomaly
# ---------------------------------------------------------------------------


def test_s3_open_fill_without_position_critical(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_fill(conn, "f1", "okx", "BTC-USDT", (NOW_OPEN - 1000) * 1000)
    conn.close()
    live = open_live_ro(live_db)
    found = check_s3_entry_parity_rejects(live, NOW_OPEN, TH)
    live.close()
    assert any(f.severity == "critical" and "f1" in f.subject for f in found)


def test_s3_position_without_entry_fill_critical(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "okx", "BTC-USDT",
                     opened_ts=NOW_OPEN - 1000)
    conn.close()
    live = open_live_ro(live_db)
    found = check_s3_entry_parity_rejects(live, NOW_OPEN, TH)
    live.close()
    assert any(f.severity == "critical" and f.subject == "pos1" for f in found)


def test_s3_matched_pair_no_finding(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "okx", "BTC-USDT",
                     opened_ts=NOW_OPEN - 1000)
    _insert_fill(conn, "f1", "okx", "BTC-USDT", (NOW_OPEN - 1000) * 1000)
    conn.close()
    live = open_live_ro(live_db)
    assert check_s3_entry_parity_rejects(live, NOW_OPEN, TH) == []
    live.close()


def test_s3_repeated_rejects_warn(live_db: Path) -> None:
    conn = _rw(live_db)
    for i in range(3):
        _insert_fault(conn, f"e{i}", NOW_OPEN - 100 - i)
    conn.close()
    live = open_live_ro(live_db)
    found = check_s3_entry_parity_rejects(live, NOW_OPEN, TH)
    live.close()
    rej = [f for f in found if f.subject == "reject:okx:DEP-USDT"]
    assert len(rej) == 1
    assert rej[0].severity == "warn"


def test_s3_two_rejects_no_warn(live_db: Path) -> None:
    conn = _rw(live_db)
    for i in range(2):
        _insert_fault(conn, f"e{i}", NOW_OPEN - 100 - i)
    conn.close()
    live = open_live_ro(live_db)
    assert check_s3_entry_parity_rejects(live, NOW_OPEN, TH) == []
    live.close()


def test_s3_reconciled_position_excluded(live_db: Path) -> None:
    """F5: status='reconciled' rows are recovery artifacts (no entry fill by
    construction) — they must not raise entry-parity false criticals."""
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", status="reconciled",
                     opened_ts=NOW_OPEN - 1000, closed_ts=NOW_OPEN - 500)
    conn.close()
    live = open_live_ro(live_db)
    found = check_s3_entry_parity_rejects(live, NOW_OPEN, TH)
    live.close()
    assert found == []


def test_s3_fault_surge_critical(live_db: Path) -> None:
    th = Thresholds(s3_fault_crit_n=5)
    conn = _rw(live_db)
    for i in range(5):
        _insert_fault(conn, f"e{i}", NOW_OPEN - 50 - i, symbol=f"SYM{i}")
    conn.close()
    live = open_live_ro(live_db)
    found = check_s3_entry_parity_rejects(live, NOW_OPEN, th)
    live.close()
    assert any(f.subject == "fault_surge" and f.severity == "critical"
               for f in found)


# ---------------------------------------------------------------------------
# S4 — stop ratchet monotonicity
# ---------------------------------------------------------------------------


def test_s4_first_pass_snapshots_no_finding(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", stop_price=100.0,
                     opened_ts=NOW_OPEN - 100)
    conn.close()
    live = open_live_ro(live_db)
    found, snap = check_s4_stop_ratchet(live, NOW_OPEN, TH, {})
    live.close()
    assert found == []
    assert snap["pos1"]["stop"] == 100.0


def test_s4_long_stop_lowered_critical(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", side="long",
                     stop_price=90.0, opened_ts=NOW_OPEN - 100)
    conn.close()
    live = open_live_ro(live_db)
    prev = {"pos1": {"side": "long", "stop": 100.0}}
    found, _snap = check_s4_stop_ratchet(live, NOW_OPEN, TH, prev)
    live.close()
    assert len(found) == 1
    assert found[0].severity == "critical"
    assert found[0].subject == "pos1"


def test_s4_long_stop_raised_ok_and_short_raised_critical(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "long1", "capital", "US100", side="long",
                     stop_price=110.0, opened_ts=NOW_OPEN - 100)
    _insert_position(conn, "short1", "capital", "DE40", side="short",
                     stop_price=210.0, opened_ts=NOW_OPEN - 100)
    conn.close()
    live = open_live_ro(live_db)
    prev = {
        "long1": {"side": "long", "stop": 100.0},
        "short1": {"side": "short", "stop": 200.0},
    }
    found, _snap = check_s4_stop_ratchet(live, NOW_OPEN, TH, prev)
    live.close()
    assert [f.subject for f in found] == ["short1"]


def test_s4_refires_while_stop_below_high_water(live_db: Path) -> None:
    """F2: the watermark (not the last snapshot) is the comparison base — a
    stop that stays regressed keeps firing instead of false-resolving."""
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", side="long",
                     stop_price=90.0, opened_ts=NOW_OPEN - 100)
    conn.close()
    prev = {"pos1": {"side": "long", "hw": 100.0, "stop": 90.0}}
    live = open_live_ro(live_db)
    found, snap = check_s4_stop_ratchet(live, NOW_OPEN, TH, prev)
    live.close()
    assert [f.subject for f in found] == ["pos1"]  # still below 100 watermark
    assert snap["pos1"]["hw"] == 100.0  # watermark never decays


def test_s4_g7_widen_downgrades_to_info_and_rebases(live_db: Path) -> None:
    """A stop loosened by a SANCTIONED G7 widen (gate_events gate_id=7
    ADJUST_EXIT with widening_applied=true) is intentional — info, not
    critical — and the watermark REBASES to the widened stop so the new
    baseline governs future ratchet checks."""
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "J225", side="long",
                     stop_price=90.0, opened_ts=NOW_OPEN - 100)
    conn.execute(
        "INSERT INTO gate_events (event_id, run_id, signal_id, position_id, "
        "gate_id, phase, decision, payload_json, created_ts) "
        "VALUES ('ev1', 'run1', 'pos1', 'pos1', 7, 'success', 'ADJUST_EXIT', "
        "'{\"stop_price\":90.0,\"widening_applied\":true}', ?)",
        (NOW_OPEN - 30,),
    )
    conn.close()
    prev = {"pos1": {"side": "long", "hw": 100.0, "stop": 100.0}}
    live = open_live_ro(live_db)
    found, snap = check_s4_stop_ratchet(live, NOW_OPEN, TH, prev)
    live.close()
    assert [f.severity for f in found] == ["info"], (
        "sanctioned G7 widen must downgrade to info"
    )
    assert "G7 widen" in found[0].summary
    assert snap["pos1"]["hw"] == 90.0  # rebased to the widened stop


def test_s4_no_widen_event_stays_critical(live_db: Path) -> None:
    """Without a recent G7 widen the same regression is a REAL ratchet break
    — critical, watermark NOT rebased (mutation guard for the gate_events
    lookup)."""
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "J225", side="long",
                     stop_price=90.0, opened_ts=NOW_OPEN - 100)
    # A G7 row that must NOT suppress: widening_applied=false (a HOLD echo).
    conn.execute(
        "INSERT INTO gate_events (event_id, run_id, signal_id, position_id, "
        "gate_id, phase, decision, payload_json, created_ts) "
        "VALUES ('ev2', 'run1', 'pos1', 'pos1', 7, 'success', 'HOLD', "
        "'{\"stop_price\":100.0,\"widening_applied\":false}', ?)",
        (NOW_OPEN - 30,),
    )
    conn.close()
    prev = {"pos1": {"side": "long", "hw": 100.0, "stop": 100.0}}
    live = open_live_ro(live_db)
    found, snap = check_s4_stop_ratchet(live, NOW_OPEN, TH, prev)
    live.close()
    assert [f.severity for f in found] == ["critical"]
    assert snap["pos1"]["hw"] == 100.0  # watermark never decays


def test_run_once_s4_violation_persists_then_recovers(
    live_db: Path, sentinel_db: Path,
) -> None:
    """F2 run_once: regressed stop stays ACTIVE on every pass (the old
    pass-to-pass snapshot diff resolved it after one pass — false all-clear);
    returning to the watermark resolves it naturally."""
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", side="long",
                     stop_price=100.0, opened_ts=NOW_OPEN - 100)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    conn = _rw(live_db)
    conn.execute("UPDATE positions SET stop_price=90.0 WHERE position_id='pos1'")
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN + 45)
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN + 90)  # still 90 → still on
    sconn = sqlite3.connect(sentinel_db)
    status, last_ts = sconn.execute(
        "SELECT status, last_ts FROM sentinel_findings "
        "WHERE check_id='S4' AND subject='pos1'"
    ).fetchone()
    sconn.close()
    assert (status, last_ts) == ("active", NOW_OPEN + 90)
    conn = _rw(live_db)
    conn.execute("UPDATE positions SET stop_price=100.0 WHERE position_id='pos1'")
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN + 135)  # back at watermark
    sconn = sqlite3.connect(sentinel_db)
    status2 = sconn.execute(
        "SELECT status FROM sentinel_findings "
        "WHERE check_id='S4' AND subject='pos1'"
    ).fetchone()[0]
    sconn.close()
    assert status2 == "resolved"


# ---------------------------------------------------------------------------
# S6 — feature availability (tick inflow + flow-size death)
# ---------------------------------------------------------------------------


def test_s6_in_session_no_inflow_warn(live_db: Path) -> None:
    # okx + capital have a FRESH durable last_tick_ts; alpaca has NO tick_inflow
    # row at all (WS never connected) → only alpaca warns no_inflow (in-session).
    conn = _rw(live_db)
    _insert_inflow(conn, "okx", last_tick_ts=NOW_OPEN - 1,
                   window_started_at=NOW_OPEN - 1000)
    _insert_inflow(conn, "capital", last_tick_ts=NOW_OPEN - 1,
                   window_started_at=NOW_OPEN - 1000)
    conn.close()  # alpaca: no tick_inflow row, RTH in-session at NOW_OPEN
    live = open_live_ro(live_db)
    found = check_s6_feature_availability(live, NOW_OPEN, TH)
    live.close()
    subjects = {f.subject for f in found}
    assert "alpaca:no_inflow" in subjects
    assert "okx:no_inflow" not in subjects
    assert "capital:no_inflow" not in subjects


def test_s6_durable_last_tick_ts_stale_warns(live_db: Path) -> None:
    # Durable freshness: a tick_inflow row whose last_tick_ts is older than the
    # no-inflow window warns EVEN with the buckets empty (post-restart). This is
    # the D2 fix — S6b reads the durable last_tick_ts, never blind for ~600s.
    conn = _rw(live_db)
    _insert_inflow(conn, "okx", last_tick_ts=NOW_OPEN - 500,  # > 300s stale
                   window_started_at=NOW_OPEN - 5000)
    conn.close()
    live = open_live_ro(live_db)
    found = check_s6_feature_availability(live, NOW_OPEN, TH)
    live.close()
    subjects = {f.subject for f in found}
    assert "okx:no_inflow" in subjects


def test_s6_session_closed_no_inflow_skipped(live_db: Path) -> None:
    live = open_live_ro(live_db)  # empty tick_inflow for everyone
    found = check_s6_feature_availability(live, NOW_WEEKEND, TH)
    live.close()
    subjects = {f.subject for f in found}
    assert "capital:no_inflow" not in subjects  # weekend → skip
    assert "alpaca:no_inflow" not in subjects  # weekend → skip
    assert "okx:no_inflow" in subjects  # always_on → judged


def test_s6_flow_size_dead_warn(live_db: Path) -> None:
    # Full 600s window (window_started_at old) + ticks but max flow size 0 → dead.
    conn = _rw(live_db)
    _insert_inflow(conn, "capital", last_tick_ts=NOW_OPEN - 1,
                   ticks_600s=60, max_flow_size_600s=0.0,
                   window_started_at=NOW_OPEN - 1000)
    _insert_inflow(conn, "okx", last_tick_ts=NOW_OPEN - 1,
                   ticks_600s=60, max_flow_size_600s=3.0,
                   window_started_at=NOW_OPEN - 1000)
    conn.close()
    live = open_live_ro(live_db)
    found = check_s6_feature_availability(live, NOW_OPEN, TH)
    live.close()
    subjects = {f.subject for f in found}
    assert "capital:size_dead" in subjects
    assert "okx:size_dead" not in subjects


def test_s6_dead_venue_no_double_fire_size_dead(live_db: Path) -> None:
    # A dead venue's tick_inflow row freezes (last_tick_ts stops advancing,
    # buckets stop decaying). It must emit no_inflow ONCE, not also keep
    # re-asserting size_dead forever — the size axis is skipped when the venue
    # is already stale (flow_not_block: still warn-only, just de-duplicated).
    conn = _rw(live_db)
    _insert_inflow(conn, "okx", last_tick_ts=NOW_OPEN - 5000,  # stale → no_inflow
                   ticks_600s=60, max_flow_size_600s=0.0,
                   window_started_at=NOW_OPEN - 9000)
    conn.close()
    live = open_live_ro(live_db)
    found = check_s6_feature_availability(live, NOW_OPEN, TH)
    live.close()
    subjects = {f.subject for f in found}
    assert "okx:no_inflow" in subjects
    assert "okx:size_dead" not in subjects   # not double-fired on a dead venue
    assert "okx:warming" not in subjects


def test_s6_flow_size_warming_when_window_not_full(live_db: Path) -> None:
    # Same size-dead pattern but window_started_at is RECENT (window < 600s):
    # report WARMING (info), NOT a size_dead warn (no silent OK/FAIL, D2 fix).
    conn = _rw(live_db)
    _insert_inflow(conn, "capital", last_tick_ts=NOW_OPEN - 1,
                   ticks_600s=60, max_flow_size_600s=0.0,
                   window_started_at=NOW_OPEN - 100)  # only 100s of window
    conn.close()
    live = open_live_ro(live_db)
    found = check_s6_feature_availability(live, NOW_OPEN, TH)
    live.close()
    by_subject = {f.subject: f for f in found}
    assert "capital:size_dead" not in by_subject
    assert "capital:warming" in by_subject
    assert by_subject["capital:warming"].severity == "info"


# ---------------------------------------------------------------------------
# S7 — zombie-drain (new status='reconciled' transitions)
# ---------------------------------------------------------------------------


def test_s7_first_pass_baselines_without_findings(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "old1", "okx", "BTC-USDT", status="reconciled",
                     opened_ts=NOW_OPEN - 9000, closed_ts=NOW_OPEN - 8000)
    conn.close()
    live = open_live_ro(live_db)
    found, state = check_s7_zombie_drain(live, NOW_OPEN, TH, {})
    live.close()
    assert found == []  # pre-existing rows = baseline, not new transitions
    assert state["ids"] == ["old1"]


def test_s7_new_reconciled_transition_warns(live_db: Path) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "old1", "okx", "BTC-USDT", status="reconciled",
                     opened_ts=NOW_OPEN - 9000, closed_ts=NOW_OPEN - 8000)
    _insert_position(conn, "zomb1", "capital", "US100", status="reconciled",
                     opened_ts=NOW_OPEN - 2000, closed_ts=NOW_OPEN - 100)
    conn.close()
    live = open_live_ro(live_db)
    found, state = check_s7_zombie_drain(live, NOW_OPEN, TH, {"ids": ["old1"]})
    live.close()
    assert [(f.subject, f.severity) for f in found] == [("zomb1", "warn")]
    assert state["ids"] == ["old1", "zomb1"]


def test_run_once_s7_detects_new_reconciled(
    live_db: Path, sentinel_db: Path,
) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "old1", "okx", "BTC-USDT", status="reconciled",
                     opened_ts=NOW_OPEN - 9000, closed_ts=NOW_OPEN - 8000)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)  # baseline pass
    conn = _rw(live_db)
    _insert_position(conn, "zomb1", "capital", "US100", status="reconciled",
                     opened_ts=NOW_OPEN - 2000, closed_ts=NOW_OPEN + 10)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN + 45)
    sconn = sqlite3.connect(sentinel_db)
    rows = sconn.execute(
        "SELECT subject, severity, status FROM sentinel_findings "
        "WHERE check_id='S7'"
    ).fetchall()
    sconn.close()
    assert rows == [("zomb1", "warn", "active")]


# ---------------------------------------------------------------------------
# run_once — persistence, dedup, resolve, state, isolation
# ---------------------------------------------------------------------------


def test_run_once_dedup_recurrence_updates_last_ts(
    live_db: Path, sentinel_db: Path,
) -> None:
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 500)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN + 45)
    sconn = sqlite3.connect(sentinel_db)
    rows = sconn.execute(
        "SELECT first_ts, last_ts, status FROM sentinel_findings "
        "WHERE check_id='S1' AND subject='okx'"
    ).fetchall()
    sconn.close()
    assert len(rows) == 1  # dedup: single row, no spam
    first_ts, last_ts, status = rows[0]
    assert (first_ts, last_ts, status) == (NOW_OPEN, NOW_OPEN + 45, "active")


def test_run_once_resolve_transition(live_db: Path, sentinel_db: Path) -> None:
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 500)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN + 44)  # fresh again
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN + 45)
    sconn = sqlite3.connect(sentinel_db)
    status, resolved_ts = sconn.execute(
        "SELECT status, resolved_ts FROM sentinel_findings "
        "WHERE check_id='S1' AND subject='okx'"
    ).fetchone()
    sconn.close()
    assert status == "resolved"
    assert resolved_ts == NOW_OPEN + 45


def test_run_once_s4_state_persists_across_passes(
    live_db: Path, sentinel_db: Path,
) -> None:
    conn = _rw(live_db)
    _insert_position(conn, "pos1", "capital", "US100", side="long",
                     stop_price=100.0, opened_ts=NOW_OPEN - 100)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    conn = _rw(live_db)
    conn.execute("UPDATE positions SET stop_price=90.0 WHERE position_id='pos1'")
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN + 45)
    sconn = sqlite3.connect(sentinel_db)
    rows = sconn.execute(
        "SELECT severity FROM sentinel_findings "
        "WHERE check_id='S4' AND subject='pos1' AND status='active'"
    ).fetchall()
    sconn.close()
    assert rows == [("critical",)]


def test_run_once_heartbeat_row(live_db: Path, sentinel_db: Path) -> None:
    res = run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    assert res.errors == {}
    sconn = sqlite3.connect(sentinel_db)
    ts, checks_run, findings_active, duration_ms = sconn.execute(
        "SELECT ts, checks_run, findings_active, duration_ms FROM sentinel_runs"
    ).fetchone()
    sconn.close()
    assert ts == NOW_OPEN
    assert checks_run == 6  # S1 S2 S3 S4 S6 S7 (S5 deferred: no reconcile table)
    assert findings_active >= 0
    assert duration_ms >= 0


# ---------------------------------------------------------------------------
# storage-split (round 4 fix): S1 (quote_ticks) + S6 (tick_inflow) read the
# marketdata sibling, not the trading live_db conn.
# ---------------------------------------------------------------------------


def _md_sibling(live_db: Path) -> Path:
    from polaris.storage.schema_marketdata import marketdata_db_path_for

    path = marketdata_db_path_for(live_db)
    conn = sqlite3.connect(path)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    return path


def test_run_once_s1_reads_marketdata_sibling_when_trading_bars_empty(
    live_db: Path, sentinel_db: Path,
) -> None:
    """quote_ticks is marketdata-domain — S1 must read the marketdata
    sibling, not the (post-split, permanently empty) trading live_db."""
    md_path = _md_sibling(live_db)
    md_conn = _rw(md_path)
    _insert_tick(md_conn, "okx", "BTC-USDT", NOW_OPEN - 5)  # fresh, marketdata-only
    md_conn.close()
    res = run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    assert res.errors == {}
    sconn = sqlite3.connect(sentinel_db)
    n = sconn.execute(
        "SELECT COUNT(*) FROM sentinel_findings WHERE check_id='S1' AND status='active'"
    ).fetchone()[0]
    sconn.close()
    assert n == 0  # fresh tick in the marketdata sibling → no staleness finding


def test_run_once_s1_marketdata_sibling_missing_degrades_to_trading_conn(
    live_db: Path, sentinel_db: Path,
) -> None:
    """No marketdata sibling booted yet (early-boot / legacy) → S1 falls back
    to the trading conn (byte-identical pre-split behaviour), never crashes."""
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 5)
    conn.close()
    res = run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    assert res.errors == {}
    sconn = sqlite3.connect(sentinel_db)
    n = sconn.execute(
        "SELECT COUNT(*) FROM sentinel_findings WHERE check_id='S1' "
        "AND subject='okx' AND status='active'"
    ).fetchone()[0]
    sconn.close()
    assert n == 0  # resolved off the fallback trading conn


def test_run_once_s6_reads_marketdata_sibling_tick_inflow(
    live_db: Path, sentinel_db: Path,
) -> None:
    """tick_inflow is marketdata-domain — S6 has the SAME bug class as S1's
    quote_ticks and must read the marketdata sibling too."""
    md_path = _md_sibling(live_db)
    md_conn = _rw(md_path)
    _insert_inflow(
        md_conn, "okx", last_tick_ts=NOW_OPEN - 5, ticks_600s=100,
        max_flow_size_600s=2.0, window_started_at=NOW_OPEN - 600,
    )
    md_conn.close()
    res = run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    assert res.errors == {}
    sconn = sqlite3.connect(sentinel_db)
    n = sconn.execute(
        "SELECT COUNT(*) FROM sentinel_findings WHERE check_id='S6' "
        "AND subject='okx:no_inflow' AND status='active'"
    ).fetchone()[0]
    sconn.close()
    assert n == 0  # fresh inflow row in the marketdata sibling → no no_inflow finding


def test_run_once_atomic_rollback_on_midwrite_failure(
    live_db: Path, sentinel_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F6: findings persist + state save + heartbeat = ONE transaction — a
    crash mid-write must not advance any of them (no snapshot-only forward)."""
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 500)  # S1 finding
    conn.close()

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated crash between persist and state save")

    monkeypatch.setattr(sentinel_mod, "_save_state", _boom)
    with pytest.raises(RuntimeError, match="simulated crash"):
        run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    sconn = sqlite3.connect(sentinel_db)
    n_findings = sconn.execute(
        "SELECT COUNT(*) FROM sentinel_findings").fetchone()[0]
    n_state = sconn.execute("SELECT COUNT(*) FROM sentinel_state").fetchone()[0]
    n_runs = sconn.execute("SELECT COUNT(*) FROM sentinel_runs").fetchone()[0]
    sconn.close()
    assert (n_findings, n_state, n_runs) == (0, 0, 0)  # full rollback


def test_check_error_isolated_and_does_not_resolve(
    live_db: Path, sentinel_db: Path,
) -> None:
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 500)  # S1 critical
    _insert_position(conn, "pos1", "capital", "US100", status="closed",
                     opened_ts=NOW_OPEN - 4000, closed_ts=NOW_OPEN - 1000)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    conn = _rw(live_db)
    conn.execute("DROP TABLE quote_ticks")  # S1 reads quote_ticks → errors
    conn.execute("DROP TABLE tick_inflow")  # S6 reads tick_inflow → errors
    conn.close()
    res = run_once(live_db, sentinel_db, now_ts=NOW_OPEN + 45)
    assert "S1" in res.errors and "S6" in res.errors
    sconn = sqlite3.connect(sentinel_db)
    s1_status = sconn.execute(
        "SELECT status FROM sentinel_findings WHERE check_id='S1' AND subject='okx'"
    ).fetchone()[0]
    s2_rows = sconn.execute(
        "SELECT last_ts FROM sentinel_findings "
        "WHERE check_id='S2' AND subject='pos1' AND status='active'"
    ).fetchall()
    sconn.close()
    assert s1_status == "active"  # errored check never resolves its findings
    assert s2_rows == [(NOW_OPEN + 45,)]  # other checks kept running


# ---------------------------------------------------------------------------
# ro guard — sentinel NEVER writes the live DB
# ---------------------------------------------------------------------------


def test_live_db_opened_read_only(live_db: Path) -> None:
    # F7: match='readonly' — a generic OperationalError (e.g. "no such table")
    # must NOT satisfy this guard; only the ro-mode write rejection counts.
    conn = open_live_ro(live_db)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute(
            "INSERT INTO strategy_fault_events "
            "(event_id, strategy_id, fault_type, event_ts) VALUES ('x','y','z',1)"
        )
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("CREATE TABLE sentinel_probe_should_fail (x INTEGER)")
    conn.close()


def test_run_once_leaves_live_db_bytes_untouched(
    live_db: Path, sentinel_db: Path,
) -> None:
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 500)
    _insert_position(conn, "pos1", "capital", "US100", status="closed",
                     opened_ts=NOW_OPEN - 4000, closed_ts=NOW_OPEN - 1000)
    conn.close()
    before = hashlib.sha256(live_db.read_bytes()).hexdigest()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    after = hashlib.sha256(live_db.read_bytes()).hexdigest()
    assert before == after  # writes go to sentinel.sqlite ONLY


# ---------------------------------------------------------------------------
# --once E2E + dashboard payload
# ---------------------------------------------------------------------------


def test_main_once_e2e(live_db: Path, sentinel_db: Path) -> None:
    now = int(time.time())
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", now - 500)
    conn.close()
    rc = main(["--db", str(live_db), "--sentinel-db", str(sentinel_db), "--once"])
    assert rc == 0
    sconn = sqlite3.connect(sentinel_db)
    n_runs = sconn.execute("SELECT COUNT(*) FROM sentinel_runs").fetchone()[0]
    n_active = sconn.execute(
        "SELECT COUNT(*) FROM sentinel_findings "
        "WHERE check_id='S1' AND subject='okx' AND status='active'"
    ).fetchone()[0]
    sconn.close()
    assert n_runs == 1
    assert n_active == 1


def test_main_once_exit_1_on_check_errors(
    live_db: Path, sentinel_db: Path,
) -> None:
    """F8: --once must signal check errors via exit code (cron/CI visibility)."""
    conn = _rw(live_db)
    conn.execute("DROP TABLE quote_ticks")  # S1 reads quote_ticks → errors
    conn.close()
    rc = main(["--db", str(live_db), "--sentinel-db", str(sentinel_db), "--once"])
    assert rc == 1


def test_api_sentinel_payload(
    live_db: Path, sentinel_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _rw(live_db)
    _insert_tick(conn, "okx", "BTC-USDT", NOW_OPEN - 500)
    conn.close()
    run_once(live_db, sentinel_db, now_ts=NOW_OPEN)
    import tools.visualizer.server as srv

    monkeypatch.setattr(srv, "_SENTINEL_DB_PATH", sentinel_db)
    payload = srv._sentinel_payload()
    assert payload["available"] is True
    assert any(
        f["check_id"] == "S1" and f["subject"] == "okx"
        for f in payload["findings"]
    )
    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["checks_run"] == 6


def test_api_sentinel_payload_missing_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.visualizer.server as srv

    monkeypatch.setattr(srv, "_SENTINEL_DB_PATH", tmp_path / "absent.sqlite")
    payload = srv._sentinel_payload()
    assert payload["available"] is False
    assert payload["findings"] == []
