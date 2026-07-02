"""Production per-signal pipeline helpers — split out of ``_production_run_signal``.

Move-only extraction for the ≤500-LOC budget. The per-signal driver
``run_pipeline_for_signal`` stays in ``_production_run_signal`` (its module-global
gate/orchestrator lookups are monkeypatch seams); the stateless helpers it calls
live here and are re-exported so existing import paths keep working.

Behavior 0 throughout: order-mode routing, the routing-coherence guard, the
universe-state reads, and the entry-admission shadow are byte-identical to the
pre-split module.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from polaris.core.live_recalc.exit_types import Bucket
from polaris.core.pipeline.gate_state import GateDecision
from polaris.core.streams import (
    StreamConfig,
    asset_class_allowed_for_venue,
)
from polaris.scripts.exit_strategy_config import _bucket_for_strategy
from polaris.strategies import STRATEGY_REGISTRY, RawSignal

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)


def _bar_order_mode(strategy_id: str) -> tuple[bool, bool]:
    """Per-family OKX order mode for a BAR-pipeline entry → ``(prefer_maker,
    marketable_limit)`` ([[ab_letrun_maker_2026-06-24]] Build B).

    A breakout/TREND bar strategy CROSSES the spread (marketable-limit, a
    cap_bps-capped taker-like fill) — the price runs while a passive order waits,
    so resting post-only would non-fill. A reversion/range bar strategy rests
    PASSIVELY at the touch (post-only) — you are fading an exhausted move, the
    touch is the natural fill. An UNREGISTERED id keeps the strength-gated default
    (both False) so the entry is never blocked. Every mode still falls back to a
    plain market order on no-fill/reject/timeout (flow_not_block).

    NOTE: the OKX entry path is the only one wired to these flags today; Capital
    bar entries stay market-default (working-order fill-rate unverified —
    measurement-shadow first per the design). The tuple is a no-op for them.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return (False, False)
    if _bucket_for_strategy(strategy_id) is Bucket.TREND:
        return (False, True)   # breakout/momentum → cross (marketable-limit)
    return (True, False)       # reversion/range → rest passively (post-only)


def _bar_maker_no_fill(strategy_id: str) -> str:
    """OKX post-only no-fill mode for ``strategy_id`` → ``"cancel"`` | ``"market"``.

    A strategy whose metadata sets ``maker_no_fill_cancel`` (the #77 weekend
    thin-book maker) resolves a post-only no-fill as a CANCEL/skip — its edge IS
    the passive deep-bid fill, so a miss is 0 realised cost, NOT a forced taker.
    Every other registered strategy — and any UNREGISTERED id — resolves to
    ``"market"`` (the legacy taker fallback, byte-identical so no existing entry
    is ever blocked; flow_not_block).
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is not None and cls.metadata.maker_no_fill_cancel:
        return "cancel"
    return "market"


def _is_shadow_first(strategy_id: str) -> bool:
    """True when ``strategy_id`` is a shadow_first strategy (SUPPRESS the order).

    A strategy whose metadata sets ``shadow_first`` (the two weekend OKX makers,
    [[weekend_maker_honest_rerun_2026-06-28]]) has its REAL venue order SUPPRESSED —
    the signal still FLOWS the full pipeline (G1-G5 + sizing) and the would-be entry
    is RECORDED, but no order is submitted (zero capital at risk on a thin sample).
    Every other registered strategy — and any UNREGISTERED id — returns ``False``
    (the live order path, byte-identical; flow_not_block — the order is deferred,
    never the signal).
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    return cls is not None and cls.metadata.shadow_first


def _maybe_register_rotation_candidate(
    state: ProdLoopState,
    *,
    results: list[Any],
    sig: RawSignal,
    venue: str,
) -> None:
    """Register a capital-blocked signal as a rotation candidate (trigger 1).

    Scans the gate results for an entry-sizer ``sizing_zero`` KILL on a binding
    cap and, if found, pushes the signal + its surfaced ``proposed_risk_pct``
    onto ``state.rotation_candidates`` via the rotation wire. Only a
    capital-block (binding cap) qualifies — a missing/zero ``proposed_risk_pct``
    or any other KILL reason is skipped (rotation only fires for a real pending
    deploy, keeping net deploy UP). Import is local to avoid a module-load cycle
    (``_production_rotation`` -> state -> tick -> run_signal).
    """
    from polaris.scripts._production_rotation import register_rotation_candidate

    for r in results:
        if r.decision != GateDecision.KILL:
            continue
        if r.payload.get("reason") != "sizing_zero":
            continue
        proposed = r.payload.get("proposed_risk_pct")
        if proposed is None:
            continue
        register_rotation_candidate(
            state, sig=sig, proposed_risk_pct=float(proposed), venue=venue,
            binding_reason=f"sizing_zero:{r.payload.get('binding_cap', '?')}",
        )
        return


def _assert_stream_asset_class_coherent(stream: StreamConfig, asset_class: str) -> bool:
    """True iff ``asset_class`` belongs on ``stream``'s venue (STEP 4 guard).

    Runtime regression catch for the STEP 2 routing correction: if a crypto tag
    leaks back into the Capital B-stream (e.g. a stale universe row or a future
    nav-tree change re-admits crypto CFDs), this returns ``False`` so the caller
    drops/flags the signal instead of sizing an off-venue (wrong-leverage)
    position. Delegates to the SSOT (``asset_class_allowed_for_venue``) — an
    unregistered venue stays permissive. This is a coherence/routing assertion,
    NOT a defensive throttle (a coherent signal is never touched).
    """
    return asset_class_allowed_for_venue(stream.venue, asset_class)


def _read_universe_state(
    conn: sqlite3.Connection, *, venue: str, symbol: str, now_ts: int,
) -> tuple[float, float]:
    """Return (spread_bps, listing_age_hours) from the universe row.

    Falls back to (5.0 bps, 365×24 h) when the row is missing — the smoke
    seed path may not have populated universe yet for cold-start runs.
    """
    row = conn.execute(
        "SELECT spread_bps, listing_ts FROM universe "
        "WHERE venue = ? AND symbol = ?",
        (venue, symbol),
    ).fetchone()
    if row is None:
        return (5.0, 24 * 365.0)
    spread_bps = float(row[0] or 5.0)
    listing_ts = int(row[1]) if row[1] is not None else (now_ts - 24 * 3600 * 365)
    listing_age_hours = max(0.0, (now_ts - listing_ts) / 3600.0)
    return (spread_bps, listing_age_hours)


def _log_entry_admission_shadow(
    shadow_conn: sqlite3.Connection | None,
    *,
    run_id: str,
    sig: RawSignal,
    venue: str,
    symbol: str,
    regime: str,
    cell_routing: dict[str, Any],
    entry_price: float,
    atr_pct: float,
) -> None:
    """Component C (SHADOW): compute the edge-first admission decision + LOG it.

    Behavior 0 — the decision is logged ONLY; the live pipeline admit/skip below
    is UNCHANGED (this call adds a single ``entry_admission_shadow`` row and
    returns). No-op when ``shadow_conn`` is None so the gated call is
    byte-identical to the legacy path (mirrors ``signal_validator._log_g3_shadow``).

    The cost is the REAL OKX round trip (``fees.real_fee_usd`` ×2 legs) expressed
    in ATR-R (``cost_usd / atr_usd``), where ``atr_usd = PNL_R_USD_DENOM *
    atr_pct * 2.0`` mirrors the close-path WHOLE-POSITION convention
    (``_production_close_effects.py`` FIX 1: ``atr_usd = size_usd * atr_pct *
    2.0``) — the SAME R unit as the cell's ``avg_pnl_r`` and the expected-move
    proxy (no $-basis mismatch). The fee leg AND the R-denominator both use the
    same representative $50 risk notional (``PNL_R_USD_DENOM``), so cost_r is
    independent of the symbol's per-unit price (a per-unit ``entry_price *
    atr_pct * 2.0`` basis blew a low-price symbol's cost_r up relative to a
    high-price symbol's at identical risk — GBPUSD 92.3R vs J225 0.0R observed).
    ``net_edge`` is never consulted. Cold cell (n_eff < CELL_POOL_MIN_N_EFF)
    ALWAYS admits.
    """
    if shadow_conn is None:
        return
    # Local imports keep this module free of an economics dependency unless the
    # shadow wire actually fires (and avoid any load-order surprise).
    from polaris.core.economics.entry_admission import (
        entry_admission_decision,
        expected_move_r_from_cell,
        real_round_trip_cost_r_from_usd,
    )
    from polaris.core.economics.entry_admission_shadow import log_entry_admission
    from polaris.core.economics.fees import real_fee_usd

    n_eff = float(cell_routing.get("n_eff", 0.0) or 0.0)
    avg_pnl_r = float(cell_routing.get("avg_pnl_r", 0.0) or 0.0)
    wins_eff = float(cell_routing.get("wins_eff", 0.0) or 0.0)
    expected_move_r = expected_move_r_from_cell(
        n_eff=n_eff, wins_eff=wins_eff, avg_pnl_r=avg_pnl_r,
    )
    # ATR-R basis: atr_usd mirrors the close-path WHOLE-POSITION convention
    # (_production_close_effects.py FIX 1: atr_usd = size_usd * atr_pct * 2.0),
    # NOT the per-unit entry_price * atr_pct * 2.0 (that basis is price-dependent
    # and blows up cost_r for low-price symbols — see the docstring above). Both
    # the fee leg and this R-denominator use the SAME representative $50 risk
    # notional (PNL_R_USD_DENOM) so cost_r is symbol-price-invariant. A degenerate
    # atr_usd <= 0 makes the cost helper fail-open to 0.0 (never manufactures a
    # would_suppress).
    from polaris.core.pipeline.payload_builder import PNL_R_USD_DENOM

    atr_usd = PNL_R_USD_DENOM * atr_pct * 2.0
    # One leg's real fee at the SAME representative $50 risk-unit notional; ×2
    # legs is applied inside real_round_trip_cost_r_from_usd, then divided by
    # atr_usd.
    real_leg_usd = real_fee_usd(venue, PNL_R_USD_DENOM)
    real_round_trip_cost_r = real_round_trip_cost_r_from_usd(
        real_fee_one_leg_usd=real_leg_usd,
        atr_usd=atr_usd,
    )
    decision = entry_admission_decision(
        cell_n_eff=n_eff,
        cell_avg_pnl_r=avg_pnl_r,
        regime=regime,
        expected_move_r=expected_move_r,
        real_round_trip_cost_r=real_round_trip_cost_r,
    )
    log_entry_admission(
        shadow_conn,
        run_id=run_id,
        signal_id=sig.signal_id,
        venue=venue,
        symbol=symbol,
        strategy_id=sig.strategy_id,
        regime=regime,
        cell_n_eff=n_eff,
        cell_avg_pnl_r=avg_pnl_r,
        expected_move_r=expected_move_r,
        real_round_trip_cost_r=real_round_trip_cost_r,
        decision=decision,
    )


def _strategy_recent_reject(
    conn: sqlite3.Connection, *, strategy_id: str, now_ts: int,
    window_sec: int = 6 * 3600,
) -> bool:
    """True if the strategy logged a reject/halt within the last 6h."""
    cutoff = now_ts - window_sec
    row = conn.execute(
        "SELECT 1 FROM strategy_fault_events "
        "WHERE strategy_id = ? AND fault_type IN ('reject', 'idempotency_conflict') "
        "AND event_ts >= ? LIMIT 1",
        (strategy_id, cutoff),
    ).fetchone()
    return row is not None
