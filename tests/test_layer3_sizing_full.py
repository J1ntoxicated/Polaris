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
from polaris.core.regime_fit import regime_fit, regime_scalar
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
    per_symbol_remaining_pct,
    rank_cut_candidates,
    resolve_cluster_id,
    resolve_cut_state,
    resolve_tier_amplifier,
    track_daily_cap,
    track_gross_cap,
    venue_per_symbol_cap,
)
from polaris.core.sizing.schema import per_symbol_equity_pct

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
# T8 — Track C caps (/debate-CONFIRMED b565392: gross 3.0, daily 0.99,
# per-symbol 0.99 on equity). NEW min() SLOT — never a chain multiplier.
# ---------------------------------------------------------------------------


def test_t8_track_c_gross_cap_is_three() -> None:
    """Track C gross cap = 3.0 (buying_power basis, /debate-CONFIRMED)."""
    assert track_gross_cap("C") == pytest.approx(3.0)


def test_t8_track_c_daily_cap_is_099() -> None:
    assert track_daily_cap("C") == pytest.approx(0.99)


def test_t8_track_c_gross_env_override() -> None:
    import os

    os.environ["POLARIS_CAP_TRACK_C_GROSS_PCT"] = "2.5"
    try:
        assert track_gross_cap("C") == pytest.approx(2.5)
    finally:
        del os.environ["POLARIS_CAP_TRACK_C_GROSS_PCT"]


def test_t8_track_c_daily_env_override() -> None:
    import os

    os.environ["POLARIS_CAP_TRACK_C_DAILY_VENUE_PCT"] = "0.5"
    try:
        assert track_daily_cap("C") == pytest.approx(0.5)
    finally:
        del os.environ["POLARIS_CAP_TRACK_C_DAILY_VENUE_PCT"]


def test_t8_per_symbol_equity_pct_default() -> None:
    assert per_symbol_equity_pct() == pytest.approx(0.99)


def test_t8_per_symbol_equity_env_override() -> None:
    import os

    os.environ["POLARIS_CAP_PER_SYMBOL_EQUITY_PCT"] = "0.42"
    try:
        assert per_symbol_equity_pct() == pytest.approx(0.42)
    finally:
        del os.environ["POLARIS_CAP_PER_SYMBOL_EQUITY_PCT"]


def test_t8_venue_per_symbol_cap_equity_branch() -> None:
    """Explicit equity product_class returns the equity per-symbol cap (0.99)."""
    assert venue_per_symbol_cap("alpaca", product_class="equity") == pytest.approx(0.99)


def test_t8_headroom_min_picks_track_c_remaining() -> None:
    """A Track C gross remaining derived from the 3.0 cap participates in the
    single min() clip as the ``track`` SLOT (NOT a multiplier)."""
    track_rem = max(0.0, track_gross_cap("C") - 2.97)  # used 2.97 -> 0.03 left
    val, name = headroom_min(
        proposed_risk_pct=0.12,
        single_trade_cap=0.09,
        per_symbol_remaining=0.50,
        underlying_remaining=0.60,
        cluster_remaining=0.40,
        track_remaining=track_rem,
        venue_daily_remaining=0.08,
        total_daily_remaining=0.10,
    )
    assert val == pytest.approx(0.03)
    assert name == "track"


# --- A/B regression: caps MUST stay exactly as before ----------------------


def test_t8_track_a_b_caps_unchanged() -> None:
    assert track_gross_cap("A") == pytest.approx(0.99)
    assert track_gross_cap("B") == pytest.approx(1.00)
    assert track_daily_cap("A") == pytest.approx(0.99)
    assert track_daily_cap("B") == pytest.approx(0.99)


def test_t8_venue_per_symbol_cap_ab_unchanged() -> None:
    assert venue_per_symbol_cap("okx") == pytest.approx(0.99)  # spot
    assert venue_per_symbol_cap("capital") == pytest.approx(0.99)  # cfd
    assert venue_per_symbol_cap("unknown_venue") == pytest.approx(0.99)  # spot fallback


# --- H1 (Phase 3 T12 review nit): the RUNTIME per-symbol remaining path. ----
# per_symbol_remaining_pct (engine.py:443) calls venue_per_symbol_cap(venue)
# WITHOUT product_class. This must still resolve the equity cap for alpaca via
# resolve_stream(venue).product_class — NOT silently miss to spot/cfd. Distinct
# env caps (spot != cfd != equity) make the branch observable; all-0.99 defaults
# would mask a dead branch. Per-symbol cap is a headroom_min SLOT (no chain
# multiplier); 0.99 equity RAISES headroom (aggressive-consistent).
def test_h1_per_symbol_remaining_alpaca_resolves_equity_cap() -> None:
    import os

    os.environ["POLARIS_CAP_PER_SYMBOL_SPOT_PCT"] = "0.50"
    os.environ["POLARIS_CAP_PER_SYMBOL_CFD_PCT"] = "0.60"
    os.environ["POLARIS_CAP_PER_SYMBOL_EQUITY_PCT"] = "0.99"
    try:
        # Runtime caller path: product_class OMITTED -> resolve via stream SSOT.
        alpaca_rem = per_symbol_remaining_pct(
            venue="alpaca", symbol="AAPL", open_positions=[]
        )
        assert alpaca_rem == pytest.approx(0.99)  # equity, NOT 0.50 spot fallback
        # A/B byte-identical: same omitted-product_class path stays spot/cfd.
        okx_rem = per_symbol_remaining_pct(
            venue="okx", symbol="BTC-USDT", open_positions=[]
        )
        assert okx_rem == pytest.approx(0.50)  # spot — unchanged
        capital_rem = per_symbol_remaining_pct(
            venue="capital", symbol="EURUSD", open_positions=[]
        )
        assert capital_rem == pytest.approx(0.60)  # cfd — unchanged
    finally:
        del os.environ["POLARIS_CAP_PER_SYMBOL_SPOT_PCT"]
        del os.environ["POLARIS_CAP_PER_SYMBOL_CFD_PCT"]
        del os.environ["POLARIS_CAP_PER_SYMBOL_EQUITY_PCT"]


def test_h1_per_symbol_remaining_alpaca_subtracts_open_risk() -> None:
    """The equity cap (0.99) participates in the min() SLOT and is reduced by
    open same-symbol risk — proving the cap is *applied*, not just resolved."""
    pos = PositionRiskState(
        venue="alpaca",
        symbol="AAPL",
        instrument_id="AAPL",
        underlying_group_id="AAPL",
        cluster_id="equity:MEGA_CAP",
        strategy="equity_tsmom",
        track="C",
        signal_strength=1.0,
        open_risk_pct=0.10,
        notional_usd=1_000.0,
        opened_ts=NOW,
    )
    rem = per_symbol_remaining_pct(
        venue="alpaca", symbol="AAPL", open_positions=[pos]
    )
    assert rem == pytest.approx(0.89)  # 0.99 equity cap - 0.10 open


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
    # base 0.02 × cont × tier(1.0) × cell(1.5) × listing(1.0). cont = the ONE
    # continuous scalar folded with regime-fit: continuous_scalar(1.2)=1.2 ×
    # regime_scalar(momentum × bull_trend = +1)=1.5 → 1.8, re-clamped to 1.5.
    cont = min(
        CONT_SCALAR_MAX,
        continuous_scalar(1.2) * regime_scalar(regime_fit("momentum", "bull_trend")),
    )
    assert sized.proposed.cell_routing_mult == 1.5
    assert sized.final_risk_pct == pytest.approx(0.02 * cont * 1.0 * 1.5 * 1.0)
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


def test_t4_weak_signal_flows_when_fill_rate_hot(memdb: sqlite3.Connection) -> None:
    """flow_not_block: a weak signal (strength<1.0) is NOT zeroed when the venue
    daily risk budget would have been 'hot'. It flows at its normal computed size;
    only the headroom_min budget caps may bind. No path zeroes a signal for being
    weak."""
    _seed_top_quartile_cell(memdb)
    weak = SignalIntent(
        signal_id="sig-weak", venue="okx", symbol="PL24-USDT",
        instrument_id="okx:PL24-USDT", underlying_group_id="crypto:PL",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime="bull_trend", direction="long", signal_strength=0.6,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
    )
    # Hot fill-rate: venue budget far below the 99% daily ceiling still leaves
    # headroom, so the budget caps DON'T bind — but fill_rate >= 0.70 used to
    # zero this weak signal. Set used to 80% of an ample track to make the OLD
    # cut fire while keeping per-trade headroom open.
    hot = PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=track_daily_cap("A") * 0.80,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=True,
    )
    sized = compute_size(
        memdb, intent=weak, risk_state=_risk_state(), portfolio=hot, now_ts=NOW + 100,
    )
    # Normal computed size: base 0.02 × cont × tier(1.0) × cell(1.5). cont = the
    # ONE continuous scalar with regime-fit folded in (still the same single
    # scalar — flow_not_block: the weak signal still FLOWS, never zeroed).
    cont = min(
        CONT_SCALAR_MAX,
        continuous_scalar(0.6) * regime_scalar(regime_fit("momentum", "bull_trend")),
    )
    expected = 0.02 * cont * 1.0 * 1.5
    assert sized.final_risk_pct == pytest.approx(expected)
    assert sized.final_risk_pct > 0.0
    assert sized.binding_cap != "fill_rate_cut"
    assert sized.binding_cap == "proposed"


def test_t4_budget_cap_still_binds_when_over_budget(memdb: sqlite3.Connection) -> None:
    """The headroom_min budget caps (the 9-stack containment) still bind when
    genuinely over budget — only the per-signal weak-signal ZEROING is gone."""
    _seed_top_quartile_cell(memdb)
    weak = SignalIntent(
        signal_id="sig-weak2", venue="okx", symbol="PL24-USDT",
        instrument_id="okx:PL24-USDT", underlying_group_id="crypto:PL",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime="bull_trend", direction="long", signal_strength=0.6,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
    )
    # Venue daily budget nearly exhausted → tiny remaining clips via headroom_min.
    venue_cap = track_daily_cap("A")
    over = PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=venue_cap - 0.001,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=True,
    )
    sized = compute_size(
        memdb, intent=weak, risk_state=_risk_state(), portfolio=over, now_ts=NOW + 100,
    )
    assert sized.final_risk_pct == pytest.approx(0.001)
    assert sized.binding_cap == "venue_daily"


def test_t4_binding_detail_exposes_venue_daily_usage(memdb: sqlite3.Connection) -> None:
    """sizing_zero observability (2026-07-10 ghost-row RCA gap-fix): the
    binding constraint's RAW used_pct/cap_pct ride alongside ``binding_cap``
    so a KILL payload shows the actual usage, not just the clipped-to-zero
    ``final_risk_pct``."""
    _seed_top_quartile_cell(memdb)
    weak = SignalIntent(
        signal_id="sig-weak3", venue="okx", symbol="PL24-USDT",
        instrument_id="okx:PL24-USDT", underlying_group_id="crypto:PL",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime="bull_trend", direction="long", signal_strength=0.6,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
    )
    venue_cap = track_daily_cap("A")
    over = PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=venue_cap - 0.001,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=True,
    )
    sized = compute_size(
        memdb, intent=weak, risk_state=_risk_state(), portfolio=over, now_ts=NOW + 100,
    )
    assert sized.binding_cap == "venue_daily"
    assert sized.binding_reason == "venue_daily_headroom_exhausted"
    assert sized.binding_used_pct == pytest.approx(venue_cap - 0.001)
    assert sized.binding_cap_pct == pytest.approx(venue_cap)


def test_t4_binding_detail_reproduces_capital_ghost_row_incident(
    memdb: sqlite3.Connection,
) -> None:
    """Mirrors the 2026-07-10 Capital incident: track massively oversubscribed
    (Σopen_risk_pct=282.91% against a 100% track cap from 56 ghost
    ``position_risk_state`` rows). The KILL payload's ``binding_used_pct`` must
    show the TRUE (un-clamped) overage, not the headroom_min-clamped 0.0."""
    _seed_top_quartile_cell(memdb)
    intent = SignalIntent(
        signal_id="sig-trackb", venue="okx", symbol="PL24-USDT",
        instrument_id="okx:PL24-USDT", underlying_group_id="crypto:PL",
        asset_class="crypto", strategy="volume_burst", track="B",
        regime="bull_trend", direction="long", signal_strength=1.2,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
    )
    over_used = track_gross_cap("B") + 1.8291  # 282.91%-style overage
    portfolio = PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"B": over_used},
        open_positions=[],
        fill_rate_active_cut=False,
    )
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=portfolio, now_ts=NOW + 100,
    )
    assert sized.final_risk_pct == 0.0
    assert sized.binding_cap == "track"
    assert sized.binding_reason == "track_headroom_exhausted"
    assert sized.binding_used_pct == pytest.approx(over_used)
    assert sized.binding_cap_pct == pytest.approx(track_gross_cap("B"))


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


# ---------------------------------------------------------------------------
# Seam1 — regime-fit folds into the ONE T4 continuous scalar (bidirectional)
# ---------------------------------------------------------------------------


def _intent_rf(
    *, regime: str, signal_family: str, strength: float = 1.0,
) -> SignalIntent:
    return SignalIntent(
        signal_id="rf", venue="okx", symbol="RF-USDT",
        instrument_id="okx:RF-USDT", underlying_group_id="crypto:RF",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime=regime, direction="long", signal_strength=strength,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
        signal_family=signal_family,
    )


def test_regime_fit_folds_one_scalar_bidirectional(memdb: sqlite3.Connection) -> None:
    """Good fit boosts, bad fit shrinks — but bad fit is still > 0 (the 0.75
    floor flows, never zeroed). The fold lives entirely in the ONE continuous
    scalar; the learner mults (session/regime/triple_block) are untouched."""
    good = compute_size(
        memdb, intent=_intent_rf(regime="bull_trend", signal_family="momentum"),
        risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    bad = compute_size(
        memdb, intent=_intent_rf(regime="chop", signal_family="momentum"),
        risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    # momentum × chop = -1 → 0.75× shrink; momentum × bull_trend = +1 → 1.5× boost.
    assert bad.final_risk_pct < good.final_risk_pct
    # flow_not_block: the bad-regime signal STILL FLOWS (never a 0 / veto).
    assert bad.final_risk_pct > 0.0
    # The other learner mults are NOT touched by regime-fit (no stacked dampener).
    assert good.proposed.session_mult == bad.proposed.session_mult
    assert good.proposed.regime_mult == bad.proposed.regime_mult
    assert good.proposed.triple_block_mult == bad.proposed.triple_block_mult


def test_regime_fit_reversion_inverts_with_family(memdb: sqlite3.Connection) -> None:
    """The SAME regime is good for one family and bad for the other — proving the
    lean is (family × regime), not a one-directional defensive cut."""
    # chop: bad for momentum (-1, shrink) but GOOD for reversion (+1, boost).
    mom_chop = compute_size(
        memdb, intent=_intent_rf(regime="chop", signal_family="momentum"),
        risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    rev_chop = compute_size(
        memdb, intent=_intent_rf(regime="chop", signal_family="reversion"),
        risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert rev_chop.final_risk_pct > mom_chop.final_risk_pct


def test_signal_family_default_backcompat_unknown_regime(memdb: sqlite3.Connection) -> None:
    """For an UNKNOWN regime the fit is 0 → scalar 1.0× → byte-identical to the
    pre-regime-fit size (no shaping, full flow)."""
    with_family = compute_size(
        memdb, intent=_intent_rf(regime="unknown", signal_family="momentum", strength=1.2),
        risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    # Same intent but the default family path (also "momentum") — identical.
    default = SignalIntent(
        signal_id="rf", venue="okx", symbol="RF-USDT",
        instrument_id="okx:RF-USDT", underlying_group_id="crypto:RF",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime="unknown", direction="long", signal_strength=1.2,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
    )
    default_sized = compute_size(
        memdb, intent=default, risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert with_family.final_risk_pct == default_sized.final_risk_pct
    # And it equals the unshaped continuous_scalar(1.2) path (×cell 1.0 default cell).
    assert with_family.proposed.continuous_scalar == pytest.approx(continuous_scalar(1.2))


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    family=st.sampled_from(["momentum", "reversion"]),
    regime=st.sampled_from(["bull_trend", "bear_trend", "chop", "crisis", "unknown"]),
    strength=st.floats(min_value=0.1, max_value=2.5),
)
def test_property_regime_fit_keeps_9stack_invariant(
    memdb: sqlite3.Connection, family: str, regime: str, strength: float,
) -> None:
    """The multiplier COUNT (factor set) is unchanged — regime-fit only sets the
    VALUE of the single continuous scalar, which stays inside its band; the
    learner mults and the proposed chain remain finite and the scalar never 0."""
    sized = compute_size(
        memdb, intent=_intent_rf(regime=regime, signal_family=family, strength=strength),
        risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert CONT_SCALAR_MIN <= sized.proposed.continuous_scalar <= CONT_SCALAR_MAX
    assert sized.proposed.continuous_scalar > 0.0
    assert math.isfinite(sized.final_risk_pct)
    assert 0.0 <= sized.final_risk_pct <= SINGLE_TRADE_ABSOLUTE_CEILING_PCT


def test_t4_compute_proposed_negative_rejected() -> None:
    with pytest.raises(ValueError):
        compute_proposed(base_risk_pct=-0.01, continuous=1.0, tier_amp=1.0, cell_mult=1.0)


def test_t4_compute_proposed_nan_rejected() -> None:
    with pytest.raises(ValueError):
        compute_proposed(base_risk_pct=float("nan"), continuous=1.0, tier_amp=1.0, cell_mult=1.0)


# ---------------------------------------------------------------------------
# R-pool headroom — pts-classes group E. Spec ⑤
# (prove_then_scale_classes_2026-07-03.md) + the module's own docstring
# (polaris/core/classes/r_pool.py, tests/test_r_pool.py) require BENCH-freed
# R routed to an EARN member to be ADDITIVE headroom (ladder-draw pattern,
# ``cap_base + addon``, folded onto ``single_trade_cap`` BEFORE
# ``headroom_min`` runs) — NEVER a competing min() slot that can clip a
# winning EARN member's size down. ``headroom_min`` itself therefore has no
# r_pool parameter at all; see tests/test_r_pool_engine_wire.py for the
# compute_size-level additive-headroom proof.
# ---------------------------------------------------------------------------


def test_headroom_min_has_no_r_pool_param() -> None:
    """headroom_min's signature has no r_pool slot — the R-pool allocation is
    folded additively onto single_trade_cap by the compute_size call site
    instead of competing here (a min() term can only shrink, never add)."""
    import inspect

    params = set(inspect.signature(headroom_min).parameters)
    assert "r_pool_remaining" not in params


def test_headroom_min_unaffected_by_r_pool_removal() -> None:
    """Ordinary headroom_min behavior (no r_pool involvement) is untouched."""
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


def test_compute_size_no_r_pool_state_byte_identical(memdb: sqlite3.Connection) -> None:
    """compute_size with no bench_freed_usd/track_members (every pre-group-E
    caller) must never bind r_pool and never error — a no-op addition."""
    _seed_top_quartile_cell(memdb)
    intent = _intent()
    assert intent.track == "A"
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW + 100,
    )
    assert math.isfinite(sized.final_risk_pct)
    assert sized.binding_cap != "r_pool"
