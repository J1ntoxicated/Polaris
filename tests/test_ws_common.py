"""Unit tests for WSStreamClient base (P4 WS foundation, M3/M5/M6).

Covers:
- full_jitter_backoff bounds (0.5s -> 30s cap, never exceeds ceiling).
- recv frames flow to the on_quote callback (parse_message contract).
- teardown is immediate: stop_evt.set() ends run() promptly via wait_for.
- REST-only give-up after max consecutive connect failures (no infinite storm).
- aclose() cancels children + closes the socket.
"""

from __future__ import annotations

import asyncio
import time

from polaris.core.data.schema import QuoteTick
from polaris.venues.ws_common import (
    BACKOFF_BASE_SEC,
    BACKOFF_CAP_SEC,
    WSStreamClient,
    full_jitter_backoff,
)


def _qt(mid: float) -> QuoteTick:
    return QuoteTick(
        instrument_id="okx:BTC-USDT",
        venue="okx",
        symbol="BTC-USDT",
        ts=1,
        bid=mid - 0.5,
        ask=mid + 0.5,
        mid=mid,
        spread_bps=10.0,
        source="okx_ws",
    )


class FakeWS:
    """Minimal async-iterable websocket stand-in."""

    def __init__(self, frames: list[str], *, hold_open: bool = False) -> None:
        self._frames = frames
        self._hold_open = hold_open
        self.sent: list[str] = []
        self.closed = False
        self.pinged = 0

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def ping(self) -> None:
        self.pinged += 1

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> FakeWS:
        self._it = iter(self._frames)
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._it)
        except StopIteration:
            if self._hold_open:
                # Keep the socket "open" so recv doesn't end the stream loop;
                # the test drives teardown via stop_evt instead.
                await asyncio.sleep(3600)
            raise StopAsyncIteration from None


class _Client(WSStreamClient):
    venue = "fake"

    def __init__(self, ws: FakeWS, **kw) -> None:
        self._fake = ws
        super().__init__(**kw)

    @property
    def ws_url(self) -> str:
        return "wss://example.test/ws"

    def subscribe_messages(self):
        return ["SUB"]

    def parse_message(self, raw):
        return _qt(float(raw)) if raw not in ("CTRL",) else None

    async def _open(self):
        return self._fake


# --- backoff -----------------------------------------------------------------


def test_backoff_within_ceiling_and_capped() -> None:
    for attempt in range(0, 20):
        d = full_jitter_backoff(attempt)
        ceiling = min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC * (2.0**attempt))
        assert 0.0 <= d <= ceiling
        assert d <= BACKOFF_CAP_SEC


def test_backoff_first_attempt_small() -> None:
    # attempt 0 ceiling = 0.5s
    for _ in range(50):
        assert 0.0 <= full_jitter_backoff(0) <= BACKOFF_BASE_SEC


# --- recv -> callback --------------------------------------------------------


async def test_frames_flow_to_callback() -> None:
    got: list[float] = []
    ws = FakeWS(["100", "CTRL", "101"], hold_open=True)
    c = _Client(ws, on_quote=lambda q: got.append(q.mid))
    stop = asyncio.Event()
    task = asyncio.create_task(c.run(stop))
    # Give the recv loop time to drain the three frames.
    for _ in range(50):
        if len(got) >= 2:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert got == [100.0, 101.0]  # CTRL frame (None) skipped
    assert ws.sent == ["SUB"]  # subscribe sent on connect


# --- teardown immediacy ------------------------------------------------------


async def test_stop_ends_run_promptly() -> None:
    # No frames, socket held open → run() blocks on stop_evt; ping_interval &
    # idle_timeout are large so only stop_evt can end it quickly.
    ws = FakeWS([], hold_open=True)
    c = _Client(
        ws,
        on_quote=lambda q: None,
        ping_interval_sec=999.0,
        idle_timeout_sec=999.0,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(c.run(stop))
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert time.monotonic() - t0 < 0.5  # immediate, not blocked on a long timer
    assert ws.closed


# --- REST-only give-up -------------------------------------------------------


async def test_rest_only_after_max_failures() -> None:
    attempts = {"n": 0}

    class _Failing(_Client):
        async def _open(self):
            attempts["n"] += 1
            raise ConnectionError("boom")

    ws = FakeWS([])
    c = _Failing(ws, on_quote=lambda q: None, max_reconnect_attempts=3)
    stop = asyncio.Event()
    # Backoff is randomized small (0.5s ceiling on early attempts); cap the test.
    await asyncio.wait_for(c.run(stop), timeout=5.0)
    assert c.rest_only is True
    assert attempts["n"] == 3  # gave up exactly at the cap, no infinite storm


async def test_gated_does_not_connect() -> None:
    opened = {"n": 0}

    class _Gated(_Client):
        async def _open(self):
            opened["n"] += 1
            return self._fake

    ws = FakeWS([], hold_open=True)
    c = _Gated(
        ws,
        on_quote=lambda q: None,
        is_gated=lambda: True,
        idle_timeout_sec=0.1,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(c.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert opened["n"] == 0  # gate kept it from ever opening a socket


# --- watchdog force-reconnect ------------------------------------------------


async def test_idle_watchdog_forces_reconnect_not_death() -> None:
    """An idle socket → watchdog cancels recv + forces reconnect (NOT death).

    Regression: the watchdog cancels ``_recv_task`` before raising
    ``_reconnect_evt``; a cancelled recv task must be treated as the expected
    force-reconnect path, not re-raised as a failure (which would propagate out
    of ``run()`` and kill the client permanently on the first idle timeout).
    """
    opens = {"n": 0}

    class _IdleWS(FakeWS):
        # __anext__ with no frames + hold_open=True blocks (idle) forever.
        pass

    class _Reconnecting(WSStreamClient):
        venue = "fake"

        @property
        def ws_url(self) -> str:
            return "wss://example.test/ws"

        def subscribe_messages(self):
            return ["SUB"]

        def parse_message(self, raw):
            return None

        async def _open(self):
            opens["n"] += 1
            return _IdleWS([], hold_open=True)

    c = _Reconnecting(
        on_quote=lambda q: None, idle_timeout_sec=0.1, ping_interval_sec=999.0
    )
    stop = asyncio.Event()
    task = asyncio.create_task(c.run(stop))
    await asyncio.sleep(0.5)
    # The client must have reconnected (>1 open) and still be alive.
    assert opens["n"] >= 2, "watchdog should force reconnects, not kill the task"
    assert not task.done()
    assert c.rest_only is False
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


# --- aclose ------------------------------------------------------------------


async def test_aclose_closes_socket() -> None:
    ws = FakeWS([], hold_open=True)
    c = _Client(ws, on_quote=lambda q: None, ping_interval_sec=999, idle_timeout_sec=999)
    stop = asyncio.Event()
    task = asyncio.create_task(c.run(stop))
    await asyncio.sleep(0.05)
    await c.aclose()
    await asyncio.wait_for(task, timeout=2.0)
    assert ws.closed
    assert stop.is_set()
