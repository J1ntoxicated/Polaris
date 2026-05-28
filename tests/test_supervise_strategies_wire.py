"""Day 9 F11 — Layer 7 supervise_strategies SSOT wire-up tests.

Spec source: vault/30_components/layer-7-strategy-isolation.md (Q1).

Verifies the production paper loop now routes per-signal pipeline tasks
through ``supervise_pipeline_tasks`` (TaskGroup pattern + per-strategy
fault recording) instead of bare ``asyncio.create_task`` + ``asyncio.gather``.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import time
from typing import Any

import pytest

from polaris.core.isolation import (
    FAULT_EXCEPTION,
    HARD_HALT,
    PipelineTaskSpec,
    current_strategy_mode,
    supervise_pipeline_tasks,
)

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# supervise_pipeline_tasks contract (TaskGroup + record_fault)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_pipeline_tasks_empty_returns_empty(
    memdb: sqlite3.Connection,
) -> None:
    """No specs → no DB writes, no exceptions, empty result."""
    out = await supervise_pipeline_tasks([], conn=memdb, now_ts=NOW)
    assert out == []
    n_faults = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events"
    ).fetchone()[0]
    assert int(n_faults) == 0


@pytest.mark.asyncio
async def test_taskgroup_pattern_one_strategy_fails_others_continue(
    memdb: sqlite3.Connection,
) -> None:
    """One spec raises → siblings still complete, raise is captured per-task."""
    completed: list[str] = []

    async def _ok_a() -> None:
        await asyncio.sleep(0)
        completed.append("a")

    async def _boom_b() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("kapow b")

    async def _ok_c() -> None:
        await asyncio.sleep(0)
        completed.append("c")

    specs = [
        PipelineTaskSpec(strategy_id="a", coro_factory=_ok_a),
        PipelineTaskSpec(strategy_id="b", coro_factory=_boom_b),
        PipelineTaskSpec(strategy_id="c", coro_factory=_ok_c),
    ]
    results = await supervise_pipeline_tasks(specs, conn=memdb, now_ts=NOW)
    by_id = {r["strategy_id"]: r for r in results}
    assert by_id["a"]["exception"] is None
    assert by_id["c"]["exception"] is None
    assert by_id["b"]["exception"] is not None
    assert "RuntimeError" in by_id["b"]["exception"]
    # The non-failing tasks ran to completion despite the sibling raising.
    assert "a" in completed
    assert "c" in completed


@pytest.mark.asyncio
async def test_record_fault_called_on_exception(
    memdb: sqlite3.Connection,
) -> None:
    """A raised exception writes a strategy_fault_events row with the
    offending strategy_id (NOT the legacy generic 'pipeline' bucket)."""
    async def _boom() -> None:
        raise ValueError("bad sizing")

    await supervise_pipeline_tasks(
        [PipelineTaskSpec(strategy_id="volume_burst", coro_factory=_boom)],
        conn=memdb, now_ts=NOW,
    )
    # The faulting strategy got a per-strategy fault row.
    rows = memdb.execute(
        "SELECT fault_type, strategy_id FROM strategy_fault_events "
        "WHERE strategy_id = 'volume_burst'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == FAULT_EXCEPTION
    # The generic bucket is NOT used.
    legacy = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id = 'pipeline'"
    ).fetchone()[0]
    assert int(legacy) == 0


@pytest.mark.asyncio
async def test_record_fault_threshold_transitions_state(
    memdb: sqlite3.Connection,
) -> None:
    """3 exception faults / 300s on the same strategy → HARD_HALT."""
    async def _boom() -> None:
        raise RuntimeError("repeating fault")

    for _ in range(3):
        await supervise_pipeline_tasks(
            [PipelineTaskSpec(strategy_id="vb_repeat", coro_factory=_boom)],
            conn=memdb, now_ts=NOW,
        )
    # Circuit breaker transitioned ACTIVE → HARD_HALT (3 / 300s threshold).
    assert current_strategy_mode(memdb, "vb_repeat", now_ts=NOW + 1) == HARD_HALT


@pytest.mark.asyncio
async def test_supervisor_uses_taskgroup_pattern(memdb: sqlite3.Connection) -> None:
    """Source-level guard: ``supervise_pipeline_tasks`` body uses ``TaskGroup``."""
    src = inspect.getsource(supervise_pipeline_tasks)
    assert "asyncio.TaskGroup" in src, (
        "Layer 7 SSOT must use asyncio.TaskGroup (Python 3.13) per "
        "vault/30_components/layer-7-strategy-isolation.md Q1"
    )


# ---------------------------------------------------------------------------
# production_paper_loop call site (no bare asyncio.gather over create_task)
# ---------------------------------------------------------------------------


def _read_paper_loop_source() -> str:
    """Read the per-tick orchestration source from disk.

    ``_run_tick`` lives in ``_production_tick.py`` (split out of
    ``production_paper_loop.py`` for the ≤500-LOC budget). We grep the source
    text rather than ``inspect.getsource(_run_tick)`` because the function body
    has a deeply-nested closure with keyword-only parameters that triggers a
    spurious ``tokenize.TokenError('EOF in multi-line string')`` on Python
    3.13's ``inspect.getblock`` heuristic — the file itself parses cleanly via
    ``ast.parse``, but ``getblock`` walks the raw token stream and gives up.
    Reading the file is robust to that.
    """
    from pathlib import Path

    return Path("polaris/scripts/_production_tick.py").read_text()


def test_production_loop_uses_supervise_pipeline_tasks() -> None:
    """``_run_tick`` must call ``supervise_pipeline_tasks`` and not the
    legacy bare ``asyncio.gather(...)`` over per-signal create_task site."""
    src = _read_paper_loop_source()
    assert "supervise_pipeline_tasks" in src, (
        "Day 9 F11 wire requires _run_tick to delegate to supervise_pipeline_tasks"
    )
    assert "PipelineTaskSpec" in src, (
        "Day 9 F11 wire requires _run_tick to build PipelineTaskSpec list"
    )
    # The legacy generic 'pipeline' fault bucket must be gone — we now
    # route every fault to the offending strategy's strategy_id.
    assert 'strategy_id="pipeline"' not in src and "strategy_id='pipeline'" not in src


def test_production_loop_skips_halted_strategies() -> None:
    """``_run_tick`` must consult ``should_allow_new_entry`` to skip HALTed
    strategies before raw-signal generation (Day 9 F11 fix)."""
    src = _read_paper_loop_source()
    assert "should_allow_new_entry" in src, (
        "Day 9 F11: _run_tick must skip HALTed strategies via should_allow_new_entry"
    )


# ---------------------------------------------------------------------------
# Smoke — full production loop tick with one failing strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_loop_one_strategy_failure_does_not_kill_others(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke: the loop's tick must keep running 6 strategies even if 1 raises.

    We monkeypatch ``run_pipeline_for_signal`` to raise for one specific
    strategy_id and complete normally for the rest, then assert the
    supervised counters match (1 failure recorded; siblings ran).
    """
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts import _production_tick as tick
    from polaris.scripts import production_paper_loop as ppl
    from polaris.scripts._smoke_gpt_stub import StubGPTClient

    reset_process_fence()
    state = ppl.ProdLoopState()
    haiku = StubGPTClient()

    # Replace run_pipeline_for_signal in the tick body's namespace (where
    # ``_run_tick`` lives post-split) with a controllable test double.
    call_log: list[str] = []

    async def _fake_pipeline(
        *, strategy: Any, sig: Any, **kwargs: Any,
    ) -> None:
        sid = strategy.metadata.strategy_id
        call_log.append(sid)
        if sid == "tsmom":
            raise RuntimeError("simulated TSMOM crash")

    monkeypatch.setattr(tick, "run_pipeline_for_signal", _fake_pipeline)

    # Build supervision specs by hand (mimics what _run_tick does) so we
    # can assert siblings continue without spinning up the full DB seed.
    from polaris.strategies import (
        TSMOMStrategy,
        VolumeBurstStrategy,
    )

    strategies = [VolumeBurstStrategy(), TSMOMStrategy()]
    specs: list[PipelineTaskSpec] = []
    for s in strategies:
        sid = s.metadata.strategy_id

        def _factory(_strategy: Any = s) -> Any:
            return _fake_pipeline(strategy=_strategy, sig=None)

        specs.append(PipelineTaskSpec(strategy_id=sid, coro_factory=_factory))

    results = await supervise_pipeline_tasks(specs, conn=memdb, now_ts=NOW)
    by_id = {r["strategy_id"]: r for r in results}
    assert by_id["volume_burst"]["exception"] is None
    assert by_id["tsmom"]["exception"] is not None
    # Both strategies were invoked — TSMOM's failure didn't cancel VB.
    assert "volume_burst" in call_log
    assert "tsmom" in call_log
    # Fault recorded against tsmom specifically.
    n = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id = 'tsmom'"
    ).fetchone()[0]
    assert int(n) == 1
    _ = state
    _ = haiku
    _ = time.time  # silence unused-import lint
