"""Deterministic replay / benchmark harness (P1).

Offline, read-only replay of the live signal -> gate -> sizing -> exit pipeline
over persisted SQLite bars. **Behaviour 0**: never touches a live trading path
and never opens the live DB writable. The live trading code
(``build_real_market_view`` / ``compute_real_regime_signal`` /
``generate_raw_signal`` / ``compute_size`` / ``exit_engine`` / ``fees``) is
*imported and reused* with zero forks — the only replay-specific code is the
driver (bar loop, fill model, sandbox conn).
"""

from __future__ import annotations

from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.ensemble import MultiHorizonReplay, MultiHorizonResult
from polaris.core.replay.models import ReplayConfig, ReplayResult, ReplayTrade

__all__ = [
    "MultiHorizonReplay",
    "MultiHorizonResult",
    "ReplayConfig",
    "ReplayEngine",
    "ReplayResult",
    "ReplayTrade",
]
