"""Gate-kill counterfactual VALUE reader — (gate_id, cohort/regime) aggregation.

DEMO/PAPER · AGGRESSIVE · flow_not_block. ``gate_kill_counterfactuals`` (07-02
BUILD) records one row per G3/G4 GPT decision with a self-refreshing
``fwd_r_24h`` (backfilled offline by ``sweep_forward_marks``) — but it had ZERO
aggregate readers (07-02 root-cause #4: the feedback leg was never closed).
This module is that reader: per (gate_id, cohort/regime) it compares
``mean_killed_fwd_r`` vs ``mean_passed_fwd_r`` so a human + ``/debate`` can see
whether a gate is killing signals that would have WON (anti-edge).

Mandate proof baked into the tests:
- SELECT-only — no INSERT/UPDATE/DELETE, ever (never auto-tunes a gate).
- Every :class:`KillValueHint` carries ``auto_apply=False`` /
  ``debate_gated=True`` — evidence for a human, never a live knob.
- Stratified floor — BOTH the KILL and PASS side of a cohort must clear the
  sample floor before it surfaces (07-02 unresolved sample-bias item).
- Fail-open — a missing table degrades to ``[]`` / ``{"present": False}``,
  never a crash.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.probes.gate_kill_value import (
    DEFAULT_MIN_COHORT,
    KillValueHint,
    compute_kill_value_hints,
    gate_kill_value_panel,
)

NOW = 1_780_000_000


def _seed_row(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    gate_id: int,
    decision: str,
    regime: str,
    fwd_r_24h: float | None,
    cost_r: float = 0.05,
) -> None:
    conn.execute(
        "INSERT INTO gate_kill_counterfactuals "
        "(event_id, run_id, signal_id, gate_id, decision, venue, symbol, "
        " strategy_id, side, regime, reason, model_used, decision_ts, "
        " mark_price, mark_ts, mark_source, atr_pct, atr_usd, cost_r, "
        " fwd_r_24h, created_ts) "
        "VALUES (?, ?, ?, ?, ?, 'okx', 'BTC-USDT', 'tsmom', 'long', ?, "
        " 'r', 'gpt', ?, 100.0, ?, 'bar_close:1H', 0.01, 2.0, ?, ?, ?)",
        (event_id, f"run-{event_id}", f"sig-{event_id}", gate_id, decision,
         regime, NOW, NOW, cost_r, fwd_r_24h, NOW),
    )


# ---------------------------------------------------------------------------
# 1 — aggregation accuracy
# ---------------------------------------------------------------------------


def test_separation_matches_synthetic_expectation(
    memdb: sqlite3.Connection,
) -> None:
    """KILL fwd_r values [1.0, 1.0, 1.0] vs PASS [0.0, 0.0, 0.0] (cost_r=0 for
    a clean expected value) -> mean_killed=1.0, mean_passed=0.0, separation =
    mean_passed - mean_killed = -1.0 (the gate is killing the WINNERS —
    anti-edge)."""
    for i in range(3):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=1.0, cost_r=0.0,
        )
    for i in range(3):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=3, decision="PASS",
            regime="chop", fwd_r_24h=0.0, cost_r=0.0,
        )
    hints = compute_kill_value_hints(memdb, min_cohort=3)
    assert len(hints) == 1
    hint = hints[0]
    assert hint.gate_id == 3
    assert hint.cohort == "chop"
    assert hint.n_killed == 3
    assert hint.n_passed == 3
    assert hint.mean_killed_fwd_r == pytest.approx(1.0)
    assert hint.mean_passed_fwd_r == pytest.approx(0.0)
    assert hint.separation == pytest.approx(-1.0)
    assert hint.anti_edge is True  # KILL mean_fwd_r > 0 == killed a winner


def test_healthy_gate_shows_positive_separation_no_anti_edge(
    memdb: sqlite3.Connection,
) -> None:
    """A gate doing its job: KILL cohort has NEGATIVE fwd_r (correctly killed
    losers), PASS cohort has POSITIVE fwd_r (correctly passed winners)."""
    for i in range(4):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=4, decision="KILL",
            regime="bull_trend", fwd_r_24h=-0.5, cost_r=0.0,
        )
    for i in range(4):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=4, decision="PASS",
            regime="bull_trend", fwd_r_24h=0.8, cost_r=0.0,
        )
    [hint] = compute_kill_value_hints(memdb, min_cohort=4)
    assert hint.mean_killed_fwd_r == pytest.approx(-0.5)
    assert hint.mean_passed_fwd_r == pytest.approx(0.8)
    assert hint.separation == pytest.approx(1.3)
    assert hint.anti_edge is False


def test_fwd_r_is_fee_adjusted_by_cost_r(memdb: sqlite3.Connection) -> None:
    """mean_*_fwd_r subtracts cost_r (mirrors v_g34_cohort_outcomes'
    fee_adj_fwd_r = fwd_r - cost_r — apples-to-apples with the existing view)."""
    for i in range(3):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=1.0, cost_r=0.1,
        )
    for i in range(3):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=3, decision="PASS",
            regime="chop", fwd_r_24h=1.0, cost_r=0.1,
        )
    [hint] = compute_kill_value_hints(memdb, min_cohort=3)
    assert hint.mean_killed_fwd_r == pytest.approx(0.9)
    assert hint.mean_passed_fwd_r == pytest.approx(0.9)


def test_unresolved_fwd_r_24h_excluded(memdb: sqlite3.Connection) -> None:
    """Rows whose 24h forward mark hasn't self-refreshed yet (fwd_r_24h IS
    NULL) are excluded from the aggregate — never a stale/half-formed row."""
    for i in range(3):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=1.0,
        )
    for i in range(3):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=3, decision="PASS",
            regime="chop", fwd_r_24h=0.0,
        )
    # Unresolved rows — must not count toward n or the mean.
    for i in range(5):
        _seed_row(
            memdb, event_id=f"u{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=None,
        )
    [hint] = compute_kill_value_hints(memdb, min_cohort=3)
    assert hint.n_killed == 3


# ---------------------------------------------------------------------------
# 2 — stratified floor / sample-bias filter (07-02 unresolved item)
# ---------------------------------------------------------------------------


def test_cohort_below_floor_on_either_side_is_suppressed(
    memdb: sqlite3.Connection,
) -> None:
    """Stratification: BOTH sides need >= min_cohort or the row is dropped —
    an unbalanced sample (e.g. 50 KILLs vs 1 PASS) is too biased to surface."""
    for i in range(10):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=1.0,
        )
    _seed_row(
        memdb, event_id="p0", gate_id=3, decision="PASS",
        regime="chop", fwd_r_24h=0.0,
    )
    assert compute_kill_value_hints(memdb, min_cohort=5) == []
    # Both sides now clear the floor.
    for i in range(1, 5):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=3, decision="PASS",
            regime="chop", fwd_r_24h=0.0,
        )
    hints = compute_kill_value_hints(memdb, min_cohort=5)
    assert len(hints) == 1
    assert hints[0].n_passed == 5


def test_default_floor_is_a_positive_constant() -> None:
    assert DEFAULT_MIN_COHORT >= 1


def test_multiple_gates_and_regimes_grouped_independently(
    memdb: sqlite3.Connection,
) -> None:
    for gate_id, regime in ((3, "chop"), (4, "bull_trend")):
        for i in range(3):
            _seed_row(
                memdb, event_id=f"k{gate_id}{regime}{i}", gate_id=gate_id,
                decision="KILL", regime=regime, fwd_r_24h=0.5,
            )
        for i in range(3):
            _seed_row(
                memdb, event_id=f"p{gate_id}{regime}{i}", gate_id=gate_id,
                decision="PASS", regime=regime, fwd_r_24h=-0.5,
            )
    hints = compute_kill_value_hints(memdb, min_cohort=3)
    assert {(h.gate_id, h.cohort) for h in hints} == {
        (3, "chop"), (4, "bull_trend"),
    }


# ---------------------------------------------------------------------------
# 3 — never-auto-applied + never-write mandates
# ---------------------------------------------------------------------------


def test_hint_is_surface_only_never_auto_applied(
    memdb: sqlite3.Connection,
) -> None:
    for i in range(3):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=1.0,
        )
    for i in range(3):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=3, decision="PASS",
            regime="chop", fwd_r_24h=0.0,
        )
    [hint] = compute_kill_value_hints(memdb, min_cohort=3)
    assert isinstance(hint, KillValueHint)
    assert hint.auto_apply is False
    assert hint.debate_gated is True
    # A digest, not a gate — no block/skip/tradeable field ever appears.
    assert not hasattr(hint, "tradeable")
    assert not hasattr(hint, "block")


def test_reader_never_writes(memdb: sqlite3.Connection) -> None:
    for i in range(3):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=1.0,
        )
    for i in range(3):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=3, decision="PASS",
            regime="chop", fwd_r_24h=0.0,
        )
    before = memdb.execute(
        "SELECT COUNT(*) FROM gate_kill_counterfactuals"
    ).fetchone()[0]
    compute_kill_value_hints(memdb, min_cohort=3)
    gate_kill_value_panel(memdb, min_cohort=3)
    after = memdb.execute(
        "SELECT COUNT(*) FROM gate_kill_counterfactuals"
    ).fetchone()[0]
    assert before == after == 6


def test_reader_executes_select_only_on_readonly_conn(
    tmp_path, memdb: sqlite3.Connection,
) -> None:
    """A ``query_only`` connection proves the reader issues nothing but SELECT
    (any write statement raises immediately under this PRAGMA)."""
    for i in range(3):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=1.0,
        )
    for i in range(3):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=3, decision="PASS",
            regime="chop", fwd_r_24h=0.0,
        )
    db_path = tmp_path / "ro.sqlite"
    disk = sqlite3.connect(db_path)
    disk.executescript(
        "".join(
            memdb.iterdump()
        )
    )
    disk.commit()
    disk.close()
    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ro.execute("PRAGMA query_only=ON")
    hints = compute_kill_value_hints(ro, min_cohort=3)
    assert len(hints) == 1
    ro.close()


# ---------------------------------------------------------------------------
# 4 — fail-open + empty-degrade
# ---------------------------------------------------------------------------


def test_missing_table_fails_open_to_empty_list(
    memdb: sqlite3.Connection,
) -> None:
    memdb.execute("DROP TABLE gate_kill_counterfactuals")
    assert compute_kill_value_hints(memdb) == []


def test_panel_empty_degrade_when_no_qualifying_cohort(
    memdb: sqlite3.Connection,
) -> None:
    assert gate_kill_value_panel(memdb) == {"present": False}


def test_panel_missing_table_empty_degrade(memdb: sqlite3.Connection) -> None:
    memdb.execute("DROP TABLE gate_kill_counterfactuals")
    assert gate_kill_value_panel(memdb) == {"present": False}


def test_panel_present_true_with_hints(memdb: sqlite3.Connection) -> None:
    for i in range(3):
        _seed_row(
            memdb, event_id=f"k{i}", gate_id=3, decision="KILL",
            regime="chop", fwd_r_24h=1.0,
        )
    for i in range(3):
        _seed_row(
            memdb, event_id=f"p{i}", gate_id=3, decision="PASS",
            regime="chop", fwd_r_24h=0.0,
        )
    panel = gate_kill_value_panel(memdb, min_cohort=3)
    assert panel["present"] is True
    assert panel["auto_apply"] is False
    assert len(panel["hints"]) == 1
    row = panel["hints"][0]
    assert row["gate_id"] == 3
    assert row["cohort"] == "chop"
    assert row["anti_edge"] is True
