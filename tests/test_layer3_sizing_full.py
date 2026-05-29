"""Layer 3 — full T4 sizing tests.

Spec source:
- vault/30_components/layer-3-sizing-risk.md
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.sizing import (
    CONT_SCALAR_MAX,
    CONT_SCALAR_MIN,
    CS3_SINGLE_AMPLIFIED_PCT,
    CS3_SINGLE_DEFAULT_PCT,
    KELLY_FRACTION_K,
    SINGLE_TRADE_ABSOLUTE_CEILING_PCT,
    SINGLE_TRADE_DEFAULT_PCT,
    CutCandidate,
    PortfolioState,
    PositionRiskState,
    SignalIntent,
    StrategyRiskState,
    cluster_remaining_pct,
    cluster_used_pct,
    compute_fill_rate,
    compute_proposed,
    compute_size,
    continuous_scalar,
    headroom_min,
    is_cold_start,
    kelly_fraction,
    kelly_or_cold_start,
    listing_watchdog_mult,
    rank_cut_candidates,
    resolve_cluster_id,
    resolve_cut_state,
    resolve_tier_amplifier,
)

NOW = 1_780_000_000


def _ctx() -> CellContext:
    return CellContext(group="spot_intraday", session="asia", direction="long", liquidity_tier="high")


# ---------------------------------------------------------------------------
# Continuous scalar
# ---------------------------------------------------------------------------


def test_t4_continuous_scalar_bounds() -> None:
    assert continuous_scalar(0.0) == CONT_SCALAR_MIN
    assert continuous_scalar(0.5) == pytest.approx(CONT_SCALAR_MIN)
    assert continuous_scalar(1.0) == pytest.approx(1.0)
    assert continuous_scalar(1.5) == pytest.approx(CONT_SCALAR_MAX)
    assert continuous_scalar(3.0) == CONT_SCALAR_MAX


@given(st.floats(min_value=-100.0, max_value=100.0))
def test_t4_continuous_scalar_always_in_range(x: float) -> None:
    out = continuous_scalar(x)
    assert CONT_SCALAR_MIN <= out <= CONT_SCALAR_MAX


@given(st.sampled_from([float("nan"), float("inf"), float("-inf")]))
def test_t4_continuous_scalar_non_finite(x: float) -> None:
    out = continuous_scalar(x)
    assert CONT_SCALAR_MIN <= out <= CONT_SCALAR_MAX


# ---------------------------------------------------------------------------
# Tier amplifier
# ---------------------------------------------------------------------------


def test_t4_tier_amplifier_3win_8_9_n_75() -> None:
    assert resolve_tier_amplifier(win_streak=3, n_closed=8, hit_rate_10=0.75) == 1.5
    # 8-9 n at 70% = no amp.
    assert resolve_tier_amplifier(win_streak=3, n_closed=8, hit_rate_10=0.70) == 1.0


def test_t4_tier_amplifier_3win_n_high() -> None:
    assert resolve_tier_amplifier(win_streak=3, n_closed=12, hit_rate_10=0.70) == 1.5


def test_t4_tier_amplifier_5win() -> None:
    assert resolve_tier_amplifier(win_streak=5, n_closed=10, hit_rate_10=0.70) == 2.0


def test_t4_tier_amplifier_8win_full() -> None:
    assert resolve_tier_amplifier(win_streak=10, n_closed=15, hit_rate_10=0.80) == 3.0


def test_t4_tier_amplifier_loss_resets() -> None:
    # win_streak=0 means a recent loss reset.
    assert resolve_tier_amplifier(win_streak=0, n_closed=20, hit_rate_10=0.90) == 1.0


# ---------------------------------------------------------------------------
# Cold-Start CS-3
# ---------------------------------------------------------------------------


def test_t4_cold_start_cs3_below_threshold() -> None:
    decision = kelly_or_cold_start(n_closed=5, p=0.6, q=0.4, amplifier_on=False)
    assert decision.cold_start
    assert decision.kelly_fraction == 0.0
    assert decision.single_cap_pct == CS3_SINGLE_DEFAULT_PCT


def test_t4_cold_start_cs3_amplifier_on() -> None:
    decision = kelly_or_cold_start(n_closed=10, p=0.6, q=0.4, amplifier_on=True)
    assert decision.cold_start
    assert decision.single_cap_pct == CS3_SINGLE_AMPLIFIED_PCT


def test_t4_cold_start_cs3_above_threshold_kelly_on() -> None:
    decision = kelly_or_cold_start(n_closed=25, p=0.6, q=0.4, amplifier_on=False)
    assert not decision.cold_start
    assert decision.single_cap_pct == SINGLE_TRADE_DEFAULT_PCT
    # kelly = k * (p - q) = 0.5 * 0.2 = 0.1
    assert decision.kelly_fraction == pytest.approx(0.5 * (0.6 - 0.4))


def test_t4_kelly_k05() -> None:
    assert kelly_fraction(0.7, 0.3) == pytest.approx(KELLY_FRACTION_K * 0.4)


def test_t4_kelly_negative_edge_zero() -> None:
    assert kelly_fraction(0.3, 0.7) == 0.0


def test_t4_kelly_invalid_inputs_zero() -> None:
    assert kelly_fraction(float("nan"), 0.5) == 0.0
    assert kelly_fraction(0.5, float("inf")) == 0.0
    assert kelly_fraction(2.0, 0.5) == 0.0


def test_is_cold_start_threshold() -> None:
    assert is_cold_start(0)
    assert is_cold_start(19)
    assert not is_cold_start(20)


# ---------------------------------------------------------------------------
# Cluster cap
# ---------------------------------------------------------------------------


def test_t4_symbol_cluster_btc_eth() -> None:
    assert resolve_cluster_id(underlying_group_id="crypto:BTC", asset_class="crypto") == "crypto:BTC+ETH"
    assert resolve_cluster_id(underlying_group_id="crypto:ETH", asset_class="crypto") == "crypto:BTC+ETH"
    # SOL not in cluster.
    assert resolve_cluster_id(underlying_group_id="crypto:SOL", asset_class="crypto") is None


def test_t4_symbol_cluster_fx_majors() -> None:
    assert resolve_cluster_id(underlying_group_id="forex:EURUSD", asset_class="forex") == "cfd:FX_MAJORS"
    assert resolve_cluster_id(underlying_group_id="forex:USDJPY", asset_class="forex") == "cfd:FX_MAJORS"
    assert resolve_cluster_id(underlying_group_id="forex:EURGBP", asset_class="forex") is None


def test_t4_symbol_cluster_xau_indices() -> None:
    assert resolve_cluster_id(underlying_group_id="commodity:XAU", asset_class="metal", symbol="XAUUSD") == "cfd:XAU+INDICES"
    assert resolve_cluster_id(underlying_group_id="index:US500", asset_class="indices", symbol="US500") == "cfd:XAU+INDICES"


def test_t4_cluster_used_and_remaining() -> None:
    pos_btc = PositionRiskState(
        venue="okx", symbol="BTC-USDT", instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC", cluster_id="crypto:BTC+ETH",
        strategy="vb", track="A", signal_strength=1.0, open_risk_pct=0.20,
        notional_usd=2000.0, opened_ts=NOW,
    )
    pos_eth = PositionRiskState(
        venue="okx", symbol="ETH-USDT", instrument_id="okx:ETH-USDT",
        underlying_group_id="crypto:ETH", cluster_id="crypto:BTC+ETH",
        strategy="vb", track="A", signal_strength=1.0, open_risk_pct=0.15,
        notional_usd=1500.0, opened_ts=NOW,
    )
    used = cluster_used_pct(cluster_id="crypto:BTC+ETH", open_positions=[pos_btc, pos_eth])
    assert used == pytest.approx(0.35)
    remaining = cluster_remaining_pct(
        cluster_id="crypto:BTC+ETH", open_positions=[pos_btc, pos_eth]
    )
    # BTC/ETH cluster cap raised to 0.99 (DEMO data-collection — count uncap)
    # → remaining = 0.99 - 0.35 = 0.64.
    assert remaining == pytest.approx(0.99 - 0.35)


def test_t4_cluster_unmapped_returns_none() -> None:
    assert cluster_remaining_pct(cluster_id=None, open_positions=[]) is None


# ---------------------------------------------------------------------------
# Fill-rate cut + hysteresis
# ---------------------------------------------------------------------------


def test_t4_fill_rate_compute() -> None:
    assert compute_fill_rate(used_risk_pct=0.05, venue_daily_ceiling_pct=0.08) == pytest.approx(0.625)


def test_t4_fill_rate_cut_70_60_hysteresis() -> None:
    # Below 70% → off.
    assert resolve_cut_state(prev_active=False, fill_rate=0.65) is False
    # At 70% → on.
    assert resolve_cut_state(prev_active=False, fill_rate=0.70) is True
    # Already on, between 60 and 70 → still on.
    assert resolve_cut_state(prev_active=True, fill_rate=0.65) is True
    # Already on, dropped to 0.60 → off.
    assert resolve_cut_state(prev_active=True, fill_rate=0.60) is False


def test_t4_rank_cut_candidates_priority() -> None:
    a = CutCandidate("a", signal_strength=0.5, cell_quartile="mid", is_listing_watch=False, staleness_seconds=10)
    b = CutCandidate("b", signal_strength=0.4, cell_quartile="bottom", is_listing_watch=False, staleness_seconds=10)
    c = CutCandidate("c", signal_strength=0.4, cell_quartile="bottom", is_listing_watch=True, staleness_seconds=20)
    d = CutCandidate("d", signal_strength=0.4, cell_quartile="bottom", is_listing_watch=False, staleness_seconds=30)
    ranked = rank_cut_candidates([a, b, c, d])
    # Lowest signal first, bottom-quartile first, listing first, oldest first.
    assert ranked[0].signal_id == "c"  # 0.4 + bottom + listing
    assert ranked[1].signal_id == "d"  # 0.4 + bottom + not-listing + older
    assert ranked[2].signal_id == "b"  # 0.4 + bottom + not-listing
    assert ranked[3].signal_id == "a"  # 0.5


# ---------------------------------------------------------------------------
# Listing watchdog
# ---------------------------------------------------------------------------


def test_t4_listing_watchdog_mult() -> None:
    assert listing_watchdog_mult(12.0) == 0.5
    assert listing_watchdog_mult(24.0) == 1.0
    assert listing_watchdog_mult(100.0) == 1.0
    assert listing_watchdog_mult(float("nan")) == 1.0


# ---------------------------------------------------------------------------
# Hard-cap headroom min() — single clip (Q5)
# ---------------------------------------------------------------------------


def test_t4_hard_cap_headroom_min_proposed_wins() -> None:
    val, name = headroom_min(
        proposed_risk_pct=0.04,
        single_trade_cap=0.08,
        per_symbol_remaining=0.50,
        underlying_remaining=0.60,
        cluster_remaining=0.40,
        track_remaining=0.60,
        venue_daily_remaining=0.08,
        total_daily_remaining=0.10,
    )
    assert val == pytest.approx(0.04)
    assert name == "proposed"


def test_t4_hard_cap_headroom_min_single_clips() -> None:
    val, name = headroom_min(
        proposed_risk_pct=0.20,
        single_trade_cap=0.08,
        per_symbol_remaining=0.50,
        underlying_remaining=None,
        cluster_remaining=None,
        track_remaining=0.60,
        venue_daily_remaining=0.08,
        total_daily_remaining=0.10,
    )
    assert val == pytest.approx(0.08)
    assert name in ("single_trade", "venue_daily")


def test_t4_hard_cap_headroom_min_cluster_clips() -> None:
    val, name = headroom_min(
        proposed_risk_pct=0.12,
        single_trade_cap=0.09,
        per_symbol_remaining=0.50,
        underlying_remaining=0.60,
        cluster_remaining=0.05,
        track_remaining=0.60,
        venue_daily_remaining=0.08,
        total_daily_remaining=0.10,
    )
    assert val == pytest.approx(0.05)
    assert name == "cluster"


# ---------------------------------------------------------------------------
# Top-level compute_size
# ---------------------------------------------------------------------------


def _seed_top_quartile_cell(conn: sqlite3.Connection) -> None:
    """Build 25-cell pool with PL24 in top quartile."""
    for i in range(25):
        for j in range(25):
            tr = TradeClose(
                key=CellKeyP0("okx", "volume_burst", f"PL{i:02d}-USDT", "bull_trend"),
                context=_ctx(),
                pnl_r=(i - 12) * 0.1,
                won=(i - 12) * 0.1 > 0,
                closed_ts=NOW + j,
            )
            update_on_trade_close(conn, tr)


def _intent(symbol: str = "PL24-USDT") -> SignalIntent:
    return SignalIntent(
        signal_id="sig-x",
        venue="okx",
        symbol=symbol,
        instrument_id=f"okx:{symbol}",
        underlying_group_id="crypto:PL",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=1.2,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
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


def test_t4_compute_size_top_quartile_amplifies(memdb: sqlite3.Connection) -> None:
    _seed_top_quartile_cell(memdb)
    sized = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    # base 0.02 × continuous(1.2)≈1.2 × tier(1.0) × cell(1.5) × listing(1.0) ≈ 0.036
    assert sized.proposed.cell_routing_mult == 1.5
    assert sized.final_risk_pct == pytest.approx(0.02 * 1.2 * 1.0 * 1.5 * 1.0)
    assert sized.final_notional_usd == pytest.approx(sized.final_risk_pct * 10_000.0)


def test_t4_compute_size_cs3_single_cap_floors(memdb: sqlite3.Connection) -> None:
    _seed_top_quartile_cell(memdb)
    # Cold-start n<20 + huge proposed via base=10% should hit CS3 cap 0.06 × ABS_CEILING.
    intent = _intent()
    intent_big = SignalIntent(
        signal_id=intent.signal_id, venue=intent.venue, symbol=intent.symbol,
        instrument_id=intent.instrument_id, underlying_group_id=intent.underlying_group_id,
        asset_class=intent.asset_class, strategy=intent.strategy, track=intent.track,
        regime=intent.regime, direction=intent.direction, signal_strength=1.5,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.10,
    )
    sized = compute_size(
        memdb, intent=intent_big, risk_state=_risk_state(n=5),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    # Single cap = min(CS3_DEFAULT=0.06, ABS_CEILING=0.09) = 0.06
    assert sized.final_risk_pct == pytest.approx(0.06)
    assert sized.binding_cap == "single_trade"


def test_t4_compute_size_finite_and_bounded(memdb: sqlite3.Connection) -> None:
    _seed_top_quartile_cell(memdb)
    sized = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert math.isfinite(sized.final_risk_pct)
    assert sized.final_risk_pct >= 0.0
    assert sized.final_risk_pct <= SINGLE_TRADE_ABSOLUTE_CEILING_PCT


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    base=st.floats(min_value=0.001, max_value=0.20),
    strength=st.floats(min_value=0.1, max_value=2.5),
    n_closed=st.integers(min_value=0, max_value=200),
    win_streak=st.integers(min_value=0, max_value=15),
    hit=st.floats(min_value=0.0, max_value=1.0),
)
def test_property_compute_size_always_finite(
    memdb: sqlite3.Connection, base: float, strength: float, n_closed: int,
    win_streak: int, hit: float,
) -> None:
    sized = compute_size(
        memdb,
        intent=SignalIntent(
            signal_id="x", venue="okx", symbol="HYP-USDT",
            instrument_id="okx:HYP-USDT", underlying_group_id="crypto:HYP",
            asset_class="crypto", strategy="volume_burst", track="A",
            regime="bull_trend", direction="long",
            signal_strength=strength, listing_age_hours=24.0,
            leverage=1.0, base_risk_pct=base,
        ),
        risk_state=StrategyRiskState(
            venue="okx", strategy="volume_burst",
            closed_trades=n_closed, kelly_p=0.5, kelly_q=0.5,
            kelly_fraction=0.0, win_streak=win_streak, hit_rate_10=hit,
            updated_ts=NOW,
        ),
        portfolio=_portfolio(),
        now_ts=NOW + 100,
    )
    assert math.isfinite(sized.final_risk_pct)
    assert 0.0 <= sized.final_risk_pct <= SINGLE_TRADE_ABSOLUTE_CEILING_PCT


def test_t4_compute_proposed_negative_rejected() -> None:
    with pytest.raises(ValueError):
        compute_proposed(base_risk_pct=-0.01, continuous=1.0, tier_amp=1.0, cell_mult=1.0)


def test_t4_compute_proposed_nan_rejected() -> None:
    with pytest.raises(ValueError):
        compute_proposed(base_risk_pct=float("nan"), continuous=1.0, tier_amp=1.0, cell_mult=1.0)
