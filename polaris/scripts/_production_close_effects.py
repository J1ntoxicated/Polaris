"""Day 8 — production close-path post-trade fan-out (fault-isolated effects).

Auxiliary fail-safe helpers split out of ``_production_close`` to keep both
modules ≤500 LOC. Each ``_safe_*`` helper wraps one post-close side effect
(fault record / regime lookup / Layer 4 cell-matrix / Layer 5 learners / G8
reflector) so a failure in one never aborts the close transaction. Imported +
called by ``_production_close._close_trade_with_real_pnl``.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    record_fault,
)
from polaris.core.learners import ClosedTrade, LearnerScheduler
from polaris.core.learners.meta_label import (
    compute_triple_barrier_label,
    persist_meta_label,
)
from polaris.core.learners.posterior import (
    cost_adjusted_pnl_r,
    maybe_update_posterior,
    maybe_update_strategy_regime_prior,
)
from polaris.core.metrics.risk_unit import r_budget_for_venue
from polaris.core.pipeline.agents import post_trade_reflector_gate
from polaris.core.pipeline.agents._gpt_client import (
    GPT_P0_MODEL,
    GPT_P1_MODEL,
)
from polaris.core.pipeline.gate_orchestrator import log_gate_event
from polaris.core.pipeline.gate_state import (
    GATE_POST_TRADE_REFLECTOR,
    GateContext,
    SignalLifecycle,
)
from polaris.core.sizing.session import resolve_venue_session
from polaris.core.streams import resolve_stream, resolve_stream_profile
from polaris.scripts._smoke_fills import SimulatedTrade

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)


def _safe_record_fault(
    conn: sqlite3.Connection, *, strategy_id: str, phase: str, exc: BaseException,
    now_ts: int, state: ProdLoopState,
) -> None:
    """Record a fault without ever propagating an exception out of this call."""
    try:
        record_fault(
            conn, strategy_id=strategy_id, fault_type=FAULT_EXCEPTION,
            now_ts=now_ts,
            detail={"phase": phase, "exc": str(exc)[:200]},
        )
        state.fault_events += 1
    except Exception as inner:  # noqa: BLE001 — fault recording is best-effort
        logger.error(
            "[fault] record_fault itself failed phase=%s strategy=%s: %r",
            phase, strategy_id, inner,
        )


def _safe_lookup_regime(
    lookup_regime: Any, conn: sqlite3.Connection, trade: SimulatedTrade,
) -> str:
    try:
        return str(lookup_regime(conn, trade.venue, trade.symbol))
    except Exception as exc:  # noqa: BLE001 — fail-open to chop
        logger.error("[L6] lookup_regime raised: %r", exc)
        return "chop"


def _safe_lookup_entry_regime(
    lookup_regime: Any, conn: sqlite3.Connection, trade: SimulatedTrade,
) -> str:
    """P1-10 fix — fold key is the regime the position was ADMITTED under.

    ``_safe_lookup_regime`` reads the LIVE Layer 6 SSOT (``regime_state``),
    which can have flipped between entry and close. Folding a close under the
    close-time regime credits/blames a DIFFERENT cell than the one that
    actually sized the entry (2026-07-02 audit: 6 of 14 folds mismatched),
    corrupting the exact learning signal the cell matrix exists to produce.
    ``positions.entry_regime`` is stamped once at open (immutable) and is the
    correct fold key. Fails open to ``_safe_lookup_regime`` (live SSOT) when
    there is no ``position_id`` or the column is NULL (legacy rows / pre-stamp
    positions) — never blocks a fold, only re-keys it.
    """
    if trade.position_id:
        try:
            row = conn.execute(
                "SELECT entry_regime FROM positions WHERE position_id = ?",
                (trade.position_id,),
            ).fetchone()
            if row is not None and row[0]:
                return str(row[0])
        except Exception as exc:  # noqa: BLE001 — fail-open to live SSOT lookup
            logger.error("[L4] entry_regime lookup raised: %r", exc)
    return _safe_lookup_regime(lookup_regime, conn, trade)


def fold_close_slice(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, lookup_regime: Any,
    slice_pnl_r: float, slice_pnl_usd: float, now_ts: int, state: ProdLoopState,
) -> None:
    """P2 R-ledger-leak fix — fold ONE close SLICE into the learning layer.

    The PARTIAL-close path used to persist the slice's ``pnl_usd`` to ``fills``
    but feed NOTHING to the cell matrix / Layer-5 learners / posterior, so a
    position that closed predominantly via partials (LUNA / DOT, 2026-06-23 g1
    audit: −$14.85 NET, 93% of the gross loss) never trained the learners — the
    learning layer under-recognised the loss (optimism bias). This folds the
    slice with the SAME real-fee NET pipeline the full close uses, so partial +
    remainder sum to exactly ONE whole-position fold (``realised_r_stream`` is
    linear in ``pnl_usd`` → a slice's R is its fraction of the whole; the full
    close folds only its final-slice R).

    Folds the cell matrix, the Layer-5 learners, the meta-label and the posterior
    (the SAME four the full close folds). It does NOT fire G8 (a post-TRADE
    reflector — terminal-only, fires once when the position fully closes) nor the
    segment / excursion writes (terminal-only). ``won`` is the real-fee NET sign,
    identical to the full close. Measurement/learning only — never read by sizing
    (the 9-stack ban + −1.0R rail are untouched). Each ``_safe_*`` helper is
    independently fail-open, mirroring the full-close fan-out.
    """
    _pnl_usd_net, pnl_r_net = compute_net_pnl_r(
        conn, trade=trade, gross_pnl_r=slice_pnl_r, gross_pnl_usd=slice_pnl_usd,
    )
    won = pnl_r_net > 0.0
    # P1-10 fix — fold key is the ADMITTING regime (positions.entry_regime),
    # not the live SSOT at close time (see _safe_lookup_entry_regime).
    regime = _safe_lookup_entry_regime(lookup_regime, conn, trade)
    _safe_update_cell_matrix(
        conn, trade=trade, regime=regime, pnl_r=pnl_r_net, won=won,
        now_ts=now_ts, state=state,
    )
    _safe_run_learners(
        conn, trade=trade, regime=regime, pnl_r=pnl_r_net, won=won,
        now_ts=now_ts, state=state,
    )
    _safe_record_meta_label(
        conn, trade=trade, regime=regime, pnl_r=pnl_r_net, won=won,
        now_ts=now_ts, state=state,
    )
    _safe_update_posterior(
        conn, trade=trade, regime=regime, pnl_r_net=pnl_r_net,
        pnl_r_gross=slice_pnl_r, now_ts=now_ts,
    )


def _safe_update_cell_matrix(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
) -> None:
    try:
        update_on_trade_close(
            conn,
            trade=TradeClose(
                key=CellKeyP0(
                    exchange=trade.venue, strategy=trade.strategy_id,
                    ticker=trade.symbol, regime=regime,
                ),
                context=CellContext(
                    # Stream SSOT (design §2.1): okx→spot, capital→cfd, identical
                    # to the prior venue-binary literal.
                    group=resolve_stream(trade.venue).product_class,
                    # Venue-native session (D2): real per-venue bucket, not a
                    # hardcoded "asia" — so the cell matrix + session learner
                    # see a meaningful split instead of a pinned floor.
                    session=resolve_venue_session(trade.venue, now_ts),
                    direction=trade.side,
                    liquidity_tier="top",
                ),
                pnl_r=pnl_r, won=won, closed_ts=now_ts,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.error("[L4] cell matrix update failed: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="cell_matrix_update", exc=exc, now_ts=now_ts, state=state,
        )


def _safe_run_learners(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
) -> None:
    try:
        sched = LearnerScheduler(conn, expected_holding_bars=20)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.error("[L5] LearnerScheduler init raised: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="learner_init", exc=exc, now_ts=now_ts, state=state,
        )
        return
    closed_record = ClosedTrade(
        trade_id=trade.signal_id, strategy_id=trade.strategy_id,
        ticker=trade.symbol, venue=trade.venue, regime=regime,
        # Venue-native session (D2): the session_mult learner now buckets on the
        # REAL per-venue session instead of a constant "asia" floor.
        session=resolve_venue_session(trade.venue, now_ts),
        pnl_r=pnl_r, won=won, holding_bars=20, closed_ts=now_ts,
    )
    for learner in sched.learners:
        try:
            learner.update(closed_record, now_ts=now_ts)
        except Exception as exc:  # noqa: BLE001 — fault-isolate per learner
            logger.error(
                "[L5] learner %s update raised: %r",
                getattr(learner, "learner_id", "<unknown>"), exc,
            )
            _safe_record_fault(
                conn, strategy_id=trade.strategy_id,
                phase=f"learner_update_{getattr(learner, 'learner_id', 'unknown')}",
                exc=exc, now_ts=now_ts, state=state,
            )


def _safe_record_meta_label(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
    expected_holding_bars: int = 20,
) -> None:
    """Meta-labeling (#10) — record the triple-barrier label for this close.

    Collection-only: writes one ``meta_labels`` row per closed trade and never
    gates sizing/exits (AGGRESSIVE bias preserved). Fail-open — a label failure
    must never abort an already-committed close. ``holding_bars`` is derived
    from elapsed wall-clock against 1-minute bars (the loop's bar cadence).
    """
    try:
        elapsed_sec = max(0, now_ts - trade.open_ts)
        holding_bars = elapsed_sec // 60
        label = compute_triple_barrier_label(
            pnl_r=pnl_r, won=won, holding_bars=holding_bars,
            expected_holding_bars=expected_holding_bars,
        )
        persist_meta_label(
            conn, trade_id=trade.signal_id, strategy_id=trade.strategy_id,
            venue=trade.venue, ticker=trade.symbol, regime=regime,
            session="asia", label=label, now_ts=now_ts,
        )
        state.meta_labels += 1
    except Exception as exc:  # noqa: BLE001 — collection-only side effect, fail-open
        logger.warning(
            "[meta-label] label record failed %s:%s: %r",
            trade.venue, trade.symbol, exc,
        )


def _safe_backfill_probe_outcome(
    *, state: ProdLoopState, trade: SimulatedTrade, realized_atr_r: float,
    mfe_r: float, mae_r: float, now_ts: int,
) -> None:
    """ADR-012 — fill the probe tuning-log outcome cols for this closed position.

    Collection-only, FAIL-OPEN (mirrors ``_safe_record_meta_label``): writes to
    the SEPARATE ``state.probe_conn`` sidecar (never the live DB) so the offline
    /debate calibration can join the observe-mode would-be decisions to the
    realized outcome. No-op when there is no position_id / no sidecar. Never
    gates sizing/exits; a backfill failure must never abort an already-committed
    close.

    Hardening #6 ruler honesty: the probe-outcome columns (``realized_pnl_r`` /
    ``mfe_r_final`` / ``giveback_r``) are ALL the per-trade-ATR EXCURSION ruler
    (``unit_tag='excursion'``), so ``realized_atr_r`` (the dollar pnl rescaled by
    the per-trade-ATR whole-position 1R) — NOT the per-stream ledger ``pnl_r`` —
    is backfilled into ``realized_pnl_r``. ``giveback_r = mfe_r − realized_atr_r``
    is then a same-ruler subtraction (both EXCURSION) instead of a meaningless
    cross-ruler one.
    """
    probe_conn = getattr(state, "probe_conn", None)
    position_id = trade.position_id
    if probe_conn is None or not position_id:
        return
    try:
        from polaris.core.probes.tuning_log import (  # noqa: PLC0415
            backfill_probe_outcome,
        )

        time_to_exit = max(0, now_ts - getattr(trade, "open_ts", now_ts))
        backfill_probe_outcome(
            probe_conn, position_id=str(position_id),
            realized_pnl_r=realized_atr_r,
            close_reason=getattr(trade, "close_reason", "") or "exit",
            mfe_r_final=mfe_r, mae_r_final=mae_r,
            giveback_r=max(0.0, mfe_r - realized_atr_r),
            time_to_exit_sec=time_to_exit, outcome_ts=now_ts,
        )
    except Exception as exc:  # noqa: BLE001 — collection-only, fail-open
        logger.warning(
            "[probe] outcome backfill failed %s:%s — close unaffected: %r",
            trade.venue, trade.symbol, exc,
        )


def _read_cost_inputs(
    conn: sqlite3.Connection, trade: SimulatedTrade
) -> tuple[float, float, float, float, float, float | None]:
    """Read (entry_fee, entry_slip, exit_fee, exit_slip, size_usd, r_budget_usd).

    P0-2 fix — BOTH legs are matched by ``contribution_id = position_id`` (the
    close fill is persisted with ``contribution_id=trade.position_id`` in
    ``_close_trade_with_real_pnl``). This stops a sibling position on the same
    (strategy, instrument) from having its close fee/slippage cross-applied.
    Only when ``position_id`` is unset (legacy callers) do we fall back to the
    latest ``(strategy, instrument)`` fill.

    FIX 2 (unit boundary, 2026-07-02 audit) — the returned denominator is now
    ``r_budget_for_venue(trade.venue)``, the SAME per-stream R_budget constant
    (OKX $1,580 / Capital $1,020 / ...) that ``gross_pnl_r`` (passed in by the
    caller as ``realised_r_stream = pnl_usd / R_budget``) is already denominated
    in. The PRIOR denominator (a 1m-bar-ATR-derived whole-position 1R,
    ``size_usd × atr_pct × 2``) was a COMPLETELY DIFFERENT unit — dividing the
    R_budget-unit gross by an ATR-unit cost pinned Capital scratches to a
    uniform −0.30R and blew OKX cost-in-R out to −0.69..−1.79R (a fee-rate /
    ATR-floor artefact, not real edge). ``pnl_r_net = gross_pnl_r -
    cost_usd/r_budget_usd`` is now a same-unit subtraction. An unknown venue
    (``r_budget_for_venue`` → 0.0) returns ``None`` so the caller SKIPS the
    cost-in-R adjustment entirely (measurement-only — never gates sizing).
    """
    inst = f"{trade.venue}:{trade.symbol}"
    entry: tuple[Any, ...] | None = None
    exit_row: tuple[Any, ...] | None = None
    if trade.position_id:
        entry = conn.execute(
            "SELECT fee_usd, slippage_bps, size_usd, fill_price FROM fills "
            "WHERE contribution_id = ? AND instrument_id = ? AND is_close = 0 "
            "ORDER BY ts_ms ASC LIMIT 1",
            (trade.position_id, inst),
        ).fetchone()
        exit_row = conn.execute(
            "SELECT fee_usd, slippage_bps FROM fills "
            "WHERE contribution_id = ? AND instrument_id = ? AND is_close = 1 "
            "ORDER BY ts_ms DESC LIMIT 1",
            (trade.position_id, inst),
        ).fetchone()
    if entry is None:
        entry = conn.execute(
            "SELECT fee_usd, slippage_bps, size_usd, fill_price FROM fills "
            "WHERE strategy_id = ? AND instrument_id = ? AND is_close = 0 "
            "ORDER BY ts_ms DESC LIMIT 1",
            (trade.strategy_id, inst),
        ).fetchone()
    if exit_row is None:
        exit_row = conn.execute(
            "SELECT fee_usd, slippage_bps FROM fills "
            "WHERE strategy_id = ? AND instrument_id = ? AND is_close = 1 "
            "ORDER BY ts_ms DESC LIMIT 1",
            (trade.strategy_id, inst),
        ).fetchone()
    entry_fee = float(entry[0]) if entry else 0.0
    entry_slip = float(entry[1]) if entry else 0.0
    size_usd = float(entry[2]) if entry else trade.notional_usd
    exit_fee = float(exit_row[0]) if exit_row else 0.0
    exit_slip = float(exit_row[1]) if exit_row else 0.0
    # FIX 2 — same R_budget constant the caller's gross_pnl_r already divides by
    # (realised_r_stream). A venue with no configured budget → None sentinel;
    # the caller skips the cost-in-R adjustment rather than divide by 0.
    budget = r_budget_for_venue(trade.venue)
    r_budget_usd: float | None = budget if budget > 0.0 else None
    return entry_fee, entry_slip, exit_fee, exit_slip, size_usd, r_budget_usd


def compute_net_pnl_r(
    conn: sqlite3.Connection, *, trade: SimulatedTrade,
    gross_pnl_r: float, gross_pnl_usd: float,
) -> tuple[float, float]:
    """REAL-fee-net ``(pnl_usd_net, pnl_r_net)`` for one closed position.

    REAL-FEE CANONICAL (FIX 1): reads the per-fill REAL fee (``fills.fee_usd``,
    stamped real at the truth boundary) + slippage for BOTH legs (entry is_close=0
    + exit is_close=1 → leg-once, no double-count) and subtracts the round-trip
    cost via :func:`cost_adjusted_pnl_r`. This is the SAME net the NIG posterior
    already used — lifted into ONE place so the close path computes it ONCE and
    feeds the IDENTICAL net R to the cell matrix, the Layer-5 learners, the
    meta-label, the posterior, AND the ``won`` verdict. Without this the cell /
    learners folded the fee-FREE gross R while only the posterior was net — a
    fee≈gross scalp looked like a winner to the cell stats and a loss to the
    posterior (the inconsistency this fix removes).

    #46 single-truth preserved: ``fills.pnl_usd`` (GROSS) + ``fills.fee_usd``
    (REAL) remain the truth; the net is an in-memory derivation only (no new truth
    column). FIX 2 (unit boundary) — the cost-in-R denominator is now the SAME
    per-stream R_budget constant ``gross_pnl_r`` is already denominated in (see
    ``_read_cost_inputs``), so ``pnl_r_net = gross_pnl_r - cost_usd/R_budget`` is
    a same-unit subtraction (previously an ATR-unit cost was subtracted from an
    R_budget-unit gross — a unit mismatch that pinned Capital scratches to a
    uniform −0.30R and inflated OKX cost-in-R to −0.69..−1.79R). An unknown venue
    (no configured R_budget) → the cost-in-R adjustment is SKIPPED (net R ==
    gross R) so the learners never fold a divide-by-zero artefact. ``pnl_usd_net``
    always nets the dollar cost (finite + sane). NEVER read by sizing
    (measurement/learning only — the 9-stack ban + −1.0R rail are untouched).

    Fail-open: any read/compute error returns ``(gross_pnl_usd, gross_pnl_r)`` so
    the already-committed close still folds a value (best-effort learning).
    """
    try:
        (
            entry_fee, entry_slip, exit_fee, exit_slip, size_usd, r_budget_usd,
        ) = _read_cost_inputs(conn, trade)
        if r_budget_usd is None:
            # Unknown venue (no configured R_budget): skip the cost-in-R
            # adjustment (net==gross) rather than divide by zero. WARN (not
            # silent) per the no-garbage mandate.
            logger.warning(
                "[close/net] %s:%s no R_budget for venue — "
                "cost-in-R adjustment skipped (net==gross)",
                trade.venue, trade.symbol,
            )
        return cost_adjusted_pnl_r(
            gross_pnl_usd=gross_pnl_usd, gross_pnl_r=gross_pnl_r, size_usd=size_usd,
            atr_usd=r_budget_usd, venue=trade.venue,
            entry_fee_usd=entry_fee, exit_fee_usd=exit_fee,
            entry_slippage_bps=entry_slip, exit_slippage_bps=exit_slip,
        )
    except Exception as exc:  # noqa: BLE001 — measure-only; fail-open to gross
        logger.warning(
            "[close/net] %s:%s real-fee net compute failed — gross fallback: %r",
            trade.venue, trade.symbol, exc,
        )
        return gross_pnl_usd, gross_pnl_r


def _safe_update_posterior(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r_net: float, pnl_r_gross: float, now_ts: int,
) -> None:
    """Edge-validation Phase 1 — fold the (pre-computed) cost-adjusted R into the
    bucket posterior. ``pnl_r_net`` is the SHARED real-fee net the close path
    computed once via :func:`compute_net_pnl_r` (no second DB read / recompute
    here — the cell matrix, learners, posterior + ``won`` all fold the identical
    value). ``pnl_r_gross`` is logged for the gross-vs-net 거동 record. Fail-open
    (``logger.warning``): a posterior failure must never abort an already-committed
    close. This table is never read by sizing.
    """
    try:
        maybe_update_posterior(
            conn, exchange=trade.venue, strategy=trade.strategy_id,
            ticker=trade.symbol, regime=regime, pnl_r_net=pnl_r_net, now_ts=now_ts,
        )
        # parent2-seed writer (audit code_review_2026-06-24): the
        # ``strategy_regime_prior`` reader (``_load_parent_prior`` — the
        # hierarchical seed for a NEW exchange×strategy×ticker×regime cell) had no
        # runtime writer, so every new cell seeded from the flat default. Charge it
        # here with the SAME cost-adjusted ``pnl_r_net`` the child cell folds (unit-
        # consistent: the parent expectancy seeds cost-adjusted children). Learning
        # seed only — never read by sizing (9-stack intact).
        maybe_update_strategy_regime_prior(
            conn, strategy=trade.strategy_id, regime=regime,
            pnl_r=pnl_r_net, now_ts=now_ts,
        )
        # Posterior fold (INFO): the cost-adjusted R observation folded into the
        # NIG bucket (exchange×strategy×ticker×regime) + the strategy×regime parent2
        # prior at close — the edge-validation 거동 record. gross pnl_r vs net
        # (after fees+slippage). Neither table is read by sizing; log only.
        logger.info(
            "[edge-validation] posterior+prior %s:%s strategy=%s regime=%s "
            "pnl_r_gross=%.3f pnl_r_net=%.3f",
            trade.venue, trade.symbol, trade.strategy_id, regime,
            pnl_r_gross, pnl_r_net,
        )
    except Exception as exc:  # noqa: BLE001 — measure-only side effect, fail-open
        logger.warning(
            "[edge-validation] posterior/prior update failed %s:%s: %r",
            trade.venue, trade.symbol, exc,
        )


async def _safe_run_g8(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
    gpt_client: Any | None = None, phase: str = "P0",
) -> None:
    g8_ctx = GateContext(
        run_id=uuid.uuid4().hex, signal_id=trade.signal_id,
        position_id=trade.position_id or f"pos_{trade.signal_id[:10]}",
        gate_id=GATE_POST_TRADE_REFLECTOR,
        venue=trade.venue, symbol=trade.symbol,
        strategy_id=trade.strategy_id,
        payload={
            "closed_trade": {
                "trade_id": trade.signal_id,
                "strategy_id": trade.strategy_id,
                "regime": regime, "session": "asia",
                "pnl_r": pnl_r, "won": won,
            },
            "closed_trade_count": len(state.closed_trades),
        },
        started_ts=now_ts, state=SignalLifecycle.CLOSED,
        # Gate architecture Phase 0: per-stream seam (read-but-no-decision in P0).
        stream_profile=resolve_stream_profile(trade.venue),
    )
    # codex 2026-05-07 P1.4 fix: phase-aware G8 dispatch so the production
    # paper harness can exercise the GPT P1 lesson branch (was hardcoded
    # ``client=None``, which silently kept G8 on the Python template even
    # when phase=="P1"). ADR-004 §Phase invariant honoured by passing
    # ``GPT_P0_MODEL`` (unused, client=None) at P0 vs. ``GPT_P1_MODEL`` +
    # gpt_client at P1.
    if phase == "P1" and gpt_client is not None:
        client_arg: Any = gpt_client
        model_arg = GPT_P1_MODEL
    else:
        client_arg = None
        model_arg = GPT_P0_MODEL
    try:
        g8_result = await post_trade_reflector_gate(
            g8_ctx, client=client_arg, conn=conn, model=model_arg,
        )
        log_gate_event(conn, g8_ctx, g8_result)
        state.g8_runs += 1
    except Exception as exc:  # noqa: BLE001 — G8 must not drop a closed trade
        logger.error("[G8] reflector raised: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="g8_reflector", exc=exc, now_ts=now_ts, state=state,
        )
