"""StateManager — paper balance cache + persist (shell P6).

Phase 12.1 extraction from realtime_runner. Encapsulates:
- in-memory cache `_balance_cache` (key: (ticker, strategy_name))
- disk read/write via runner.load_state / save_state
- thread/loop-safe single-loop access (asyncio)

API:
    sm = StateManager()
    bal = sm.load(ticker, strategy_name, starting_usd=5000.0)
    sm.save(ticker, strategy_name, balance)
    sm.invalidate(ticker, strategy_name)  # for tests / hard reload

Backward-compat: realtime_runner._load_balance / _save_balance retained as
module-level shims that delegate to a singleton StateManager instance.
"""
from __future__ import annotations

from typing import Optional

from src.paper.runner import load_state, save_state
from src.paper.state import PaperBalance


class StateManager:
    """In-memory paper balance cache with disk persistence."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], PaperBalance] = {}

    def load(
        self,
        ticker: str,
        strategy_name: str,
        starting_usd: float = 5000.0,
    ) -> PaperBalance:
        """Cache hit → return cached. Cache miss → disk read + populate."""
        key = (ticker, strategy_name)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        bal = load_state(ticker, strategy_name, starting_usd=starting_usd)
        self._cache[key] = bal
        return bal

    def save(
        self,
        ticker: str,
        strategy_name: str,
        balance: PaperBalance,
    ) -> None:
        """Atomic: cache write + disk write (and shadow ledger if enabled)."""
        self._cache[(ticker, strategy_name)] = balance
        save_state(ticker, strategy_name, balance)

    def invalidate(self, ticker: str, strategy_name: str) -> None:
        """Drop cache entry (next load will hit disk)."""
        self._cache.pop((ticker, strategy_name), None)

    def clear(self) -> None:
        """Drop entire cache — for tests / hard reload."""
        self._cache.clear()

    def cache_size(self) -> int:
        return len(self._cache)

    def get_cached(
        self, ticker: str, strategy_name: str,
    ) -> Optional[PaperBalance]:
        """Return cached balance without disk fallback (None if miss)."""
        return self._cache.get((ticker, strategy_name))


# Module-level singleton — used by realtime_runner shim for backward compat.
_default_manager: Optional[StateManager] = None


def get_default_manager() -> StateManager:
    """Lazy singleton accessor."""
    global _default_manager
    if _default_manager is None:
        _default_manager = StateManager()
    return _default_manager


def reset_default_manager() -> None:
    """Test helper — drop singleton + cache."""
    global _default_manager
    _default_manager = None
