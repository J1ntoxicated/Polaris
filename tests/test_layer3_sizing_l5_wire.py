"""Layer 3 ← Layer 5 wire — session/regime/triple_block applied at T4 sizing.

Plan: .claude/plans/p0_l5_l3_sizing_wire.md (10 TDD tests).
Spec: vault/30_components/layer-3-sizing-risk.md Q1 + layer-5-learner-network.md Q2.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.learners.base import (
    LEARNER_INDIVIDUAL_MULT_CLIP,
    LEARNER_PRODUCT_CLIP,
    NEUTRAL_MULT,
    TRIPLE_BLOCK_DURATION_SEC,
    TRIPLE_BLOCK_SIZE_MULT,
)
from polaris.core.sizing.engine import (
    SignalIntent,
    compute_size,
)
from polaris.core.sizing.schema import (
    PortfolioState,
    StrategyRiskState,
)
from polaris.core.sizing.session import derive_session

NOW = 1_715_000_000  # 2024-05-06 09:33:20 UTC → asia/eu overlap territory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent(
    *,
    symbol: str = "BTC-USDT",
    strategy: str = "volume_burst",
    regime: str = "bull_trend",
    base_risk_pct: float = 0.02,
    signal_strength: float = 1.0,
    session: str | None = None,
) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-l5",
        venue="okx",
        symbol=symbol,
        instrument_id=f"okx:{symbol}",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        strategy=strategy,
        track="A",
        regime=regime,
        direction="long",
        signal_strength=signal_strength,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=base_risk_pct,
        session=session,
    )


def _risk_state(n: int = 25) -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx",
        strategy="volume_burst",
        closed_trades=n,
        kelly_p=0.55,
        kelly_q=0.45,
        kelly_fraction=0.05,
        win_streak=0,
        hit_rate_10=0.55,
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


def _seed_learner_value(
    conn: sqlite3.Connection,
    *,
    learner_id: str,
    key: str,
    value: float,
    n_eff: float = 25.0,
    wins_eff: float = 14.0,
) -> None:
    """Seed a learner_state row with a chosen value past the sparse threshold."""
    conn.execute(
        "INSERT INTO learner_state "
        "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
        " pending_delta, updated_at) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, ?)",
        (learner_id, key, value, n_eff, wins_eff, NOW),
    )


def _seed_triple_block(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    strategy_id: str,
    regime: str,
    until_ts: int,
) -> None:
    conn.execute(
        "INSERT INTO learner_blocks "
        "(ticker, strategy_id, regime, size_mult, reason, source_learner, "
        " blocked_until_ts, created_ts) "
        "VALUES (?, ?, ?, ?, 'wr_exp_low', 'regime_mult', ?, ?)",
        (ticker, strategy_id, regime, TRIPLE_BLOCK_SIZE_MULT, until_ts, NOW),
    )


# ---------------------------------------------------------------------------
# Tests (10 — order matches plan TDD list)
# ---------------------------------------------------------------------------


def test_compute_size_no_learner_state(memdb: sqlite3.Connection) -> None:
    """1. Empty learner_state → sparse fallback NEUTRAL_MULT for all three mults."""
    sized = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.session_mult == pytest.approx(NEUTRAL_MULT)
    assert sized.proposed.regime_mult == pytest.approx(NEUTRAL_MULT)
    assert sized.proposed.triple_block_mult == pytest.approx(NEUTRAL_MULT)
    # Sanity: T4 chain identical to pre-wire (base × cont × tier × cell × listing)
    # with NEUTRAL learner product. base=0.02, cont(1.0)=1.0, tier(n=25,ws=0)=1.0,
    # cell(empty)=1.0, listing(72h)=1.0 → 0.02.
    assert sized.final_risk_pct == pytest.approx(0.02)


def test_compute_size_session_mult_promote(memdb: sqlite3.Connection) -> None:
    """2. session_mult value=1.1 (WR≥55% post-commit) → applied to notional."""
    session_label = derive_session(NOW)
    _seed_learner_value(
        memdb, learner_id="session_mult",
        key=f"volume_burst:{session_label}", value=1.1,
    )
    sized = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.session_mult == pytest.approx(1.1)
    # 0.02 × 1.0 × 1.0 × 1.0 × 1.0 × 1.1 = 0.022
    assert sized.final_risk_pct == pytest.approx(0.022)


def test_compute_size_regime_mult_demote(memdb: sqlite3.Connection) -> None:
    """3. regime_mult value=0.9 (WR≤40% post-commit) → applied."""
    _seed_learner_value(
        memdb, learner_id="regime_mult",
        key="volume_burst:bull_trend", value=0.9,
    )
    sized = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.regime_mult == pytest.approx(0.9)
    assert sized.final_risk_pct == pytest.approx(0.02 * 0.9)


def test_compute_size_triple_block_active(memdb: sqlite3.Connection) -> None:
    """4. Active triple block → size_mult=0.3 applied (entry allowed, sized down)."""
    _seed_triple_block(
        memdb, ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", until_ts=NOW + TRIPLE_BLOCK_DURATION_SEC,
    )
    sized = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.triple_block_mult == pytest.approx(TRIPLE_BLOCK_SIZE_MULT)
    assert sized.final_risk_pct == pytest.approx(0.02 * TRIPLE_BLOCK_SIZE_MULT)
    # Critical: entry NOT blocked — final_risk_pct > 0 (aggressive bias preserved).
    assert sized.final_risk_pct > 0.0


def test_compute_size_individual_clip(memdb: sqlite3.Connection) -> None:
    """5. Raw session_mult=10.0 → clipped to LEARNER_INDIVIDUAL_MULT_CLIP[1]=3.0."""
    session_label = derive_session(NOW)
    # _upsert_state already clips on write, so bypass via direct INSERT with
    # raw 10.0 — but BaseLearner.get_mult also re-clips, so the engine sees 3.0.
    memdb.execute(
        "INSERT INTO learner_state "
        "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
        " pending_delta, updated_at) VALUES "
        "('session_mult', ?, 10.0, 25.0, 14.0, 0.0, 0.0, ?)",
        (f"volume_burst:{session_label}", NOW),
    )
    sized = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.session_mult == pytest.approx(LEARNER_INDIVIDUAL_MULT_CLIP[1])


def test_compute_size_product_clip(memdb: sqlite3.Connection) -> None:
    """6. session × regime × block product > LEARNER_PRODUCT_CLIP[1]=5.0 → clipped.

    Each individually clipped to 3.0, raw product = 27.0 → product clip to 5.0.
    """
    session_label = derive_session(NOW)
    # Both learners at top of individual clip (3.0).
    memdb.execute(
        "INSERT INTO learner_state "
        "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
        " pending_delta, updated_at) VALUES "
        "('session_mult', ?, 3.0, 25.0, 18.0, 0.0, 0.0, ?)",
        (f"volume_burst:{session_label}", NOW),
    )
    memdb.execute(
        "INSERT INTO learner_state "
        "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
        " pending_delta, updated_at) VALUES "
        "('regime_mult', 'volume_burst:bull_trend', 3.0, 25.0, 18.0, 0.0, 0.0, ?)",
        (NOW,),
    )
    # triple_block_mult absent (no block row) → 1.0. Raw product = 9.0 → 5.0 cap.
    sized = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    # Final L5 contribution capped at LEARNER_PRODUCT_CLIP[1].
    # Check via final_risk_pct: 0.02 × 1.0 × 1.0 × 1.0 × 1.0 × clipped_product.
    # But hard cap CS3 default = 0.06 may bind first (n_eff in risk_state=25 so
    # Kelly path: kelly_fraction=0.05 × KELLY_FRACTION_K=0.5 → 0.025; min with
    # ABS_CEILING=0.09). Use a low base to expose the L5 product directly.
    sized_lo = compute_size(
        memdb, intent=_intent(base_risk_pct=0.001), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    # raw product 9.0 → clipped 5.0 → 0.001 × 5.0 = 0.005
    assert sized_lo.final_risk_pct == pytest.approx(0.005)
    assert sized.proposed.session_mult == pytest.approx(3.0)
    assert sized.proposed.regime_mult == pytest.approx(3.0)


def test_session_derivation_utc_boundaries() -> None:
    """7. UTC hour → asia/eu/us boundaries with closest-mid-window tiebreak."""
    # 00:00 → asia only
    assert derive_session(0) == "asia"
    # 03:00 → asia only (mid=4)
    assert derive_session(3 * 3600) == "asia"
    # 07:00 → asia(4) vs eu(11.5): |7-4|=3 vs |7-11.5|=4.5 → asia wins
    assert derive_session(7 * 3600) == "asia"
    # 08:00 → eu only (asia [0,8))
    assert derive_session(8 * 3600) == "eu"
    # 11:00 → eu only (us starts at 13)
    assert derive_session(11 * 3600) == "eu"
    # 13:00 → eu(11.5) vs us(17.5): |13-11.5|=1.5 vs |13-17.5|=4.5 → eu
    assert derive_session(13 * 3600) == "eu"
    # 15:00 → eu(11.5) vs us(17.5): |15-11.5|=3.5 vs |15-17.5|=2.5 → us
    assert derive_session(15 * 3600) == "us"
    # 16:00 → us only
    assert derive_session(16 * 3600) == "us"
    # 22:00 → closed band → asia (start of next)
    assert derive_session(22 * 3600) == "asia"
    # 23:59:59 → closed → asia
    assert derive_session(23 * 3600 + 3599) == "asia"


def test_signal_intent_session_default_derived(memdb: sqlite3.Connection) -> None:
    """8. SignalIntent.session=None → derive_session(now_ts) used at sizing time.

    Seed session_mult for the *derived* session only; lookup should hit.
    Use a ts deep in asia band (4 AM UTC).
    """
    asia_ts = (NOW // 86400) * 86400 + 4 * 3600  # 04:00 UTC same day
    assert derive_session(asia_ts) == "asia"
    _seed_learner_value(
        memdb, learner_id="session_mult",
        key="volume_burst:asia", value=1.2,
    )
    sized = compute_size(
        memdb, intent=_intent(session=None), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=asia_ts,
    )
    assert sized.proposed.session_mult == pytest.approx(1.2)


def test_compute_size_disabled_learner_neutral(memdb: sqlite3.Connection) -> None:
    """9. ``enabled=False`` learner → NEUTRAL_MULT fallback (toggle path).

    BaseLearner.enabled is class attribute; flip via subclass-shaped seed (we
    instead test the disabled-path indirectly: even with a strong stored value,
    a disabled learner returns the fallback. We exercise the path by stubbing
    the learner class attribute in a context guard.)
    """
    from polaris.core.learners.session import SessionMultLearner

    session_label = derive_session(NOW)
    _seed_learner_value(
        memdb, learner_id="session_mult",
        key=f"volume_burst:{session_label}", value=1.3,
    )
    # Flip class attr off, run, flip back. (Engine instantiates with disabled.)
    SessionMultLearner.enabled = False
    try:
        sized = compute_size(
            memdb, intent=_intent(), risk_state=_risk_state(),
            portfolio=_portfolio(), now_ts=NOW,
        )
    finally:
        SessionMultLearner.enabled = True
    # Disabled → fallback_value() = NEUTRAL_MULT, ignoring the seeded 1.3.
    assert sized.proposed.session_mult == pytest.approx(NEUTRAL_MULT)


def test_sizing_proposal_audit_fields(memdb: sqlite3.Connection) -> None:
    """10. SizingProposal carries session_mult / regime_mult / triple_block_mult."""
    session_label = derive_session(NOW)
    _seed_learner_value(
        memdb, learner_id="session_mult",
        key=f"volume_burst:{session_label}", value=1.15,
    )
    _seed_learner_value(
        memdb, learner_id="regime_mult",
        key="volume_burst:bull_trend", value=0.95,
    )
    _seed_triple_block(
        memdb, ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", until_ts=NOW + TRIPLE_BLOCK_DURATION_SEC,
    )
    sized = compute_size(
        memdb, intent=_intent(base_risk_pct=0.005), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    # Audit trail present + values match seeded learner state.
    assert sized.proposed.session_mult == pytest.approx(1.15)
    assert sized.proposed.regime_mult == pytest.approx(0.95)
    assert sized.proposed.triple_block_mult == pytest.approx(TRIPLE_BLOCK_SIZE_MULT)
    # And product clip envelope respected.
    product = (
        sized.proposed.session_mult
        * sized.proposed.regime_mult
        * sized.proposed.triple_block_mult
    )
    lo, hi = LEARNER_PRODUCT_CLIP
    assert lo <= product <= hi
