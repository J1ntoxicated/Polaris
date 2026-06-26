"""Venue-agnostic async token bucket — REST request PACING (flow_not_block).

Root-cause fix for the OKX / Alpaca ``429 Too Many Requests`` storm (live: OKX
18k candles 429 + Alpaca 17k data 429). The bar-ingest fan-out fired far more
requests per window than the venue cap allows (OKX market-data 20 req / 2 s per
IP; Alpaca basic data 200 req / min), and there was no client-side pacing — the
only guard was an after-the-fact backoff that retried INTO the same overage.

``AsyncTokenBucket`` paces calls to stay UNDER the cap. It does not block trading:
``acquire`` waits for a token (pacing, not rejection). A caller may pass a small
``timeout`` to bound that wait; ``acquire`` then returns ``False`` if no token
freed up in time. The bar-ingest callers use this as a SOFT cap — on a ``False``
they proceed token-less rather than drop the bar (flow_not_block), so a saturated
bucket can only pace, never starve, the fan-out; the per-venue 429 backoff is the
safety net for that rare over-cap pass. The live entry / size / exit price path
never flows through here (it rides the WS quote stream), so no trade is ever
gated. flow_not_block + degrade-never-crash: the bucket NEVER raises.

Design
------
- Continuous refill at ``rate / per_sec`` tokens per second, capped at ``rate``
  (no burst-debt accumulation after an idle gap).
- An ``asyncio.Lock`` serializes the token arithmetic so a concurrent gather
  cannot over-grant (the exact over-limit burst that caused the storm).
- ``now`` is injectable (monotonic by default) so tests drive a frozen clock.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Final

__all__ = ["AsyncTokenBucket"]

# Below this we treat a wait as "immediate" — avoids a 0-length sleep churn when
# a token is a hair away (sub-millisecond) from refilling.
_MIN_SLEEP_SEC: Final[float] = 0.001


class AsyncTokenBucket:
    """Async token bucket pacing REST calls to ``rate`` per ``per_sec`` seconds.

    Parameters
    ----------
    rate:
        Token capacity AND the number replenished every ``per_sec`` seconds
        (e.g. ``rate=20, per_sec=2.0`` → OKX market-data 20 req / 2 s = 10/s).
    per_sec:
        The replenishment window in seconds.
    now:
        Monotonic clock source (injectable for tests). Defaults to
        ``time.monotonic``.
    """

    __slots__ = ("_capacity", "_refill_per_sec", "_tokens", "_now", "_last", "_lock")

    def __init__(
        self,
        *,
        rate: int,
        per_sec: float,
        now: Callable[[], float] | None = None,
    ) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate}")
        if per_sec <= 0.0:
            raise ValueError(f"per_sec must be positive, got {per_sec}")
        self._capacity: float = float(rate)
        self._refill_per_sec: float = float(rate) / per_sec
        self._tokens: float = float(rate)  # start full (allow the initial burst)
        self._now: Callable[[], float] = now if now is not None else time.monotonic
        self._last: float = self._now()
        self._lock = asyncio.Lock()

    def _refill_locked(self) -> None:
        """Add elapsed-time tokens, capped at capacity. Caller holds the lock."""
        t = self._now()
        elapsed = t - self._last
        if elapsed > 0.0:
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._refill_per_sec
            )
            self._last = t

    async def acquire(self, *, timeout: float | None = None) -> bool:
        """Take one token, returning ``True`` on success.

        ``timeout``:
        - ``None`` → wait as long as needed (pure pacing; always returns True).
        - ``0.0`` → take a token only if one is available right now, else return
          ``False`` immediately (no block, no sleep).
        - ``> 0`` → wait up to ``timeout`` seconds for a token; return ``False``
          if it does not free up in time.

        The caller decides what ``False`` means (the bar-ingest hot path treats it
        as a soft cap and proceeds token-less — see ``okx``/``alpaca`` adapters).
        NEVER raises. Repeated tiny sleeps re-check under the lock so a concurrent
        fan-out cannot over-grant.
        """
        deadline: float | None = None
        if timeout is not None and timeout > 0.0:
            deadline = self._now() + timeout
        while True:
            async with self._lock:
                self._refill_locked()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                # Not enough — compute the wait until the next whole token.
                deficit = 1.0 - self._tokens
                wait = deficit / self._refill_per_sec
            # Outside the lock: decide whether to defer or sleep-and-retry.
            if timeout is not None:
                if timeout <= 0.0:
                    return False
                remaining = deadline - self._now() if deadline is not None else 0.0
                if remaining <= 0.0:
                    return False
                wait = min(wait, remaining)
            await self._sleep(max(wait, _MIN_SLEEP_SEC))

    async def _sleep(self, delay: float) -> None:
        """Indirection so tests can monkeypatch the pacing sleep to a no-op."""
        await asyncio.sleep(delay)
