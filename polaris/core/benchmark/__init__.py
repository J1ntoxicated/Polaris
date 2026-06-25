"""Benchmark layer (P1) — baselines, statistics, walk-forward, 3-tier gate.

Pure offline math on top of the deterministic replay engine
(``polaris.core.replay``). **Behaviour 0**: read-only / no live trading path,
no live DB writable. The 3-tier gate judges held-out edge significance under
the REAL OKX fee schedule on the same clock as the baselines — it is NOT a
calendar / time-elapsed gate (spec §정직 framing).
"""

from __future__ import annotations

from polaris.core.benchmark.baselines import (
    BaselineCurve,
    bollinger_baseline,
    buy_and_hold_baseline,
    tsmom_baseline,
)
from polaris.core.benchmark.cpcv import CPCVSplit, cpcv_splits
from polaris.core.benchmark.gate import GateResult, TierResult, evaluate_gate
from polaris.core.benchmark.overfit import (
    DEFAULT_DSR_ADMIT,
    DEFAULT_PBO_MAX,
    OverfitVerdict,
    admit_strategy,
    pbo,
)
from polaris.core.benchmark.statistics import (
    deflated_sharpe,
    probabilistic_sharpe,
    sharpe_ratio,
    sharpe_spread,
)
from polaris.core.benchmark.walk_forward import WalkForwardSplit, walk_forward_splits

__all__ = [
    "DEFAULT_DSR_ADMIT",
    "DEFAULT_PBO_MAX",
    "BaselineCurve",
    "CPCVSplit",
    "GateResult",
    "OverfitVerdict",
    "TierResult",
    "WalkForwardSplit",
    "admit_strategy",
    "bollinger_baseline",
    "buy_and_hold_baseline",
    "cpcv_splits",
    "deflated_sharpe",
    "evaluate_gate",
    "pbo",
    "probabilistic_sharpe",
    "sharpe_ratio",
    "sharpe_spread",
    "tsmom_baseline",
    "walk_forward_splits",
]
