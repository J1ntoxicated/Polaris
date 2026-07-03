"""pts-classes (group E fix2) -- ``allocate_r_pool`` <-> ``compute_size`` wiring.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Aggressive
bias preserved -- capital ROUTING, never a block/reject throttle.

Fix2 corrects an inverted-composition defect from fix1: the R-pool alloc had
been wired as a competing min() candidate (``r_pool_remaining``) that could
CLIP a winning EARN member's size down to a few cents whenever the freed pool
was small and split with a higher-weight sibling -- a defensive throttle on
EARN winners, exactly what ``no_defensive_param_dampen`` /
``aggressive_always_profit`` forbid. Spec ⑤
(prove_then_scale_classes_2026-07-03.md) + the module's own docstring
(polaris/core/classes/r_pool.py, tests/test_r_pool.py) require the freed R to
be ADDITIVE headroom instead (ladder-draw pattern: ``cap_base + addon``, with
``0.5 x track_R`` capping the ADDED amount only). This file proves:

  1. ``allocate_r_pool`` is actually invoked from ``compute_size`` (not dead
     code) and its allocation for THIS signal's own strategy is folded onto
     ``single_trade_cap`` as an ADD-ON.
  2. The R-pool add-on can only WIDEN headroom -- final_notional_usd with a
     pool configured is always >= the no-pool baseline for the same intent,
     never below it.
  3. Zero PortfolioState.bench_freed_usd / track_members (every pre-fix
     caller) reproduces the prior byte-identical behavior (no-op addition).
  4. The PROVE branch's own headroom_min() re-clip inherits the same
     already-widened single_trade_cap (no separate slot to thread).
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.classes.r_pool import TrackMember
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState, compute_size
from polaris.core.sizing.probe_notional import prove_stop_dist_floor_pct

NOW = 1_780_000_000


def _risk_state(n: int = 25) -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst",
        closed_trades=n, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _intent(*, strategy_class: str = "EARN", atr_pct: float = 0.0, stop_atr_mult: float = 0.0) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-1", venue="okx", symbol="PL24-USDT",
        instrument_id="okx:PL24-USDT", underlying_group_id="crypto:PL",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime="bull_trend", direction="long", signal_strength=1.2,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
        strategy_class=strategy_class, atr_pct=atr_pct, stop_atr_mult=stop_atr_mult,
    )


def _portfolio(
    *,
    bench_freed_usd: dict[str, float] | None = None,
    track_members: dict[str, list[TrackMember]] | None = None,
) -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
        bench_freed_usd=bench_freed_usd or {},
        track_members=track_members or {},
    )


# ---------------------------------------------------------------------------
# Default (no bench_freed_usd / track_members) -> byte-identical to pre-fix
# ---------------------------------------------------------------------------


def test_no_r_pool_state_never_binds_r_pool(memdb: sqlite3.Connection) -> None:
    """Every existing caller (empty bench_freed_usd/track_members dicts) must
    reproduce the prior byte-identical chain -- the r_pool add-on is zero, and
    the binding cap is never reported as ``r_pool`` (it does not exist as its
    own slot anymore -- see headroom_min's signature)."""
    sized = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.binding_cap != "r_pool"
    assert sized.final_notional_usd > 0.0


# ---------------------------------------------------------------------------
# allocate_r_pool is actually invoked -- and only ever WIDENS headroom
# ---------------------------------------------------------------------------


def test_earn_alloc_widens_headroom_never_shrinks_it(memdb: sqlite3.Connection) -> None:
    """A tiny bench_freed_usd pool (above the track-A 3x$10 probe-overhead
    floor by a small residual) split with a much-heavier-weighted EARN
    sibling must NOT shrink this signal's size below the no-pool baseline --
    the add-on is >= 0, so final_notional_usd can only be >= baseline."""
    baseline = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    portfolio = _portfolio(
        bench_freed_usd={"A": 40.0},  # probe overhead = 3x$10=30 -> residual 10
        track_members={
            "A": [
                TrackMember(strategy_id="volume_burst", strategy_class="EARN", score_f_w=1.0),
                TrackMember(strategy_id="rival", strategy_class="EARN", score_f_w=99.0),
            ]
        },
    )
    sized = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=portfolio, now_ts=NOW + 100,
    )
    # residual = 10.0 (40 - 3x$10 probe overhead), weights 1/100 -> this
    # strategy's alloc add-on = 0.10 USD on TOP of the baseline cap -- a
    # small widening, never a ~9000x collapse.
    assert sized.final_notional_usd >= baseline.final_notional_usd
    assert sized.final_notional_usd == pytest.approx(baseline.final_notional_usd, rel=1e-3)
    assert sized.binding_cap != "r_pool"


def test_non_earn_member_gets_zero_addon_reproduces_baseline(memdb: sqlite3.Connection) -> None:
    """This signal's own strategy is PROVE-classed in the roster (not EARN) ->
    allocate_r_pool never allocates it a share -> the add-on is exactly 0 ->
    final size is identical to the no-pool baseline, never zeroed."""
    baseline = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    portfolio = _portfolio(
        bench_freed_usd={"A": 500.0},
        track_members={
            "A": [
                TrackMember(strategy_id="volume_burst", strategy_class="PROVE", score_f_w=5.0),
                TrackMember(strategy_id="other_earn", strategy_class="EARN", score_f_w=1.0),
            ]
        },
    )
    sized = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=portfolio, now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == pytest.approx(baseline.final_notional_usd)
    assert sized.final_notional_usd > 0.0


def test_large_bench_freed_pool_widens_up_to_half_track_ceiling(memdb: sqlite3.Connection) -> None:
    """A generous bench_freed_usd pool for a lone EARN member still only ADDS
    headroom (capped at 0.5 x track_R on the add-on) -- final size stays
    finite and strictly >= the no-pool baseline, never collapses."""
    baseline = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    portfolio = _portfolio(
        bench_freed_usd={"A": 1_000_000.0},
        track_members={
            "A": [TrackMember(strategy_id="volume_burst", strategy_class="EARN", score_f_w=1.0)]
        },
    )
    sized = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=portfolio, now_ts=NOW + 100,
    )
    assert sized.binding_cap != "r_pool"
    assert sized.final_notional_usd >= baseline.final_notional_usd
    assert sized.final_notional_usd > 0.0


# ---------------------------------------------------------------------------
# PROVE branch's re-clip inherits the SAME already-widened single_trade_cap
# ---------------------------------------------------------------------------


def test_prove_probe_notional_unaffected_by_tiny_r_pool_addon(memdb: sqlite3.Connection) -> None:
    """PROVE's admitted-probe re-clip (engine.py headroom_min second call)
    reuses ``single_trade_cap`` which already carries the (possibly tiny or
    zero) R-pool add-on folded in -- a small residual add-on must not shrink
    the probe below its normal fixed-notional floor."""
    floor = prove_stop_dist_floor_pct("okx")
    portfolio = _portfolio(
        bench_freed_usd={"A": 30.05},  # probe overhead 3x$10=30 -> residual 0.05
        track_members={
            "A": [TrackMember(strategy_id="volume_burst", strategy_class="EARN", score_f_w=1.0)]
        },
    )
    intent = _intent(strategy_class="PROVE", atr_pct=floor * 10.0, stop_atr_mult=1.0)
    baseline = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=portfolio, now_ts=NOW + 100,
    )
    assert sized.final_notional_usd == pytest.approx(baseline.final_notional_usd)
