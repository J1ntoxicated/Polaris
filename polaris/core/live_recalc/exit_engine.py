"""Layer 6 — precise-exit engine (#26 EXPECTANCY, not a defensive throttle).

Jin's #1 loss-defense: 손실방어 = 정밀 엑싯 (adaptive stop / timing), NOT size
reduction and NOT entry blocking. This module is pure per-position exit math —
it lets winners run (ATR-anchored trailing stop + MFE-driven harvest FSM) and
cuts dead losers (round-trip break-even + stale-loser timeout). It never:

* reduces position size (T4 sizing chain is untouched / OUTSIDE this module),
* blocks or vetoes an entry (entry-side ``flow_not_block`` unchanged),
* adds a P&L / strategy / bot halt (per-position close decisions only).

The G6 hard ``stop_hit`` rail (pnl_r <= -1.0R) is the catastrophic backstop and
stays in the orchestrator — these precise exits ADD on top of it.

Pure + unit-testable: no I/O. The tick loop reads tracked state from the
``positions`` row, calls these helpers, and persists the returned state back.

Trading parameters (CONSERVATIVE defaults adopted from the auto_invasion
``exit_fsm`` reference; env-overridable; FLAGGED as pending /debate calibration):

* ``EXIT_ATR_TRAIL_MULT`` (POLARIS_EXIT_ATR_TRAIL_MULT, default 2.0) — trailing
  stop distance in ATR units below the peak (long) / above the trough (short).
* ``EXIT_FSM_TOUCH_R`` (POLARIS_EXIT_FSM_TOUCH_R, default 0.5) — MFE in R to
  advance OPEN -> TOUCHED.
* ``EXIT_FSM_PROTECT_R`` (POLARIS_EXIT_FSM_PROTECT_R, default 1.0) — MFE in R to
  advance -> PROTECTED.
* ``EXIT_FSM_HARVEST_R`` (POLARIS_EXIT_FSM_HARVEST_R, default 2.0) — MFE in R to
  advance -> HARVEST.
* ``EXIT_HARVEST_TRAIL_MULT`` (POLARIS_EXIT_HARVEST_TRAIL_MULT, default 1.0) —
  tighter ATR trail once in HARVEST (locks more of a big winner; still only
  ratchets toward profit, never loosens).
* ``EXIT_LOSER_TIMEOUT_SEC`` (POLARIS_EXIT_LOSER_TIMEOUT_SEC, default 900) —
  age in seconds after which a still-OPEN losing position (never touched
  profit) is closed.
* ``EXIT_LOSER_TIMEOUT_EXT_MULT`` (POLARIS_EXIT_LOSER_TIMEOUT_EXT_MULT,
  default 2.0) — peak-extension: a position that once touched profit gets the
  timeout multiplied by this before a stale-loser close fires.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

# --- Exit-state FSM labels (TEXT in positions.exit_state) ------------------
EXIT_STATE_OPEN: Final[str] = "open"
EXIT_STATE_TOUCHED: Final[str] = "touched"
EXIT_STATE_PROTECTED: Final[str] = "protected"
EXIT_STATE_HARVEST: Final[str] = "harvest"

# FSM order so we never regress a state (max-MFE is monotone).
_STATE_RANK: Final[dict[str, int]] = {
    EXIT_STATE_OPEN: 0,
    EXIT_STATE_TOUCHED: 1,
    EXIT_STATE_PROTECTED: 2,
    EXIT_STATE_HARVEST: 3,
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# --- Trading parameters (CONSERVATIVE defaults — FLAG pending /debate) ------
EXIT_ATR_TRAIL_MULT: Final[float] = _env_float("POLARIS_EXIT_ATR_TRAIL_MULT", 2.0)
EXIT_HARVEST_TRAIL_MULT: Final[float] = _env_float(
    "POLARIS_EXIT_HARVEST_TRAIL_MULT", 1.0
)
EXIT_FSM_TOUCH_R: Final[float] = _env_float("POLARIS_EXIT_FSM_TOUCH_R", 0.5)
EXIT_FSM_PROTECT_R: Final[float] = _env_float("POLARIS_EXIT_FSM_PROTECT_R", 1.0)
EXIT_FSM_HARVEST_R: Final[float] = _env_float("POLARIS_EXIT_FSM_HARVEST_R", 2.0)
EXIT_LOSER_TIMEOUT_SEC: Final[float] = _env_float(
    "POLARIS_EXIT_LOSER_TIMEOUT_SEC", 900.0
)
EXIT_LOSER_TIMEOUT_EXT_MULT: Final[float] = _env_float(
    "POLARIS_EXIT_LOSER_TIMEOUT_EXT_MULT", 2.0
)

_ATR_USD_FLOOR: Final[float] = 1e-6


@dataclass(slots=True)
class ExitState:
    """Per-position precise-exit state (mirrors the persisted columns).

    ``peak_price`` / ``trough_price`` are the running price extremes; ``stop_price``
    is the ratcheting ATR-trailing stop; ``exit_state`` is the FSM label.
    ``mfe_r`` / ``mae_r`` are the excursion telemetry in R units.
    """

    peak_price: float
    trough_price: float
    stop_price: float | None
    exit_state: str
    mfe_r: float = 0.0
    mae_r: float = 0.0


@dataclass(slots=True)
class ExitDecision:
    """Outcome of one precise-exit evaluation for a position."""

    state: ExitState
    close: bool
    close_reason: str | None = None


def _atr_one_usd(*, entry_price: float, atr_pct: float) -> float:
    """One-ATR distance in price terms (floored finite)."""
    return max(entry_price * max(atr_pct, 0.0), _ATR_USD_FLOOR)


def _atr_r_usd(*, entry_price: float, atr_pct: float) -> float:
    """R-unit denominator in price terms — matches the realised-PnL path
    (``entry_price * atr_pct * 2.0`` with the same 2x stop convention as
    ``compute_unrealized_pnl_r`` / ``real_pnl_r_from_fills``)."""
    return max(entry_price * max(atr_pct, 0.0) * 2.0, _ATR_USD_FLOOR)


def init_exit_state(*, entry_price: float, side: str) -> ExitState:
    """Seed extremes at entry; no stop yet (set on first ratchet)."""
    return ExitState(
        peak_price=entry_price,
        trough_price=entry_price,
        stop_price=None,
        exit_state=EXIT_STATE_OPEN,
    )


def _next_fsm_state(current: str, mfe_r: float) -> str:
    """Advance the FSM by MFE (monotone — never regress)."""
    if mfe_r >= EXIT_FSM_HARVEST_R:
        target = EXIT_STATE_HARVEST
    elif mfe_r >= EXIT_FSM_PROTECT_R:
        target = EXIT_STATE_PROTECTED
    elif mfe_r >= EXIT_FSM_TOUCH_R:
        target = EXIT_STATE_TOUCHED
    else:
        target = EXIT_STATE_OPEN
    cur_rank = _STATE_RANK.get(current, 0)
    tgt_rank = _STATE_RANK.get(target, 0)
    return target if tgt_rank > cur_rank else current


def _trailing_stop(
    *, side: str, anchor_extreme: float, atr_one: float, trail_mult: float,
    prev_stop: float | None,
) -> float:
    """ATR-anchored stop that only ratchets TOWARD profit (never loosens).

    Long: ``peak - trail_mult*ATR``, clamped up to ``max(prev_stop, ...)``.
    Short: ``trough + trail_mult*ATR``, clamped down to ``min(prev_stop, ...)``.
    """
    if side == "long":
        candidate = anchor_extreme - trail_mult * atr_one
        return candidate if prev_stop is None else max(prev_stop, candidate)
    candidate = anchor_extreme + trail_mult * atr_one
    return candidate if prev_stop is None else min(prev_stop, candidate)


def evaluate_exit(
    *,
    prev: ExitState,
    side: str,
    entry_price: float,
    last_price: float,
    atr_pct: float,
    pnl_r: float,
    held_seconds: int,
    loser_timeout_sec: float | None = None,
) -> ExitDecision:
    """Advance excursion + stop + FSM for one position; decide close-or-hold.

    Returns the NEW ``ExitState`` (to persist) and whether a precise exit
    fired. This NEVER changes size and NEVER blocks an entry — close-or-hold of
    THIS position only. The G6 -1.0R hard stop_hit rail stays in the caller.

    ``loser_timeout_sec`` (Component B): the stale-loser timeout floor for THIS
    position, scaled to its strategy timeframe by the caller (a 1H thesis is not
    force-closed at the flat 900s). ``None`` keeps the flat
    ``EXIT_LOSER_TIMEOUT_SEC`` default (fast strategies stay short). This only
    moves the TIMEOUT horizon — the ATR-trailing stop and the protected-BEP
    exit (the precise exits) are untouched.
    """
    # 1. Update price extremes (running peak / trough over the position life).
    peak = max(prev.peak_price, last_price)
    trough = min(prev.trough_price, last_price)

    # 2. Excursion in R units (favourable >= 0, adverse <= 0).
    atr_r = _atr_r_usd(entry_price=entry_price, atr_pct=atr_pct)
    if side == "long":
        mfe_r = max(0.0, (peak - entry_price) / atr_r)
        mae_r = min(0.0, (trough - entry_price) / atr_r)
    else:
        mfe_r = max(0.0, (entry_price - trough) / atr_r)
        mae_r = min(0.0, (entry_price - peak) / atr_r)

    # 3. FSM advance by MFE (monotone — never regress).
    new_state = _next_fsm_state(prev.exit_state, mfe_r)

    # 4. ATR-trailing stop. Tighter trail once in HARVEST (locks the winner;
    #    still only ratchets toward profit — never loosens).
    atr_one = _atr_one_usd(entry_price=entry_price, atr_pct=atr_pct)
    trail_mult = (
        EXIT_HARVEST_TRAIL_MULT
        if new_state == EXIT_STATE_HARVEST
        else EXIT_ATR_TRAIL_MULT
    )
    anchor = peak if side == "long" else trough
    stop_price = _trailing_stop(
        side=side, anchor_extreme=anchor, atr_one=atr_one,
        trail_mult=trail_mult, prev_stop=prev.stop_price,
    )

    state = ExitState(
        peak_price=peak, trough_price=trough, stop_price=stop_price,
        exit_state=new_state, mfe_r=mfe_r, mae_r=mae_r,
    )

    # 5. Close decisions (precise exits — per-position only).
    #    (a) PROTECTED break-even FIRST: a round-tripped winner that gave it all
    #        back closes at break-even — the tighter, more precise exit takes
    #        priority over the wider ATR trail (don't wait for the trail when a
    #        once-protected winner has already round-tripped negative).
    if new_state in (EXIT_STATE_PROTECTED, EXIT_STATE_HARVEST) and pnl_r < 0.0:
        return ExitDecision(state=state, close=True, close_reason="protected_bep")

    #    (b) ATR-trailing stop touched (let-winners-run trail).
    stop_touched = (
        (side == "long" and last_price <= stop_price)
        or (side == "short" and last_price >= stop_price)
    )
    if stop_touched:
        return ExitDecision(state=state, close=True, close_reason="atr_trail_stop")

    #    (c) Stale-loser timeout — a currently-losing position past its timeout
    #        is closed. Peak-extension: a position that ONCE touched profit
    #        earned more rope, so its timeout is multiplied by
    #        EXIT_LOSER_TIMEOUT_EXT_MULT before the close fires (a never-profit
    #        loser still times out at the BASE EXIT_LOSER_TIMEOUT_SEC). Still a
    #        per-position exit — no size change, no entry block, no halt.
    touched_profit = _STATE_RANK.get(new_state, 0) > _STATE_RANK[EXIT_STATE_OPEN]
    timeout = (
        EXIT_LOSER_TIMEOUT_SEC
        if loser_timeout_sec is None
        else loser_timeout_sec
    )
    if touched_profit:
        timeout *= EXIT_LOSER_TIMEOUT_EXT_MULT
    if pnl_r < 0.0 and held_seconds > timeout:
        return ExitDecision(state=state, close=True, close_reason="loser_timeout")

    return ExitDecision(state=state, close=False, close_reason=None)


__all__ = [
    "EXIT_ATR_TRAIL_MULT",
    "EXIT_FSM_HARVEST_R",
    "EXIT_FSM_PROTECT_R",
    "EXIT_FSM_TOUCH_R",
    "EXIT_HARVEST_TRAIL_MULT",
    "EXIT_LOSER_TIMEOUT_EXT_MULT",
    "EXIT_LOSER_TIMEOUT_SEC",
    "EXIT_STATE_HARVEST",
    "EXIT_STATE_OPEN",
    "EXIT_STATE_PROTECTED",
    "EXIT_STATE_TOUCHED",
    "ExitDecision",
    "ExitState",
    "evaluate_exit",
    "init_exit_state",
]
