"""Layer 2 — Per-Gate AI agent registry.

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 model split)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (§Phase P0/P1)

P0 mapping (handler is a coroutine ``(ctx) -> GateResult``):
    G1 universe_scanner       -> Haiku
    G2 strategy_signal_gen    -> Python (fan-out)
    G3 signal_validator       -> Haiku
    G4 pre_entry_watcher      -> Haiku (30s loop)
    G5 entry_sizer            -> Python (T4)
    G6 position_monitor       -> Python
    G7 adaptive_exit          -> Python
    G8 post_trade_reflector   -> **Python template** (Haiku optional, P1 Sonnet)

The G8 agent module accepts an optional ``client`` kwarg to support the
P1 Sonnet upgrade path; ``GateOrchestrator(phase="P0")`` (default) forces
the client argument to ``None`` for G8 so the P0 invariant is enforced
at the orchestration boundary regardless of caller wiring.
"""

from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate
from polaris.core.pipeline.agents.entry_sizer import entry_sizer_gate
from polaris.core.pipeline.agents.position_monitor import position_monitor_gate
from polaris.core.pipeline.agents.post_trade_reflector import post_trade_reflector_gate
from polaris.core.pipeline.agents.pre_entry_watcher import pre_entry_watcher_gate
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.agents.strategy_signal_gen import strategy_signal_gate
from polaris.core.pipeline.agents.universe_scanner import universe_scanner_gate

__all__ = [
    "adaptive_exit_gate",
    "entry_sizer_gate",
    "position_monitor_gate",
    "post_trade_reflector_gate",
    "pre_entry_watcher_gate",
    "signal_validator_gate",
    "strategy_signal_gate",
    "universe_scanner_gate",
]
