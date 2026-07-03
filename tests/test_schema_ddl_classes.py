"""Tests for the ``strategy_class`` table (pts-classes group A — storage).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). This is
a pure storage-layer feature: no sizing/9-stack/-1.0R rail touch, no
defensive throttle — ``strategy_class`` is a performance-tiered capital
*routing* record (which strategies get how much track_R headroom), not a
block/reject filter. BENCH is still fully signaled/learned/shadow-priced,
never suppressed.

Spec: PK=(venue, strategy_id) UPSERT + PRAGMA-guard ALTER (pending_opens
schema-drift precedent, ``schema.py::_apply_post_migrations``). DDL must be
idempotent across repeated ``init_db()`` calls (fresh boot + every restart
re-runs ``ALL_DDL``).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from polaris.core.lifecycle.recover import bootstrap_replay_strategy_class, hydrate_strategy_class
from polaris.storage.schema import init_db


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _upsert(
    conn: sqlite3.Connection,
    *,
    venue: str = "okx",
    strategy_id: str = "gold_riskoff_trend_amplify",
    strategy_class: str = "EARN",
    window_w: int = 20,
    f_track_cap: float = 1.0,
    dwell: int = 0,
    epoch_id: int = 1,
    last_transition_ts: int = 1_700_000_000,
    kill_state: str = "ACTIVE",
    ladder_step: int = 0,
    qty: float = 0.0,
    cum_fees: float = 0.0,
    cum_pnl: float = 0.0,
    intent_ring: str = "[]",
    shadow_ring: str = "[]",
    probe_fee_24h: float = 0.0,
) -> None:
    conn.execute(
        """
        INSERT INTO strategy_class
            (venue, strategy_id, strategy_class, window_w, f_track_cap, dwell,
             epoch_id, last_transition_ts, kill_state, ladder_step, qty,
             cum_fees, cum_pnl, intent_ring, shadow_ring, probe_fee_24h)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(venue, strategy_id) DO UPDATE SET
            strategy_class=excluded.strategy_class,
            window_w=excluded.window_w,
            f_track_cap=excluded.f_track_cap,
            dwell=excluded.dwell,
            epoch_id=excluded.epoch_id,
            last_transition_ts=excluded.last_transition_ts,
            kill_state=excluded.kill_state,
            ladder_step=excluded.ladder_step,
            qty=excluded.qty,
            cum_fees=excluded.cum_fees,
            cum_pnl=excluded.cum_pnl,
            intent_ring=excluded.intent_ring,
            shadow_ring=excluded.shadow_ring,
            probe_fee_24h=excluded.probe_fee_24h
        """,
        (
            venue, strategy_id, strategy_class, window_w, f_track_cap, dwell,
            epoch_id, last_transition_ts, kill_state, ladder_step, qty,
            cum_fees, cum_pnl, intent_ring, shadow_ring, probe_fee_24h,
        ),
    )


# ---------------------------------------------------------------------------
# DDL idempotency
# ---------------------------------------------------------------------------


def test_ddl_runs_twice_idempotent(tmp_path):
    db = tmp_path / "idem.sqlite"
    init_db(db)
    conn = init_db(db)  # second full init_db pass on the SAME file
    cols = {row[1] for row in conn.execute("PRAGMA table_info(strategy_class)")}
    assert "strategy_class" in cols
    assert "f_track_cap" in cols
    assert "probe_fee_24h" in cols


def test_pk_is_venue_strategy_id(conn):
    pk_cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(strategy_class)").fetchall()
        if row[5] > 0
    ]
    assert pk_cols == ["venue", "strategy_id"]


def test_upsert_replaces_row_not_duplicates(conn):
    _upsert(conn, strategy_class="EARN", dwell=0)
    _upsert(conn, strategy_class="PROVE", dwell=3)
    rows = conn.execute(
        "SELECT strategy_class, dwell FROM strategy_class "
        "WHERE venue='okx' AND strategy_id='gold_riskoff_trend_amplify'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0] == ("PROVE", 3)


def test_all_required_columns_present(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(strategy_class)")}
    required = {
        "venue", "strategy_id", "strategy_class", "window_w", "f_track_cap",
        "dwell", "epoch_id", "last_transition_ts", "kill_state", "ladder_step",
        "qty", "cum_fees", "cum_pnl", "intent_ring", "shadow_ring",
        "probe_fee_24h",
    }
    assert required.issubset(cols)


# ---------------------------------------------------------------------------
# hydrate_strategy_class — restart must NOT reset class/epoch/dwell state
# ---------------------------------------------------------------------------


def test_hydrate_empty_returns_empty(conn):
    assert hydrate_strategy_class(conn) == {}


def test_hydrate_restores_persisted_row_verbatim(conn):
    _upsert(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
        strategy_class="EARN", window_w=20, f_track_cap=1.5, dwell=4,
        epoch_id=3, last_transition_ts=1_700_500_000, kill_state="ACTIVE",
        ladder_step=2, qty=0.0, cum_fees=12.5, cum_pnl=88.0,
        intent_ring=json.dumps([1, 2, 3]), shadow_ring=json.dumps([0.1, 0.2]),
        probe_fee_24h=3.25,
    )
    out = hydrate_strategy_class(conn)
    key = ("okx", "gold_riskoff_trend_amplify")
    assert key in out
    row = out[key]
    assert row.strategy_class == "EARN"
    assert row.dwell == 4
    assert row.epoch_id == 3
    assert row.f_track_cap == 1.5
    assert row.kill_state == "ACTIVE"
    assert row.ladder_step == 2
    assert row.cum_fees == 12.5
    assert row.cum_pnl == 88.0
    assert row.intent_ring == [1, 2, 3]
    assert row.shadow_ring == [0.1, 0.2]
    assert row.probe_fee_24h == 3.25


def test_hydrate_does_not_reset_on_reboot(conn):
    """Restart-does-not-reset invariant: a KILL/PROVE state at boot #1 must
    still read back as KILL/PROVE at boot #2 (no fresh-DB-shaped reset).
    """
    _upsert(conn, strategy_id="rsi_bb_pullback", strategy_class="KILL", dwell=9, epoch_id=7)
    first = hydrate_strategy_class(conn)
    # Simulate a second boot against the SAME connection/db (no mutation).
    second = hydrate_strategy_class(conn)
    assert first == second
    assert second[("okx", "rsi_bb_pullback")].strategy_class == "KILL"
    assert second[("okx", "rsi_bb_pullback")].dwell == 9


def test_hydrate_multiple_venues_and_strategies(conn):
    _upsert(conn, venue="okx", strategy_id="a", strategy_class="EARN")
    _upsert(conn, venue="capital", strategy_id="a", strategy_class="PROVE")
    _upsert(conn, venue="capital", strategy_id="b", strategy_class="BENCH")
    out = hydrate_strategy_class(conn)
    assert set(out.keys()) == {
        ("okx", "a"), ("capital", "a"), ("capital", "b"),
    }


# ---------------------------------------------------------------------------
# bootstrap_replay_strategy_class — 5-week history -> score_F -> initial class
# ---------------------------------------------------------------------------


def _fake_score_f_factory(scores: dict[tuple[str, str], float]):
    def _score_f(
        conn: sqlite3.Connection, venue: str, strategy_id: str, lookback_days: int,
    ) -> float:
        return scores.get((venue, strategy_id), 0.0)
    return _score_f


def test_bootstrap_replay_skips_when_row_already_exists(conn):
    """Bootstrap must NEVER clobber a live-tracked class — only seeds absent rows."""
    _upsert(conn, venue="okx", strategy_id="a", strategy_class="KILL", epoch_id=5)
    bootstrap_replay_strategy_class(
        conn,
        candidates=[("okx", "a")],
        score_f=_fake_score_f_factory({("okx", "a"): 999.0}),
        lookback_days=35,
        now_ts=1_700_000_000,
    )
    row = hydrate_strategy_class(conn)[("okx", "a")]
    # untouched — still the pre-existing KILL/epoch_id=5, not re-derived from score_F
    assert row.strategy_class == "KILL"
    assert row.epoch_id == 5


def test_bootstrap_replay_seeds_current_earner_as_earn_immediately(conn):
    bootstrap_replay_strategy_class(
        conn,
        candidates=[("okx", "gold_riskoff_trend_amplify")],
        score_f=_fake_score_f_factory({("okx", "gold_riskoff_trend_amplify"): 2.0}),
        lookback_days=35,
        now_ts=1_700_000_000,
    )
    row = hydrate_strategy_class(conn)[("okx", "gold_riskoff_trend_amplify")]
    assert row.strategy_class == "EARN"
    assert row.epoch_id == 1
    assert row.dwell == 0


def test_bootstrap_replay_does_not_reset_all_to_prove(conn):
    """Explicit rejection-keyword-adjacent guard: bootstrap must NOT flatten
    every candidate to PROVE regardless of score — a positive score_F seeds
    EARN immediately (no probation delay imposed on a proven earner)."""
    bootstrap_replay_strategy_class(
        conn,
        candidates=[("okx", "a"), ("okx", "b"), ("okx", "c")],
        score_f=_fake_score_f_factory(
            {("okx", "a"): 5.0, ("okx", "b"): -3.0, ("okx", "c"): 0.0}
        ),
        lookback_days=35,
        now_ts=1_700_000_000,
    )
    out = hydrate_strategy_class(conn)
    assert out[("okx", "a")].strategy_class == "EARN"
    assert out[("okx", "b")].strategy_class != "EARN"
    # not a blanket PROVE-reset — at least one candidate landed EARN.
    classes = {v.strategy_class for v in out.values()}
    assert "EARN" in classes


def test_bootstrap_replay_multiple_candidates_independent(conn):
    bootstrap_replay_strategy_class(
        conn,
        candidates=[("okx", "a"), ("capital", "b")],
        score_f=_fake_score_f_factory({("okx", "a"): 1.0, ("capital", "b"): -1.0}),
        lookback_days=35,
        now_ts=1_700_000_000,
    )
    out = hydrate_strategy_class(conn)
    assert len(out) == 2
    assert out[("okx", "a")].epoch_id == 1
    assert out[("capital", "b")].epoch_id == 1
