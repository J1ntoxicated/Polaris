"""AsyncTokenBucket — venue-agnostic request pacing (flow_not_block).

DEMO/PAPER paper-trading bot. The bucket PACES outgoing REST calls to stay
under a venue's published rate cap (OKX candles 20/2s, Alpaca data 200/min). It
never REJECTS a call — ``acquire`` waits for a token (pacing), and only an
explicit ``timeout`` makes it return ``False`` so the caller can defer a
non-urgent bar re-fetch to the next cadence (degrade-never-crash). The live
entry/size/exit price path never flows through the bucket — aggressive bias and
flow_not_block are intact.
"""

from __future__ import annotations

import asyncio

import pytest

from polaris.core.ratelimit import AsyncTokenBucket


def test_initial_burst_up_to_capacity_is_immediate() -> None:
    """A fresh bucket grants ``capacity`` tokens with zero wait (full burst)."""
    clock = {"t": 100.0}
    bucket = AsyncTokenBucket(rate=20, per_sec=2.0, now=lambda: clock["t"])
    # 20 immediate acquisitions, no clock advance needed.
    for _ in range(20):
        assert asyncio.run(bucket.acquire(timeout=0.0)) is True


def test_empty_bucket_zero_timeout_returns_false_no_block() -> None:
    """When drained, a zero-timeout acquire returns False instead of blocking.

    This is the storm-guard: a non-urgent bar re-fetch defers to the next
    cadence rather than queueing unboundedly (flow_not_block — the symbol is
    not blocked, just paced; it fetches next tick). NEVER raises.
    """
    clock = {"t": 0.0}
    bucket = AsyncTokenBucket(rate=2, per_sec=2.0, now=lambda: clock["t"])
    assert asyncio.run(bucket.acquire(timeout=0.0)) is True
    assert asyncio.run(bucket.acquire(timeout=0.0)) is True
    # Drained, clock frozen → no refill → defer.
    assert asyncio.run(bucket.acquire(timeout=0.0)) is False


def test_refill_after_elapsed_time_grants_again() -> None:
    """Tokens refill continuously at ``rate/per_sec``; elapsed time re-grants."""
    clock = {"t": 0.0}
    # 10 tokens/sec refill (20 per 2s).
    bucket = AsyncTokenBucket(rate=20, per_sec=2.0, now=lambda: clock["t"])
    for _ in range(20):
        assert asyncio.run(bucket.acquire(timeout=0.0)) is True
    assert asyncio.run(bucket.acquire(timeout=0.0)) is False  # drained
    clock["t"] = 1.0  # 1s elapsed → +10 tokens
    for _ in range(10):
        assert asyncio.run(bucket.acquire(timeout=0.0)) is True
    assert asyncio.run(bucket.acquire(timeout=0.0)) is False  # drained again


def test_capacity_caps_accumulation() -> None:
    """Idle time does not let tokens accumulate past ``capacity`` (no burst debt)."""
    clock = {"t": 0.0}
    bucket = AsyncTokenBucket(rate=5, per_sec=1.0, now=lambda: clock["t"])
    clock["t"] = 1000.0  # huge idle — would be 5000 tokens uncapped
    granted = 0
    while asyncio.run(bucket.acquire(timeout=0.0)):
        granted += 1
        if granted > 100:  # safety
            break
    assert granted == 5  # capped at capacity, not 5000


def test_acquire_waits_then_succeeds_within_timeout() -> None:
    """A positive timeout WAITS for a refill (pacing) and then succeeds.

    Uses real asyncio.sleep with a real monotonic clock (default ``now``) so the
    wait/refill arithmetic is exercised end-to-end, not just the frozen-clock
    branch. Bounded tiny so the test stays fast.
    """
    bucket = AsyncTokenBucket(rate=2, per_sec=0.1)  # 20 tokens/sec → ~50ms/token

    async def drive() -> bool:
        assert await bucket.acquire(timeout=1.0) is True
        assert await bucket.acquire(timeout=1.0) is True
        # Drained; next needs a refill (~50ms). Timeout 1s is ample.
        return await bucket.acquire(timeout=1.0)

    assert asyncio.run(drive()) is True


def test_concurrent_acquire_never_exceeds_rate() -> None:
    """Under concurrent fan-out, total immediate grants never exceed capacity.

    Mirrors the bar-ingest gather: many coroutines hit the bucket at once. The
    asyncio.Lock must serialize the token math so no over-grant slips through
    (the over-limit burst that triggered the 429 storm).
    """
    clock = {"t": 0.0}
    bucket = AsyncTokenBucket(rate=8, per_sec=2.0, now=lambda: clock["t"])

    async def drive() -> int:
        results = await asyncio.gather(
            *(bucket.acquire(timeout=0.0) for _ in range(50))
        )
        return sum(1 for r in results if r)

    granted = asyncio.run(drive())
    assert granted == 8  # exactly capacity, never 9+


def test_invalid_config_rejected() -> None:
    """rate/per_sec must be positive — a misconfig is a programming error."""
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate=0, per_sec=2.0)
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate=20, per_sec=0.0)
