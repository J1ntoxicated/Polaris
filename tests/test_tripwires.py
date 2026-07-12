"""Tests for tripwires (fee-split v1 FLIP item 5 — T5 rollback-review
tripwires + T4 label-churn). DEMO/PAPER only. Every signal here is
auto-FLAG-only — these tests assert the FLAG value, never any blocking
side-effect (there is none to assert; the module has no writer to
strategy_class/positions)."""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.classes.remap_table import RemapEntry, upsert_remap_entry
from polaris.core.classes.transition import TransitionThresholds
from polaris.core.classes.tripwires import (
    KILL_FIRE_RATE_MIN_TRACKS,
    MIN_DWELL_FLOOR,
    compute_tripwire_report,
)
from polaris.storage.schema import init_db

NOW = 1_800_000_000


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_closed(conn, *, position_id, venue, strategy_id, closed_ts, pnl_usd, size_usd=1000.0):
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts) VALUES (?, ?, 'BTC-USDT', ?, ?, ?, 'long', 1.0, 'closed', ?, ?)",
        (position_id, venue, strategy_id, strategy_id, strategy_id, closed_ts - 3600, closed_ts),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', ?, 100.0, 1.0, ?, ?, ?, ?, 1)",
        (uuid.uuid4().hex, venue, f"{venue}:BTC-USDT", strategy_id, size_usd,
         closed_ts * 1000, uuid.uuid4().hex, position_id, pnl_usd),
    )


def _upsert_class(conn, *, venue="okx", strategy_id="s1", klass="EARN", dwell=25,
                   last_transition_ts=NOW - 10 * 86_400):
    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, "
        "window_w, dwell, ladder_step, epoch_id, last_transition_ts, "
        "kill_state, shadow_ring) VALUES (?, ?, ?, 20, ?, 0, 1, ?, 'ACTIVE', '[]')",
        (venue, strategy_id, klass, dwell, last_transition_ts),
    )


def test_empty_db_reports_all_clean(conn):
    report = compute_tripwire_report(conn, now_ts=NOW)
    assert report.shadow_divergence_flag is False
    assert report.earn_flag is False
    assert report.kill_fire_rate_flag is False
    assert report.remap_drift_flag is False
    assert report.label_churn_flag is False


# ---------------------------------------------------------------------------
# Shadow divergence — R2 rework mixed-scale-ledger fix. score_f_events is
# append-only/no-backfill, so the persisted score_contrib column's scale
# depends on when a row was written; both verdicts must be reconstructed
# fresh from net_usd/fee_denom_usd/notional_usd, never from that column.
# ---------------------------------------------------------------------------


def test_shadow_divergence_excludes_rows_without_notional(conn):
    """A pre-fee-split-v0 row (notional_usd NULL) has no reconstructible
    gross axis — excluded from divergence samples, never fabricated."""
    from polaris.core.classes.score_f import rollup_score_f

    _mk_closed(conn, position_id="c1", venue="okx", strategy_id="s1",
               closed_ts=NOW, pnl_usd=10.0)
    conn.commit()
    rollup_score_f(conn, now_ts=NOW)
    conn.execute("UPDATE score_f_events SET notional_usd = NULL WHERE position_id = 'c1'")
    conn.commit()

    report = compute_tripwire_report(conn, now_ts=NOW)
    assert report.shadow_divergence.n_samples == 0


def test_shadow_divergence_ignores_stale_persisted_score_contrib(conn):
    """Corrupting the PERSISTED score_contrib column (simulating a legacy
    pre-flip row read verbatim) must not move the divergence verdict — both
    axes are reconstructed fresh from net_usd/fee_denom_usd/notional_usd."""
    from polaris.core.classes.score_f import rollup_score_f

    _mk_closed(conn, position_id="c1", venue="okx", strategy_id="s1",
               closed_ts=NOW, pnl_usd=10.0)
    conn.commit()
    rollup_score_f(conn, now_ts=NOW)
    before = compute_tripwire_report(conn, now_ts=NOW).shadow_divergence

    conn.execute("UPDATE score_f_events SET score_contrib = -999999.0 WHERE position_id = 'c1'")
    conn.commit()
    after = compute_tripwire_report(conn, now_ts=NOW).shadow_divergence

    assert after.behavior_divergence_pct == pytest.approx(before.behavior_divergence_pct)
    assert after.n_samples == before.n_samples


# ---------------------------------------------------------------------------
# EARN gross-negative signal
# ---------------------------------------------------------------------------


def test_earn_flag_true_when_gross_sum_and_lcb95_negative(conn):
    _upsert_class(conn, venue="okx", strategy_id="loser", klass="EARN")
    for i in range(20):
        _mk_closed(conn, position_id=f"p{i}", venue="okx", strategy_id="loser",
                   closed_ts=NOW - i * 100, pnl_usd=-5.0)
    conn.commit()
    from polaris.core.classes.score_f import rollup_score_f
    rollup_score_f(conn, now_ts=NOW)
    report = compute_tripwire_report(conn, now_ts=NOW)
    assert report.earn_gross_sum < 0.0
    assert report.earn_flag is True


def test_earn_flag_false_when_gross_positive(conn):
    _upsert_class(conn, venue="okx", strategy_id="winner", klass="EARN")
    for i in range(20):
        _mk_closed(conn, position_id=f"w{i}", venue="okx", strategy_id="winner",
                   closed_ts=NOW - i * 100, pnl_usd=5.0)
    conn.commit()
    from polaris.core.classes.score_f import rollup_score_f
    rollup_score_f(conn, now_ts=NOW)
    report = compute_tripwire_report(conn, now_ts=NOW)
    assert report.earn_gross_sum > 0.0
    assert report.earn_flag is False


# ---------------------------------------------------------------------------
# kill-fire-rate proxy (demotion density)
# ---------------------------------------------------------------------------


def test_kill_fire_rate_flags_on_min_tracks_today(conn):
    for i in range(KILL_FIRE_RATE_MIN_TRACKS):
        _upsert_class(conn, venue="okx", strategy_id=f"t{i}", last_transition_ts=NOW - 3600)
    conn.commit()
    report = compute_tripwire_report(conn, now_ts=NOW)
    assert report.demoted_tracks_today == KILL_FIRE_RATE_MIN_TRACKS
    assert report.kill_fire_rate_flag is True


def test_kill_fire_rate_clean_with_one_recent_transition(conn):
    _upsert_class(conn, venue="okx", strategy_id="t0", last_transition_ts=NOW - 3600)
    conn.commit()
    report = compute_tripwire_report(conn, now_ts=NOW)
    assert report.kill_fire_rate_flag is False


# ---------------------------------------------------------------------------
# remap drift (reads score_f_remap_table.stale)
# ---------------------------------------------------------------------------


def test_remap_drift_flag_reflects_stale_rows(conn):
    entry1 = RemapEntry(
        scope_key="okx:s1", scope_type="track", n_samples=30, computed_ts=NOW - 1000,
        thresholds=TransitionThresholds(), ref_population=[1.0] * 30,
    )
    upsert_remap_entry(conn, entry1)
    conn.commit()
    entry2 = RemapEntry(
        scope_key="okx:s1", scope_type="track", n_samples=30, computed_ts=NOW,
        thresholds=TransitionThresholds(), ref_population=[500.0 + i for i in range(30)],
    )
    upsert_remap_entry(conn, entry2)
    conn.commit()
    report = compute_tripwire_report(conn, now_ts=NOW)
    assert "okx:s1" in report.remap_stale_scopes
    assert report.remap_drift_flag is True


# ---------------------------------------------------------------------------
# label churn (T4) — dwell floor proxy
# ---------------------------------------------------------------------------


def test_label_churn_flags_low_dwell_track(conn):
    _upsert_class(conn, venue="okx", strategy_id="churny", dwell=MIN_DWELL_FLOOR - 1)
    conn.commit()
    report = compute_tripwire_report(conn, now_ts=NOW)
    assert "okx:churny" in report.churn_flagged_tracks
    assert report.label_churn_flag is True


def test_label_churn_clean_at_dwell_floor(conn):
    _upsert_class(conn, venue="okx", strategy_id="stable", dwell=MIN_DWELL_FLOOR)
    conn.commit()
    report = compute_tripwire_report(conn, now_ts=NOW)
    assert "okx:stable" not in report.churn_flagged_tracks
