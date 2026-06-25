"""strategy_regime_prior runtime writer — parent2-seed charging on close.

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack ban.

The audit (``code_review_2026-06-24`` / dead-code sweep ``w4wb7123o``) found the
``strategy_regime_prior`` table had a READER (``posterior._load_parent_prior``,
the parent2 hierarchical seed) + DDL but ZERO runtime writer — only test fixtures
INSERT, so the parent2 seed was permanently the weak default. This wires the
close-path writer: each closed trade folds its ``(strategy, regime, pnl_r)`` into
the strategy×regime NIG prior so a NEW (exchange×strategy×ticker×regime) cell is
seeded from the accumulated strategy×regime expectancy instead of the flat
default. Measurement-only — this prior is NEVER read by sizing (9-stack intact).
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.learners.posterior import (
    NIGPosterior,
    maybe_update_strategy_regime_prior,
    nig_update,
)
from polaris.storage.schema import init_db


@pytest.fixture()
def memdb() -> sqlite3.Connection:
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _read_prior(
    conn: sqlite3.Connection, *, strategy: str, regime: str
) -> tuple[float, float, float, float, float, int] | None:
    row = conn.execute(
        "SELECT mu0, kappa0, alpha0, beta0, m2, n_samples "
        "FROM strategy_regime_prior WHERE strategy=? AND regime=?",
        (strategy, regime),
    ).fetchone()
    if row is None:
        return None
    return (
        float(row[0]), float(row[1]), float(row[2]),
        float(row[3]), float(row[4]), int(row[5]),
    )


def test_first_close_creates_prior_row(memdb: sqlite3.Connection) -> None:
    # No row yet → the first close seeds from the default weak prior + folds one R.
    assert _read_prior(memdb, strategy="volume_burst", regime="bull_trend") is None
    maybe_update_strategy_regime_prior(
        memdb, strategy="volume_burst", regime="bull_trend",
        pnl_r=0.8, now_ts=1000,
    )
    row = _read_prior(memdb, strategy="volume_burst", regime="bull_trend")
    assert row is not None
    assert row[5] == 1  # n_samples


def test_prior_accumulates_strategy_regime_ev(memdb: sqlite3.Connection) -> None:
    # N closes in the same (strategy, regime) bucket == a single batch NIG update
    # seeded by the default weak prior (online Welford parity).
    samples = [0.5, 1.2, -0.4, 0.8]
    for r in samples:
        maybe_update_strategy_regime_prior(
            memdb, strategy="micro_reversion", regime="chop",
            pnl_r=r, now_ts=2000,
        )
    row = _read_prior(memdb, strategy="micro_reversion", regime="chop")
    assert row is not None
    # Replay the SAME folds through the pure NIG primitive from the default prior.
    post = NIGPosterior(mu=0.0, kappa=1.0, alpha=1.0, beta=1.0)
    for r in samples:
        post = nig_update(post, r)
    assert row[0] == pytest.approx(post.mu, rel=1e-12)   # mu0
    assert row[1] == pytest.approx(post.kappa, rel=1e-12)  # kappa0
    assert row[2] == pytest.approx(post.alpha, rel=1e-12)  # alpha0
    assert row[3] == pytest.approx(post.beta, rel=1e-12)   # beta0
    assert row[4] == pytest.approx(post.m2, rel=1e-12)     # m2
    assert row[5] == len(samples)                          # n_samples


def test_distinct_buckets_are_independent(memdb: sqlite3.Connection) -> None:
    maybe_update_strategy_regime_prior(
        memdb, strategy="s1", regime="bull_trend", pnl_r=1.0, now_ts=1
    )
    maybe_update_strategy_regime_prior(
        memdb, strategy="s1", regime="bear_trend", pnl_r=-1.0, now_ts=1
    )
    bull = _read_prior(memdb, strategy="s1", regime="bull_trend")
    bear = _read_prior(memdb, strategy="s1", regime="bear_trend")
    assert bull is not None and bear is not None
    assert bull[5] == 1 and bear[5] == 1
    assert bull[0] != bear[0]  # divergent mu0 — buckets do not cross-contaminate


def test_writer_seeds_parent_prior_reader(memdb: sqlite3.Connection) -> None:
    # End-to-end: after charging the prior, the parent2 reader sees the charged
    # (non-default) seed instead of the weak default → a new cell is seeded from
    # accumulated strategy×regime expectancy.
    from polaris.core.learners.posterior import maybe_update_posterior

    for r in [1.5, 1.4, 1.6, 1.5]:
        maybe_update_strategy_regime_prior(
            memdb, strategy="trend_x", regime="bull_trend", pnl_r=r, now_ts=10
        )
    # A brand-new cell for that strategy×regime now seeds from the charged prior.
    maybe_update_posterior(
        memdb, exchange="okx", strategy="trend_x", ticker="NEW-USDT",
        regime="bull_trend", pnl_r_net=0.0, now_ts=20,
    )
    cell = memdb.execute(
        "SELECT mu FROM learner_posterior WHERE exchange='okx' AND strategy='trend_x' "
        "AND ticker='NEW-USDT' AND regime='bull_trend'"
    ).fetchone()
    assert cell is not None
    # The cell's posterior mean is pulled toward the strongly-positive charged
    # prior (well above the 0.0 default), not anchored at the weak default.
    assert float(cell[0]) > 0.5
