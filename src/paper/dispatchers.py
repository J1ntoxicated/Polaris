"""Strategy signal dispatchers — registry pattern (Phase 10).

Replaces the if/elif primary == "X" chain in realtime_runner._eval_and_act.
Each strategy type (primary_tf) registers a dispatcher function via decorator.

DispatchContext encapsulates per-tick inputs. DispatcherFn returns Signal | None.

Migration strategy:
    Phase 10.1: framework + 3 representative dispatchers (carry, funding, grid).
    Phase 12 (god module breakdown): migrate remaining 16 dispatchers, then
    delete the if/elif chain entirely.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from src.domain.signal import Signal
from src.domain.strategy import Strategy


@dataclass(frozen=True)
class DispatchContext:
    """Per-tick context passed to a dispatcher.

    Built once per (hypo, ticker) tick by the runner. Carries pre-fetched
    book/quote so dispatchers don't refetch.
    """
    strategy: Strategy
    hypo: dict
    ticker: str
    tick_ts_ms: int
    tick_price: float
    full_tick: dict | None
    book: dict
    bid: float
    ask: float


DispatcherFn = Callable[[DispatchContext], Optional[Signal]]

# Registry: primary_tf → dispatcher function
DISPATCHERS: dict[str, DispatcherFn] = {}


def register_dispatcher(primary_tf: str) -> Callable[[DispatcherFn], DispatcherFn]:
    """Decorator — register a dispatcher for a primary_tf name.

    Example:
        @register_dispatcher("carry")
        def _carry(ctx: DispatchContext) -> Optional[Signal]:
            ...
            return signal
    """
    def decorator(fn: DispatcherFn) -> DispatcherFn:
        if primary_tf in DISPATCHERS:
            raise ValueError(
                f"dispatcher for primary_tf={primary_tf!r} already registered"
            )
        DISPATCHERS[primary_tf] = fn
        return fn
    return decorator


def get_dispatcher(primary_tf: str) -> Optional[DispatcherFn]:
    """Lookup helper — returns None if no registered dispatcher."""
    return DISPATCHERS.get(primary_tf)


def is_registered(primary_tf: str) -> bool:
    return primary_tf in DISPATCHERS


def reset_registry() -> None:
    """Test helper — clear the registry between tests."""
    DISPATCHERS.clear()
