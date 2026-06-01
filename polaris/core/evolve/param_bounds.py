"""P0a PARAM_BOUNDS — per-strategy ENTRY-SET-varying knob grids.

SSOT for the P0a edge-search grid. Keys are the lowercase CLASS-ATTRIBUTE
names introduced by the STEP-2 refactor (e.g. ``vol_z_threshold``), so
``make_variant`` can override them via dynamic subclassing.

ONLY knobs that change **which trades happen** (the trade SET) appear here —
the entry-trigger thresholds. Everything else is EXCLUDED:

  * LOCKED params (precomputed fixed-window indicators: every donchian /
    momentum / rsi / ma / adx WINDOW & PERIOD) — varying them is inert in
    replay (the field is precomputed) or belongs to the T4 / Layer-3 sizing
    chain (out of P0a scope, 9-stack untouched).
  * ``ttl_bars`` — provably INERT in the replay precise-exit FSM:
    ``evaluate_exit`` never reads it (it ticks on ATR-trail / protected-BEP /
    loser-timeout, driven by ``held_seconds`` + ``pnl_r``, not the signal TTL).
    Varying it does not change the trade SET, so it is intentionally NOT
    searched here. (The class-attr defaults on the strategies remain for
    behavior-0; see the strategy modules.)
  * ``momentum_gain`` / ``gap_gain`` — pure signal-STRENGTH knobs. They scale
    ``RawSignal.strength`` -> ``sizing_hint`` -> notional/pnl, but NOT which
    bars produce a signal, so they change pnl magnitude but not the trade SET.
    Tuning sizing is not a P0a ENTRY-search target, so they are intentionally
    NOT searched here.

Strategies with NO entry-set knob (``tsmom``, ``xau_indices_trend``,
``equity_tsmom``: a bare ``momentum_20bar > 0`` trigger) therefore have an empty
grid — their DEFAULT config is still evaluated (1 trial), but they contribute 0
entry-search breadth (reported honestly).

Grid points stay <=3 per param, within the design bounds.
"""

from __future__ import annotations

# strategy_id -> {class_attr_name: tuple(grid_points)}
# ONLY entry-trigger thresholds that change WHICH trades happen (the trade SET).
PARAM_BOUNDS: dict[str, dict[str, tuple[float, ...]]] = {
    # --- Track A: OKX SPOT (live in replay) ---
    "volume_burst": {
        # bounds [1.5, 3.5] — entry trigger on volume z-score
        "vol_z_threshold": (2.0, 2.5, 3.0),
        # bounds [0.0002, 0.0015] — liquidity floor that admits/blocks entries
        "atr_floor_pct": (0.0003, 0.0005, 0.001),
    },
    "rsi_bb_pullback": {
        # bounds [20, 40] — entry trigger on RSI level
        "rsi_threshold": (25.0, 30.0, 35.0),
    },
    "spot_donchian": {
        # bounds [15, 30] — entry trigger on ADX trend-strength floor
        "adx_threshold": (18.0, 20.0, 25.0),
    },
    # --- Track B: Capital CFD (1H live in replay; session_breakout dead) ---
    "fx_breakout_basket": {
        # bounds [15, 30] — entry trigger on ADX trend-strength floor
        "adx_threshold": (18.0, 20.0, 25.0),
    },
    "session_breakout": {
        # bounds [1, 2.5] — multiplier on the session_atr VALUE -> entry level
        "atr_mult": (1.0, 1.5, 2.0),
    },
    # --- Track C: Alpaca equity (data-unavailable in replay -> 0 trades) ---
    "equity_rsi_bb_pullback": {
        # bounds [20, 40] — entry trigger on RSI level
        "rsi_threshold": (25.0, 30.0, 35.0),
    },
    "equity_gap_go": {
        # bounds [0.01, 0.05] — entry trigger on the gap-up size
        "gap_pct": (0.015, 0.02, 0.03),
    },
    # tsmom / xau_indices_trend / equity_tsmom: bare ``momentum_20bar > 0``
    # trigger — NO entry-set knob (momentum_gain is a strength/sizing knob, not
    # an entry trigger). Empty grid -> default-only evaluation, 0 search breadth.
}


def varyable_params(strategy_id: str) -> tuple[str, ...]:
    """Return the varyable class-attribute names for ``strategy_id`` (or ())."""
    return tuple(PARAM_BOUNDS.get(strategy_id, {}).keys())


__all__ = ["PARAM_BOUNDS", "varyable_params"]
