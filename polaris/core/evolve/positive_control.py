"""P0a positive control — synthetic strong-edge gate-discrimination probe (FIX 1).

OFFLINE ONLY. Touches NO live trading / sizing / T4 chain / gate thresholds.
These helpers synthesise a DETERMINISTIC strong low-noise edge and run it through
the EXISTING benchmark gate (thresholds untouched) to answer one question: is the
held-out N large enough for the gate to certify ANY real edge? If not, a 0-pass
result is validation starvation, not feature exhaustion.

Extracted from :mod:`polaris.core.evolve.evaluator` (pure move — behavior-0); the
public names are re-exported from the evaluator and the package ``__init__`` so
every existing import path still resolves.
"""

from __future__ import annotations

from polaris.core.benchmark.gate import evaluate_gate
from polaris.core.metrics.risk_unit import R_USD_PROXY
from polaris.core.replay.models import ReplayResult, ReplayTrade

__all__ = [
    "gate_can_discriminate",
    "min_passable_n",
]

# Positive-control synthetic edge (FIX 1). A STRONG, LOW-NOISE per-trade net-R
# edge: mean +0.2R with a small ±0.1R spread (sd≈0.1 -> per-trade Sharpe ≈ 2).
# This is the edge the gate SHOULD be able to certify if it has enough held-out
# N; if it cannot at the run's realised OOS N, the instrument is validation-
# starved, not feature-exhausted.
_PC_MEAN: float = 0.2
_PC_SPREAD: float = 0.1
# The synthetic USD pnl is pnl_r * this so the gate's bot_sharpe path is
# internally consistent (not read by the stat tier). This synthetic probe has
# no real venue/trade, so it unifies onto ``risk_unit.R_USD_PROXY`` — the ONE
# documented $-per-R fallback proxy — rather than a private duplicate constant
# (2026-07-07 R4-proxy retirement).
_PNL_R_USD_DENOM: float = R_USD_PROXY


def _synthetic_strong_edge(n_trades: int, *, mean: float, spread: float) -> list[float]:
    """A DETERMINISTIC strong low-noise per-trade net-R sample of size ``n_trades``.

    Built from a fixed 2-value repeating pattern ``[mean+spread, mean-spread]`` —
    NO randomness, NO wall-clock (the workflow runtime forbids nondeterminism).
    The pattern has mean ``mean`` and a controlled spread so the positive control
    is byte-reproducible across runs.
    """
    if n_trades <= 0:
        return []
    pattern = (mean + spread, mean - spread)
    return [pattern[i % 2] for i in range(n_trades)]


def gate_can_discriminate(
    *, n_trades: int, mean: float = _PC_MEAN, spread: float = _PC_SPREAD
) -> bool:
    """POSITIVE CONTROL: can the gate's statistical tier certify a STRONG edge at
    ``n_trades`` held-out trades?

    Synthesises the deterministic strong low-noise net-R sample
    (:func:`_synthetic_strong_edge`) and runs it through the EXISTING
    :func:`evaluate_gate` (gate thresholds untouched), returning whether its
    STATISTICAL tier passes — i.e. ``dsr >= 0.95 AND lcb > 0`` at this N. A single
    synthetic strategy group means the gate leaves the deflation un-deflated
    (single trial), so this isolates the "is N big enough to clear the NIG LCB"
    question. ``False`` at the run's realised OOS N == validation starvation.
    """
    sample = _synthetic_strong_edge(n_trades, mean=mean, spread=spread)
    if len(sample) < 2:
        return False
    trades = tuple(
        ReplayTrade(
            entry_ts=i, exit_ts=i, symbol="PC", strategy="positive_control",
            regime="mixed", side="long", entry_price=1.0, exit_price=1.0,
            notional=1.0, gross_pnl_usd=r * _PNL_R_USD_DENOM, rt_fee_usd=0.0,
            net_pnl_usd=r * _PNL_R_USD_DENOM, pnl_r=r, exit_reason="pc",
        )
        for i, r in enumerate(sample)
    )
    result = ReplayResult(trades=trades, equity_curve_real_fee_net=(), metrics={})
    # We read ONLY the statistical tier (lcb>0 AND dsr>=0.95); relative /
    # risk_adjusted need baselines we deliberately do not supply.
    gate = evaluate_gate(replay=result, baselines={}, starting_equity=10_000.0)
    return bool(gate.tiers["statistical"].passed)


def min_passable_n(
    *, mean: float = _PC_MEAN, spread: float = _PC_SPREAD, max_n: int = 2000
) -> int:
    """Smallest N at which the synthetic strong edge clears the gate (FIX 1).

    Linear upward search (the predicate is monotone in N once the NIG LCB turns
    positive). Returns the first passing N, or ``max_n + 1`` (a sentinel strictly
    greater than the bound) when the edge is UNREACHABLE within ``max_n`` — e.g.
    a wide spread where ``mean - sd <= 0`` keeps the LCB negative at every N.
    """
    for n in range(2, max_n + 1):
        if gate_can_discriminate(n_trades=n, mean=mean, spread=spread):
            return n
    return max_n + 1
