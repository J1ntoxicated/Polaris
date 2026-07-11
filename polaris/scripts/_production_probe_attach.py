"""ADR-012 Slice 1 — observe-only probe attach for ``_evaluate_position``.

DEMO/PAPER. Split out of ``_production_recalc.py`` for the ≤500-LOC budget. One
pure/sync entry point, ``observe_probes``, called INLINE in the recalc pass
AROUND ``run_precise_exit`` (after ``pnl_r`` is computed, after
``run_session_forced_exit`` returns False). It builds a zero-fetch
``ProbeContext``, runs the bus, composes the engine decision in ``observe``
mode, and logs both to the ``data/probes.sqlite`` sidecar.

OBSERVE-ONLY, PROVABLY BYTE-IDENTICAL: it threads NOTHING into
``run_precise_exit`` (its kwargs stay default) and is fully fail-open — a None
sidecar / a raising probe / a fail-open writer never breaks the tick. The hard
rails (−1R G6, protected-BEP, loser-timeout, Q9, hard-MAX) BYPASS this entirely.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from typing import TYPE_CHECKING

from polaris.core.live_recalc.exit_engine import Bucket, bucket_from_correlation_group
from polaris.core.probes import ProbeContext
from polaris.core.probes.tuning_log import log_probe_decisions, log_probe_readings
from polaris.core.streams import resolve_stream
from polaris.strategies import STRATEGY_REGISTRY
from polaris.venues.capital.session_calendar import capital_seconds_to_close

if TYPE_CHECKING:
    from polaris.scripts._production_recalc import ActivePositionRow
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

__all__ = ["PROBE_ENGINE_MODE", "observe_probes", "wire_probe_sidecar"]

# POLARIS_PROBE_ENGINE = observe | trail_only | full (default observe; only
# observe is wired this slice). A non-observe value still composes in observe
# semantics here — the engine threads ZERO knobs until Slice 2 wires them — so
# an accidental flag flip cannot change a live exit before Slice 2.
PROBE_ENGINE_MODE: str = os.environ.get("POLARIS_PROBE_ENGINE", "observe").strip() or (
    "observe"
)

# G6 gate id (the probe attach rides the G6 monitor pass). Imported lazily-style
# as a constant to avoid a heavy import at module load.
_GATE_POSITION_MONITOR: int = 6


def wire_probe_sidecar(state: ProdLoopState, *, probe_db_path: str) -> None:
    """Open ``data/probes.sqlite`` + register the observe-mode probe bus/engine.

    Called once at boot. Fail-open: any error leaves ``state.probe_conn`` None so
    the attach degrades to a pure no-op (the live exit is never affected). Imports
    are local so the catalog/engine only load when the sidecar is wired.
    """
    from polaris.core.probes import ExitEngine, ProbeBus  # noqa: PLC0415
    from polaris.core.probes.catalog import (  # noqa: PLC0415
        LossDefenseProbe,
        ProfitTakingProbe,
        RegimeFitProbe,
        SessionHoursProbe,
        TechnicalProbe,
    )
    from polaris.core.probes.tuning_log import open_probe_db  # noqa: PLC0415

    try:
        state.probe_conn = open_probe_db(probe_db_path)
        state.probe_bus = ProbeBus(
            [ProfitTakingProbe(), LossDefenseProbe(), TechnicalProbe(),
             SessionHoursProbe(), RegimeFitProbe()]
        )
        state.probe_engine = ExitEngine()
        logger.info(
            "[probe] tuning-log sidecar wired (mode=%s) → %s",
            PROBE_ENGINE_MODE, probe_db_path,
        )
    except Exception as exc:  # noqa: BLE001 — sidecar is optional, never halts boot
        state.probe_conn = None
        state.probe_bus = None
        state.probe_engine = None
        logger.warning("[probe] sidecar wiring failed — observe disabled: %r", exc)


def _seconds_to_close(venue: str, now_ts: int) -> int | None:
    """TIME-only seconds to the venue's next session close (None = ABSTAIN).

    Resolves the venue's stream asset_class and asks the shipped
    ``capital_seconds_to_close``. always_on (OKX crypto) has no asset_class
    close → None → the session probe ABSTAINS. Fail-open: any resolve error → None.
    """
    try:
        stream = resolve_stream(venue)
    except (KeyError, ValueError):
        return None
    # Use the first declared asset_class as the close-calendar key (single-class
    # streams are the norm; multi-class falls back to the first, which is the
    # session-calendar driver). always_on / unknown → None inside the helper.
    asset_class = next(iter(stream.asset_classes), "")
    return capital_seconds_to_close(asset_class, now_ts)


def _signal_family_for_strategy(strategy_id: str) -> str:
    """Regime-fit family for ``strategy_id`` — "reversion" | "momentum".

    Mirrors the SAME entry-side derivation ``_sizer_payload`` uses
    (``bucket_from_correlation_group`` off the strategy's registered
    ``correlation_group_id``): REVERSION bucket → "reversion", else
    "momentum" (an unregistered id, e.g. a tick-engine id such as
    ``micro_reversion``, is NOT in ``STRATEGY_REGISTRY``, which mirrors
    ``exit_strategy_config``'s own TREND/"momentum" default — flow_not_block:
    never guess adverse). Callers that already track the TRUE per-position
    family (the tick engine's ``family_by_position``) pass it explicitly
    instead of relying on this fallback.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return "momentum"
    bucket = bucket_from_correlation_group(cls.metadata.correlation_group_id)
    return "reversion" if bucket is Bucket.REVERSION else "momentum"


def _excursion_r(
    *, side: str, entry_price: float, peak: float | None, trough: float | None,
    denom_pct: float,
) -> tuple[float, float]:
    """Derive (mfe_r, mae_r) from the persisted peak/trough vs entry.

    Mirrors ``evaluate_exit``'s excursion math but READ-ONLY from data already in
    hand (no fetch). A missing peak/trough → 0.0 (neutral, the probe will abstain).
    """
    atr_r = max(entry_price * denom_pct, 1e-9)
    pk = entry_price if peak is None else float(peak)
    tr = entry_price if trough is None else float(trough)
    if side == "long":
        mfe_r = max(0.0, (pk - entry_price) / atr_r)
        mae_r = min(0.0, (tr - entry_price) / atr_r)
    else:
        mfe_r = max(0.0, (entry_price - tr) / atr_r)
        mae_r = min(0.0, (entry_price - pk) / atr_r)
    return mfe_r, mae_r


def observe_probes(
    *,
    state: ProdLoopState,
    pos: ActivePositionRow,
    side: str,
    entry_price: float,
    last_price: float,
    atr_pct: float,
    entry_atr_pct: float | None,
    pnl_r: float,
    held_seconds: int,
    regime: str,
    now_ts: int,
    run_id: str,
    mark_source: str = "bar",
    signal_family: str | None = None,
) -> None:
    """Run the probe → engine → tuning-log triple in OBSERVE mode (fail-open).

    PURE/SYNC and called INLINE before ``run_precise_exit`` — no await, so the
    FSM read-modify-write atomicity is untouched. Threads NOTHING into the exit.
    A None sidecar / bus / engine makes this a no-op. Any unexpected error is
    swallowed (the exit must never crash on the observe sidecar).

    Hardening #2 (2026-06-23): ``mark_source`` records WHICH cadence observed —
    ``'bar'`` (the bar recalc pass, the default) or ``'tick'`` (the tick exit
    pass). The tick attach is the SAME observe-only sidecar: it threads NOTHING
    into ``run_precise_exit`` and never enters the FSM critical section.

    W2 (RegimeFitProbe, design-exit-matrix.md §B): ``signal_family`` is
    CALLER-supplied when already tracked (the tick engine's
    ``family_by_position`` — the only source that knows the TRUE family for a
    tick-engine strategy such as ``micro_reversion``). ``None`` (the bar-path
    default) derives it from the position's registered strategy instead
    (``_signal_family_for_strategy``) — imprecise ONLY for a tick-engine
    position observed on the BAR cadence (no registry entry to resolve), the
    SAME imprecision the live ``_production_tick_mfe`` harvest schedule already
    accepts by hardcoding "momentum". Either way this is PURE OBSERVE telemetry
    — it never reaches a live knob.
    """
    bus = getattr(state, "probe_bus", None)
    engine = getattr(state, "probe_engine", None)
    if bus is None or engine is None:
        return
    try:
        denom_pct = atr_pct if entry_atr_pct is None else entry_atr_pct
        mfe_r, mae_r = _excursion_r(
            side=side, entry_price=entry_price,
            peak=pos.get("peak_price"), trough=pos.get("trough_price"),
            denom_pct=max(denom_pct, 1e-4),
        )
        recent = pos.get("recent_ticks", [])
        family = signal_family if signal_family is not None else (
            _signal_family_for_strategy(
                str(pos.get("active_strategy_id") or pos.get("strategy") or "")
            )
        )
        ctx = ProbeContext(
            position_id=str(pos["position_id"]),
            venue=str(pos["venue"]),
            symbol=str(pos["symbol"]),
            underlying_group_id=str(pos.get("underlying_group_id", "")),
            side=side,
            entry_price=entry_price,
            last_price=last_price,
            atr_pct=atr_pct,
            pnl_r=pnl_r,
            mfe_r=mfe_r,
            mae_r=mae_r,
            held_seconds=held_seconds,
            volume_now=float(pos.get("volume_now", 0.0)),
            volume_z=float(pos.get("volume_z", 0.0)),
            atr_slope=float(pos.get("atr_slope", 0.0)),
            recent_ticks=recent if isinstance(recent, list) else [],
            exit_state=str(pos.get("exit_state", "open")),
            regime=regime,
            now_ts=now_ts,
            seconds_to_close=_seconds_to_close(str(pos["venue"]), now_ts),
            signal_family=family,
        )
        readings = bus.observe(ctx)
        decision = engine.compose(readings, mode="observe")
        logger.info(
            "[probe/verdict] %s:%s pos=%s src=%s probes=%s action=%s "
            "lean=%+.3f applied=%s regime=%s pnl_r=%.2f "
            "(OBSERVE-ONLY — threads nothing into the exit)",
            ctx.venue, ctx.symbol, ctx.position_id, mark_source,
            decision.contributing, decision.action,
            decision.composite_lean, decision.applied, regime, pnl_r,
        )
        probe_conn: sqlite3.Connection | None = getattr(state, "probe_conn", None)
        eval_id = uuid.uuid4().hex
        log_probe_readings(
            probe_conn, ts=now_ts, gate_id=_GATE_POSITION_MONITOR,
            position_id=ctx.position_id, readings=readings,
        )
        log_probe_decisions(
            probe_conn, ts=now_ts, run_id=run_id, position_id=ctx.position_id,
            mode="observe", decision=decision, pnl_r_at_decision=pnl_r,
            pnl_r_truth=pnl_r, mark_source=mark_source, mark_age_ms=0,
            exit_state=ctx.exit_state, eval_id=eval_id, regime=ctx.regime,
        )
        state.probe_observe_evals = getattr(state, "probe_observe_evals", 0) + 1
    except Exception as exc:  # noqa: BLE001 — observe sidecar must never break the tick
        logger.warning(
            "[probe] observe attach failed for %s — exit unaffected: %r",
            pos.get("position_id", "?"), exc,
        )
