"""Probability of Backtest Overfitting (PBO/CSCV) + admission gate TDD.

PBO (Bailey, Borwein, Lopez de Prado, Zhu 2015 — CSCV): given a performance
matrix (rows = trial configs, cols = backtest paths/sub-periods), split the
columns into all balanced (IS, OOS) combinations; for each, pick the IS-best
config and look up its OOS rank. PBO = fraction of splits where the IS-best
ranks below the OOS median (logit < 0). High PBO => the in-sample winner is a
phantom edge that does not generalise.

Admission gate (NEW strategy onboarding): admit only when the deflated Sharpe
(reused from ``benchmark.statistics``) clears a floor AND PBO is below the
overfit ceiling. This is an EXPECTANCY admission test for which strategies ENTER
the registry — never a throttle on the running bot's sizing (flow_not_block).
"""

from __future__ import annotations

from polaris.core.benchmark.overfit import (
    DEFAULT_DSR_ADMIT,
    DEFAULT_PBO_MAX,
    OverfitVerdict,
    admit_strategy,
    pbo,
)


# --- PBO of a generalising matrix ≈ 0 --------------------------------------
def test_pbo_low_when_is_winner_generalises() -> None:
    """One config dominates on EVERY path — the IS-best is always OOS-best, so
    backtest overfitting is ~0."""
    n_paths = 10
    # config 0 = strong on every path; configs 1..4 = uniformly weaker.
    matrix = [
        [1.0 + 0.01 * p for p in range(n_paths)],   # best everywhere
        [0.5 + 0.01 * p for p in range(n_paths)],
        [0.3 + 0.01 * p for p in range(n_paths)],
        [0.2 + 0.01 * p for p in range(n_paths)],
        [0.1 + 0.01 * p for p in range(n_paths)],
    ]
    assert pbo(matrix) <= 0.05


# --- PBO of a noise / overfit matrix is high --------------------------------
def test_pbo_high_when_is_winner_is_noise() -> None:
    """An anti-correlated matrix where whoever wins IS tends to lose OOS — the
    canonical overfit signature — must yield a high PBO."""
    # 4 configs, 8 paths. Build a perfectly anti-persistent ranking: on the
    # first half of paths config A leads, on the second half it trails (and the
    # mirror for the others), so the IS-best on one column-half is the OOS-worst
    # on the other.
    paths = 8
    matrix: list[list[float]] = []
    for cfg in range(4):
        row: list[float] = []
        for p in range(paths):
            # rank flips between the two path halves.
            rank = cfg if p < paths // 2 else (3 - cfg)
            row.append(float(rank))
        matrix.append(row)
    assert pbo(matrix) >= 0.5


# --- admission gate: edge admitted, phantom edge rejected ------------------
def test_admit_real_edge() -> None:
    v = admit_strategy(deflated_sharpe=0.99, pbo_value=0.10)
    assert isinstance(v, OverfitVerdict)
    assert v.admit is True
    assert "admit" in v.reason.lower()


def test_reject_phantom_edge_high_pbo() -> None:
    # High in-sample Sharpe but it does not generalise (PBO above ceiling).
    v = admit_strategy(deflated_sharpe=0.99, pbo_value=0.80)
    assert v.admit is False
    assert "pbo" in v.reason.lower()


def test_reject_weak_deflated_sharpe() -> None:
    # Generalises (low PBO) but the deflated Sharpe never cleared the floor —
    # no significant edge after trial deflation.
    v = admit_strategy(deflated_sharpe=0.20, pbo_value=0.05)
    assert v.admit is False
    assert "sharpe" in v.reason.lower() or "dsr" in v.reason.lower()


def test_gate_uses_named_thresholds() -> None:
    """Thresholds are named module constants (no magic numbers in callers)."""
    assert 0.0 < DEFAULT_PBO_MAX < 1.0
    assert 0.5 < DEFAULT_DSR_ADMIT <= 1.0
    # Exactly-at-threshold is admitted (>= / <=, documented boundary).
    v = admit_strategy(deflated_sharpe=DEFAULT_DSR_ADMIT, pbo_value=DEFAULT_PBO_MAX)
    assert v.admit is True


# --- degenerate inputs -----------------------------------------------------
def test_pbo_degenerate_returns_half() -> None:
    # < 2 configs or < 2 paths => no usable IS/OOS split => 0.5 (no evidence).
    assert pbo([[1.0, 2.0, 3.0, 4.0]]) == 0.5
    assert pbo([]) == 0.5
    assert pbo([[1.0], [2.0]]) == 0.5


def test_pbo_determinism() -> None:
    matrix = [
        [1.0, 0.2, 0.9, 0.1, 0.8, 0.3],
        [0.2, 1.0, 0.1, 0.9, 0.2, 0.8],
        [0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
    ]
    assert pbo(matrix) == pbo(matrix)
