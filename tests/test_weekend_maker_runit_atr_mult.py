"""① R-unit ATR-mult per-strategy — weekend makers widen to 3×ATR (FIX-EXIT).

DEMO/PAPER only — virtual funds. AGGRESSIVE / flow_not_block: this WIDENS the
R-unit ATR-distance for the two validated weekend OKX makers (the
``weekend_maker_honest_rerun_2026-06-28`` finding — "rail fixed −1.0R, only R-unit
ATR mult widens" flips the OOS median positive: flush +0.065, funding +0.722).

🚨 The −1.0R rail COEFFICIENT is ABSOLUTELY UNCHANGED. Only the R-unit ATR-distance
(the denominator the pnl_r / mfe_r excursion is measured in) widens: a wider ATR
multiplier means the SAME −1.0R rung corresponds to a WIDER price stop (more room
for the bounded revert), and a fixed-$ fee shrinks as a fraction of R. Sizing /
the 9-stack / the trade path are UNTOUCHED. Default (every other strategy) stays
2.0×ATR → byte-identical.
"""

from __future__ import annotations

from polaris.core.indicators.production import compute_unrealized_pnl_r
from polaris.core.live_recalc.exit_engine import evaluate_exit, init_exit_state
from polaris.core.metrics.risk_unit import STOP_ATR_MULT
from polaris.scripts.exit_strategy_config import _stop_atr_mult_for_strategy

THIN_BOOK_ID = "weekend_thin_book_flush_maker"
FUNDING_ID = "weekend_funding_capitulation_maker"
WEEKEND_STOP_ATR_MULT = 3.0


# ── the per-strategy lookup ──────────────────────────────────────────────────


def test_weekend_makers_widen_to_3x_atr() -> None:
    # atr_pct=0.01 (1%) is well above the fee floor for every venue (see
    # test_fee_aware_runit_floor.py), so the base weekend-maker multiplier is
    # returned unchanged — the fee floor is a no-op at this ATR%.
    assert _stop_atr_mult_for_strategy(THIN_BOOK_ID, atr_pct=0.01) == WEEKEND_STOP_ATR_MULT
    assert _stop_atr_mult_for_strategy(FUNDING_ID, atr_pct=0.01) == WEEKEND_STOP_ATR_MULT


def test_every_other_strategy_keeps_2x_default_byte_identical() -> None:
    # The module SSOT default (= the realised/excursion 2-ATR convention). Any
    # non-weekend-maker registered strategy AND any unregistered id resolve to it
    # → the R ruler is byte-identical for every existing position at this ATR%
    # (0.01, above the fee floor for every venue — see
    # test_fee_aware_runit_floor.py for the floor's OWN widening behaviour).
    assert _stop_atr_mult_for_strategy("spot_donchian_breakout", atr_pct=0.01) == STOP_ATR_MULT
    assert _stop_atr_mult_for_strategy("session_breakout", atr_pct=0.01) == STOP_ATR_MULT
    assert _stop_atr_mult_for_strategy("__not_a_strategy__", atr_pct=0.01) == STOP_ATR_MULT
    assert STOP_ATR_MULT == 2.0  # the SSOT default is the legacy 2-ATR convention


# ── the mechanism: wider R-unit ⇒ smaller |pnl_r| for the SAME $ move ─────────


def test_wider_atr_mult_reports_smaller_pnl_r_for_same_move() -> None:
    # Same entry, same adverse move, same ATR% — the ONLY difference is the R-unit
    # ATR multiplier. A 3×ATR denominator is 1.5× the 2×ATR denominator, so the
    # SAME price move reads as a SMALLER |pnl_r| (1/1.5 = 0.667×). The −1.0R rail
    # therefore fires at a 1.5× WIDER price stop (MORE room — aggressive), with the
    # rail coefficient itself untouched.
    common = dict(side="long", entry_price=100.0, last_price=99.0, atr_pct=0.01)
    pnl_r_2x = compute_unrealized_pnl_r(**common, stop_atr_mult=2.0)
    pnl_r_3x = compute_unrealized_pnl_r(**common, stop_atr_mult=3.0)
    assert pnl_r_2x < 0.0 and pnl_r_3x < 0.0
    # 3× denominator = 1.5× the 2× denominator → |pnl_r| is exactly 2/3 of it.
    assert abs(pnl_r_3x / pnl_r_2x - (2.0 / 3.0)) < 1e-9


def test_rail_minus_one_r_fires_at_wider_dollar_stop_with_3x() -> None:
    # The −1.0R rung is a FIXED coefficient. Find the price at which pnl_r == −1.0R
    # for each multiplier: with 3×ATR it is a WIDER (further-from-entry) price than
    # with 2×ATR. The rail did not move; the R-unit it measures against widened.
    entry, atr_pct = 100.0, 0.01
    # pnl_r == -1.0 ⇔ (entry - last) == entry*atr_pct*mult ⇔ last = entry*(1 - atr_pct*mult)
    rail_px_2x = entry * (1.0 - atr_pct * 2.0)  # 98.0
    rail_px_3x = entry * (1.0 - atr_pct * 3.0)  # 97.0
    assert rail_px_3x < rail_px_2x  # 3× rail is a wider (deeper) stop
    assert (
        abs(
            compute_unrealized_pnl_r(
                side="long", entry_price=entry, last_price=rail_px_2x,
                atr_pct=atr_pct, stop_atr_mult=2.0,
            )
            + 1.0
        )
        < 1e-9
    )
    assert (
        abs(
            compute_unrealized_pnl_r(
                side="long", entry_price=entry, last_price=rail_px_3x,
                atr_pct=atr_pct, stop_atr_mult=3.0,
            )
            + 1.0
        )
        < 1e-9
    )


# ── evaluate_exit FSM excursion respects the wider denominator ────────────────


def test_evaluate_exit_mfe_r_scales_with_stop_atr_mult() -> None:
    # The excursion FSM (mfe_r / mae_r) is denominated in the R-unit ATR distance.
    # A favourable peak reads as a SMALLER mfe_r under the wider 3×ATR unit (so a
    # fee-sized excursion is a smaller fraction of R — the FIX-EXIT mechanism).
    common = dict(
        prev=init_exit_state(entry_price=100.0, side="long"),
        side="long", entry_price=100.0, last_price=101.0, atr_pct=0.01,
        pnl_r=0.5, held_seconds=10,
    )
    d2 = evaluate_exit(**common, stop_atr_mult=2.0)
    d3 = evaluate_exit(**common, stop_atr_mult=3.0)
    assert d2.state.mfe_r > 0.0 and d3.state.mfe_r > 0.0
    # 3× denominator = 1.5× the 2× denominator → mfe_r is 2/3 of it.
    assert abs(d3.state.mfe_r / d2.state.mfe_r - (2.0 / 3.0)) < 1e-9


def test_evaluate_exit_default_2x_byte_identical() -> None:
    # The default (no stop_atr_mult passed) is byte-identical to an explicit 2.0 —
    # every existing caller (which never passes the kwarg) keeps the legacy ruler.
    common = dict(
        prev=init_exit_state(entry_price=100.0, side="long"),
        side="long", entry_price=100.0, last_price=101.0, atr_pct=0.01,
        pnl_r=0.5, held_seconds=10,
    )
    d_default = evaluate_exit(**common)
    d_explicit = evaluate_exit(**common, stop_atr_mult=2.0)
    assert d_default.state.mfe_r == d_explicit.state.mfe_r
    assert d_default.state.mae_r == d_explicit.state.mae_r
    assert d_default.state.stop_price == d_explicit.state.stop_price
