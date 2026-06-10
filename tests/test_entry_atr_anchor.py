"""Entry-time ATR anchor — exit-engine R denominator + excursion floors.

DEMO/PAPER. Bug-fix coverage for problem #2 (anchorless R denominator): the
per-tick recomputed atr_pct shrank during volatility contraction, inflating
mfe_r/mae_r 4-8x (live: -92.58R that was really -11.45%). ``evaluate_exit``
now accepts the entry-time anchor for the R DENOMINATOR while the trail width
keeps using the CURRENT timeframe ATR (a Chandelier trail measures today's
noise; the R unit measures the entry-time risk). Relative floors replace the
absolute 1e-6 so a degenerate ATR can never explode R (-463,734R class).
Measurement + per-position exit only — no sizing, no entry block, no halt.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from polaris.core.live_recalc.excursion import (
    compute_excursion_r,
    compute_mae_r,
    compute_mfe_r,
)
from polaris.core.live_recalc.exit_engine import (
    EXIT_ATR_TRAIL_MULT,
    ExitState,
    evaluate_exit,
    init_exit_state,
)


def _state(entry: float = 100.0) -> ExitState:
    return init_exit_state(entry_price=entry, side="long")


# --- anchor denominates R; trail keeps the current ATR ----------------------


def test_anchor_denominates_mfe_under_vol_contraction() -> None:
    """Volatility contraction scenario: entry anchor 4% vs current 0.5%.

    Without the anchor a +2 move on entry=100 reads mfe=2.0R (HARVEST!); with
    the anchor it is the honest 0.25R (still OPEN)."""
    prev = _state()
    d = evaluate_exit(
        prev=prev, side="long", entry_price=100.0, last_price=102.0,
        atr_pct=0.005, pnl_r=0.25, held_seconds=10,
        entry_atr_pct=0.04,
    )
    assert d.state.mfe_r == pytest.approx(2.0 / (100.0 * 0.04 * 2.0))
    assert d.state.exit_state == "open"  # NOT harvest — honest R
    assert d.close is False


def test_anchor_fixed_mfe_invariant_to_current_atr() -> None:
    """With the anchor set, sweeping the CURRENT atr_pct must not move
    mfe_r/mae_r at all (the denominator is pinned at entry)."""
    vals = []
    for current in (0.001, 0.005, 0.02, 0.08):
        d = evaluate_exit(
            prev=_state(), side="long", entry_price=100.0, last_price=101.0,
            atr_pct=current, pnl_r=0.1, held_seconds=10, entry_atr_pct=0.04,
        )
        vals.append((d.state.mfe_r, d.state.mae_r))
    assert len(set(vals)) == 1


def test_trail_width_uses_current_atr_not_anchor() -> None:
    # anchor 4% (wide) but current 0.5% (tight) → the trail must be the tight
    # CURRENT distance: stop = peak - 2.0 * (100 * 0.005) = 101.0.
    d = evaluate_exit(
        prev=_state(), side="long", entry_price=100.0, last_price=102.0,
        atr_pct=0.005, pnl_r=0.25, held_seconds=10, entry_atr_pct=0.04,
    )
    assert d.state.stop_price == pytest.approx(
        102.0 - EXIT_ATR_TRAIL_MULT * 100.0 * 0.005
    )


def test_no_anchor_keeps_existing_behaviour() -> None:
    """entry_atr_pct=None (default) → byte-identical to the pre-fix call."""
    kwargs = dict(
        prev=_state(), side="long", entry_price=100.0, last_price=101.0,
        atr_pct=0.01, pnl_r=0.5, held_seconds=10,
    )
    base = evaluate_exit(**kwargs)  # type: ignore[arg-type]
    explicit = evaluate_exit(**kwargs, entry_atr_pct=None)  # type: ignore[arg-type]
    assert base == explicit
    # mfe denominator = current atr (legacy): 1.0 / (100*0.01*2) = 0.5R.
    assert base.state.mfe_r == pytest.approx(0.5)


def test_short_side_anchor_symmetric() -> None:
    d = evaluate_exit(
        prev=init_exit_state(entry_price=100.0, side="short"), side="short",
        entry_price=100.0, last_price=98.0, atr_pct=0.005, pnl_r=0.25,
        held_seconds=10, entry_atr_pct=0.04,
    )
    assert d.state.mfe_r == pytest.approx(2.0 / (100.0 * 0.04 * 2.0))


# --- relative floor + cap (degenerate inputs can't explode R) ----------------


def test_degenerate_atr_bounds_r_relative_to_price() -> None:
    """atr_pct→0: |R| is bounded by price-move% / 0.01% (the relative floor),
    not the absolute 1e-6 floor that produced -463,734R."""
    d = evaluate_exit(
        prev=_state(), side="long", entry_price=100.0, last_price=101.0,
        atr_pct=0.0, pnl_r=0.0, held_seconds=10,
    )
    # floor = entry*1e-4 → denominator 0.01 → 1.0 move = 100R, capped at 100.
    assert d.state.mfe_r <= 100.0
    assert d.state.mfe_r == pytest.approx(100.0)


def test_zero_entry_price_stays_finite() -> None:
    d = evaluate_exit(
        prev=init_exit_state(entry_price=0.0, side="long"), side="long",
        entry_price=0.0, last_price=1.0, atr_pct=0.0, pnl_r=0.0,
        held_seconds=10,
    )
    assert d.state.mfe_r == 100.0  # capped, finite (1e-6 last-resort floor)


def test_excursion_relative_floor_and_cap() -> None:
    # atr_usd=0 on a 38000-priced index CFD (J225 class): floor = 3.8.
    mfe = compute_mfe_r(
        entry_price=38_000.0, peak_price=38_038.0, trough_price=38_000.0,
        side="long", atr_usd=0.0,
    )
    assert mfe == pytest.approx(38.0 / 3.8)  # = move% / 0.01% = 10R
    mae = compute_mae_r(
        entry_price=38_000.0, peak_price=38_000.0, trough_price=0.0,
        side="long", atr_usd=0.0,
    )
    assert mae == -100.0  # capped


def test_excursion_normal_atr_unchanged() -> None:
    # Non-degenerate atr_usd well above the relative floor → same values as
    # the pre-fix math (regression: floors only bind on degenerate inputs).
    mfe, mae = compute_excursion_r(
        entry_price=100.0, peak_price=103.0, trough_price=99.0,
        side="long", atr_usd=2.0,
    )
    assert mfe == pytest.approx(1.5)
    assert mae == pytest.approx(-0.5)


# --- hypothesis properties ----------------------------------------------------


@given(
    current_atr=st.floats(min_value=1e-5, max_value=0.5),
    peak=st.floats(min_value=100.0, max_value=200.0),
)
def test_property_anchor_pins_mfe_against_current_atr(
    current_atr: float, peak: float,
) -> None:
    d = evaluate_exit(
        prev=_state(), side="long", entry_price=100.0, last_price=peak,
        atr_pct=current_atr, pnl_r=0.0, held_seconds=1, entry_atr_pct=0.02,
    )
    expected = min(100.0, (peak - 100.0) / (100.0 * 0.02 * 2.0))
    assert d.state.mfe_r == pytest.approx(expected)


@given(
    entry=st.floats(min_value=0.0, max_value=1e6),
    peak_off=st.floats(min_value=0.0, max_value=1e5),
    trough_off=st.floats(min_value=0.0, max_value=1e5),
    atr_usd=st.floats(min_value=0.0, max_value=1e4),
    side=st.sampled_from(["long", "short"]),
)
def test_property_excursion_signs_and_caps(
    entry: float, peak_off: float, trough_off: float, atr_usd: float, side: str,
) -> None:
    mfe, mae = compute_excursion_r(
        entry_price=entry, peak_price=entry + peak_off,
        trough_price=entry - trough_off, side=side, atr_usd=atr_usd,
    )
    assert 0.0 <= mfe <= 100.0
    assert -100.0 <= mae <= 0.0
