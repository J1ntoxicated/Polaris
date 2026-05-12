"""Layer 7 — strategy worker (asyncio task per strategy + supervisor).

Spec source: vault/30_components/layer-7-strategy-isolation.md (Q1).

P0 = asyncio task per strategy. The supervisor:
  1. Awaits ``strategy.tick(snapshot)`` for each strategy in parallel.
  2. Catches every exception and feeds it to ``circuit_breaker.record_fault``
     (no silent ``except: pass``).
  3. Skips strategies whose mode is HARD_HALT / RISK_ONLY.

Mutable shared state must NOT be touched by ``strategy.tick``. The snapshot is
frozen and any allocation must go through :class:`AllocatorFence`.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from polaris.core.isolation.circuit_breaker import (
    ACTIVE,
    FAULT_EXCEPTION,
    HARD_HALT,
    RISK_ONLY,
    current_strategy_mode,
    record_fault,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AccountSnapshot",
    "PipelineTaskSpec",
    "StrategyProtocol",
    "build_account_snapshot",
    "run_strategy_task",
    "supervise_pipeline_tasks",
    "supervise_strategies",
]


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Immutable per-cycle view fan-out to every strategy task."""

    snapshot_id: str
    created_ts: int
    positions: tuple[dict[str, Any], ...] = ()
    open_orders: tuple[dict[str, Any], ...] = ()
    balances: tuple[dict[str, Any], ...] = ()
    venue_limits: dict[str, float] | None = None
    cluster_usage: dict[str, float] | None = None


class StrategyProtocol(Protocol):
    """Minimal strategy contract the supervisor expects.

    ``tick`` runs in ACTIVE mode (entries + exits). ``tick_risk_only`` runs in
    RISK_ONLY mode (no new entries, exit/stop-update only). Strategies that do
    not implement ``tick_risk_only`` are silently skipped during RISK_ONLY —
    they will resume on manual reset back to ACTIVE.
    """

    strategy_id: str

    async def tick(self, snapshot: AccountSnapshot) -> None: ...


def build_account_snapshot(
    *,
    now_ts: int,
    positions: tuple[dict[str, Any], ...] = (),
    open_orders: tuple[dict[str, Any], ...] = (),
    balances: tuple[dict[str, Any], ...] = (),
    venue_limits: dict[str, float] | None = None,
    cluster_usage: dict[str, float] | None = None,
) -> AccountSnapshot:
    """Build an immutable snapshot — caller is responsible for freezing the inputs."""
    return AccountSnapshot(
        snapshot_id=str(uuid.uuid4()),
        created_ts=now_ts,
        positions=positions,
        open_orders=open_orders,
        balances=balances,
        venue_limits=venue_limits,
        cluster_usage=cluster_usage,
    )


async def run_strategy_task(
    strategy: StrategyProtocol,
    snapshot: AccountSnapshot,
    *,
    conn: sqlite3.Connection,
    now_ts: int,
    has_open_positions: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Run one strategy's tick, capturing exceptions into the circuit breaker.

    ``ACTIVE`` → call ``strategy.tick(snapshot)``.
    ``RISK_ONLY`` → call ``strategy.tick_risk_only(snapshot)`` if the strategy
    implements it (P0 spec: exit/stop-update path); otherwise no-op.
    ``SOFT_HALT`` / ``HARD_HALT`` → no-op.

    Returns ``{"strategy_id": ..., "mode": ..., "exception": Optional[str]}``.
    """
    mode = current_strategy_mode(conn, strategy.strategy_id, now_ts=now_ts)

    if mode == RISK_ONLY:
        risk_hook = getattr(strategy, "tick_risk_only", None)
        if risk_hook is None:
            return {"strategy_id": strategy.strategy_id, "mode": mode, "exception": None}
        try:
            await risk_hook(snapshot)
            return {"strategy_id": strategy.strategy_id, "mode": mode, "exception": None}
        except Exception as exc:  # noqa: BLE001 — supervisor must not swallow silently
            has_pos = (
                bool(has_open_positions(strategy.strategy_id)) if has_open_positions else True
            )
            result = record_fault(
                conn,
                strategy_id=strategy.strategy_id,
                fault_type=FAULT_EXCEPTION,
                now_ts=now_ts,
                detail={
                    "exc_type": type(exc).__name__,
                    "exc_msg": str(exc)[:500],
                    "phase": "risk_only_tick",
                },
                has_open_positions=has_pos,
            )
            return {
                "strategy_id": strategy.strategy_id,
                "mode": result.get("mode", RISK_ONLY),
                "exception": f"{type(exc).__name__}: {exc}",
            }

    if mode != ACTIVE:
        logger.debug(
            "[isolation] strategy=%s skipped — mode=%s",
            strategy.strategy_id,
            mode,
        )
        return {"strategy_id": strategy.strategy_id, "mode": mode, "exception": None}

    try:
        await strategy.tick(snapshot)
        logger.debug(
            "[isolation] strategy=%s tick OK snapshot=%s",
            strategy.strategy_id,
            snapshot.snapshot_id,
        )
        return {"strategy_id": strategy.strategy_id, "mode": ACTIVE, "exception": None}
    except Exception as exc:  # noqa: BLE001 — supervisor must not swallow silently
        has_pos = bool(has_open_positions(strategy.strategy_id)) if has_open_positions else False
        result = record_fault(
            conn,
            strategy_id=strategy.strategy_id,
            fault_type=FAULT_EXCEPTION,
            now_ts=now_ts,
            detail={"exc_type": type(exc).__name__, "exc_msg": str(exc)[:500]},
            has_open_positions=has_pos,
        )
        logger.error(
            "[isolation] strategy=%s tick FAULT %s: %s → mode=%s",
            strategy.strategy_id,
            type(exc).__name__,
            str(exc)[:200],
            result.get("mode", HARD_HALT),
        )
        return {
            "strategy_id": strategy.strategy_id,
            "mode": result.get("mode", HARD_HALT),
            "exception": f"{type(exc).__name__}: {exc}",
        }


async def supervise_strategies(
    strategies: list[StrategyProtocol],
    snapshot_provider: Callable[[], Awaitable[AccountSnapshot]],
    *,
    conn: sqlite3.Connection,
    now_ts_provider: Callable[[], int],
    has_open_positions: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Run one supervisor cycle: build snapshot, fan-out, gather results.

    Day 9 F11 fix: now uses ``asyncio.TaskGroup`` so a strategy crash cannot
    abort the rest of the cycle. Each ``run_strategy_task`` already records
    faults to the circuit breaker, so a TaskGroup-wrapped iteration retains
    fault propagation while keeping siblings alive (TG would normally cancel
    on raise — ``run_strategy_task`` swallows + records, so it never raises).
    """
    snapshot = await snapshot_provider()
    now_ts = now_ts_provider()
    results: list[dict[str, Any]] = []
    async with asyncio.TaskGroup() as tg:
        running = [
            tg.create_task(
                run_strategy_task(
                    s, snapshot, conn=conn, now_ts=now_ts,
                    has_open_positions=has_open_positions,
                )
            )
            for s in strategies
        ]
    for task in running:
        results.append(task.result())
    return results


@dataclass(frozen=True, slots=True)
class PipelineTaskSpec:
    """Spec for one supervised pipeline task.

    The production loop builds one of these per (strategy, signal) pair after
    raw-signal generation. The supervisor below wraps the coroutine in a
    TaskGroup, catches any escaped exception, and records a strategy-scoped
    fault so the circuit breaker can transition the offending strategy
    (ACTIVE → SOFT_HALT → HARD_HALT → RISK_ONLY) without aborting siblings.
    """

    strategy_id: str
    coro_factory: Callable[[], Awaitable[None]]


async def supervise_pipeline_tasks(
    specs: list[PipelineTaskSpec],
    *,
    conn: sqlite3.Connection,
    now_ts: int,
    fault_phase: str = "pipeline_supervisor",
) -> list[dict[str, Any]]:
    """Layer 7 SSOT supervisor for per-signal pipeline tasks.

    Spec source: ``vault/30_components/layer-7-strategy-isolation.md`` Q1.

    Day 9 F11 fix replaces the bare ``asyncio.gather(..., return_exceptions=True)``
    site in ``production_paper_loop._run_tick`` with this Layer-7 SSOT helper.
    Behaviour contract:

    * Uses ``asyncio.TaskGroup`` (Python 3.13) so the supervisor itself sees
      every task to completion.
    * Per-task ``try/except BaseException`` (excluding ``CancelledError``) so
      one strategy fail cannot cancel siblings via TG's default propagation.
    * Routes every escaped exception to ``circuit_breaker.record_fault`` with
      the offending strategy's ``strategy_id`` (NOT the generic ``"pipeline"``
      bucket the legacy code used) so the 4-state machine + halts work.

    Returns one result dict per spec: ``{"strategy_id", "exception"}``.
    """
    if not specs:
        return []
    results: list[dict[str, Any]] = []

    async def _wrap(spec: PipelineTaskSpec) -> dict[str, Any]:
        try:
            await spec.coro_factory()
            return {"strategy_id": spec.strategy_id, "exception": None}
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — supervisor must never swallow silently
            try:
                record_fault(
                    conn,
                    strategy_id=spec.strategy_id,
                    fault_type=FAULT_EXCEPTION,
                    now_ts=now_ts,
                    detail={
                        "phase": fault_phase,
                        "exc_type": type(exc).__name__,
                        "exc_msg": str(exc)[:500],
                    },
                )
            except Exception as record_exc:  # noqa: BLE001 — best-effort, do not lose original
                logger.error(
                    "[isolation] record_fault failed for %s: %r",
                    spec.strategy_id, record_exc,
                )
            logger.error(
                "[isolation/pipeline] strategy=%s task FAULT %s: %s",
                spec.strategy_id, type(exc).__name__, str(exc)[:200],
            )
            return {
                "strategy_id": spec.strategy_id,
                "exception": f"{type(exc).__name__}: {exc}",
            }

    async with asyncio.TaskGroup() as tg:
        running = [tg.create_task(_wrap(spec)) for spec in specs]
    for task in running:
        results.append(task.result())
    return results
