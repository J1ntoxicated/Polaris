"""P0-1 (fee-aware R-unit floor, generalized) — trade_mess_full_audit_2026-07-02.

DEMO/PAPER only — virtual funds. AGGRESSIVE / flow_not_block: this WIDENS the
R-unit ATR-distance (never narrows it, never blocks/cuts size) whenever the
strategy's OWN 2x-ATR (or weekend-maker 3x-ATR) R-unit would be smaller than
``FEE_FLOOR_K`` round trips of REAL fee — the finding that Capital FX raw 2xATR
(3.4-17.4bps) sat BELOW the 6bps round-trip fee so every BEP/lock/peak-lock rung
fired at a GUARANTEED realised loss (fee-in-R 0.30-1.76R). The floor generalizes
the weekend-maker 3xATR fix (frozenset-scoped, d5c98a1) to EVERY strategy: the
G6 rail (``compute_unrealized_pnl_r``), the excursion FSM (``evaluate_exit``),
and ``risk_usd_at_entry`` all read ``stop_atr_mult`` from the SAME
``_stop_atr_mult_for_strategy`` choke point, so the BEP/lock/peak-lock rungs
inherit the floor automatically without touching their own R-unit math.

The −1.0R rail COEFFICIENT (max_loss_r=1.0, G6 monitor) is UNTOUCHED — only the
ATR-distance the R-unit denominates against widens (more room, aggressive).
Sizing / the 9-stack / the trade path are NEVER touched. When the strategy's own
base multiplier ALREADY clears the fee floor (e.g. a wide-ATR instrument), the
floor is a no-op (max() — never narrows).
"""

from __future__ import annotations

from polaris.core.economics.fees import CAPITAL_REAL_BPS, OKX_REAL_TAKER_BPS
from polaris.core.live_recalc.exit_engine import evaluate_exit, init_exit_state
from polaris.core.live_recalc.exit_types import MfeProtectSchedule
from polaris.core.metrics.risk_unit import STOP_ATR_MULT
from polaris.scripts.exit_strategy_config import (
    FEE_FLOOR_K,
    _fee_round_trip_pct_for_venue,
    _stop_atr_mult_for_strategy,
)

WEEKEND_MULT = 3.0
THIN_BOOK_ID = "weekend_thin_book_flush_maker"
FUNDING_ID = "weekend_funding_capitulation_maker"


def test_fee_floor_lifts_a_thin_atr_capital_fx_below_the_frozenset() -> None:
    # session_breakout (capital, NOT in the weekend-maker frozenset) at a TIGHT
    # ATR% (0.05% -> matches the audit's measured 3.4-17.4bps raw-2xATR range at
    # the low end) must widen past its 2xATR base once that 2xATR R-unit sits
    # below k round trips of the REAL Capital fee.
    atr_pct = 0.0005  # 5 bps 1-ATR -> 2xATR R-unit = 10 bps
    round_trip_pct = _fee_round_trip_pct_for_venue("capital")
    mult = _stop_atr_mult_for_strategy("session_breakout", atr_pct=atr_pct)
    # The floor guarantees stop_atr_mult * atr_pct >= FEE_FLOOR_K * round_trip.
    assert mult * atr_pct >= FEE_FLOOR_K * round_trip_pct - 1e-12
    assert mult > STOP_ATR_MULT  # widened past the plain 2xATR SSOT default


def test_fee_floor_is_noop_when_base_mult_already_clears_fee() -> None:
    # A generous ATR% (1%) makes the plain 2xATR R-unit (2%) far above the fee
    # floor for any real venue schedule -> the floor changes NOTHING (max() is a
    # no-op) -> byte-identical to the pre-floor SSOT default.
    atr_pct = 0.01
    mult = _stop_atr_mult_for_strategy("session_breakout", atr_pct=atr_pct)
    assert mult == STOP_ATR_MULT


def test_weekend_maker_3x_preserved_when_it_already_clears_the_floor() -> None:
    # d5c98a1 behaviour must survive: at a normal ATR% the weekend-maker 3xATR
    # stays exactly 3.0 (the floor does not override an already-wider base).
    atr_pct = 0.01
    assert _stop_atr_mult_for_strategy(THIN_BOOK_ID, atr_pct=atr_pct) == WEEKEND_MULT
    assert _stop_atr_mult_for_strategy(FUNDING_ID, atr_pct=atr_pct) == WEEKEND_MULT


def test_fee_floor_widens_weekend_maker_further_at_extreme_tight_atr() -> None:
    # max(WEEKEND_MULT, fee_floor_mult): if ATR% is degenerate-tight even the
    # weekend maker's 3xATR would sit below the fee floor -> the floor wins
    # (never narrows below 3.0, only ever widens further -- aggressive).
    atr_pct = 1e-5  # pathologically flat bar
    mult = _stop_atr_mult_for_strategy(THIN_BOOK_ID, atr_pct=atr_pct)
    round_trip_pct = _fee_round_trip_pct_for_venue("okx")
    assert mult >= WEEKEND_MULT
    assert mult * atr_pct >= FEE_FLOOR_K * round_trip_pct - 1e-12


def test_zero_or_negative_atr_pct_degrades_to_base_mult() -> None:
    # A degenerate atr_pct (<=0, caller passed a bad anchor) cannot be divided by
    # -> the floor is skipped and the base (weekend-or-SSOT) multiplier is
    # returned unchanged (fail-open, never raises, never blocks).
    assert _stop_atr_mult_for_strategy("session_breakout", atr_pct=0.0) == STOP_ATR_MULT
    assert _stop_atr_mult_for_strategy(THIN_BOOK_ID, atr_pct=-0.01) == WEEKEND_MULT


def test_unregistered_strategy_still_gets_a_fee_floor() -> None:
    # An unregistered id keeps the OKX real-taker fallback venue (fees.py
    # convention) so even an unknown strategy's R-unit cannot sit below the fee
    # floor -- the floor is a GLOBAL invariant, not a per-strategy opt-in.
    atr_pct = 0.0002
    mult = _stop_atr_mult_for_strategy("__not_a_strategy__", atr_pct=atr_pct)
    round_trip_pct = 2.0 * OKX_REAL_TAKER_BPS / 10_000.0
    assert mult * atr_pct >= FEE_FLOOR_K * round_trip_pct - 1e-12


def test_fee_round_trip_pct_matches_venue_schedule() -> None:
    assert _fee_round_trip_pct_for_venue("okx") == 2.0 * OKX_REAL_TAKER_BPS / 10_000.0
    assert (
        _fee_round_trip_pct_for_venue("capital")
        == 2.0 * CAPITAL_REAL_BPS / 10_000.0
    )


def test_fee_floor_never_narrows_an_already_wide_base() -> None:
    # A clearly-fee-clearing atr_pct never narrows the existing weekend-maker /
    # SSOT base multipliers (max() is monotone, no regression path).
    assert _stop_atr_mult_for_strategy("spot_donchian_breakout", atr_pct=0.02) == (
        STOP_ATR_MULT
    )


# ── BEP/lock/peak-lock rungs AUTOMATICALLY inherit the fee floor ────────────
# (trade_mess_full_audit_2026-07-02 P0-1 mandate: "BEP/lock/peak-lock rung이
# R-unit 기준이라 자동 승계됨을 테스트로 증명" — none of exit_engine.py's rung
# math changes; they all denominate in ``atr_r = entry_price * atr_pct *
# stop_atr_mult``, so widening ``stop_atr_mult`` via the fee floor widens the
# PRICE distance every rung locks at, with zero edits to evaluate_exit itself.)


def test_bep_rung_price_widens_with_the_fee_floor_stop_atr_mult() -> None:
    # session_breakout (capital) at a tight ATR%: the fee-floor-derived
    # stop_atr_mult must produce a WIDER BEP/protect/lock price distance than the
    # plain 2xATR SSOT default would — proving the rung inherits the floor via
    # stop_atr_mult alone, no rung-math change.
    atr_pct = 0.0005
    fee_floor_mult = _stop_atr_mult_for_strategy("session_breakout", atr_pct=atr_pct)
    assert fee_floor_mult > STOP_ATR_MULT  # the floor actually engaged here

    schedule = MfeProtectSchedule(bep_at_r=0.30, protect_at_r=0.45, lock_r=0.20)
    d_plain = evaluate_exit(
        prev=init_exit_state(entry_price=100.0, side="long"),
        side="long", entry_price=100.0, last_price=100.05, atr_pct=atr_pct,
        pnl_r=0.3, held_seconds=10, mfe_protect=schedule,
        stop_atr_mult=STOP_ATR_MULT,
    )
    d_floored = evaluate_exit(
        prev=init_exit_state(entry_price=100.0, side="long"),
        side="long", entry_price=100.0, last_price=100.05, atr_pct=atr_pct,
        pnl_r=0.3, held_seconds=10, mfe_protect=schedule,
        stop_atr_mult=fee_floor_mult,
    )
    # Same $ move, same schedule -- the floored (wider R-unit) run reads a
    # SMALLER mfe_r for the identical price move (the FIX-EXIT mechanism), so it
    # has NOT yet armed the BEP floor the plain 2xATR run already armed.
    assert d_plain.state.mfe_r > d_floored.state.mfe_r
    # The BEP stop (once armed) sits at entry_price -- structurally identical
    # ratchet math either way; what differs is HOW MUCH price move it takes to
    # arm it, which is exactly the fee-floor's protective effect (fewer
    # guaranteed-loss BEP arms on fee-sized noise).
    assert d_plain.state.stop_price is not None
    assert d_floored.state.stop_price is not None
