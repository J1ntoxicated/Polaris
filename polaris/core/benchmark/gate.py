"""Pure 3-tier benchmark gate — relative / risk-adjusted / statistical + CIs.

Spec: ``.claude/plans/p1_replay_benchmark_harness_2026-05-31.md`` §3-tier +
§정직 framing. The gate is the go-live decision surface, but it is **edge
significance on held-out bars under the REAL fee schedule on the shared clock**
— NOT a calendar / time-elapsed gate.

Tiers (all on the SAME bars / fee / clock as the baselines):
  1. relative      — bot real-fee-net Sharpe spread > 0 vs EVERY baseline.
  2. risk_adjusted — bot per-trade Sharpe + spread vs each baseline.
  3. statistical   — PSR + deflated-Sharpe (trial-adjusted) + a NIG posterior
                     lower-confidence-bound on the per-trade net-R mean.

Honest framing: a positive point estimate whose CI lower bound is not
significant is reported as ``"edge present, CI wide"`` and does NOT pass. No
throttle, no "weeks elapsed". Pure: no DB, no network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from polaris.core.benchmark.baselines import BaselineCurve
from polaris.core.benchmark.statistics import (
    deflated_sharpe,
    kurtosis,
    probabilistic_sharpe,
    sharpe_ratio,
    sharpe_spread,
    skewness,
)
from polaris.core.learners.posterior import NIGPosterior, nig_update
from polaris.core.replay.models import ReplayResult, ReplayTrade

__all__ = [
    "GateResult",
    "TierResult",
    "evaluate_gate",
]

# One-sided LCB z (mirrors confidence.LCB_Z): z = 1.0 ≈ one standard error on the
# NIG marginal-t scale. Reused so the dashboard CI is one definition project-wide.
_LCB_Z: float = 1.0
# PSR significance floor for the statistical tier (probability the true Sharpe
# beats the benchmark). 0.95 = a conventional one-sided 95% confidence the edge
# is real. This is a SIGNIFICANCE threshold on held-out edge, not a time gate.
_PSR_PASS: float = 0.95
_PRIOR = NIGPosterior(mu=0.0, kappa=1.0, alpha=1.0, beta=1.0)


@dataclass(frozen=True, slots=True)
class TierResult:
    """One benchmark tier's verdict + its evidence note."""

    name: str
    passed: bool
    note: str
    detail: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GateResult:
    """Full 3-tier verdict + point estimates and CIs for the dashboard.

    ``passed`` is the conjunction of all three tiers (relative AND risk_adjusted
    AND statistical). ``sharpe_spreads`` maps each baseline name -> the bot's
    Sharpe spread over it. ``net_pnl_r_ci`` is the NIG LCB/UCB band on the
    per-trade net-R mean. ``is_oos_spread`` is the walk-forward overfit flag
    (bot IS Sharpe − OOS Sharpe; 0.0 when not supplied).
    """

    passed: bool
    tiers: dict[str, TierResult]
    bot_sharpe: float
    sharpe_spreads: dict[str, float]
    psr: float
    deflated_sharpe: float
    net_pnl_r_mean: float
    net_pnl_r_ci: tuple[float, float]
    net_pnl_real_fee: float
    max_dd: float
    turnover: float
    fee_drag_real_r: float
    n: int
    is_oos_spread: float = 0.0


def _per_trade_net_r(trades: tuple[ReplayTrade, ...]) -> list[float]:
    return [t.pnl_r for t in trades]


def _trial_sharpes(trades: tuple[ReplayTrade, ...]) -> list[float]:
    """Per-strategy Sharpe estimates — the deflated-Sharpe trial population.

    BLdP deflation corrects the multiple-testing of having SEARCHED several
    strategy configurations and kept the best. The honest trial population is
    therefore the per-strategy Sharpe estimates: group the replay trades by
    ``strategy`` and compute each group's per-trade Sharpe. ``Var`` over these
    is the cross-trial dispersion ``expected_max_sharpe`` consumes. A group with
    < 2 trades has no usable Sharpe (sharpe_ratio -> 0.0) and is dropped so it
    does not pull the variance toward an artificial 0.
    """
    by_strategy: dict[str, list[float]] = {}
    for t in trades:
        by_strategy.setdefault(t.strategy, []).append(t.pnl_r)
    sharpes: list[float] = []
    for rets in by_strategy.values():
        if len(rets) < 2:
            continue
        sharpes.append(sharpe_ratio(rets, periods=1.0))
    return sharpes


def _per_trade_returns(result: ReplayResult, starting_equity: float) -> list[float]:
    """Per-trade net-fee return relative to equity-before (matches the engine's
    Sharpe definition so the bot ratio is comparable to the baselines)."""
    rets: list[float] = []
    equity = float(starting_equity)
    for tr in result.trades:
        base = equity if equity > 0.0 else starting_equity
        rets.append(tr.net_pnl_usd / base if base != 0.0 else 0.0)
        equity += tr.net_pnl_usd
    return rets


def _nig_band(samples: list[float]) -> tuple[float, float]:
    """NIG posterior (LCB, UCB) on the mean of ``samples`` (reuses confidence's
    ``_nig_lcb`` math: μ_n ± z·marginal-t scale). Empty -> (0, 0)."""
    if not samples:
        return (0.0, 0.0)
    post = _PRIOR
    for x in samples:
        post = nig_update(post, x)
    if post.alpha <= 0.0 or post.kappa <= 0.0:
        return (post.mu, post.mu)
    scale_sq = post.beta * (post.kappa + 1.0) / (post.alpha * post.kappa)
    if scale_sq <= 0.0:
        return (post.mu, post.mu)
    half = _LCB_Z * math.sqrt(scale_sq)
    return (float(post.mu - half), float(post.mu + half))


def evaluate_gate(
    *,
    replay: ReplayResult,
    baselines: dict[str, BaselineCurve],
    trials: int = 1,
    starting_equity: float = 10_000.0,
    is_oos_spread: float = 0.0,
) -> GateResult:
    """Evaluate the 3-tier gate for a replay run against same-clock baselines.

    ``trials`` is retained for signature stability but is NOT the deflation
    input: the deflated-Sharpe trial population is derived from the realised
    trades (per-strategy Sharpe estimates) so the BLdP multiple-testing
    correction reflects the search that actually happened. ``is_oos_spread`` is
    the walk-forward overfit flag, passed in by the caller (the gate does not run
    the splits itself — it judges one run).
    """
    trades = replay.trades
    n = len(trades)
    bot_rets = _per_trade_returns(replay, starting_equity)
    bot_sharpe = sharpe_ratio(bot_rets, periods=1.0)

    # --- Tier 1: relative (spread > 0 vs EVERY baseline) --------------------
    spreads: dict[str, float] = {}
    for name, bc in baselines.items():
        b_sharpe = sharpe_ratio(list(bc.returns), periods=1.0)
        spreads[name] = sharpe_spread(bot_sharpe, [b_sharpe])
    relative_pass = bool(n >= 2 and spreads and all(v > 0.0 for v in spreads.values()))
    relative = TierResult(
        name="relative",
        passed=relative_pass,
        note=(
            "bot Sharpe spread > 0 vs all baselines"
            if relative_pass
            else ("no baselines supplied" if not spreads else "loses to >=1 baseline / too few trades")
        ),
        detail=dict(spreads),
    )

    # --- Tier 2: risk-adjusted (Sharpe + worst spread) ----------------------
    worst_spread = min(spreads.values()) if spreads else bot_sharpe
    risk_pass = bool(n >= 2 and worst_spread > 0.0)
    risk_adjusted = TierResult(
        name="risk_adjusted",
        passed=risk_pass,
        note=f"bot per-trade Sharpe={bot_sharpe:.3f}, worst spread={worst_spread:.3f}",
        detail={"bot_sharpe": bot_sharpe, "worst_spread": worst_spread},
    )

    # --- Tier 3: statistical (PSR + deflated + NIG CI) ----------------------
    net_r = _per_trade_net_r(trades)
    skew = skewness(net_r)
    kurt = kurtosis(net_r)
    psr = probabilistic_sharpe(sr=bot_sharpe, n=n, skew=skew, kurt=kurt, sr_star=0.0)
    # Trial population for the deflated Sharpe = the per-strategy Sharpe estimates
    # (the multiple-testing BLdP deflation corrects). T = group count;
    # sr_variance = Var of those Sharpes. Only ONE usable group (or none) means we
    # never searched -> deflation is undefined, so we leave DSR == PSR and label
    # it un-deflated honestly. The caller's ``trials`` arg is NOT a deflation
    # input (the search dispersion must come from the realised trades).
    trial_sharpes = _trial_sharpes(trades)
    n_trials = len(trial_sharpes)
    if n_trials > 1:
        sr_variance = _stat_var(trial_sharpes)
        dsr = deflated_sharpe(
            sr=bot_sharpe, n=n, skew=skew, kurt=kurt,
            trials=n_trials, sr_variance=sr_variance,
        )
        deflated_label = "deflated"
    else:
        sr_variance = 0.0
        dsr = psr  # un-deflated: a single (or no) trial cannot be deflated
        deflated_label = "un-deflated(single trial)"
    lcb, ucb = _nig_band(net_r)
    mean_r = (sum(net_r) / n) if n else 0.0
    significant = bool(n >= 2 and dsr >= _PSR_PASS and lcb > 0.0)
    if significant:
        stat_note = (
            f"PSR={psr:.3f} {deflated_label}={dsr:.3f} "
            f"LCB(net-R)={lcb:+.4f} — significant"
        )
    elif n >= 2 and mean_r > 0.0:
        stat_note = (
            f"edge present, CI wide ({deflated_label}={dsr:.3f}, "
            f"LCB={lcb:+.4f} not significant)"
        )
    else:
        stat_note = f"no edge ({deflated_label}={dsr:.3f}, mean net-R={mean_r:+.4f})"
    statistical = TierResult(
        name="statistical",
        passed=significant,
        note=stat_note,
        detail={
            "psr": psr, "deflated_sharpe": dsr, "skew": skew, "kurt": kurt,
            "lcb_net_r": lcb, "ucb_net_r": ucb, "mean_net_r": mean_r,
            "deflation_trials": float(n_trials), "sr_variance": sr_variance,
        },
    )

    tiers = {"relative": relative, "risk_adjusted": risk_adjusted, "statistical": statistical}
    metrics = replay.metrics
    return GateResult(
        passed=relative.passed and risk_adjusted.passed and statistical.passed,
        tiers=tiers,
        bot_sharpe=bot_sharpe,
        sharpe_spreads=spreads,
        psr=psr,
        deflated_sharpe=dsr,
        net_pnl_r_mean=mean_r,
        net_pnl_r_ci=(lcb, ucb),
        net_pnl_real_fee=float(metrics.get("net_pnl", 0.0)),
        max_dd=float(metrics.get("max_dd", 0.0)),
        turnover=float(metrics.get("turnover", 0.0)),
        fee_drag_real_r=float(metrics.get("fee_drag_real_r", 0.0)),
        n=n,
        is_oos_spread=is_oos_spread,
    )


def _stat_var(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    return sum((x - mean) ** 2 for x in xs) / (n - 1)
