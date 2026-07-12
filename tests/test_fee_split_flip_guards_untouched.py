"""fee-split v1 FLIP — item 6 confirmation: guards the flip must NOT touch.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).

Scope note (evidence-based, not guessed — grepped this codebase before
writing these tests): a literal "100-trade gross<0 unconditional kill" rule
(referenced in vault forensics as a design invariant) has NO dedicated
code/writer in this repository as of this build — ``kill_state`` is never
assigned "KILLED" anywhere in ``polaris/`` (only test fixtures construct it
directly), so there is no such mechanism to regression-test beyond the
KILL-state short-circuit itself (covered below + test_transition_thresholds.py).
What DOES exist and IS covered here: the admission rails (fee-floor K=3
stop-distance gate, probe fee cap via f_track_cap's legacy fee_denom_usd)
and the KILL/tripwire priority-order, none of which read score_contrib or
TransitionThresholds — the flip's blast radius provably stops at those
boundaries.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.classes.score_f import compute_score_f, f_track_cap, rollup_score_f
from polaris.core.classes.transition import TransitionInput, evaluate_transition
from polaris.core.sizing.probe_cap import probe_cap_check
from polaris.core.sizing.probe_notional import (
    FEE_FLOOR_K,
    probe_notional_usd,
    prove_admission_ok,
    prove_stop_dist_floor_pct,
)
from polaris.storage.schema import init_db

NOW = 1_800_000_000


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_closed(conn, *, position_id, venue, strategy_id, closed_ts, pnl_usd, fee_usd, size_usd=1000.0):
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts) VALUES (?, ?, 'BTC-USDT', ?, ?, ?, 'long', 1.0, 'closed', ?, ?)",
        (position_id, venue, strategy_id, strategy_id, strategy_id, closed_ts - 3600, closed_ts),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', ?, 100.0, ?, ?, ?, ?, ?, 1)",
        (uuid.uuid4().hex, venue, f"{venue}:BTC-USDT", strategy_id, size_usd,
         fee_usd, closed_ts * 1000, uuid.uuid4().hex, position_id, pnl_usd),
    )


# ---------------------------------------------------------------------------
# Admission rails — fee-floor K=3 stop-distance gate (unchanged constant +
# unchanged decision function, neither imports score_f/transition at all).
# ---------------------------------------------------------------------------


def test_fee_floor_k_constant_unchanged():
    assert FEE_FLOOR_K == 3.0


def test_prove_admission_ok_characterization():
    okx_floor = prove_stop_dist_floor_pct("okx")
    assert prove_admission_ok(venue="okx", stop_dist_pct=okx_floor + 0.0001) is True
    assert prove_admission_ok(venue="okx", stop_dist_pct=okx_floor) is False  # exact floor never admits
    assert prove_admission_ok(venue="okx", stop_dist_pct=0.0) is False


def test_probe_notional_usd_characterization():
    # No currently-registered venue charges a flat per-trade fee -> degenerates
    # to the venue_min_notional floor (probe_notional.py's own U2 contract).
    assert probe_notional_usd("okx") == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Probe fee cap (f_track_cap) — legacy fee_denom_usd formula, unaffected by
# the score_contrib flip (score_f.py's flip touches score_contrib only).
# ---------------------------------------------------------------------------


def test_f_track_cap_still_uses_legacy_fee_denom_not_gross_bps(conn):
    """f_track_cap's median is over fee_denom_usd (max(fee, notional-floor))
    — a large positive gross_bps swing (via a big winning pnl) must NOT move
    this cap; only the fee itself does."""
    for i, (fee, pnl) in enumerate([(1.0, 100.0), (2.0, -9999.0), (3.0, 9999.0)]):
        _mk_closed(conn, position_id=f"c{i}", venue="okx", strategy_id="s1",
                   closed_ts=NOW - i * 100, pnl_usd=pnl, fee_usd=fee)
    conn.commit()
    rollup_score_f(conn, now_ts=NOW)
    result = f_track_cap(conn, venue="okx", strategy_id="s1", window_w=20, prove_fallback_usd=0.5)
    assert result.f_track_cap_usd == pytest.approx(2.0)  # median(1,2,3) — pnl swings irrelevant


def test_probe_cap_check_admits_within_budget(conn):
    result = probe_cap_check(
        conn, venue="okx", strategy_id="s1", track="A", now_ts=NOW,
        probe_fee_24h_usd=0.0, f_track_cap_usd=1.0,
    )
    assert result.reason == ""
    assert result.admitted is True


# ---------------------------------------------------------------------------
# KILL / tripwire priority order — first-match-wins, never gated by
# score_contrib scale or TransitionThresholds (already covered in depth by
# test_transition_thresholds.py; one end-to-end sanity check here).
# ---------------------------------------------------------------------------


def test_kill_state_short_circuits_regardless_of_scores(conn):
    for i, pnl in enumerate([100.0] * 5):
        _mk_closed(conn, position_id=f"k{i}", venue="okx", strategy_id="killed_track",
                   closed_ts=NOW - i * 100, pnl_usd=pnl, fee_usd=1.0)
    conn.commit()
    rollup_score_f(conn, now_ts=NOW)
    events = compute_score_f(conn, venue="okx", strategy_id="killed_track")
    scores = [e.score_contrib for e in events]  # flipped gross_bps values

    inp = TransitionInput(
        strategy_class="EARN", kill_state="KILLED", window_w=20, dwell=0,
        ladder_step=0, epoch_id=1, last_transition_ts=NOW, now_ts=NOW,
        timeframe_bucket="intraday", shadow_scores=[], intent_scores=scores,
        recent_r_multiples=[], n_fills_total=5, n_signals_recent=5,
        n_fills_recent=5, last_50_fill_rate=1.0,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"
    assert result.reason == "KILL_NO_AUTO_REVIVE"
