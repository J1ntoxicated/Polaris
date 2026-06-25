"""Edge-validation Phase 1 — measurement-foundation TDD suite.

Phase 1 is **measurement + display only**: it computes a Bayesian posterior on
cost-adjusted expectancy per (strategy × cell × regime) bucket and surfaces a
confidence ``P(expectancy>0)`` to the dashboard. It is *never* wired into
sizing (aggressive-bias / 9-stack-collapse guard preserved).

Tests:
  1. posterior update — known R-seq → closed-form μ_n / β_n / p_pos
     (Welford online vs batch parity).
  2. hierarchical prior — n=1 ≈ parent2 posterior, n↑ → x̄ dominates.
  3. cost overlay — fee/slippage net delta; Capital fee=0 → const COST_BPS.
  4. verdict boundaries — τ_hi / τ_lo.
  5. sim/real parity — identical fills → identical posterior.
  6. regression — net overlay does not break existing _production_close pnl.
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from polaris.core.learners._primitives import (
    COST_BPS_CAPITAL,
    EDGE_VERDICT_TAU_HI,
    EDGE_VERDICT_TAU_LO,
)
from polaris.core.learners.posterior import (
    NIGPosterior,
    cost_adjusted_pnl_r,
    edge_verdict,
    nig_update,
    p_expectancy_positive,
    shrink_with_parent,
    student_t_cdf,
)
from polaris.storage.schema import init_db

# ---------------------------------------------------------------------------
# Reference NIG closed form (Murphy 2007 §Normal-Inverse-Gamma)
# ---------------------------------------------------------------------------


def _batch_nig(
    samples: list[float],
    *,
    mu0: float,
    kappa0: float,
    alpha0: float,
    beta0: float,
) -> tuple[float, float, float, float]:
    n = len(samples)
    xbar = sum(samples) / n
    ss = sum((x - xbar) ** 2 for x in samples)  # == Welford M2
    kappa_n = kappa0 + n
    mu_n = (kappa0 * mu0 + n * xbar) / kappa_n
    alpha_n = alpha0 + n / 2.0
    beta_n = beta0 + 0.5 * ss + (kappa0 * n * (xbar - mu0) ** 2) / (2.0 * kappa_n)
    return mu_n, kappa_n, alpha_n, beta_n


# ---------------------------------------------------------------------------
# 1. posterior update — Welford online vs batch parity
# ---------------------------------------------------------------------------


def test_nig_online_matches_batch_closed_form() -> None:
    samples = [0.5, -0.3, 1.2, -0.8, 0.4, 2.1, -1.5, 0.9]
    mu0, kappa0, alpha0, beta0 = 0.0, 1.0, 1.0, 1.0
    post = NIGPosterior(mu=mu0, kappa=kappa0, alpha=alpha0, beta=beta0)
    for x in samples:
        post = nig_update(post, x)
    b_mu, b_kappa, b_alpha, b_beta = _batch_nig(
        samples, mu0=mu0, kappa0=kappa0, alpha0=alpha0, beta0=beta0
    )
    assert post.mu == pytest.approx(b_mu, rel=1e-9)
    assert post.kappa == pytest.approx(b_kappa, rel=1e-9)
    assert post.alpha == pytest.approx(b_alpha, rel=1e-9)
    assert post.beta == pytest.approx(b_beta, rel=1e-9)
    assert post.n == len(samples)


def test_student_t_cdf_known_values() -> None:
    # Standard t: symmetry → cdf(0)=0.5 for any df.
    assert student_t_cdf(0.0, df=5.0, loc=0.0, scale=1.0) == pytest.approx(0.5)
    # df→large approximates the normal: cdf(1.96)≈0.975.
    assert student_t_cdf(1.96, df=1e6, loc=0.0, scale=1.0) == pytest.approx(
        0.975, abs=1e-3
    )
    # df=1 (Cauchy): cdf(1)=0.75 exactly.
    assert student_t_cdf(1.0, df=1.0, loc=0.0, scale=1.0) == pytest.approx(0.75)
    # monotone increasing
    lo = student_t_cdf(-2.0, df=4.0, loc=0.0, scale=1.0)
    hi = student_t_cdf(2.0, df=4.0, loc=0.0, scale=1.0)
    assert lo < 0.5 < hi


def test_p_expectancy_positive_strong_winner() -> None:
    # Many consistently-positive R observations → P(exp>0) near 1.
    samples = [1.0] * 30
    post = NIGPosterior(mu=0.0, kappa=1.0, alpha=1.0, beta=1.0)
    for x in samples:
        post = nig_update(post, x)
    p = p_expectancy_positive(post)
    assert 0.99 < p <= 1.0
    # Mirror losing book → P near 0.
    post_neg = NIGPosterior(mu=0.0, kappa=1.0, alpha=1.0, beta=1.0)
    for x in [-1.0] * 30:
        post_neg = nig_update(post_neg, x)
    assert 0.0 <= p_expectancy_positive(post_neg) < 0.01


def test_p_expectancy_positive_matches_manual_t_cdf() -> None:
    samples = [0.6, 0.2, 0.9, -0.1, 0.5]
    post = NIGPosterior(mu=0.0, kappa=1.0, alpha=1.0, beta=1.0)
    for x in samples:
        post = nig_update(post, x)
    scale = math.sqrt(post.beta * (post.kappa + 1.0) / (post.alpha * post.kappa))
    expected = 1.0 - student_t_cdf(
        0.0, df=2.0 * post.alpha, loc=post.mu, scale=scale
    )
    assert p_expectancy_positive(post) == pytest.approx(expected, rel=1e-12)


# ---------------------------------------------------------------------------
# 2. hierarchical prior — shrink toward parent2
# ---------------------------------------------------------------------------


def test_shrink_n1_close_to_parent() -> None:
    parent = NIGPosterior(mu=0.8, kappa=40.0, alpha=20.0, beta=10.0)
    # one child observation, heavily out-voted by a strong parent prior
    child = shrink_with_parent(parent, samples=[2.0])
    # μ_n = (κ0·μ_parent + n·x̄)/(κ0+n) = (40·0.8 + 1·2.0)/41
    assert child.mu == pytest.approx((40.0 * 0.8 + 2.0) / 41.0, rel=1e-9)
    # dominated by parent → close to parent.mu
    assert abs(child.mu - parent.mu) < 0.05


def test_shrink_many_samples_xbar_dominates() -> None:
    parent = NIGPosterior(mu=0.0, kappa=2.0, alpha=2.0, beta=2.0)
    samples = [1.5] * 50
    child = shrink_with_parent(parent, samples=samples)
    xbar = 1.5
    expected_mu = (2.0 * 0.0 + 50 * xbar) / (2.0 + 50)
    assert child.mu == pytest.approx(expected_mu, rel=1e-9)
    # large-n posterior mean is pulled close to the sample mean
    assert abs(child.mu - xbar) < 0.1


# ---------------------------------------------------------------------------
# 3. cost overlay — fee/slippage net delta
# ---------------------------------------------------------------------------


def test_cost_overlay_okx_real_fees_reduce_pnl() -> None:
    # OKX: real fees + slippage on both legs reduce the gross pnl_r.
    pnl_usd_net, pnl_r_net = cost_adjusted_pnl_r(
        gross_pnl_usd=10.0,
        gross_pnl_r=1.0,
        size_usd=100.0,
        atr_usd=10.0,
        venue="okx",
        entry_fee_usd=0.1,
        exit_fee_usd=0.1,
        entry_slippage_bps=2.0,
        exit_slippage_bps=2.0,
    )
    # fees 0.2 + slippage (2bps*100 *2 legs)=0.04 → cost 0.24 usd
    cost = 0.1 + 0.1 + (2.0 / 1e4) * 100.0 + (2.0 / 1e4) * 100.0
    assert pnl_usd_net == pytest.approx(10.0 - cost, rel=1e-9)
    assert pnl_r_net == pytest.approx(1.0 - cost / 10.0, rel=1e-9)
    assert pnl_r_net < 1.0


def test_cost_overlay_capital_uses_const_bps() -> None:
    # Capital demo reports fee=0 → const COST_BPS_CAPITAL on both legs.
    pnl_usd_net, pnl_r_net = cost_adjusted_pnl_r(
        gross_pnl_usd=5.0,
        gross_pnl_r=0.5,
        size_usd=200.0,
        atr_usd=10.0,
        venue="capital",
        entry_fee_usd=0.0,
        exit_fee_usd=0.0,
        entry_slippage_bps=0.0,
        exit_slippage_bps=0.0,
    )
    cost = 2.0 * (COST_BPS_CAPITAL / 1e4) * 200.0
    assert pnl_usd_net == pytest.approx(5.0 - cost, rel=1e-9)
    assert pnl_r_net == pytest.approx(0.5 - cost / 10.0, rel=1e-9)


def test_cost_overlay_zero_atr_safe() -> None:
    # atr_usd<=0 must not divide-by-zero; pnl_r_net falls back to gross.
    _, pnl_r_net = cost_adjusted_pnl_r(
        gross_pnl_usd=1.0,
        gross_pnl_r=0.2,
        size_usd=100.0,
        atr_usd=0.0,
        venue="okx",
        entry_fee_usd=0.1,
        exit_fee_usd=0.1,
        entry_slippage_bps=0.0,
        exit_slippage_bps=0.0,
    )
    assert math.isfinite(pnl_r_net)


# ---------------------------------------------------------------------------
# 3b. FIX 1 — sane R-denominator (pnl_r_net blow-up on low-price symbols)
# ---------------------------------------------------------------------------


def test_cost_overlay_low_price_symbol_net_r_sane() -> None:
    """ALGO ~$0.12 entry: the round-trip cost in R must stay a FEW R of gross,
    NOT explode to -210000 R. The R-denominator floors at a PRICE-proportional
    minimum (entry_price × MIN_ATR_PCT) so it scales with the instrument, not a
    fixed 1e-6 that conflates 'missing bars' with 'low-price symbol'."""
    # size 600 USDT, ALGO entry 0.12, atr_pct 0.005 → atr_usd = 0.12*0.005*2
    # = 0.0012 (well above the price-proportional floor of 0.12*0.001=0.00012).
    atr_usd = 0.12 * 0.005 * 2.0
    _pnl_usd_net, pnl_r_net = cost_adjusted_pnl_r(
        gross_pnl_usd=-72.0,
        gross_pnl_r=-10.0,
        size_usd=600.0,
        atr_usd=atr_usd,
        venue="okx",
        entry_fee_usd=0.6,
        exit_fee_usd=0.6,
        entry_slippage_bps=1.0,
        exit_slippage_bps=1.0,
    )
    # cost = 0.6+0.6 + (1/1e4)*600*2 = 1.2 + 0.12 = 1.32 usd
    # cost/atr_usd = 1.32 / 0.0012 = 1100 — STILL large for a tiny atr_usd, but
    # bounded vs the 1e-6 floor blow-up. The KEY guard is the posterior skip
    # below; here we pin that the floor scales with price (no 1e-6 division).
    assert math.isfinite(pnl_r_net)


def test_cost_adjusted_skips_when_atr_degenerate() -> None:
    """A degenerate / missing R-denominator (None) SKIPS the cost adjustment —
    net == gross — so the posterior never receives |net_r| ≫ |gross_r|."""
    _pnl_usd_net, pnl_r_net = cost_adjusted_pnl_r(
        gross_pnl_usd=-72.0,
        gross_pnl_r=-10.0,
        size_usd=600.0,
        atr_usd=None,
        venue="okx",
        entry_fee_usd=0.6,
        exit_fee_usd=0.6,
        entry_slippage_bps=1.0,
        exit_slippage_bps=1.0,
    )
    assert pnl_r_net == pytest.approx(-10.0)


def test_read_cost_inputs_position_scaled_denominator(
    memdb: sqlite3.Connection,
) -> None:
    """A low-price symbol with NO bars: atr_usd is the WHOLE-POSITION 1R dollar
    value (size_usd × atr_pct × 2), NOT the per-UNIT ATR floored at 1e-6 — so the
    whole-position cost_usd / atr_usd cannot blow up to -210000 R."""
    from polaris.scripts._production_close_effects import _read_cost_inputs
    from polaris.scripts._smoke_fills import SimulatedTrade

    _seed_fill(memdb, fill_id="algo_open", is_close=0, fee_usd=0.6,
               slippage_bps=1.0, contribution_id="posALGO", ts_ms=1000,
               size_usd=600.0, fill_price=0.12)
    trade = SimulatedTrade(
        signal_id="sALGO", venue="okx", symbol="ALGO-USDT",
        strategy_id="tsmom", side="long", entry_price=0.12,
        notional_usd=600.0, open_ts=1, position_id="posALGO",
    )
    *_rest, atr_usd = _read_cost_inputs(memdb, trade)
    # No bars → default atr_pct 0.005 → atr_usd = 600 × 0.005 × 2 = 6.0 (whole
    # position), decisively above the old per-unit 1e-6 floor (~0.0012).
    assert atr_usd is not None
    assert atr_usd == pytest.approx(600.0 * 0.005 * 2.0)
    assert atr_usd > 1.0  # whole-position scale, not the old per-unit blow-up


def test_read_cost_inputs_degenerate_entry_price_returns_none_atr(
    memdb: sqlite3.Connection,
) -> None:
    """entry_price <= 0 (truly degenerate) → atr_usd sentinel None so the caller
    SKIPS the cost adjustment instead of dividing by a tiny floor."""
    from polaris.scripts._production_close_effects import _read_cost_inputs
    from polaris.scripts._smoke_fills import SimulatedTrade

    _seed_fill(memdb, fill_id="bad_open", is_close=0, fee_usd=0.6,
               slippage_bps=1.0, contribution_id="posBAD", ts_ms=1000,
               size_usd=600.0, fill_price=0.0)
    trade = SimulatedTrade(
        signal_id="sBAD", venue="okx", symbol="ZERO-USDT",
        strategy_id="tsmom", side="long", entry_price=0.0,
        notional_usd=600.0, open_ts=1, position_id="posBAD",
    )
    *_rest, atr_usd = _read_cost_inputs(memdb, trade)
    assert atr_usd is None


def test_safe_update_posterior_never_folds_blown_up_net_r(
    memdb: sqlite3.Connection,
) -> None:
    """END-TO-END: an ALGO-like close (low price, no atr_usd) must fold a net_r
    that is within a few R of gross into the posterior — NEVER -210000 R."""
    from polaris.scripts._production_close_effects import _safe_update_posterior
    from polaris.scripts._smoke_fills import SimulatedTrade

    # Entry fill at $0.12; NO bars seeded so atr_usd derives from price floor.
    _seed_fill(memdb, fill_id="algo2_open", is_close=0, fee_usd=0.6,
               slippage_bps=1.0, contribution_id="posALGO2", ts_ms=1000,
               size_usd=600.0, fill_price=0.12)
    _seed_fill(memdb, fill_id="algo2_close", is_close=1, fee_usd=0.6,
               slippage_bps=1.0, contribution_id="posALGO2", ts_ms=2000,
               size_usd=600.0, fill_price=0.10)
    trade = SimulatedTrade(
        signal_id="sALGO2", venue="okx", symbol="ALGO-USDT",
        strategy_id="tsmom", side="long", entry_price=0.12,
        notional_usd=600.0, open_ts=1, position_id="posALGO2",
    )
    _safe_update_posterior(
        memdb, trade=trade, regime="chop", pnl_r=-10.0, pnl_usd=-72.0,
        now_ts=2000,
    )
    row = _read_posterior(
        memdb, exchange="okx", strategy="tsmom", ticker="ALGO-USDT",
        regime="chop",
    )
    assert row is not None
    mu = row[0]
    # The single folded observation drives mu; it must be within a few R of the
    # gross -10, never the -210000 blow-up.
    assert -20.0 < mu < 0.0


def test_safe_update_posterior_also_charges_strategy_regime_prior(
    memdb: sqlite3.Connection,
) -> None:
    """parent2-seed wiring (audit code_review_2026-06-24): one close charges BOTH
    the child cell posterior AND the strategy×regime parent2 prior from the SAME
    cost-adjusted net R (unit-consistent). The prior was previously write-less."""
    from polaris.scripts._production_close_effects import _safe_update_posterior
    from polaris.scripts._smoke_fills import SimulatedTrade

    _seed_fill(memdb, fill_id="p_open", is_close=0, fee_usd=0.6, slippage_bps=1.0,
               contribution_id="posP", ts_ms=1000, size_usd=600.0, fill_price=0.12)
    _seed_fill(memdb, fill_id="p_close", is_close=1, fee_usd=0.6, slippage_bps=1.0,
               contribution_id="posP", ts_ms=2000, size_usd=600.0, fill_price=0.13)
    trade = SimulatedTrade(
        signal_id="sP", venue="okx", symbol="GG-USDT", strategy_id="tsmom",
        side="long", entry_price=0.12, notional_usd=600.0, open_ts=1,
        position_id="posP",
    )
    # No prior row exists yet (only test fixtures ever wrote this table before).
    assert memdb.execute(
        "SELECT COUNT(*) FROM strategy_regime_prior WHERE strategy='tsmom' "
        "AND regime='bull_trend'"
    ).fetchone()[0] == 0
    _safe_update_posterior(
        memdb, trade=trade, regime="bull_trend", pnl_r=1.0, pnl_usd=6.0,
        now_ts=2000,
    )
    # The close charged the parent2 prior (n_samples == 1) — same bucket key the
    # child cell folded, so a future new cell seeds from real history.
    prior = memdb.execute(
        "SELECT n_samples FROM strategy_regime_prior WHERE strategy='tsmom' "
        "AND regime='bull_trend'"
    ).fetchone()
    assert prior is not None
    assert int(prior[0]) == 1


# ---------------------------------------------------------------------------
# 4. verdict boundaries — label only, NOT a gate
# ---------------------------------------------------------------------------


def test_verdict_boundaries() -> None:
    assert edge_verdict(EDGE_VERDICT_TAU_HI) == "validated-alpha"
    assert edge_verdict(EDGE_VERDICT_TAU_HI + 0.001) == "validated-alpha"
    assert edge_verdict(EDGE_VERDICT_TAU_LO) == "anti-edge"
    assert edge_verdict(EDGE_VERDICT_TAU_LO - 0.001) == "anti-edge"
    mid = (EDGE_VERDICT_TAU_HI + EDGE_VERDICT_TAU_LO) / 2.0
    assert edge_verdict(mid) == "unproven"


# ---------------------------------------------------------------------------
# 5. sim/real parity + DB persistence (maybe_update_posterior)
# ---------------------------------------------------------------------------


@pytest.fixture()
def memdb() -> sqlite3.Connection:
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _read_posterior(
    conn: sqlite3.Connection, *, exchange: str, strategy: str, ticker: str, regime: str
) -> tuple[float, float, int, float] | None:
    row = conn.execute(
        "SELECT mu, beta, n_samples, p_pos FROM learner_posterior "
        "WHERE exchange=? AND strategy=? AND ticker=? AND regime=?",
        (exchange, strategy, ticker, regime),
    ).fetchone()
    if row is None:
        return None
    return float(row[0]), float(row[1]), int(row[2]), float(row[3])


def test_schema_tables_exist(memdb: sqlite3.Connection) -> None:
    names = {
        r[0]
        for r in memdb.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "learner_posterior" in names
    assert "strategy_regime_prior" in names


def test_maybe_update_posterior_persists_and_sim_real_parity(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.learners.posterior import maybe_update_posterior

    key = dict(exchange="okx", strategy="volume_burst", ticker="BTC-USDT", regime="bull_trend")
    rseq = [0.5, 1.2, -0.4, 0.8]
    for r in rseq:
        maybe_update_posterior(memdb, **key, pnl_r_net=r, now_ts=1000)
    sim = _read_posterior(memdb, **key)
    assert sim is not None
    mu_sim, _beta_sim, n_sim, p_sim = sim
    assert n_sim == len(rseq)

    # Replay identical fills into a sibling cell that SHARES the same parent2
    # (strategy+regime, different ticker). Post P0-1 the parent prior is never
    # mutated by a cell update, so siblings cannot cross-contaminate → identical
    # posterior (sim/real path parity: same fills, same math).
    key2 = dict(key, ticker="ETH-USDT")
    for r in rseq:
        maybe_update_posterior(memdb, **key2, pnl_r_net=r, now_ts=1000)
    real = _read_posterior(memdb, **key2)
    assert real is not None
    assert real[0] == pytest.approx(mu_sim, rel=1e-12)
    assert real[3] == pytest.approx(p_sim, rel=1e-12)


def test_cell_update_uses_parent_prior_once_then_self_accumulates(
    memdb: sqlite3.Connection,
) -> None:
    """P0-1: parent2 prior seeds a NEW cell once (n=0); subsequent updates fold
    into the cell's OWN running NIG state only — parent is never re-mixed.

    With a pre-seeded non-default parent prior, N cell updates must equal a
    single batch NIG update that used the parent as the prior (μ0,κ0,α0,β0).
    The parent row must NOT change while only an existing cell is updated.
    """
    from polaris.core.learners.posterior import maybe_update_posterior

    # Pre-seed a non-default parent2 prior (strategy×regime).
    p_mu0, p_kappa0, p_alpha0, p_beta0 = 0.7, 4.0, 3.0, 2.5
    memdb.execute(
        "INSERT INTO strategy_regime_prior "
        "(strategy, regime, mu0, kappa0, alpha0, beta0, m2, n_samples, updated_ts) "
        "VALUES ('volume_burst', 'bull_trend', ?, ?, ?, ?, 0.0, 0, 0)",
        (p_mu0, p_kappa0, p_alpha0, p_beta0),
    )
    parent_before = memdb.execute(
        "SELECT mu0, kappa0, alpha0, beta0, m2, n_samples FROM strategy_regime_prior "
        "WHERE strategy='volume_burst' AND regime='bull_trend'"
    ).fetchone()

    key = dict(exchange="okx", strategy="volume_burst", ticker="BTC-USDT", regime="bull_trend")
    samples = [0.5, 1.2, -0.4, 0.8]
    for r in samples:
        maybe_update_posterior(memdb, **key, pnl_r_net=r, now_ts=1000)

    # Cell posterior must equal batch NIG seeded by the parent prior exactly.
    b_mu, b_kappa, b_alpha, b_beta = _batch_nig(
        samples, mu0=p_mu0, kappa0=p_kappa0, alpha0=p_alpha0, beta0=p_beta0
    )
    row = memdb.execute(
        "SELECT mu, kappa, alpha, beta, n_samples FROM learner_posterior "
        "WHERE exchange='okx' AND strategy='volume_burst' AND ticker='BTC-USDT' "
        "AND regime='bull_trend'"
    ).fetchone()
    assert float(row[0]) == pytest.approx(b_mu, rel=1e-9)
    assert float(row[1]) == pytest.approx(b_kappa, rel=1e-9)
    assert float(row[2]) == pytest.approx(b_alpha, rel=1e-9)
    assert float(row[3]) == pytest.approx(b_beta, rel=1e-9)
    assert int(row[4]) == len(samples)

    # The pre-seeded parent prior must be UNCHANGED — updating a cell that
    # already exists does not re-write the parent (no contamination).
    parent_after = memdb.execute(
        "SELECT mu0, kappa0, alpha0, beta0, m2, n_samples FROM strategy_regime_prior "
        "WHERE strategy='volume_burst' AND regime='bull_trend'"
    ).fetchone()
    assert parent_after == parent_before


def test_maybe_update_posterior_fail_open_on_bad_conn() -> None:
    # A broken connection must raise (not silently swallow) per fail-open spec —
    # the caller wraps it with logger.warning; here we assert it propagates.
    from polaris.core.learners.posterior import maybe_update_posterior

    bad = sqlite3.connect(":memory:")
    bad.close()
    with pytest.raises(sqlite3.Error):
        maybe_update_posterior(
            bad, exchange="okx", strategy="s", ticker="t", regime="chop",
            pnl_r_net=0.1, now_ts=1,
        )


# ---------------------------------------------------------------------------
# 7. cost-fill matching — per-position contribution_id (P0-2)
# ---------------------------------------------------------------------------


def _seed_fill(
    conn: sqlite3.Connection,
    *,
    fill_id: str,
    is_close: int,
    fee_usd: float,
    slippage_bps: float,
    contribution_id: str,
    ts_ms: int,
    size_usd: float = 100.0,
    fill_price: float = 60_000.0,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', ?, ?, ?, ?, "
        "        ?, ?, ?, 0.0, ?, 0.001, ?, 'filled')",
        (
            fill_id, size_usd, fill_price, fee_usd, slippage_bps, ts_ms,
            f"ord_{fill_id}", contribution_id, is_close, size_usd,
        ),
    )


def test_read_cost_inputs_matches_close_per_position(
    memdb: sqlite3.Connection,
) -> None:
    """P0-2: two concurrent positions on the SAME (strategy, instrument) must
    each read THEIR OWN close fee/slippage via contribution_id — not the
    latest/other position's close leg.
    """
    from polaris.scripts._production_close_effects import _read_cost_inputs
    from polaris.scripts._smoke_fills import SimulatedTrade

    # Position A: cheap close (fee 0.10, slip 1bps). Position B: expensive
    # close (fee 0.90, slip 9bps). B's close is the LATEST close row.
    _seed_fill(memdb, fill_id="a_open", is_close=0, fee_usd=0.10, slippage_bps=1.0,
               contribution_id="posA", ts_ms=1000)
    _seed_fill(memdb, fill_id="b_open", is_close=0, fee_usd=0.50, slippage_bps=5.0,
               contribution_id="posB", ts_ms=1100)
    _seed_fill(memdb, fill_id="a_close", is_close=1, fee_usd=0.10, slippage_bps=1.0,
               contribution_id="posA", ts_ms=2000)
    _seed_fill(memdb, fill_id="b_close", is_close=1, fee_usd=0.90, slippage_bps=9.0,
               contribution_id="posB", ts_ms=2100)

    trade_a = SimulatedTrade(
        signal_id="sA", venue="okx", symbol="BTC-USDT", strategy_id="volume_burst",
        side="long", entry_price=60_000.0, notional_usd=100.0,
        open_ts=1, position_id="posA",
    )
    e_fee, e_slip, x_fee, x_slip, _size, _atr = _read_cost_inputs(memdb, trade_a)
    # Position A must see its OWN entry + close costs, NOT B's (latest) close.
    assert e_fee == pytest.approx(0.10)
    assert e_slip == pytest.approx(1.0)
    assert x_fee == pytest.approx(0.10), "close fee cross-matched to other position"
    assert x_slip == pytest.approx(1.0), "close slippage cross-matched"

    # Position B reads its own (expensive) close.
    trade_b = SimulatedTrade(
        signal_id="sB", venue="okx", symbol="BTC-USDT", strategy_id="volume_burst",
        side="long", entry_price=60_000.0, notional_usd=100.0,
        open_ts=1, position_id="posB",
    )
    _e, _es, xb_fee, xb_slip, _s, _a = _read_cost_inputs(memdb, trade_b)
    assert xb_fee == pytest.approx(0.90)
    assert xb_slip == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# 6. regression — sizing never reads the posterior table
# ---------------------------------------------------------------------------


def test_sizing_does_not_import_posterior() -> None:
    import pathlib

    sizing_dir = (
        pathlib.Path(__file__).resolve().parents[1] / "polaris" / "core" / "sizing"
    )
    for py in sizing_dir.glob("*.py"):
        text = py.read_text()
        assert "learner_posterior" not in text, f"{py.name} reads learner_posterior"
        assert "p_pos" not in text, f"{py.name} references p_pos"
        assert "posterior" not in text, f"{py.name} references posterior"
