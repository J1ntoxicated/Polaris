"""Layer 3 — position-count uncap (DEMO/PAPER data-collection, AGGRESSIVE bias).

Goal: the structural %-of-equity caps that would indirectly throttle the number
of concurrent open positions are raised to high values (~0.99/1.00) so they no
longer bind at low position counts, and are env-overridable for durability.

Invariant preserved: the T4 single ``headroom_min()`` clip + hard-MAX absolute
single-trade ceiling stay intact (no 9-stack, no extra chain accumulation). We
only widen the cap VALUES, never remove a ``min()`` member.

Spec source:
- vault/30_components/layer-3-sizing-risk.md
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.sizing.cluster_cap import (
    CLUSTER_DEFINITIONS,
    cluster_remaining_pct,
    resolve_cluster_definitions,
)
from polaris.core.sizing.engine import (
    SignalIntent,
    compute_size,
    per_symbol_remaining_pct,
    track_daily_cap,
    track_gross_cap,
    underlying_remaining_pct,
    venue_per_symbol_cap,
)
from polaris.core.sizing.schema import (
    SINGLE_TRADE_ABSOLUTE_CEILING_PCT,
    PortfolioState,
    PositionRiskState,
    StrategyRiskState,
    total_daily_risk_ceiling_pct,
)

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Cap VALUES are high enough to no longer bind at low position counts.
# ---------------------------------------------------------------------------


def test_per_symbol_caps_non_binding_high() -> None:
    # OKX SPOT + Capital CFD per-symbol caps both ~1.0 (data-collection).
    assert venue_per_symbol_cap("okx") >= 0.99
    assert venue_per_symbol_cap("capital") >= 0.99


def test_track_gross_caps_non_binding_high() -> None:
    assert track_gross_cap("A") >= 0.99
    assert track_gross_cap("B") >= 0.99


def test_track_daily_caps_non_binding_high() -> None:
    assert track_daily_cap("A") >= 0.99
    assert track_daily_cap("B") >= 0.99


def test_total_daily_ceiling_non_binding_high() -> None:
    assert total_daily_risk_ceiling_pct() >= 0.99


def test_underlying_default_cap_non_binding_high() -> None:
    # No open positions → full headroom == the (raised) cap.
    rem = underlying_remaining_pct(underlying_group_id="crypto:BTC", open_positions=[])
    assert rem >= 0.99


def test_cluster_caps_non_binding_high() -> None:
    for cap in CLUSTER_DEFINITIONS.values():
        assert cap >= 0.99


# ---------------------------------------------------------------------------
# Many concurrent positions can open: at base 2%/position the previously-tight
# caps (cluster BTC/ETH 40% ≈ 20 positions) no longer block ~30 stacked
# positions. We assert headroom remains positive well past the old limits.
# ---------------------------------------------------------------------------


def _pos(symbol: str, ug: str, cluster: str | None, *, risk: float = 0.02) -> PositionRiskState:
    return PositionRiskState(
        venue="okx",
        symbol=symbol,
        instrument_id=f"okx:{symbol}",
        underlying_group_id=ug,
        cluster_id=cluster,
        strategy="volume_burst",
        track="A",
        signal_strength=1.0,
        open_risk_pct=risk,
        notional_usd=200.0,
        opened_ts=NOW,
    )


def test_cluster_headroom_past_old_40pct_limit() -> None:
    # 25 BTC/ETH positions at 2% = 50% used. Old cluster cap 0.40 would have
    # gone to 0 at 20 positions; raised cap leaves headroom for more.
    open_positions = [
        _pos("BTC-USDT", "crypto:BTC", "crypto:BTC+ETH") for _ in range(25)
    ]
    rem = cluster_remaining_pct(cluster_id="crypto:BTC+ETH", open_positions=open_positions)
    assert rem is not None
    assert rem > 0.0  # would have been 0.0 under the old 0.40 cap


def test_per_symbol_headroom_past_old_50pct_limit() -> None:
    # 30 positions on one symbol at 2% = 60% used. Old SPOT cap 0.50 → 0 at 25.
    open_positions = [_pos("BTC-USDT", "crypto:BTC", None) for _ in range(30)]
    rem = per_symbol_remaining_pct(
        venue="okx", symbol="BTC-USDT", open_positions=open_positions
    )
    assert rem > 0.0  # would have been 0.0 under the old 0.50 cap


def test_underlying_headroom_past_old_60pct_limit() -> None:
    # 35 positions sharing an underlying at 2% = 70% used. Old default 0.60 → 0.
    open_positions = [_pos(f"S{i}-USDT", "crypto:BTC", None) for i in range(35)]
    rem = underlying_remaining_pct(
        underlying_group_id="crypto:BTC", open_positions=open_positions
    )
    assert rem > 0.0


# ---------------------------------------------------------------------------
# Env override.
# ---------------------------------------------------------------------------


def test_env_override_per_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_CAP_PER_SYMBOL_SPOT_PCT", "0.42")
    assert venue_per_symbol_cap("okx") == pytest.approx(0.42)


def test_env_override_total_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_CAP_TOTAL_DAILY_PCT", "0.33")
    assert total_daily_risk_ceiling_pct() == pytest.approx(0.33)


def test_env_override_track_gross(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_CAP_TRACK_A_GROSS_PCT", "0.55")
    assert track_gross_cap("A") == pytest.approx(0.55)


def test_env_override_cluster_btc_eth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_CAP_CLUSTER_BTC_ETH_PCT", "0.41")
    defs = resolve_cluster_definitions()
    assert defs["crypto:BTC+ETH"] == pytest.approx(0.41)


def test_env_override_underlying(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_CAP_UNDERLYING_PCT", "0.50")
    rem = underlying_remaining_pct(underlying_group_id="crypto:BTC", open_positions=[])
    assert rem == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# Structural integrity: hard-MAX single-trade absolute ceiling still clips,
# so widening the count caps did NOT remove the min() backstop.
# ---------------------------------------------------------------------------


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
    )


def test_single_trade_ceiling_still_backstops(memdb: sqlite3.Connection) -> None:
    intent = SignalIntent(
        signal_id="sig",
        venue="okx",
        symbol="HYP-USDT",
        instrument_id="okx:HYP-USDT",
        underlying_group_id="crypto:HYP",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=1.5,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.50,  # absurdly large → must still clip to ceiling
    )
    sized = compute_size(
        memdb,
        intent=intent,
        risk_state=StrategyRiskState(
            venue="okx",
            strategy="volume_burst",
            closed_trades=50,
            kelly_p=0.6,
            kelly_q=0.4,
            kelly_fraction=0.1,
            win_streak=2,
            hit_rate_10=0.6,
            updated_ts=NOW,
        ),
        portfolio=_portfolio(),
        now_ts=NOW + 100,
    )
    assert sized.final_risk_pct <= SINGLE_TRADE_ABSOLUTE_CEILING_PCT
    assert sized.binding_cap == "single_trade"
