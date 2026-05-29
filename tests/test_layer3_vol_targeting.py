"""Layer 3 — T4 vol-targeting continuous scalar tests (#8 vol-targeting).

The T4 continuous scalar is made vol-aware: instead of a fixed signal-strength
ramp, the *single* continuous scalar can be derived from realized volatility via
inverse weighting ``target_vol / realized_vol`` (ex-ante / past-only — no
look-ahead). High realized vol → smaller scalar; low realized vol → larger
scalar. The result is clipped to the SAME [0.75, 1.50] band, so the T4 chain
(tier amplifier × cell mult × Kelly × hard-MAX min()) and the 9-stack ban are
unchanged — only the one scalar is replaced with a vol-aware value.

Spec source:
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (T4 single scalar)
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from polaris.core.sizing import (
    CONT_SCALAR_MAX,
    CONT_SCALAR_MIN,
    DEFAULT_TARGET_VOL,
    PortfolioState,
    SignalIntent,
    StrategyRiskState,
    compute_size,
    ewma_realized_vol,
    vol_targeted_scalar,
)

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# vol_targeted_scalar — inverse weighting, clipped to [0.75, 1.50]
# ---------------------------------------------------------------------------


def test_vol_scalar_at_target_is_one() -> None:
    # realized == target → target/realized == 1.0 → neutral scalar.
    assert vol_targeted_scalar(realized_vol=0.02, target_vol=0.02) == pytest.approx(1.0)


def test_vol_scalar_high_vol_shrinks() -> None:
    # realized 2× target → 0.5 raw → clipped up to floor 0.75 (still < 1.0).
    s = vol_targeted_scalar(realized_vol=0.04, target_vol=0.02)
    assert s < 1.0
    assert CONT_SCALAR_MIN <= s <= CONT_SCALAR_MAX


def test_vol_scalar_low_vol_grows() -> None:
    # realized 0.5× target → 2.0 raw → clipped down to ceiling 1.50 (still > 1.0).
    s = vol_targeted_scalar(realized_vol=0.01, target_vol=0.02)
    assert s > 1.0
    assert CONT_SCALAR_MIN <= s <= CONT_SCALAR_MAX


def test_vol_scalar_monotone_decreasing_in_realized_vol() -> None:
    # Higher realized vol must never increase the scalar (inverse weighting).
    lo = vol_targeted_scalar(realized_vol=0.005, target_vol=0.02)
    mid = vol_targeted_scalar(realized_vol=0.02, target_vol=0.02)
    hi = vol_targeted_scalar(realized_vol=0.08, target_vol=0.02)
    assert lo >= mid >= hi


def test_vol_scalar_extremes_clip_to_band() -> None:
    # Tiny realized vol → huge raw ratio → ceiling.
    assert vol_targeted_scalar(realized_vol=1e-9, target_vol=0.02) == pytest.approx(CONT_SCALAR_MAX)
    # Huge realized vol → ~0 raw ratio → floor.
    assert vol_targeted_scalar(realized_vol=10.0, target_vol=0.02) == pytest.approx(CONT_SCALAR_MIN)


def test_vol_scalar_nonfinite_or_nonpositive_returns_floor() -> None:
    # Anti-collapse / defensive: bad inputs return the floor (never 0, never raise).
    assert vol_targeted_scalar(realized_vol=float("nan"), target_vol=0.02) == CONT_SCALAR_MIN
    assert vol_targeted_scalar(realized_vol=0.0, target_vol=0.02) == CONT_SCALAR_MIN
    assert vol_targeted_scalar(realized_vol=-0.01, target_vol=0.02) == CONT_SCALAR_MIN
    assert vol_targeted_scalar(realized_vol=0.02, target_vol=0.0) == CONT_SCALAR_MIN


@given(
    rv=st.floats(min_value=1e-6, max_value=5.0),
    tv=st.floats(min_value=1e-4, max_value=0.5),
)
def test_vol_scalar_always_in_band(rv: float, tv: float) -> None:
    s = vol_targeted_scalar(realized_vol=rv, target_vol=tv)
    assert CONT_SCALAR_MIN <= s <= CONT_SCALAR_MAX


# ---------------------------------------------------------------------------
# ewma_realized_vol — ex-ante (past-only) EWMA of returns
# ---------------------------------------------------------------------------


def test_ewma_vol_empty_returns_none() -> None:
    assert ewma_realized_vol([]) is None


def test_ewma_vol_single_return_ok() -> None:
    # vol = sqrt(EWMA of squared returns around zero) → |r| for one obs.
    v = ewma_realized_vol([0.03])
    assert v is not None
    assert v == pytest.approx(0.03)


def test_ewma_vol_constant_returns_zero_vol() -> None:
    # All identical returns → zero dispersion around mean → ~0 vol.
    v = ewma_realized_vol([0.0, 0.0, 0.0, 0.0])
    assert v is not None
    assert v == pytest.approx(0.0, abs=1e-12)


def test_ewma_vol_higher_dispersion_higher_vol() -> None:
    calm = ewma_realized_vol([0.001, -0.001, 0.001, -0.001, 0.001])
    wild = ewma_realized_vol([0.05, -0.05, 0.05, -0.05, 0.05])
    assert calm is not None and wild is not None
    assert wild > calm


def test_ewma_vol_recent_weighted_more() -> None:
    # A recent vol spike (last element) should lift EWMA vs the same spike early.
    recent_spike = ewma_realized_vol([0.0, 0.0, 0.0, 0.0, 0.1])
    early_spike = ewma_realized_vol([0.1, 0.0, 0.0, 0.0, 0.0])
    assert recent_spike is not None and early_spike is not None
    assert recent_spike > early_spike


def test_ewma_vol_nonfinite_ignored() -> None:
    v = ewma_realized_vol([0.01, float("nan"), -0.01, float("inf"), 0.01])
    assert v is not None
    assert math.isfinite(v)


# ---------------------------------------------------------------------------
# compute_size integration — same chain, same clip, only scalar is vol-aware
# ---------------------------------------------------------------------------


def _intent(*, realized_vol: float | None, strength: float = 1.0) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-vol",
        venue="okx",
        symbol="VOL-USDT",
        instrument_id="okx:VOL-USDT",
        underlying_group_id="crypto:VOL",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=strength,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
        realized_vol=realized_vol,
    )


def _risk_state(n: int = 25) -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst",
        closed_trades=n, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
    )


def test_compute_size_high_vol_smaller_than_low_vol(memdb: sqlite3.Connection) -> None:
    """Identical chain (same cell, tier, caps); only realized_vol differs.

    High realized vol → smaller continuous scalar → smaller final risk.
    """
    high = compute_size(
        memdb, intent=_intent(realized_vol=0.08), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    low = compute_size(
        memdb, intent=_intent(realized_vol=0.005), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert high.proposed.continuous_scalar < low.proposed.continuous_scalar
    assert high.final_risk_pct < low.final_risk_pct
    # Rest of the chain is identical (no new multiplier introduced).
    assert high.proposed.tier_amplifier == low.proposed.tier_amplifier
    assert high.proposed.cell_routing_mult == low.proposed.cell_routing_mult
    assert high.proposed.listing_watchdog_mult == low.proposed.listing_watchdog_mult


def test_compute_size_vol_scalar_within_band(memdb: sqlite3.Connection) -> None:
    for rv in (0.001, 0.02, 0.5, 1e-9, 10.0):
        sized = compute_size(
            memdb, intent=_intent(realized_vol=rv), risk_state=_risk_state(),
            portfolio=_portfolio(), now_ts=NOW + 100,
        )
        assert CONT_SCALAR_MIN <= sized.proposed.continuous_scalar <= CONT_SCALAR_MAX


def test_compute_size_no_vol_falls_back_to_strength(memdb: sqlite3.Connection) -> None:
    """When realized_vol is None, the legacy signal_strength ramp is used."""
    from polaris.core.sizing import continuous_scalar

    sized = compute_size(
        memdb, intent=_intent(realized_vol=None, strength=1.4),
        risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.proposed.continuous_scalar == pytest.approx(continuous_scalar(1.4))


def test_compute_size_vol_at_target_is_neutral(memdb: sqlite3.Connection) -> None:
    sized = compute_size(
        memdb, intent=_intent(realized_vol=DEFAULT_TARGET_VOL),
        risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.proposed.continuous_scalar == pytest.approx(1.0)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(rv=st.floats(min_value=1e-6, max_value=5.0))
def test_property_compute_size_vol_aware_bounded(memdb: sqlite3.Connection, rv: float) -> None:
    sized = compute_size(
        memdb, intent=_intent(realized_vol=rv), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert math.isfinite(sized.final_risk_pct)
    assert CONT_SCALAR_MIN <= sized.proposed.continuous_scalar <= CONT_SCALAR_MAX
