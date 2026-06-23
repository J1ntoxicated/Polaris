"""Venue-agnostic WebSocket streaming client base.

Design SSOT: ``.claude/plans/p4_ws_realtime_price_2026-06-01.md`` (M3/M5/M6).

``WSStreamClient`` drives: connect -> subscribe -> recv-loop -> on-msg callback,
with full-jitter exponential backoff, a heartbeat ping task, an idle staleness
watchdog (force-reconnect), an injectable session gate, and cooperative
``stop_evt`` teardown.

Invariants (AGGRESSIVE — degrade, never halt):
- Every wait that could delay teardown uses ``asyncio.wait_for(stop_evt.wait(),
  timeout=...)`` so ``stop_evt.set()`` ends it immediately (M6).
- Backoff is full-jitter exponential, 0.5s -> 30s cap.
- After ``max_reconnect_attempts`` consecutive failed connects the client
  transitions to a terminal ``rest_only`` state and stops retrying (no infinite
  reconnect storm — REST ingest remains the fallback, M3).
- Force-reconnect cancels the prior recv task before starting a new one so two
  recv loops can never run on overlapping sockets (double-recv guard).
- ``aclose()`` cancels every child task (ping / watchdog / the run driver) and
  closes the live websocket.

Subclasses implement the venue specifics:
- ``ws_url`` / ``subscribe_messages()`` / ``ping_message()``
- ``parse_message(raw)`` -> ``QuoteTick | None`` (None = control/ignored frame)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from polaris.core.data.schema import QuoteTick

logger = logging.getLogger(__name__)

BACKOFF_BASE_SEC = 0.5
BACKOFF_CAP_SEC = 30.0
DEFAULT_PING_INTERVAL_SEC = 20.0
DEFAULT_IDLE_TIMEOUT_SEC = 30.0
DEFAULT_MAX_RECONNECT_ATTEMPTS = 8


def full_jitter_backoff(attempt: int) -> float:
    """Full-jitter exponential backoff in ``[0, min(cap, base*2**attempt)]``.

    ``attempt`` is 0-based (first retry = 0). Returns a random delay so a fleet
    of clients does not reconnect in lockstep (thundering herd).
    """
    ceiling = min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC * (2.0**attempt))
    return random.uniform(0.0, ceiling)


class WSStreamClient:
    """Base streaming client. One instance == one venue WS connection."""

    venue: str = "ws"

    def __init__(
        self,
        *,
        on_quote: Callable[[QuoteTick], None],
        is_gated: Callable[[], bool] | None = None,
        ping_interval_sec: float = DEFAULT_PING_INTERVAL_SEC,
        idle_timeout_sec: float = DEFAULT_IDLE_TIMEOUT_SEC,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self._on_quote = on_quote
        self._is_gated = is_gated if is_gated is not None else (lambda: False)
        self._ping_interval = ping_interval_sec
        self._idle_timeout = idle_timeout_sec
        self._max_reconnect_attempts = max_reconnect_attempts

        self._ws: ClientConnection | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._stop_evt: asyncio.Event | None = None
        # Force-reconnect signal raised by the watchdog; the run loop observes it.
        self._reconnect_evt = asyncio.Event()
        self._last_msg_monotonic = 0.0
        # Terminal state — set once connect fails max_reconnect_attempts in a row.
        self.rest_only = False
        self.connected = False
        self.messages_received = 0
        # Increment 1 (incremental re-subscribe): the symbol set LAST sent to the
        # live socket. Seeded on (re)connect from ``subscribe_messages`` (the
        # startup subscription) so a delta after connect diffs against what the
        # socket actually carries. ``apply_subscription_delta`` re-syncs it.
        self._subscribed: set[str] = set()

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @property
    def ws_url(self) -> str:
        raise NotImplementedError

    def subscribe_messages(self) -> Iterable[str]:
        """Text frames to send right after connect (auth + subscribe)."""
        raise NotImplementedError

    def ping_message(self) -> str | None:
        """App-level keepalive frame, or None to rely on protocol pings."""
        return None

    def parse_message(self, raw: str | bytes) -> QuoteTick | None:
        """Parse one inbound frame to a QuoteTick, or None for control frames."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Incremental re-subscribe (Increment 1 — frozen-socket fix)
    # ------------------------------------------------------------------

    def current_subscription(self) -> set[str]:
        """The venue's CURRENT desired symbol/epic set (subclass override).

        Base default = the last set sent to the socket (no-op delta). A venue that
        supports incremental re-subscribe returns its live ``set_symbols`` /
        ``set_epics`` set here so ``apply_subscription_delta`` can diff it.
        """
        return set(self._subscribed)

    def subscribe_delta_frames(
        self, added: set[str], removed: set[str]
    ) -> list[str]:
        """Frames to (un)subscribe the delta on a LIVE socket (subclass override).

        Default = ``[]`` (no incremental support → the change takes effect on the
        next reconnect, the pre-Increment-1 behaviour). A venue overrides this to
        emit its subscribe(added) + unsubscribe(removed) frames.
        """
        _ = added, removed
        return []

    async def apply_subscription_delta(self) -> None:
        """Send the live (un)subscribe delta on a CONNECTED socket; else no-op.

        Diffs ``current_subscription()`` (the desired set) against ``_subscribed``
        (what the socket carries) and, when connected, sends the venue delta
        frames — so a focus churn reaches the LIVE socket WITHOUT a reconnect (the
        audit's 'frozen socket' fix). Best-effort + degrade-never-halt: a send
        failure is suppressed (the watchdog/reconnect remains the backstop). When
        NOT connected the desired set is left for the next reconnect's
        ``subscribe_messages`` (unchanged fallback). flow_not_block: the socket now
        FOLLOWS focus — a FLOW INCREASE, never a throttle.
        """
        desired = self.current_subscription()
        if not self.connected or self._ws is None:
            # Defer to the next reconnect; keep _subscribed as the live truth so a
            # reconnect re-seeds from subscribe_messages (the latest set).
            return
        added = desired - self._subscribed
        removed = self._subscribed - desired
        if not added and not removed:
            return
        frames = self.subscribe_delta_frames(added, removed)
        ws = self._ws
        for frame in frames:
            with contextlib.suppress(Exception):
                await ws.send(frame)
        # Re-sync the live-truth set regardless of per-frame send outcome: the
        # next delta diffs against the new desired set (a dropped frame is healed
        # by the next reconnect's full subscribe).
        self._subscribed = set(desired)

    async def _open(self) -> ClientConnection:
        """Open the websocket. Overridable for venue-specific headers.

        ``ping_timeout=None``: keep the library's keepalive PING (server-side
        idle keepalive) but never close on a slow/blocked PONG. The live event
        loop is occasionally blocked >20s by the bar pipeline (GPT/DB), which
        tripped the default 20s pong timeout → recurring 1011 drops that starved
        the tick engine. Liveness of a TRULY dead socket is owned by the idle
        watchdog (``_last_msg_monotonic`` → force-reconnect), not the pong clock.
        """
        return await websockets.connect(self.ws_url, ping_timeout=None)

    # ------------------------------------------------------------------
    # Public driver
    # ------------------------------------------------------------------

    async def run(self, stop_evt: asyncio.Event) -> None:
        """Connect/recv with reconnect until ``stop_evt`` or REST-only give-up."""
        self._stop_evt = stop_evt
        attempt = 0
        while not stop_evt.is_set():
            if self._is_gated():
                # Off-session (weekend / RTH closed): wait, don't hammer connect.
                if await self._sleep_or_stop(min(self._idle_timeout, 5.0)):
                    break
                attempt = 0
                continue
            try:
                await self._connect_and_stream(stop_evt)
                # Clean exit of the stream loop (stop or force-reconnect) resets
                # the failure counter — only *failed connects* count toward cap.
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — degrade, never halt
                # A drop AFTER a successful connect+subscribe (e.g. a keepalive
                # ping timeout) is NOT a connect-failure: the endpoint IS
                # reachable, so reconnect forever and do NOT count it toward the
                # REST-only give-up cap (which is only for an UNREACHABLE
                # endpoint). ``self.connected`` is still True here — the finally
                # that resets it runs AFTER this except. Without this, transient
                # connect-then-drop cycles accumulate across the session and
                # permanently kill the WS the tick engine depends on (live:
                # 8 connect-then-drop over ~1h → 'giving up' → tick engine idle).
                if self.connected:
                    attempt = 0
                else:
                    attempt += 1
                logger.warning(
                    "[%s ws] connection error (attempt %d/%d): %r",
                    self.venue, attempt, self._max_reconnect_attempts, exc,
                )
                if attempt >= self._max_reconnect_attempts:
                    self.rest_only = True
                    logger.error(
                        "[%s ws] %d consecutive failures — giving up, REST-only mode",
                        self.venue, attempt,
                    )
                    break
                delay = full_jitter_backoff(attempt - 1)
                if await self._sleep_or_stop(delay):
                    break
            finally:
                await self._teardown_connection()

    async def _connect_and_stream(self, stop_evt: asyncio.Event) -> None:
        self._reconnect_evt.clear()
        self._ws = await self._open()
        self.connected = True
        self._last_msg_monotonic = time.monotonic()
        for msg in self.subscribe_messages():
            await self._ws.send(msg)
        # Seed the incremental-resubscribe truth set: the socket now carries the
        # current desired set (a subsequent delta diffs against this).
        self._subscribed = self.current_subscription()
        logger.info("[%s ws] connected + subscribed", self.venue)

        # recv loop runs as its own task so the watchdog can cancel it on stall
        # (force-reconnect) without leaving a second recv loop on a stale socket.
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._ping_task = asyncio.create_task(self._ping_loop(stop_evt))
        self._watchdog_task = asyncio.create_task(self._watchdog_loop(stop_evt))

        # Wait for whichever fires first: stop, forced reconnect, or recv ending.
        stop_wait = asyncio.create_task(stop_evt.wait())
        recon_wait = asyncio.create_task(self._reconnect_evt.wait())
        done, pending = await asyncio.wait(
            {self._recv_task, stop_wait, recon_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in (stop_wait, recon_wait):
            if t in pending:
                t.cancel()
        # If recv ended with an exception, re-raise so run() backs off. A
        # *cancelled* recv task means the watchdog forced a reconnect (it cancels
        # recv before setting _reconnect_evt) — that is the expected force-
        # reconnect path, NOT a failure, so swallow the CancelledError and let
        # run() loop into a fresh connect (otherwise it would propagate out of
        # run() and kill the client permanently on the first idle timeout).
        if self._recv_task in done and not self._recv_task.cancelled():
            exc = self._recv_task.exception()
            if exc is not None:
                raise exc

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        ws = self._ws
        burst = 0
        async for raw in ws:  # one await per message → cooperative yield
            self._last_msg_monotonic = time.monotonic()
            self.messages_received += 1
            tick = self.parse_message(raw)
            if tick is not None:
                self._on_quote(tick)
            burst += 1
            if burst >= 64:
                # Starvation guard: under a burst, yield so the flush task and
                # the rest of the loop get scheduled.
                burst = 0
                await asyncio.sleep(0)

    async def _ping_loop(self, stop_evt: asyncio.Event) -> None:
        while not stop_evt.is_set():
            if await self._sleep_or_stop(self._ping_interval):
                return
            ws = self._ws
            if ws is None:
                return
            msg = self.ping_message()
            try:
                if msg is not None:
                    await ws.send(msg)
                else:
                    await ws.ping()
            except Exception:  # noqa: BLE001 — ping failure surfaces via recv/watchdog
                return

    async def _watchdog_loop(self, stop_evt: asyncio.Event) -> None:
        while not stop_evt.is_set():
            if await self._sleep_or_stop(self._idle_timeout / 2.0):
                return
            idle = time.monotonic() - self._last_msg_monotonic
            if idle >= self._idle_timeout:
                logger.warning(
                    "[%s ws] idle %.1fs >= %.1fs — forcing reconnect",
                    self.venue, idle, self._idle_timeout,
                )
                # Cancel the recv task NOW so no second recv loop starts on the
                # next connect while this stale socket still has one (double-recv).
                if self._recv_task is not None:
                    self._recv_task.cancel()
                self._reconnect_evt.set()
                return

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def _sleep_or_stop(self, timeout: float) -> bool:
        """Wait ``timeout`` seconds or until ``stop_evt``. Returns True if stopped."""
        assert self._stop_evt is not None
        if timeout <= 0:
            return self._stop_evt.is_set()
        try:
            await asyncio.wait_for(self._stop_evt.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def _teardown_connection(self) -> None:
        """Cancel per-connection child tasks and close the live websocket."""
        for task in (self._recv_task, self._ping_task, self._watchdog_task):
            if task is not None:
                task.cancel()
        for task in (self._recv_task, self._ping_task, self._watchdog_task):
            if task is not None:
                await _swallow(task)
        self._recv_task = None
        self._ping_task = None
        self._watchdog_task = None
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        self.connected = False

    async def aclose(self) -> None:
        """Full shutdown — set stop, tear down children + socket."""
        if self._stop_evt is not None:
            self._stop_evt.set()
        await self._teardown_connection()


async def _swallow(task: asyncio.Task[Any] | Awaitable[Any]) -> None:
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
