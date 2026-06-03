"""Offload TDD — tick-engine loop heartbeat + stall detector.

A 3-min loop-thread stall (learner full-DB backup / baseline 7d rescan) used to
be INVISIBLE: ``run_tick_decision_loop`` only emits its telemetry line every
30s, so a freeze just dropped ticks with no marker. We add a cheap
per-iteration heartbeat (loop counter + monotonic stamp on ``TickEngineState``)
and a WARNING when an iteration gap exceeds the cadence by more than a slack.

Pins:
  1. ``TickEngineState`` carries ``loop_count`` + ``last_loop_mono`` and the
     loop advances them every iteration (visible even with an empty focus).
  2. A debug "alive" heartbeat line is emitted each iteration.
  3. A stall (iteration gap > cadence + slack) emits a WARNING with the gap.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import pytest

from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._production_tick_engine import (
    TickEngineState,
    run_tick_decision_loop,
)


async def _run_n_iterations(
    conn: sqlite3.Connection, eng: TickEngineState, n: int, cadence: float
) -> None:
    stop = asyncio.Event()

    async def _stopper() -> None:
        while eng.loop_count < n:
            await asyncio.sleep(0)
        stop.set()

    state = ProdLoopState()
    await asyncio.gather(
        run_tick_decision_loop(
            conn, state, stop,
            okx_adapter=None, capital_session=None, alpaca_adapter=None,
            phase="P0", real_roundtrip=False,
            tick_engine_state=eng, cadence_sec=cadence,
        ),
        _stopper(),
    )


@pytest.mark.asyncio
async def test_heartbeat_advances_loop_count(memdb: sqlite3.Connection) -> None:
    """Empty focus still advances the per-iteration heartbeat counters."""
    eng = TickEngineState()
    assert eng.loop_count == 0
    assert eng.last_loop_mono == 0.0
    await _run_n_iterations(memdb, eng, n=3, cadence=0.0)
    assert eng.loop_count >= 3
    assert eng.last_loop_mono > 0.0


@pytest.mark.asyncio
async def test_heartbeat_debug_line_emitted(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Each iteration emits a debug 'alive' heartbeat."""
    eng = TickEngineState()
    with caplog.at_level(logging.DEBUG, logger="polaris.scripts._production_tick_engine"):
        await _run_n_iterations(memdb, eng, n=2, cadence=0.0)
    assert any("heartbeat" in r.message for r in caplog.records), (
        "expected a per-iteration heartbeat debug line"
    )


@pytest.mark.asyncio
async def test_stall_emits_warning(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """An iteration gap beyond cadence+slack must surface a WARNING.

    We simulate a stall by pre-seeding ``last_loop_mono`` to a stamp far in the
    past — the first real iteration then sees a gap of ``now - past`` that
    blows past ``cadence + STALL_SLACK_SEC`` and must warn. This needs no clock
    monkeypatching (which would corrupt asyncio's own scheduling).
    """
    eng = TickEngineState()
    # ~100s "ago" — a 3-min-style starvation. The loop will overwrite this on
    # iteration 1, but it reads the prior value FIRST to compute the gap.
    eng.last_loop_mono = time.monotonic() - 100.0
    with caplog.at_level(logging.WARNING, logger="polaris.scripts._production_tick_engine"):
        await _run_n_iterations(memdb, eng, n=2, cadence=0.5)
    stall_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "stall" in r.message.lower()
    ]
    assert stall_warnings, "a >cadence iteration gap should log a stall WARNING"
