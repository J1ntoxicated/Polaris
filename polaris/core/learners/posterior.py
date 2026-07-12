"""Edge-validation Phase 1 — Bayesian posterior on cost-adjusted expectancy.

**Measurement + display only.** Computes a Normal-Inverse-Gamma (NIG) conjugate
posterior on ``expectancy = E[R-multiple]`` per (strategy × cell × regime)
bucket and a confidence ``P(expectancy>0)``. This module is *never* imported by
``polaris.core.sizing`` — Phase 1 leaves the aggressive-bias sizing chain and
the 9-stack-collapse guard completely untouched.

Model (Murphy 2007 §Normal-Inverse-Gamma):
    likelihood : R_i ~ Normal(mu, sigma^2)   (mu, precision unknown)
    prior      : NIG(mu0, kappa0, alpha0, beta0)
    online     : Welford (mean, M2) → NIG hyper-params
        kappa_n = kappa0 + n
        mu_n    = (kappa0*mu0 + n*xbar) / kappa_n
        alpha_n = alpha0 + n/2
        beta_n  = beta0 + 0.5*M2 + (kappa0*n*(xbar-mu0)^2) / (2*kappa_n)
    marginal on mu is Student-t:
        df = 2*alpha_n, loc = mu_n,
        scale = sqrt(beta_n*(kappa_n+1)/(alpha_n*kappa_n))
    P(exp>0) = 1 - StudentT_cdf(0; df, loc, scale)

The Student-t CDF is implemented with stdlib ``math`` only (regularized
incomplete beta via a Lentz continued fraction) — no scipy/numpy dependency is
added to ``pyproject.toml``.

Spec source: edge-validation Phase 1 (Jin approved 2026-05-29).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from polaris.core.learners._primitives import (
    COST_BPS_CAPITAL,
    COST_BPS_OKX_FALLBACK,
    EDGE_VERDICT_TAU_HI,
    EDGE_VERDICT_TAU_LO,
)

# Default NIG prior when a bucket has no parent2 seed — weakly informative,
# centred at zero expectancy.
_DEFAULT_MU0 = 0.0
_DEFAULT_KAPPA0 = 1.0
_DEFAULT_ALPHA0 = 1.0
_DEFAULT_BETA0 = 1.0


@dataclass(frozen=True, slots=True)
class NIGPosterior:
    """Normal-Inverse-Gamma posterior hyper-parameters + sample count + M2.

    ``m2`` is the Welford sum-of-squared-deviations carried so an online
    update can recover ``beta`` incrementally without re-reading the raw
    R-sequence. ``mean`` is the running sample mean.
    """

    mu: float
    kappa: float
    alpha: float
    beta: float
    n: int = 0
    mean: float = 0.0
    m2: float = 0.0


def nig_update(post: NIGPosterior, x: float) -> NIGPosterior:
    """Fold one R-multiple observation into the NIG posterior (Welford online).

    Maintains the running ``mean``/``m2`` (Welford) so the closed-form NIG
    hyper-params can be recomputed from the *original* prior each step — this
    keeps the online path bit-for-bit identical to a batch closed-form update.
    """
    n0 = post.n
    # Recover the original prior (mu0, kappa0, alpha0, beta0) from the current
    # state: kappa0 = kappa - n0, alpha0 = alpha - n0/2, mu0/beta0 backed out.
    kappa0 = post.kappa - n0
    alpha0 = post.alpha - n0 / 2.0
    if n0 == 0:
        mu0 = post.mu
        beta0 = post.beta
    else:
        # mu_n = (kappa0*mu0 + n0*xbar)/(kappa0+n0) → solve for mu0
        mu0 = (post.mu * (kappa0 + n0) - n0 * post.mean) / kappa0 if kappa0 != 0 else post.mu
        beta0 = post.beta - 0.5 * post.m2 - (
            kappa0 * n0 * (post.mean - mu0) ** 2
        ) / (2.0 * (kappa0 + n0))
    # Welford increment
    n = n0 + 1
    delta = x - post.mean
    mean = post.mean + delta / n
    m2 = post.m2 + delta * (x - mean)
    # Closed-form NIG from the recovered prior + updated Welford stats
    kappa_n = kappa0 + n
    mu_n = (kappa0 * mu0 + n * mean) / kappa_n
    alpha_n = alpha0 + n / 2.0
    beta_n = beta0 + 0.5 * m2 + (kappa0 * n * (mean - mu0) ** 2) / (2.0 * kappa_n)
    return NIGPosterior(
        mu=mu_n, kappa=kappa_n, alpha=alpha_n, beta=beta_n, n=n, mean=mean, m2=m2
    )


def shrink_with_parent(parent: NIGPosterior, *, samples: list[float]) -> NIGPosterior:
    """Hierarchical shrink: borrow ``parent`` (parent2 = strategy×regime) as prior.

    The child bucket starts from the parent's ``(mu, kappa)`` as ``(mu0, kappa0)``
    so sparse cells are pulled toward the parent expectancy; as ``n`` grows the
    sample mean ``xbar`` dominates (standard conjugate shrinkage).
    """
    post = NIGPosterior(
        mu=parent.mu,
        kappa=parent.kappa,
        alpha=parent.alpha,
        beta=parent.beta,
        n=0,
        mean=0.0,
        m2=0.0,
    )
    for x in samples:
        post = nig_update(post, x)
    return post


# ---------------------------------------------------------------------------
# Student-t CDF (stdlib math only) — regularized incomplete beta
# ---------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """Continued-fraction expansion for the incomplete beta (Lentz)."""
    max_iter = 300
    eps = 3.0e-12
    fpmin = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2.0 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) via continued fraction."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(ln_beta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(x: float, *, df: float, loc: float = 0.0, scale: float = 1.0) -> float:
    """CDF of a (location-scale) Student-t with ``df`` degrees of freedom.

    Uses the regularized incomplete beta identity:
        P(T<=t) = 1 - 0.5*I_{df/(df+t^2)}(df/2, 1/2)   for t>=0
                =     0.5*I_{df/(df+t^2)}(df/2, 1/2)    for t<0
    """
    if scale <= 0.0:
        return 0.5
    t = (x - loc) / scale
    xx = df / (df + t * t)
    ib = _betai(df / 2.0, 0.5, xx)
    if t >= 0.0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def p_expectancy_positive(post: NIGPosterior) -> float:
    """P(expectancy>0) from the marginal Student-t on ``mu``."""
    if post.alpha <= 0.0 or post.kappa <= 0.0:
        return 0.5
    scale_sq = post.beta * (post.kappa + 1.0) / (post.alpha * post.kappa)
    if scale_sq <= 0.0:
        return 1.0 if post.mu > 0.0 else 0.0
    scale = math.sqrt(scale_sq)
    return 1.0 - student_t_cdf(0.0, df=2.0 * post.alpha, loc=post.mu, scale=scale)


def edge_verdict(p_pos: float) -> str:
    """Map ``P(exp>0)`` to a display label. NOT a sizing gate, no n-gate."""
    if p_pos >= EDGE_VERDICT_TAU_HI:
        return "validated-alpha"
    if p_pos <= EDGE_VERDICT_TAU_LO:
        return "anti-edge"
    return "unproven"


# ---------------------------------------------------------------------------
# Cost overlay — net (cost-adjusted) PnL
# ---------------------------------------------------------------------------


def cost_adjusted_pnl_r(
    *,
    gross_pnl_usd: float,
    gross_pnl_r: float,
    size_usd: float,
    atr_usd: float | None,
    venue: str,
    entry_fee_usd: float,
    exit_fee_usd: float,
    entry_slippage_bps: float | None,
    exit_slippage_bps: float | None,
) -> tuple[float, float]:
    """Subtract round-trip cost (fees + slippage) → (pnl_usd_net, pnl_r_net).

    OKX fills carry real fee + slippage; Capital demo reports ``fee=0`` so a
    constant ``COST_BPS_CAPITAL`` stands in on both legs. The R-denominator is
    the same ``atr_usd`` used for the gross R, so ``pnl_r_net = pnl_r -
    cost_usd/atr_usd``. The gross inputs are never mutated.

    FIX 1 (pnl_r_net blow-up): ``atr_usd`` is ``None`` when the R-denominator is
    DEGENERATE (entry_price missing/<=0). In that case the cost-in-R adjustment
    is SKIPPED (``pnl_r_net = gross_pnl_r``) — never divide cost by a tiny floor
    that explodes the net-R (the live NIG posterior was being fed -210000 R).
    ``pnl_usd_net`` still nets the dollar cost (always finite + sane).

    FIX 2 (ledger-reconcile forensic 2026-07-12, bug① phantom slippage):
    ``entry_slippage_bps``/``exit_slippage_bps`` are ``None`` ONLY when the
    caller found no matching fill row — ``COST_BPS_OKX_FALLBACK`` (1bp) then
    stands in as the estimate. An honest recorded ``0.0`` (a real fill with
    genuinely zero slippage) is a value, not a missing signal, and must flow
    through unchanged — the previous ``x or FALLBACK`` treated 0.0 as falsy
    and force-applied the 1bp fallback on EVERY zero-slippage fill.
    """
    if venue == "capital":
        cost_usd = 2.0 * (COST_BPS_CAPITAL / 1e4) * abs(size_usd)
    else:
        entry_bps = (
            COST_BPS_OKX_FALLBACK if entry_slippage_bps is None else entry_slippage_bps
        )
        exit_bps = (
            COST_BPS_OKX_FALLBACK if exit_slippage_bps is None else exit_slippage_bps
        )
        slip_entry = entry_bps / 1e4 * abs(size_usd)
        slip_exit = exit_bps / 1e4 * abs(size_usd)
        cost_usd = abs(entry_fee_usd) + abs(exit_fee_usd) + slip_entry + slip_exit
    pnl_usd_net = gross_pnl_usd - cost_usd
    if atr_usd is None or atr_usd <= 0.0:
        # Degenerate R-denominator → skip the cost-in-R adjustment (net==gross)
        # so the posterior never receives |net_r| ≫ |gross_r|.
        pnl_r_net = gross_pnl_r
    else:
        pnl_r_net = gross_pnl_r - cost_usd / atr_usd
    return pnl_usd_net, pnl_r_net


# ---------------------------------------------------------------------------
# Persistence — learner_posterior + strategy_regime_prior (parent2 seed)
# ---------------------------------------------------------------------------


def _load_parent_prior(
    conn: sqlite3.Connection, *, strategy: str, regime: str
) -> NIGPosterior:
    """Read the parent2 (strategy×regime) prior, or the default weak prior."""
    row = conn.execute(
        "SELECT mu0, kappa0, alpha0, beta0 FROM strategy_regime_prior "
        "WHERE strategy=? AND regime=?",
        (strategy, regime),
    ).fetchone()
    if row is None:
        return NIGPosterior(
            mu=_DEFAULT_MU0,
            kappa=_DEFAULT_KAPPA0,
            alpha=_DEFAULT_ALPHA0,
            beta=_DEFAULT_BETA0,
        )
    return NIGPosterior(
        mu=float(row[0]), kappa=float(row[1]), alpha=float(row[2]), beta=float(row[3])
    )


def _load_posterior(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    strategy: str,
    ticker: str,
    regime: str,
) -> NIGPosterior | None:
    row = conn.execute(
        "SELECT mu, kappa, alpha, beta, m2, running_mean, n_samples "
        "FROM learner_posterior "
        "WHERE exchange=? AND strategy=? AND ticker=? AND regime=?",
        (exchange, strategy, ticker, regime),
    ).fetchone()
    if row is None:
        return None
    # The full running NIG state (incl. Welford ``running_mean`` + ``m2``) is
    # persisted, so an existing cell folds the next observation into its OWN
    # accumulated state — the parent prior is NOT re-mixed (P0-1 fix).
    return NIGPosterior(
        mu=float(row[0]),
        kappa=float(row[1]),
        alpha=float(row[2]),
        beta=float(row[3]),
        n=int(row[6]),
        mean=float(row[5]),
        m2=float(row[4]),
    )


def maybe_update_posterior(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    strategy: str,
    ticker: str,
    regime: str,
    pnl_r_net: float,
    now_ts: int,
) -> None:
    """Fold one cost-adjusted R observation into the bucket posterior.

    P0-1 fix — the parent2 prior (strategy×regime) seeds a **new** cell exactly
    once (when it has no row yet); from then on each observation folds into the
    cell's OWN accumulated NIG state (μ_n, κ_n, α_n, β_n + Welford mean/M2). The
    parent prior is never re-mixed on subsequent updates, so the cell posterior
    equals a single batch NIG update seeded by the parent (closed-form parity),
    and the ``strategy_regime_prior`` row is left untouched (no contamination).

    **Fail-open**: raises ``sqlite3.Error`` on a broken connection so the
    caller can ``logger.warning`` — we do not silently swallow DB errors.
    This table is *never* read by the sizing pipeline.
    """
    existing = _load_posterior(
        conn, exchange=exchange, strategy=strategy, ticker=ticker, regime=regime
    )
    if existing is None:
        # New cell: seed the prior from the parent2 posterior ONCE (n=0).
        parent = _load_parent_prior(conn, strategy=strategy, regime=regime)
        post = NIGPosterior(
            mu=parent.mu, kappa=parent.kappa, alpha=parent.alpha, beta=parent.beta
        )
    else:
        # Existing cell: fold into its own accumulated state — no parent re-mix.
        post = existing
    new_post = nig_update(post, pnl_r_net)
    p_pos = p_expectancy_positive(new_post)
    conn.execute(
        "INSERT INTO learner_posterior "
        "(exchange, strategy, ticker, regime, mu, kappa, alpha, beta, m2, "
        " running_mean, n_samples, p_pos, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(exchange, strategy, ticker, regime) DO UPDATE SET "
        " mu=excluded.mu, kappa=excluded.kappa, alpha=excluded.alpha, "
        " beta=excluded.beta, m2=excluded.m2, running_mean=excluded.running_mean, "
        " n_samples=excluded.n_samples, p_pos=excluded.p_pos, "
        " updated_ts=excluded.updated_ts",
        (
            exchange, strategy, ticker, regime,
            new_post.mu, new_post.kappa, new_post.alpha, new_post.beta,
            new_post.m2, new_post.mean, new_post.n, p_pos, now_ts,
        ),
    )


def maybe_update_strategy_regime_prior(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    regime: str,
    pnl_r: float,
    now_ts: int,
) -> None:
    """Fold one closed-trade R into the strategy×regime parent2 prior.

    Wiring fix (audit ``code_review_2026-06-24``): ``strategy_regime_prior`` had a
    READER (``_load_parent_prior`` — the parent2 hierarchical seed) but NO runtime
    writer, so a new (exchange×strategy×ticker×regime) cell was always seeded from
    the flat weak default. This charges that prior on every close: the bucket's
    full running NIG state (μ0,κ0,α0,β0 + Welford mean/M2) accumulates the
    strategy×regime expectancy, so the NEXT new cell in this bucket seeds from
    real history. Welford-online so N folds equal a single batch update seeded by
    the default prior (closed-form parity), exactly like ``maybe_update_posterior``.

    This prior is a LEARNING seed only — it is NEVER read by the sizing pipeline
    (9-stack ban intact; signal-only). Raises ``sqlite3.Error`` on a broken
    connection so the close-path caller can ``logger.warning`` (fail-open there).
    """
    row = conn.execute(
        "SELECT mu0, kappa0, alpha0, beta0, m2, n_samples "
        "FROM strategy_regime_prior WHERE strategy=? AND regime=?",
        (strategy, regime),
    ).fetchone()
    if row is None:
        post = NIGPosterior(
            mu=_DEFAULT_MU0, kappa=_DEFAULT_KAPPA0,
            alpha=_DEFAULT_ALPHA0, beta=_DEFAULT_BETA0,
        )
    else:
        n = int(row[5])
        post = NIGPosterior(
            mu=float(row[0]), kappa=float(row[1]),
            alpha=float(row[2]), beta=float(row[3]),
            n=n, mean=_running_mean_from_default_prior(float(row[0]), n),
            m2=float(row[4]),
        )
    new_post = nig_update(post, pnl_r)
    conn.execute(
        "INSERT INTO strategy_regime_prior "
        "(strategy, regime, mu0, kappa0, alpha0, beta0, m2, n_samples, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(strategy, regime) DO UPDATE SET "
        " mu0=excluded.mu0, kappa0=excluded.kappa0, alpha0=excluded.alpha0, "
        " beta0=excluded.beta0, m2=excluded.m2, n_samples=excluded.n_samples, "
        " updated_ts=excluded.updated_ts",
        (
            strategy, regime, new_post.mu, new_post.kappa, new_post.alpha,
            new_post.beta, new_post.m2, new_post.n, now_ts,
        ),
    )


def _running_mean_from_default_prior(mu_n: float, n: int) -> float:
    """Recover the Welford running mean ``xbar`` from a persisted prior row.

    ``strategy_regime_prior`` stores μ_n/κ_n/m2/n (no raw running mean column), but
    ``nig_update``'s Welford recurrence needs ``post.mean``. Unlike the per-cell
    ``learner_posterior`` (whose prior is the variable parent2), this table ALWAYS
    starts from the FIXED default weak prior (μ0=``_DEFAULT_MU0``, κ0=
    ``_DEFAULT_KAPPA0``), so the sample mean is exactly recoverable from the closed
    form ``mu_n = (κ0·μ0 + n·xbar)/(κ0 + n)`` →
    ``xbar = (mu_n·(κ0 + n) − κ0·μ0)/n``. n=0 → mean unused by ``nig_update``'s
    prior-recovery branch; return μ_n.
    """
    if n <= 0:
        return mu_n
    return (mu_n * (_DEFAULT_KAPPA0 + n) - _DEFAULT_KAPPA0 * _DEFAULT_MU0) / n


__all__ = [
    "NIGPosterior",
    "cost_adjusted_pnl_r",
    "edge_verdict",
    "maybe_update_posterior",
    "maybe_update_strategy_regime_prior",
    "nig_update",
    "p_expectancy_positive",
    "shrink_with_parent",
    "student_t_cdf",
]
