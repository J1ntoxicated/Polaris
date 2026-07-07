"""Step M (2026-06-22) — canonical risk-unit (R) measurement redesign.

R is a DETERMINISTIC rescaling of the ``fills.pnl_usd`` dollar truth:
``realised_R = pnl_usd / risk_usd`` where ``risk_usd`` = entry stop distance ×
filled size. These tests pin the algebra that makes the dollar ledger and the R
ledger AGREE (same sign, same bleeder ranking), the unified ±100 clamp, and the
entry-ATR sanity band.
"""

from __future__ import annotations

import math

import pytest

from polaris.core.metrics.risk_unit import (
    BASE_RISK_PCT,
    ENTRY_ATR_PCT_MAX,
    ENTRY_ATR_PCT_MIN,
    R_CLAMP,
    STOP_ATR_MULT,
    clamp_entry_atr_pct,
    clamp_r,
    r_budget_for_stream,
    r_budget_for_venue,
    realised_r,
    realised_r_stream,
    risk_usd_at_entry,
)

# --- risk_usd_at_entry ------------------------------------------------------


def test_risk_usd_is_stop_distance_times_size() -> None:
    # entry 100, atr% 1%, 2 ATR stop, 3 units → 100*0.01*2*3 = 6.0
    assert risk_usd_at_entry(
        entry_price=100.0, entry_atr_pct=0.01, base_qty=3.0
    ) == pytest.approx(6.0)


def test_risk_usd_uses_stop_atr_mult_ssot() -> None:
    risk = risk_usd_at_entry(entry_price=100.0, entry_atr_pct=0.01, base_qty=1.0)
    assert risk == pytest.approx(100.0 * 0.01 * STOP_ATR_MULT * 1.0)


def test_risk_usd_zero_on_degenerate_inputs() -> None:
    assert risk_usd_at_entry(entry_price=0.0, entry_atr_pct=0.01, base_qty=1.0) == 0.0
    assert risk_usd_at_entry(entry_price=100.0, entry_atr_pct=0.01, base_qty=0.0) == 0.0


def test_risk_usd_clamps_collapsed_atr_anchor() -> None:
    # A flat/stale ~0 anchor must NOT collapse risk_usd to ~0 (R blow-up guard).
    # With the min-atr clamp the atr term is price·5e-4·2·qty = 0.1% of notional,
    # which is always below the 0.5% notional floor — so the notional floor is the
    # binding minimum here. Either way risk_usd is bounded well above ~0.
    from polaris.core.metrics.risk_unit import RISK_USD_NOTIONAL_PCT

    risk = risk_usd_at_entry(entry_price=100.0, entry_atr_pct=1e-9, base_qty=100.0)
    notional_floor = RISK_USD_NOTIONAL_PCT * 100.0 * 100.0  # 0.5% of $10,000 = $50
    assert risk == pytest.approx(notional_floor)
    assert risk > 0.0


def test_risk_usd_floored_against_phantom_r() -> None:
    # [[harvest_generalization_2026-06-23]]: measured risk_usd dropped to $0.0058
    # on tiny OKX notionals → a -$2 loss read as a phantom multi-R (mis-trains the
    # learner, mis-ranks bleeders). risk_usd is now floored at
    # max(RISK_USD_ABS_FLOOR, RISK_USD_NOTIONAL_PCT * notional) so per-trade
    # mfe_r/mae_r cannot explode. Measurement-only — never sizes/gates/blocks.
    from polaris.core.metrics.risk_unit import (
        RISK_USD_ABS_FLOOR,
        RISK_USD_NOTIONAL_PCT,
    )

    # Tiny notional + collapsed atr → without a floor risk_usd would be ~0. The
    # floor = max(abs floor, pct * notional). $130 notional, 0.5% → $0.65; the
    # absolute floor backstops even smaller notionals.
    risk = risk_usd_at_entry(entry_price=130.0, entry_atr_pct=1e-9, base_qty=1.0)
    notional = 130.0
    expected_floor = max(RISK_USD_ABS_FLOOR, RISK_USD_NOTIONAL_PCT * notional)
    assert risk >= expected_floor
    assert risk == pytest.approx(expected_floor)
    # The phantom-R is bounded: a -$2 loss on the floored risk is a sane R.
    assert abs(realised_r(pnl_usd=-2.0, risk_usd=risk)) < 10.0
    # A genuinely large risk_usd (real ATR + size) is NEVER floored UP (the floor
    # only lifts the degenerate-small ones; it never reduces a real risk unit).
    big = risk_usd_at_entry(entry_price=100.0, entry_atr_pct=0.02, base_qty=50.0)
    assert big == pytest.approx(100.0 * 0.02 * STOP_ATR_MULT * 50.0)  # = $200, unfloored


def test_risk_usd_converts_quote_ccy_to_usd() -> None:
    # USDJPY-style quote-ccy entry: price/notional are in JPY, so risk_usd must
    # be scaled by the quote->USD rate (~1/150 for JPY) — NOT left raw-JPY.
    # Large enough notional that neither floor binds, isolating the conversion.
    # entry 15000 (JPY), atr% 1%, 2 ATR, 1000 units, rate 1/150 -> USD terms:
    # (15000*0.01*2*1000) * (1/150) = 2000.0
    rate = 1.0 / 150.0
    risk = risk_usd_at_entry(
        entry_price=15_000.0, entry_atr_pct=0.01, base_qty=1_000.0,
        quote_usd_rate=rate,
    )
    unconverted = risk_usd_at_entry(
        entry_price=15_000.0, entry_atr_pct=0.01, base_qty=1_000.0,
    )
    assert risk == pytest.approx(unconverted * rate)
    assert risk == pytest.approx(2_000.0)


def test_risk_usd_default_rate_is_byte_identical_to_pre_fix() -> None:
    # Default quote_usd_rate=1.0 (USD-quoted OKX/Alpaca) must be unchanged.
    assert risk_usd_at_entry(
        entry_price=100.0, entry_atr_pct=0.01, base_qty=3.0
    ) == pytest.approx(6.0)


def test_risk_usd_zero_on_nonpositive_rate() -> None:
    assert risk_usd_at_entry(
        entry_price=100.0, entry_atr_pct=0.01, base_qty=1.0, quote_usd_rate=0.0
    ) == 0.0


def test_risk_usd_abs_floor_backstops_micro_notional() -> None:
    # An ultra-small notional where even the pct floor is sub-cent → the absolute
    # floor is the binding minimum (never returns ~0 for a positive position).
    from polaris.core.metrics.risk_unit import RISK_USD_ABS_FLOOR

    risk = risk_usd_at_entry(entry_price=1.0, entry_atr_pct=1e-9, base_qty=1.0)
    assert risk >= RISK_USD_ABS_FLOOR
    assert risk == pytest.approx(RISK_USD_ABS_FLOOR)


# --- realised_r: the rescaling that makes ledgers AGREE ---------------------


def test_realised_r_is_pnl_usd_over_risk_usd() -> None:
    # +$60 pnl on a $6 risk unit → +10R.
    assert realised_r(pnl_usd=60.0, risk_usd=6.0) == pytest.approx(10.0)


def test_realised_r_sign_matches_dollar_truth() -> None:
    # The core agreement invariant: sign(R) == sign(pnl_usd) always.
    for pnl in (-123.4, -1.0, 1.0, 999.0):
        r = realised_r(pnl_usd=pnl, risk_usd=6.0)
        assert math.copysign(1.0, r) == math.copysign(1.0, pnl)


def test_realised_r_preserves_bleeder_ranking() -> None:
    # Same risk_usd → ranking by R == ranking by $ (same bleeders, same order).
    risk = 6.0
    pnls = {"BNT": -300.0, "ADA": -50.0, "BTC": 120.0}
    by_dollar = sorted(pnls, key=lambda k: pnls[k])
    rs = {k: realised_r(pnl_usd=v, risk_usd=risk) for k, v in pnls.items()}
    by_r = sorted(rs, key=lambda k: rs[k])
    assert by_dollar == by_r


def test_realised_r_zero_on_unknowable_risk() -> None:
    assert realised_r(pnl_usd=60.0, risk_usd=0.0) == 0.0
    assert realised_r(pnl_usd=60.0, risk_usd=-1.0) == 0.0


# --- the round-trip: R via $ == R via the close-path algebra ----------------


def test_r_consistency_dollar_and_atr_paths_match() -> None:
    """Same trade → same R whether computed pnl_usd/risk_usd OR pnl_abs/atr_usd.

    Close path: pnl_usd = pnl_abs * base_qty ; risk_usd = atr_usd * base_qty.
    So pnl_usd/risk_usd == pnl_abs/atr_usd — the two derivations are identical.
    """
    entry, atr_pct, qty = 100.0, 0.01, 4.0
    exit_price = 103.0
    pnl_abs = exit_price - entry            # long
    atr_usd = entry * atr_pct * STOP_ATR_MULT
    pnl_usd = pnl_abs * qty
    risk_usd = risk_usd_at_entry(entry_price=entry, entry_atr_pct=atr_pct, base_qty=qty)
    r_via_dollars = realised_r(pnl_usd=pnl_usd, risk_usd=risk_usd)
    r_via_atr = clamp_r(pnl_abs / atr_usd)
    assert r_via_dollars == pytest.approx(r_via_atr)


# --- unified clamp ----------------------------------------------------------


def test_clamp_r_is_plus_minus_100() -> None:
    assert R_CLAMP == 100.0
    assert clamp_r(500.0) == 100.0
    assert clamp_r(-500.0) == -100.0
    assert clamp_r(7.5) == pytest.approx(7.5)


def test_clamp_r_nan_is_zero() -> None:
    assert clamp_r(float("nan")) == 0.0
    assert clamp_r(float("inf")) == 100.0


def test_realised_r_clamped_at_100() -> None:
    # A tiny risk unit must not produce an unbounded R on the decision path.
    assert realised_r(pnl_usd=1e6, risk_usd=1.0) == 100.0
    assert realised_r(pnl_usd=-1e6, risk_usd=1.0) == -100.0


# --- Step N: stream-common R (per-stream R_budget denominator) --------------


def test_r_budget_is_base_risk_pct_times_starting_equity() -> None:
    # OKX 100k, Capital 100k → 2% each (uniform $100k virtual seed, Jin
    # 2026-07-07). (Default env-free constants.)
    assert r_budget_for_stream("A_okx_crypto") == pytest.approx(BASE_RISK_PCT * 100_000.0)
    assert r_budget_for_stream("B_capital_cfd") == pytest.approx(BASE_RISK_PCT * 100_000.0)
    assert r_budget_for_stream("A_okx_crypto") == pytest.approx(2_000.0)
    assert r_budget_for_stream("B_capital_cfd") == pytest.approx(2_000.0)


def test_r_budget_alpaca_has_deterministic_fallback() -> None:
    # Alpaca R_budget must be DEFINED (non-zero) without a live probe — the
    # deterministic starting-equity constant guarantees R is never undefined.
    assert r_budget_for_stream("C_alpaca_equity") > 0.0
    # A live-probe override supersedes the constant.
    assert r_budget_for_stream(
        "C_alpaca_equity", equity_override=200_000.0
    ) == pytest.approx(BASE_RISK_PCT * 200_000.0)


def test_r_budget_by_venue_maps_through_stream() -> None:
    assert r_budget_for_venue("okx") == pytest.approx(r_budget_for_stream("A_okx_crypto"))
    assert r_budget_for_venue("capital") == pytest.approx(r_budget_for_stream("B_capital_cfd"))
    assert r_budget_for_venue("alpaca") == pytest.approx(r_budget_for_stream("C_alpaca_equity"))


def test_r_budget_unknown_venue_is_zero() -> None:
    assert r_budget_for_venue("kraken") == 0.0
    assert r_budget_for_stream("Z_unknown") == 0.0


def test_realised_r_stream_same_dollar_loss_comparable_across_venues() -> None:
    """THE core goal: a −$2 OKX loss and a −$2 Alpaca loss map to COMPARABLE R.

    Before (per-trade ATR risk_usd): OKX read ~−1.3R, Alpaca ~−0.005R — a >200×
    spread. With the stream-common R_budget the two are within the same order of
    magnitude (scaled only by each stream's intended risk budget).
    """
    r_okx = realised_r_stream(pnl_usd=-2.0, venue="okx")
    r_alpaca = realised_r_stream(pnl_usd=-2.0, venue="alpaca")
    assert r_okx < 0.0 and r_alpaca < 0.0
    # Same order of magnitude — the 200× venue skew is gone (ratio well under 5×).
    ratio = abs(r_okx) / abs(r_alpaca)
    assert 0.2 < ratio < 5.0


def test_realised_r_stream_sign_matches_dollar_truth() -> None:
    for pnl in (-123.4, -1.0, 1.0, 999.0):
        for venue in ("okx", "capital", "alpaca"):
            r = realised_r_stream(pnl_usd=pnl, venue=venue)
            assert math.copysign(1.0, r) == math.copysign(1.0, pnl)


def test_realised_r_stream_within_venue_is_linear_rescale() -> None:
    # Within ONE venue R is a pure linear rescale of $ → identical bleeder order.
    pnls = {"BNT": -300.0, "ADA": -50.0, "BTC": 120.0}
    rs = {k: realised_r_stream(pnl_usd=v, venue="okx") for k, v in pnls.items()}
    assert sorted(pnls, key=lambda k: pnls[k]) == sorted(rs, key=lambda k: rs[k])
    budget = r_budget_for_venue("okx")
    for k, v in pnls.items():
        assert rs[k] == pytest.approx(v / budget)


def test_realised_r_stream_unknown_venue_is_zero() -> None:
    assert realised_r_stream(pnl_usd=-5.0, venue="kraken") == 0.0


def test_realised_r_stream_clamped_at_100() -> None:
    # A catastrophic $ loss still clamps at ±100 (one telemetry bound).
    assert realised_r_stream(pnl_usd=1e9, venue="okx") == R_CLAMP
    assert realised_r_stream(pnl_usd=-1e9, venue="okx") == -R_CLAMP


# --- entry ATR sanity band --------------------------------------------------


def test_clamp_entry_atr_bounds_both_sides() -> None:
    assert clamp_entry_atr_pct(1e-9) == ENTRY_ATR_PCT_MIN     # collapse guard
    assert clamp_entry_atr_pct(0.99) == ENTRY_ATR_PCT_MAX     # high-side guard
    assert clamp_entry_atr_pct(0.01) == pytest.approx(0.01)   # in-band unchanged


def test_clamp_entry_atr_nonfinite_to_floor() -> None:
    assert clamp_entry_atr_pct(float("nan")) == ENTRY_ATR_PCT_MIN
    assert clamp_entry_atr_pct(0.0) == ENTRY_ATR_PCT_MIN
    assert clamp_entry_atr_pct(-0.5) == ENTRY_ATR_PCT_MIN
