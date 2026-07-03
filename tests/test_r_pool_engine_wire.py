"""pts-classes (group E fix1) — ``allocate_r_pool`` <-> ``compute_size`` wiring.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Aggressive
bias preserved -- capital ROUTING, never a block/reject throttle.

Prior to this fix, ``allocate_r_pool`` (polaris/core/classes/r_pool.py) had
ZERO callers -- nothing fed ``PortfolioState.bench_freed_usd`` /
``track_members`` into ``compute_size``, and the ``r_pool_remaining``
min()-slot was a static ``0.5 x track_gross_cap`` constant that could never
bind (always far above the single_trade slot). This file proves:

  1. ``allocate_r_pool`` is actually invoked from ``compute_size`` (the R-pool
     ALLOCATION result, not just the static half-track-cap ceiling, drives the
     slot -- spec: ``cap = min(alloc, 기존cap, 0.5 x track_R)``).
  2. The allocation for THIS signal's own strategy binds the sizing when it is
     the tightest constraint.
  3. Zero PortfolioState.bench_freed_usd / track_members (every pre-fix
     caller) reproduces the prior byte-identical behavior (r_pool slot never
     the tightest -- min() no-op, non-breaking default).
  4. The PROVE branch's own headroom_min() re-clip also threads the SAME
     r_pool slot (it was omitted entirely pre-fix).
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
    reproduce the prior byte-identical chain -- the r_pool slot must never be
    the binding cap when there is no R-pool state at all."""
    sized = compute_size(
        memdb, intent=_intent(strategy_class="EARN"), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert sized.binding_cap != "r_pool"
    assert sized.final_notional_usd > 0.0


# ---------------------------------------------------------------------------
# allocate_r_pool is actually invoked -- the member's own allocation binds
# ---------------------------------------------------------------------------


def test_earn_members_own_allocation_binds_when_tightest(memdb: sqlite3.Connection) -> None:
    """A tiny bench_freed_usd pool (above the track-A 3x$10 probe-overhead
    floor by a small residual) with a rival EARN sibling in the same track ->
    this signal's strategy only gets a small slice of the residual, and THAT
    allocation (not the static 0.5xtrack_R ceiling) is what clips it."""
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
    # strategy's alloc = 0.10 USD -> far below every other cap -> binds.
    assert sized.binding_cap == "r_pool"
    basis = portfolio.equity_usd * 1.0
    assert sized.final_notional_usd == pytest.approx(10.0 * (1.0 / 100.0), abs=1e-6)
    assert sized.final_notional_usd < basis * 0.001


def test_non_earn_member_gets_zero_allocation_and_binds_at_zero(memdb: sqlite3.Connection) -> None:
    """This signal's own strategy is PROVE-classed in the roster (not EARN) ->
    allocate_r_pool never allocates it a share -> its r_pool slot is 0, and
    (being EARN-routed through compute_size's %-chain here) that zero binds."""
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
    assert sized.binding_cap == "r_pool"
    assert sized.final_notional_usd == 0.0


def test_large_bench_freed_pool_does_not_bind_below_single_trade(memdb: sqlite3.Connection) -> None:
    """A generous bench_freed_usd pool for a lone EARN member reproduces the
    ordinary chain (r_pool alloc >> single_trade cap -> never binds)."""
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
    assert sized.final_notional_usd > 0.0


# ---------------------------------------------------------------------------
# PROVE branch's re-clip also threads the SAME r_pool slot (pre-fix omission)
# ---------------------------------------------------------------------------


def test_prove_probe_notional_also_bound_by_r_pool_slot(memdb: sqlite3.Connection) -> None:
    """PROVE's admitted-probe re-clip (engine.py ~989) must consume the SAME
    r_pool_remaining slot as the outer headroom_min -- pre-fix it silently
    dropped the kwarg entirely on this second call."""
    floor = prove_stop_dist_floor_pct("okx")
    portfolio = _portfolio(
        bench_freed_usd={"A": 30.05},  # probe overhead 3x$10=30 -> residual 0.05
        track_members={
            "A": [TrackMember(strategy_id="volume_burst", strategy_class="EARN", score_f_w=1.0)]
        },
    )
    intent = _intent(strategy_class="PROVE", atr_pct=floor * 10.0, stop_atr_mult=1.0)
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=portfolio, now_ts=NOW + 100,
    )
    # r_pool alloc = 0.05 USD, far below the probe notional floor -> binds.
    assert sized.final_notional_usd == pytest.approx(0.05, abs=1e-6)
