"""Smoke paper-loop shared state: focus universe + per-run loop counters.

Split out of ``smoke_paper_loop`` so the tick body (``smoke_paper_loop``) and
the gate-pipeline helpers (``_smoke_pipeline``) can both reference ``FocusEntry``
and ``LoopState`` without a circular import. ``smoke_paper_loop`` re-exports
``FOCUS`` / ``FocusEntry`` / ``LoopState`` so existing import paths keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._smoke_fills import SimulatedTrade


@dataclass(frozen=True, slots=True)
class FocusEntry:
    venue: str
    symbol: str
    timeframe: str
    asset_class: str


FOCUS: tuple[FocusEntry, ...] = (
    FocusEntry("okx", "BTC-USDT", "1m", "crypto"),
    FocusEntry("okx", "ETH-USDT", "1m", "crypto"),
    FocusEntry("capital", "EURUSD", "1m", "forex"),
    FocusEntry("capital", "GOLD", "1m", "commodity"),
)


@dataclass(slots=True)
class LoopState:
    fills_open: list[Fill]
    fills_close: list[Fill]
    open_trades: list[SimulatedTrade]
    closed_trades: list[SimulatedTrade]
    signals_emitted: int = 0
    pipeline_runs: int = 0
    pipeline_kills: int = 0
    gate_pass_counts: dict[int, int] = field(default_factory=dict)
    fills_persisted: int = 0
    full_pipeline_runs: int = 0
    full_pipeline_sized: int = 0
