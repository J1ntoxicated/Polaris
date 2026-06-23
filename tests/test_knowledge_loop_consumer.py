"""Knowledge-loop READ-consumer — offline digest of the probe tuning-log.

DEMO/PAPER · AGGRESSIVE · flow_not_block. This is the BUILD-3/3 read side of the
knowledge loop: the ``probe_decisions`` sidecar (ADR-012) and the ``ai_lessons``
ledger were write-only (zero SELECT readers, inert). This consumer READS them and
emits a per-regime *surface hint* — a measurement, NEVER an auto-applied knob.

Mandate proof baked into the tests:
- The consumer NEVER writes (no INSERT/UPDATE/DELETE; read-only).
- The hint carries ``auto_apply=False`` + a ``/debate`` gate flag on every row
  (ADR-011 deterministic core; ADR-012 never-auto-applied; /debate-gated).
- It is a digest, not a gate: it cannot block/skip/size-cut anything — it returns
  data only. No regime is ever marked "untradeable".
"""

from __future__ import annotations

import contextlib
import sqlite3

import pytest

from polaris.core.probes.knowledge_loop import (
    KnowledgeHint,
    consume_probe_tuning_log,
)
from polaris.core.probes.tuning_log import open_probe_db


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    regime: str,
    action: str,
    realized_pnl_r: float | None,
    giveback_r: float | None,
    run_id: str = "r1",
) -> None:
    conn.execute(
        "INSERT INTO probe_decisions "
        "(decision_id, eval_id, ts, run_id, position_id, mode, regime, "
        " composite_lean, action, applied, realized_pnl_r, giveback_r) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            decision_id,
            f"e_{decision_id}",
            1,
            run_id,
            f"p_{decision_id}",
            "thesis",
            regime,
            0.0,
            action,
            0,
            realized_pnl_r,
            giveback_r,
        ),
    )


@pytest.fixture()
def probe_db(tmp_path) -> sqlite3.Connection:
    conn = open_probe_db(tmp_path / "probes.sqlite")
    # probe_decisions has no `regime` column in the base DDL; add it so the
    # consumer can group by regime. Idempotent for the test DB.
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE probe_decisions ADD COLUMN regime TEXT")
    return conn


def test_empty_db_returns_no_hints(probe_db: sqlite3.Connection) -> None:
    assert consume_probe_tuning_log(probe_db) == []


def test_aggregates_by_regime_with_realized_outcome(
    probe_db: sqlite3.Connection,
) -> None:
    # chop: two closed TIGHTEN decisions that gave back R (mis-regime churn).
    _seed_decision(
        probe_db, decision_id="d1", regime="chop", action="TIGHTEN",
        realized_pnl_r=-0.3, giveback_r=0.8,
    )
    _seed_decision(
        probe_db, decision_id="d2", regime="chop", action="TIGHTEN",
        realized_pnl_r=0.1, giveback_r=0.4,
    )
    # bull_trend: one WIDEN that let a winner run.
    _seed_decision(
        probe_db, decision_id="d3", regime="bull_trend", action="WIDEN",
        realized_pnl_r=1.5, giveback_r=0.1,
    )
    # an OPEN decision (realized NULL) must be excluded from the aggregate.
    _seed_decision(
        probe_db, decision_id="d4", regime="chop", action="TIGHTEN",
        realized_pnl_r=None, giveback_r=None,
    )

    hints = consume_probe_tuning_log(probe_db, min_closed=1)
    by_regime = {h.regime: h for h in hints}

    assert set(by_regime) == {"chop", "bull_trend"}
    chop = by_regime["chop"]
    assert chop.n_closed == 2  # the open d4 excluded
    assert chop.avg_realized_r == pytest.approx((-0.3 + 0.1) / 2)
    assert chop.avg_giveback_r == pytest.approx((0.8 + 0.4) / 2)
    assert by_regime["bull_trend"].n_closed == 1


def test_hint_is_surface_only_never_auto_applied(
    probe_db: sqlite3.Connection,
) -> None:
    _seed_decision(
        probe_db, decision_id="d1", regime="chop", action="TIGHTEN",
        realized_pnl_r=-0.5, giveback_r=0.9,
    )
    [hint] = consume_probe_tuning_log(probe_db, min_closed=1)
    assert isinstance(hint, KnowledgeHint)
    # ADR-011/ADR-012: never auto-applied, /debate-gated. Hard invariant.
    assert hint.auto_apply is False
    assert hint.debate_gated is True
    # It is a digest, not a gate — there is no "tradeable" / block field.
    assert not hasattr(hint, "tradeable")
    assert not hasattr(hint, "block")


def test_consumer_is_read_only(probe_db: sqlite3.Connection) -> None:
    _seed_decision(
        probe_db, decision_id="d1", regime="chop", action="TIGHTEN",
        realized_pnl_r=-0.5, giveback_r=0.9,
    )
    before = probe_db.execute("SELECT COUNT(*) FROM probe_decisions").fetchone()[0]
    consume_probe_tuning_log(probe_db, min_closed=1)
    after = probe_db.execute("SELECT COUNT(*) FROM probe_decisions").fetchone()[0]
    assert before == after == 1


def test_min_sample_floor_suppresses_thin_regimes(
    probe_db: sqlite3.Connection,
) -> None:
    # A single closed decision is below the surfacing floor by default.
    _seed_decision(
        probe_db, decision_id="d1", regime="crisis", action="TIGHTEN",
        realized_pnl_r=-0.2, giveback_r=0.5,
    )
    assert consume_probe_tuning_log(probe_db, min_closed=2) == []
    # ...but always returned when the floor is met.
    _seed_decision(
        probe_db, decision_id="d2", regime="crisis", action="TIGHTEN",
        realized_pnl_r=-0.1, giveback_r=0.3,
    )
    [hint] = consume_probe_tuning_log(probe_db, min_closed=2)
    assert hint.regime == "crisis"
    assert hint.n_closed == 2
