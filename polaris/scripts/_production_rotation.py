"""Capital-rotation WIRE — integrate the pure evaluator into the live tick.

DEMO/PAPER ONLY — virtual funds. This module is the I/O layer between the live
production tick and the pure :mod:`polaris.core.rotation` evaluator. It owns the
five integration pieces the evaluator cannot (it is pure / no I/O):

1. **capital-kill propagation** — :func:`register_rotation_candidate` pushes a
   capital-blocked NEW signal (entry_sizer ``sizing_zero`` on a binding cap, or
   OKX ``insufficient_balance``/51008) onto ``state.rotation_candidates`` with
   its conviction-derived ``proposed_risk_pct`` (the capital SCALE only).
2. :func:`evaluate_capital_rotation` — per venue: build held forward scores +
   the candidate E$ from the learner posterior, call the pure
   :func:`polaris.core.rotation.choose_rotation`, and on a fire CLOSE the single
   weakest victim, then GUARANTEE a close->settle->reopen ordering: it does NOT
   ``reserve_and_submit`` the winner inline this tick — the freed OKX availBal
   only settles after the close sell, so the winner is left in
   ``state.rotation_candidates`` for the NEXT tick's normal pipeline pass (which
   now sizes successfully because capital freed). MAX_PER_TICK = 1.
3. **vacated-side anti-churn** — a post-close cooldown is registered on the
   victim (venue, symbol, strategy); :func:`rotation_vacated_cooldown_active`
   closes the reentry backdoor for that name (NO strong-signal escape hatch).
4. :func:`read_cell_posterior` — the ``learner_posterior`` reader (mu / marginal
   -t scale / alpha / n), with a ``cell_matrix_p0.avg_pnl_r`` fallback for a
   held victim's forward score.
5. **telemetry** — every fire appends a record to ``state.rotations``.

rotation = capital EFFICIENCY (redeploy finite capital to a higher-EV pending
entry → net deployed capital goes UP), NOT a defensive throttle: no P&L halt,
no blanket loss limiter, no block filter; winners (``exit_state`` protected /
harvest) are NEVER victims; it adds NO T4 sizing multiplier (9-stack ban
intact). loser_timeout coordination: a position the precise-exit FSM already
closed this tick has ``status='closed'`` and is invisible to the held loader, so
rotation can never double-close it (it defers to the exit engine).
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

from polaris.core.rotation import (
    ROTATION_CREDIBLE_LEVEL,
    ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD,
    RotationCandidate,
    choose_rotation,
    expected_dollar_edge_held,
    expected_dollar_edge_new,
    forward_score,
    rotation_cost_usd,
)
from polaris.scripts._production_rotation_reader import (
    build_rotation_candidate,
    load_held_positions,
    read_cell_posterior,
)
from polaris.strategies import RawSignal

if TYPE_CHECKING:
    from polaris.scripts._production_state import ProdLoopState

logger = logging.getLogger(__name__)

__all__ = [
    "ROTATION_VACATED_COOLDOWN_SEC",
    "build_rotation_candidate",
    "evaluate_capital_rotation",
    "read_cell_posterior",
    "register_rotation_candidate",
    "rotation_vacated_cooldown_active",
]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if math.isfinite(val) and val > 0.0 else default


# Vacated-side post-close cooldown (anti-churn). A just-rotated victim's
# (venue, symbol, strategy) cannot be re-entered for this window — NOT even by a
# strong signal (the reentry backdoor is closed). Defaults to the MIN_HOLD floor
# so the freed capital is not immediately re-spent on the name we just exited.
ROTATION_VACATED_COOLDOWN_SEC: Final[float] = _env_float(
    "POLARIS_ROTATION_VACATED_COOLDOWN_SEC", 300.0
)


# ---------------------------------------------------------------------------
# 0. display-only telemetry persistence (read-only dashboard observability).
# ---------------------------------------------------------------------------


def _persist_rotation_telemetry(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
    venue: str,
    victim_symbol: str,
    victim_strategy: str,
    winner_symbol: str,
    e_new: float,
    e_held: float,
    margin: float,
    cost: float,
) -> None:
    """Append one rotation-fire row to ``loop_rotation_events`` (display-only).

    Pure observability for the read-only dashboard — the live rotation decision
    is already done (``state.rotations`` holds the authoritative in-memory
    record). Best-effort: any sqlite error (e.g. older schema lacking the table)
    is swallowed so telemetry can never disturb the loop. Touches ONLY this
    isolated table; never positions/fills/sizing/gating.
    """
    try:
        conn.execute(
            "INSERT INTO loop_rotation_events (ts, venue, victim_symbol, "
            "victim_strategy, winner_symbol, e_new, e_held, margin, cost) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                int(now_ts), str(venue), str(victim_symbol),
                str(victim_strategy), str(winner_symbol), float(e_new),
                float(e_held), float(margin), float(cost),
            ),
        )
    except sqlite3.Error:
        logger.debug("[rotation] telemetry persist skipped (best-effort)")


# ---------------------------------------------------------------------------
# 1. capital-kill propagation.
# ---------------------------------------------------------------------------


def register_rotation_candidate(
    state: ProdLoopState,
    *,
    sig: RawSignal,
    proposed_risk_pct: float,
    venue: str,
    binding_reason: str,
) -> None:
    """Push a capital-blocked NEW signal onto ``state.rotation_candidates``.

    Called at the two trigger seams: entry_sizer ``sizing_zero`` (on a binding
    cap) and OKX ``insufficient_balance``/51008. ``proposed_risk_pct`` is the
    conviction-derived capital SCALE the sizer asked for (it survives even when
    the cap clipped ``final_risk_pct`` to 0). A non-positive scale is not a real
    pending deploy → skipped (rotation only fires for a concrete pending entry,
    keeping net deploy UP). Idempotent per (venue, symbol, strategy) so a
    re-blocked signal does not stack duplicates within a tick.
    """
    if not math.isfinite(proposed_risk_pct) or proposed_risk_pct <= 0.0:
        return
    key = (venue, sig.symbol, sig.strategy_id)
    for existing in state.rotation_candidates:
        ex_sig = existing["signal"]
        if (existing["venue"], ex_sig.symbol, ex_sig.strategy_id) == key:
            return
    state.rotation_candidates.append(
        {
            "signal": sig,
            "proposed_risk_pct": float(proposed_risk_pct),
            "venue": venue,
            "binding_reason": binding_reason,
        }
    )


# ---------------------------------------------------------------------------
# 3. vacated-side anti-churn cooldown.
# ---------------------------------------------------------------------------


def rotation_vacated_cooldown_active(
    state: ProdLoopState,
    *,
    venue: str,
    symbol: str,
    strategy: str,
    now_ts: int,
) -> bool:
    """True when (venue, symbol, strategy) was JUST rotated out and is cooling.

    There is deliberately NO strength/exempt argument: the vacated cooldown has
    no strong-signal escape hatch (cross-verify item 4 — the reentry backdoor is
    closed for the just-exited name). Returns ``False`` once the window expires.
    """
    until = state.rotation_vacated_cooldowns.get((venue, symbol, strategy))
    if until is None:
        return False
    return now_ts < until


# ---------------------------------------------------------------------------
# 2 + 3 + 5. evaluate_capital_rotation — the live hook.
# ---------------------------------------------------------------------------


async def evaluate_capital_rotation(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    now_ts: int,
    close_specific: Callable[..., Any],
    lookup_regime: Callable[[sqlite3.Connection, str, str], str],
    equity: float,
    reserve_and_submit: Callable[..., Any] | None = None,
    improvement_margin_usd: float = ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD,
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    capital_session: Any = None,
    gpt_client: Any = None,
    phase: str = "P0",
) -> int:
    """Per-venue rotation pass. Returns the number of rotations fired (0 or 1).

    For each venue with at least one capital-blocked candidate, builds the held
    forward scores + candidate E$ and asks the pure
    :func:`polaris.core.rotation.choose_rotation` for a (victim, winner) swap.
    On a fire it CLOSES the victim via ``close_specific`` then:

      * registers the vacated-side cooldown on the victim (anti-churn),
      * records telemetry on ``state.rotations``,
      * does NOT ``reserve_and_submit`` the winner this tick (close->settle->
        reopen ordering — the freed OKX availBal only settles after the close
        sell). ``reserve_and_submit`` is accepted for signature symmetry but is
        intentionally NOT invoked here; the winner is re-proposed by its strategy
        on the NEXT tick once capital is free.

    ``state.rotation_candidates`` is CLEARED at the end of the pass — it is
    per-tick scratch, so a stale block does not rotate on a later tick after the
    capital picture changed. MAX_PER_TICK = 1: at most one swap per call
    (structural cascade guard). A failed close (``close_specific`` returns False
    — venue reject / not in state) records NO telemetry and NO cooldown
    (rotation did not happen).
    """
    if not state.rotation_candidates:
        return 0
    venues = sorted({c["venue"] for c in state.rotation_candidates})
    fired_count = 0
    for venue in venues:
        fired = await _rotate_one_venue(
            conn, state=state, venue=venue, now_ts=now_ts,
            close_specific=close_specific, lookup_regime=lookup_regime,
            equity=equity, improvement_margin_usd=improvement_margin_usd,
            real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
            capital_session=capital_session, gpt_client=gpt_client, phase=phase,
        )
        if fired:
            fired_count = 1  # MAX_PER_TICK = 1 — stop after the first swap.
            break
    state.rotation_candidates.clear()
    return fired_count


async def _rotate_one_venue(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    venue: str,
    now_ts: int,
    close_specific: Callable[..., Any],
    lookup_regime: Callable[[sqlite3.Connection, str, str], str],
    equity: float,
    improvement_margin_usd: float,
    real_roundtrip: bool,
    okx_adapter: Any,
    capital_session: Any,
    gpt_client: Any,
    phase: str,
) -> bool:
    """Evaluate + (maybe) fire ONE rotation on a single venue. True if fired."""
    candidates: list[RotationCandidate] = []
    for entry in state.rotation_candidates:
        if entry["venue"] != venue:
            continue
        sig: RawSignal = entry["signal"]
        regime = lookup_regime(conn, venue, sig.symbol)
        cand = build_rotation_candidate(
            conn, sig=sig, venue=venue, symbol=sig.symbol, regime=regime,
            proposed_risk_pct=float(entry["proposed_risk_pct"]), equity=equity,
        )
        if cand is not None:
            candidates.append(cand)
    if not candidates:
        return False

    held, id_map = load_held_positions(
        conn, venue=venue, now_ts=now_ts, lookup_regime=lookup_regime, equity=equity
    )
    if not held:
        return False

    reason_out: dict[str, object] = {}
    chosen = choose_rotation(
        blocked_candidates=candidates,
        held_positions=held,
        equity=equity,
        venue=venue,
        improvement_margin_usd=improvement_margin_usd,
        reason_out=reason_out,
    )
    if chosen is None:
        # Observability: there ARE capital-blocked candidates + held on this venue
        # but no swap fired — surface WHY so a silent capital-blocked stall (the
        # OKX-availBal case that needed a forensic trace) is visible in the bot
        # log. logger.info (not debug) is safe here: this point is only reached
        # when candidates+held both exist, so it is not per-tick spam. Pure
        # logging — the no-fire decision above is unchanged.
        logger.info(
            "[rotation] venue=%s candidates=%d but no fire: reason=%s "
            "(weakest_victim=%s best_cand_edge=%s e_held=%s e_new=%s "
            "margin=%s cost=%s)",
            venue, len(candidates), reason_out.get("reason", "unknown"),
            reason_out.get("weakest_victim", "-"),
            reason_out.get("best_cand_edge", "-"),
            reason_out.get("e_held", "-"), reason_out.get("e_new", "-"),
            reason_out.get("margin", "-"), reason_out.get("cost", "-"),
        )
        return False
    victim, winner = chosen
    meta = id_map.get(victim.position_id, {})
    v_symbol = meta.get("symbol", "")
    v_strategy = meta.get("strategy", "")

    # Recompute the two dollar edges for telemetry (same formulas the evaluator
    # used internally — both in $, comparable).
    assert victim.fwd_R_held is not None  # select_weakest guarantees finite R
    e_held = expected_dollar_edge_held(
        fwd_R_held=victim.fwd_R_held,
        open_risk_pct_held=victim.open_risk_pct_held,
        equity=equity,
    )
    winner_edge = forward_score(
        winner.cell_stats, lower_credible_bound=ROTATION_CREDIBLE_LEVEL
    )
    e_new = (
        expected_dollar_edge_new(
            fwd_mu_new=winner_edge,
            proposed_risk_pct=winner.proposed_risk_pct,
            equity=equity,
        )
        if winner_edge is not None
        else 0.0
    )
    cost = rotation_cost_usd(
        close_size_usd=victim.open_risk_pct_held * equity,
        open_size_usd=winner.proposed_size_usd,
        venue=venue,
    )

    # CLOSE the victim FIRST (capital is freed by the close sell; the winner is
    # NOT submitted this tick — settle ordering). A failed close means rotation
    # did not happen: no telemetry, no cooldown.
    # P2-12: name the trigger so the lineage exit_reason isn't the 'exit'
    # fallback (observability only — no close-path behaviour change).
    closed = await close_specific(
        conn, state=state, position_id=victim.position_id, now_ts=now_ts,
        lookup_regime=lookup_regime, gpt_client=gpt_client, phase=phase,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, close_reason="rotation",
    )
    if not closed:
        logger.info(
            "[rotation] victim %s close did not persist — rotation deferred",
            victim.position_id,
        )
        return False

    # Vacated-side anti-churn: close the reentry backdoor for the just-exited
    # name (no strong-signal escape hatch).
    if v_symbol and v_strategy:
        state.rotation_vacated_cooldowns[(venue, v_symbol, v_strategy)] = (
            now_ts + int(ROTATION_VACATED_COOLDOWN_SEC)
        )

    same_symbol_reopen = sum(
        1 for rec in state.rotations if rec.get("winner_symbol") == winner.candidate_id
    )
    hour_ago = now_ts - 3600
    rotations_this_hour = (
        sum(1 for rec in state.rotations if int(rec.get("ts", 0)) >= hour_ago) + 1
    )
    state.rotations.append(
        {
            "ts": now_ts,
            "venue": venue,
            "victim_id": victim.position_id,
            "victim_symbol": v_symbol,
            "victim_strategy": v_strategy,
            "victim_fwd_R": victim.fwd_R_held,
            "victim_pnl_r": victim.pnl_r,
            "winner_symbol": winner.candidate_id,
            "e_new": e_new,
            "e_held": e_held,
            "margin": improvement_margin_usd,
            "cost": cost,
            "same_symbol_reopen_count": same_symbol_reopen,
            "rotations_this_hour": rotations_this_hour,
        }
    )
    # Display-only telemetry persistence (follow-up #12): mirror the fire into
    # the read-only dashboard's loop_rotation_events table so the snapshot can
    # surface a count + last-rotation detail (state.rotations is in-process and
    # unreachable from the read-only dashboard). Best-effort: a write failure /
    # missing table NEVER affects the rotation (it already happened). Touches
    # ONLY this isolated telemetry table — no positions/fills/sizing/gating.
    _persist_rotation_telemetry(
        conn, now_ts=now_ts, venue=venue, victim_symbol=v_symbol,
        victim_strategy=v_strategy, winner_symbol=winner.candidate_id,
        e_new=e_new, e_held=e_held, margin=improvement_margin_usd, cost=cost,
    )
    logger.info(
        "[rotation] FIRED venue=%s victim=%s(%s pnl_r=%.3f fwd_R=%.3f) -> "
        "winner=%s E$_new=%.2f E$_held=%.2f margin=%.2f cost=%.2f "
        "(winner enqueued for next-tick reopen, NOT same-tick)",
        venue, victim.position_id, v_symbol, victim.pnl_r, victim.fwd_R_held,
        winner.candidate_id, e_new, e_held, improvement_margin_usd, cost,
    )
    return True
