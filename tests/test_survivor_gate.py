"""P0a survivor -> virtual PROVE wire — 3 admission guards (TDD).

OFFLINE ONLY (mirrors ``test_p0a_registry.py``'s own framing). Every test
opens a throwaway ``tmp_path`` sqlite file via ``open_registry`` — NEVER the
live ``data/polaris_live.sqlite`` and NEVER the live bot. DEMO/PAPER virtual
capital only; nothing here touches sizing / the T4 chain / 9-stack.

Test plan (task spec):
  (a) isolated grid-corner case REJECT (no adjacent-cell co-pass).
  (b) grid-corner ADMIT when an adjacent grid point also OOS-passes.
  (c) k-independent-cell REJECT (winner-carried by a single cell).
  (d) k-independent-cell ADMIT once >= k distinct cells pass.
  (e) cohort cap throttles the (k+1)th concurrent admission per track, and
      ``resolve_survivor_admission`` frees the slot for the next cycle.
  (f) PROVE-only structural guard: no code path in this module can ever write
      a class other than PROVE — EARN is never reachable without the
      (separately-owned, untouched) live ``evaluate_transition`` FSM.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from polaris.core.classes.r_pool import PROBE_CONCURRENCY_CAP
from polaris.core.evolve.registry import (
    SurvivorAdmissionRow,
    TrialRow,
    open_registry,
    record_trial,
    resolve_survivor_admission,
)
from polaris.core.evolve.survivor_gate import (
    K_MIN_INDEPENDENT_CELLS,
    SurvivorTrial,
    SurvivorVerdict,
    cohort_cap_guard,
    evaluate_survivor,
    grid_corner_guard,
    independent_cell_guard,
)

# rsi_bb_pullback's PARAM_BOUNDS grid: rsi_threshold in (25.0, 30.0, 35.0) —
# a single-axis 3-point grid. index0=25.0 (corner/min), index1=30.0 (mid,
# the only adjacent neighbor of either corner), index2=35.0 (corner/max).
_STRATEGY = "rsi_bb_pullback"
_VENUE = "okx"  # rsi_bb_pullback's live venue (STREAMS["A_okx_crypto"])


def _trial(
    *,
    cell_key: str,
    variant_run_id: str,
    variant_id: str,
    rsi_threshold: float,
    oos_pass: int,
) -> TrialRow:
    return TrialRow(
        cell_key=cell_key,
        variant_run_id=variant_run_id,
        exchange=_VENUE,
        strategy=_STRATEGY,
        ticker="POOL[BTC-USDT]",
        regime="mixed",
        variant_id=variant_id,
        param_json=json.dumps({"rsi_threshold": rsi_threshold}),
        is_pass=oos_pass,
        oos_pass=oos_pass,
        n_trades=10,
        pnl_r_mean=0.3,
        lcb=0.1,
        ucb=0.5,
        dsr=0.97,
        trials_searched=3,
        is_oos_spread=0.0,
        created_ts=1_720_000_000,
    )


_WINNER_ID = "rsi_bb_pullback|rsi_threshold=25.0"  # corner (min)
_MID_ID = "rsi_bb_pullback|rsi_threshold=30.0"  # adjacent to both corners
_MAX_ID = "rsi_bb_pullback|rsi_threshold=35.0"  # opposite corner


# ---------------------------------------------------------------------------
# (a) isolated grid-corner REJECT
# ---------------------------------------------------------------------------


def test_isolated_grid_corner_rejected(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    ck = "okx|rsi_bb_pullback|POOL[BTC-USDT]|mixed"
    # Winner at the MIN corner passes; its only grid neighbor (mid) does NOT.
    record_trial(conn, _trial(cell_key=ck, variant_run_id="r1", variant_id=_WINNER_ID, rsi_threshold=25.0, oos_pass=1))
    record_trial(conn, _trial(cell_key=ck, variant_run_id="r2", variant_id=_MID_ID, rsi_threshold=30.0, oos_pass=0))

    verdict = grid_corner_guard(
        [
            _row_from(r)
            for r in conn.execute(
                "SELECT variant_id, cell_key, param_json, oos_pass FROM variant_trials"
            ).fetchall()
        ],
        strategy=_STRATEGY,
        cell_key=ck,
        variant_id=_WINNER_ID,
    )
    assert verdict.passed is False
    assert verdict.reason == "ISOLATED_GRID_CORNER"
    conn.close()


def _row_from(r: tuple[object, ...]) -> SurvivorTrial:
    return SurvivorTrial(variant_id=str(r[0]), cell_key=str(r[1]), param_json=str(r[2]), oos_pass=bool(r[3]))


# ---------------------------------------------------------------------------
# (b) grid-corner ADMIT when the adjacent grid point also passes
# ---------------------------------------------------------------------------


def test_grid_corner_admitted_with_adjacent_pass(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    ck = "okx|rsi_bb_pullback|POOL[BTC-USDT]|mixed"
    record_trial(conn, _trial(cell_key=ck, variant_run_id="r1", variant_id=_WINNER_ID, rsi_threshold=25.0, oos_pass=1))
    # Adjacent mid-point ALSO passes -> not isolated.
    record_trial(conn, _trial(cell_key=ck, variant_run_id="r2", variant_id=_MID_ID, rsi_threshold=30.0, oos_pass=1))

    rows = [
        _row_from(r)
        for r in conn.execute(
            "SELECT variant_id, cell_key, param_json, oos_pass FROM variant_trials"
        ).fetchall()
    ]
    verdict = grid_corner_guard(rows, strategy=_STRATEGY, cell_key=ck, variant_id=_WINNER_ID)
    assert verdict.passed is True
    assert verdict.reason == "GRID_CORNER_ADJACENT_PASS"
    conn.close()


def test_non_corner_winner_passes_trivially(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    ck = "okx|rsi_bb_pullback|POOL[BTC-USDT]|mixed"
    # The MID point (30.0) is not a grid corner (neither index 0 nor 2) — no
    # neighbor pass required.
    record_trial(conn, _trial(cell_key=ck, variant_run_id="r1", variant_id=_MID_ID, rsi_threshold=30.0, oos_pass=1))
    rows = [
        _row_from(r)
        for r in conn.execute(
            "SELECT variant_id, cell_key, param_json, oos_pass FROM variant_trials"
        ).fetchall()
    ]
    verdict = grid_corner_guard(rows, strategy=_STRATEGY, cell_key=ck, variant_id=_MID_ID)
    assert verdict.passed is True
    assert verdict.reason == "GRID_CORNER_NOT_APPLICABLE"
    conn.close()


# ---------------------------------------------------------------------------
# (c)/(d) k-independent-cell guard
# ---------------------------------------------------------------------------


def test_k_independent_cells_insufficient_rejected() -> None:
    # Same variant passes in only ONE cell — winner-carried, must reject.
    rows = [
        SurvivorTrial(variant_id=_MID_ID, cell_key="okx|rsi_bb_pullback|A|mixed", param_json="{}", oos_pass=True),
        SurvivorTrial(variant_id=_MID_ID, cell_key="okx|rsi_bb_pullback|A|mixed", param_json="{}", oos_pass=True),
    ]
    verdict, n = independent_cell_guard(rows, variant_id=_MID_ID, k=K_MIN_INDEPENDENT_CELLS)
    assert verdict.passed is False
    assert n == 1
    assert "INSUFFICIENT" in verdict.reason


def test_k_independent_cells_sufficient_admitted() -> None:
    rows = [
        SurvivorTrial(variant_id=_MID_ID, cell_key=f"okx|rsi_bb_pullback|POOL{i}|mixed", param_json="{}", oos_pass=True)
        for i in range(K_MIN_INDEPENDENT_CELLS)
    ]
    verdict, n = independent_cell_guard(rows, variant_id=_MID_ID, k=K_MIN_INDEPENDENT_CELLS)
    assert verdict.passed is True
    assert n == K_MIN_INDEPENDENT_CELLS


# ---------------------------------------------------------------------------
# (e) cohort cap throttle + release valve
# ---------------------------------------------------------------------------


def _admit_strong_survivor(
    conn: sqlite3.Connection, *, variant_id: str, pool_tag: str, now_ts: int
) -> SurvivorVerdict:
    """Seed >= K_MIN_INDEPENDENT_CELLS independent, non-corner-isolated passing
    cells for ``variant_id`` (mid grid point — never a corner, so guard 1 is
    always N/A here), then evaluate it for admission."""
    for i in range(K_MIN_INDEPENDENT_CELLS):
        ck = f"okx|rsi_bb_pullback|{pool_tag}{i}|mixed"
        record_trial(
            conn,
            _trial(cell_key=ck, variant_run_id=f"{variant_id}-{i}", variant_id=variant_id, rsi_threshold=30.0, oos_pass=1),
        )
    return evaluate_survivor(
        conn,
        strategy=_STRATEGY,
        venue=_VENUE,
        variant_id=variant_id,
        cell_key=f"okx|rsi_bb_pullback|{pool_tag}0|mixed",
        now_ts=now_ts,
    )


def test_cohort_cap_throttles_then_releases(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    cap = PROBE_CONCURRENCY_CAP["A"]  # okx -> track A

    admitted_ids: list[str] = []
    for i in range(cap):
        vid = f"rsi_bb_pullback|rsi_threshold=30.0#slot{i}"
        verdict = _admit_strong_survivor(conn, variant_id=vid, pool_tag=f"SLOT{i}_", now_ts=1_720_000_000 + i)
        assert verdict.admit is True, verdict.reason
        admitted_ids.append(vid)

    # cap-th + 1 admission is throttled, NOT permanently blocked.
    overflow_id = "rsi_bb_pullback|rsi_threshold=30.0#overflow"
    overflow_verdict = _admit_strong_survivor(conn, variant_id=overflow_id, pool_tag="OVERFLOW_", now_ts=1_720_000_100)
    assert overflow_verdict.admit is False
    assert "COHORT_CAP_EXHAUSTED" in overflow_verdict.reason

    # cohort_cap_guard reports exhausted directly too.
    assert cohort_cap_guard(conn, track="A").passed is False

    # Resolve one admitted survivor out of PROVE -> slot frees; overflow admits.
    resolve_survivor_admission(conn, venue=_VENUE, variant_id=admitted_ids[0], resolved_ts=1_720_000_200)
    assert cohort_cap_guard(conn, track="A").passed is True

    retried = _admit_strong_survivor(conn, variant_id=overflow_id, pool_tag="OVERFLOW_", now_ts=1_720_000_300)
    assert retried.admit is True, retried.reason
    conn.close()


# ---------------------------------------------------------------------------
# (f) PROVE-only structural guard — EARN is never reachable from this module
# ---------------------------------------------------------------------------


def test_admission_never_encodes_a_class_other_than_prove(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    vid = "rsi_bb_pullback|rsi_threshold=30.0#classcheck"
    verdict = _admit_strong_survivor(conn, variant_id=vid, pool_tag="CLASS_", now_ts=1_720_000_000)
    assert verdict.admit is True

    # survivor_admissions carries NO class-like column at all.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(survivor_admissions)").fetchall()}
    assert "strategy_class" not in cols
    assert "class" not in cols
    conn.close()


def test_no_earn_literal_or_transition_import_in_survivor_gate() -> None:
    """No CODE-level (non-docstring) string constant equals ``"EARN"``, and the
    module never imports ``evaluate_transition`` / ``transition.py`` — EARN
    promotion has no reachable path from this module, prose caveats aside."""
    import ast
    import inspect

    from polaris.core.evolve import survivor_gate

    source = inspect.getsource(survivor_gate)
    tree = ast.parse(source)
    string_constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "EARN" not in string_constants

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.add(node.module or "")
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
    assert "evaluate_transition" not in imported_names
    assert not any("classes.transition" in name for name in imported_names)


def test_record_survivor_admission_signature_has_no_class_param() -> None:
    fields = set(SurvivorAdmissionRow.__dataclass_fields__)
    assert "strategy_class" not in fields
    assert "class" not in fields


# ---------------------------------------------------------------------------
# integration: full evaluate_survivor rejects on each guard independently
# ---------------------------------------------------------------------------


def test_evaluate_survivor_rejects_isolated_corner_before_cohort_check(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    ck = "okx|rsi_bb_pullback|POOL[BTC-USDT]|mixed"
    record_trial(conn, _trial(cell_key=ck, variant_run_id="r1", variant_id=_WINNER_ID, rsi_threshold=25.0, oos_pass=1))
    # No adjacent pass recorded at all -> isolated corner.
    verdict = evaluate_survivor(
        conn, strategy=_STRATEGY, venue=_VENUE, variant_id=_WINNER_ID, cell_key=ck, now_ts=1_720_000_000
    )
    assert verdict.admit is False
    assert verdict.reason == "ISOLATED_GRID_CORNER"
    # Nothing was persisted.
    assert conn.execute("SELECT COUNT(*) FROM survivor_admissions").fetchone()[0] == 0
    conn.close()
