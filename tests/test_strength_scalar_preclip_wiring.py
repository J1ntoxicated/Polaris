"""TDD — engine.compute_size wiring for fold_strength_scalar (blocker fix).

DEMO/PAPER only. Aggressive bias preserved (flow_not_block). 9-stack ban:
this test structurally proves the (1b) regime-fit / (1c) judge_conviction /
(1d) strength_scalar folds all combine into ONE pre-clip product with a
SINGLE clamp at the end — not fold_strength_scalar() being handed an
already-clamped intermediate (Wave B R2 BREAK #2 pathology).

Spec source: vault/50_research/debates/waveB_sizing_params_2026-07-02.md
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState, compute_size
from polaris.core.sizing.schema import CONT_SCALAR_MAX

NOW = 1_900_000_000


def _intent(strength_scalar: float) -> SignalIntent:
    # momentum x bull_trend -> regime_fit=+1.0 -> regime_scalar raw = 1.5 (ceiling
    # saturating if clamped here). signal_strength picked so the pre-regime cont
    # is comfortably inside the band (continuous_scalar(1.0) == 1.0, see schema).
    return SignalIntent(
        signal_id="sig-preclip",
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=1.0,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
        signal_family="momentum",  # (momentum, trend) fit = +1.0 -> regime_scalar raw 1.5
        judge_conviction=1.12,  # pushes the pre-clip product further past 1.5
        strength_scalar=strength_scalar,
    )


def _risk_state() -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst", closed_trades=25,
        kelly_p=0.55, kelly_q=0.45, kelly_fraction=0.05,
        win_streak=0, hit_rate_10=0.5, updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[],
        fill_rate_active_cut=False,
    )


def test_strength_scalar_folds_the_true_preclip_product_not_a_saturated_intermediate(
    memdb: sqlite3.Connection,
) -> None:
    """(1b) regime-fit and (1c) judge_conviction push the RAW pre-clip product to
    1.0 * 1.5 * 1.12 = 1.68, well past CONT_SCALAR_MAX=1.5. A sub-1.0
    strength_scalar folded onto that TRUE raw product (spec-correct) must land
    at clamp(1.68 * 0.6) = 1.008 -- NOT clamp(clamp(1.68)=1.5) * 0.6 = 0.9,
    which is what a caller gets if it clamps at (1b)/(1c) before handing the
    ALREADY-clamped value to fold_strength_scalar at (1d)."""
    sized = compute_size(
        memdb, intent=_intent(0.6), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.continuous_scalar == pytest.approx(1.008, abs=1e-6)
    # The buggy (clamp-then-multiply) value must NOT appear.
    assert sized.proposed.continuous_scalar != pytest.approx(0.9, abs=1e-6)


def test_strength_scalar_one_clamp_ceiling_still_binds(memdb: sqlite3.Connection) -> None:
    """A strength_scalar >= 1.0 on an already-past-ceiling raw product still
    clamps to CONT_SCALAR_MAX exactly once (no double-clamp drift)."""
    sized = compute_size(
        memdb, intent=_intent(1.3), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.continuous_scalar == CONT_SCALAR_MAX


def test_strength_scalar_noop_default_byte_identical(memdb: sqlite3.Connection) -> None:
    """strength_scalar=1.0 (default / absent) must be byte-identical to the
    pre-agenda-② (1b)+(1c)-only chain: clamp(1.68 * 1.0) = clamp(1.68) = 1.5."""
    sized = compute_size(
        memdb, intent=_intent(1.0), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.continuous_scalar == CONT_SCALAR_MAX
