"""Capital.com streaming WebSocket client (P4 — M4).

Design SSOT: ``.claude/plans/p4_ws_realtime_price_2026-06-01.md`` (M4).

Endpoint: ``wss://api-streaming-capital.backend-capital.com/connect``.

Token handling (M4 — the design-review correction):
- Capital session tokens (CST + X-SECURITY-TOKEN) expire after **10 min of
  inactivity**, not on a fixed lifetime. The WS keepalive ``ping`` (sent every
  <10 min) counts as activity, so a *streaming* session never idle-expires —
  steady-state needs NO ``ensure_tokens()`` call.
- ``ensure_tokens()`` is therefore called **only** before a (re)connect that
  follows a >10-min idle gap (long disconnect, weekend/session-gate off→on).
  ``_ensure_session_fresh`` decides this from the last-activity monotonic stamp.
- The loop-owned ``CapitalSession`` is reused (its CST + security token are read
  off ``session.tokens``); we never mint our own. 401/expired re-login already
  lives in ``CapitalSession.request`` / ``ensure_tokens``.

Subscribe: ``marketData.subscribe`` with the epics list chunked ≤ 40 per frame.
Gating: FX / weekend off-session via ``is_gated`` (no connect when closed).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import websockets
from websockets.asyncio.client import ClientConnection

from polaris.core.data.canonical import capital_market_to_quote_tick
from polaris.core.data.schema import QuoteTick
from polaris.venues.ws_common import WSStreamClient

logger = logging.getLogger(__name__)

CAPITAL_WS_URL: str = "wss://api-streaming-capital.backend-capital.com/connect"
# Subscribe at most this many epics per ``marketData.subscribe`` frame.
CAPITAL_EPIC_CHUNK: int = 40
# Keepalive must be < 10 min so the token never idle-expires while streaming. We
# ping every 9 min (540 s) — the same conservative budget the REST session uses.
CAPITAL_PING_INTERVAL_SEC: float = 540.0
# A reconnect after this much idle is a "long gap" → re-auth before reconnecting.
CAPITAL_LONG_GAP_SEC: float = 600.0  # 10 min


class _SessionLike(Protocol):
    @property
    def tokens(self) -> Any: ...
    async def ensure_tokens(self) -> Any: ...


class CapitalMarketWS(WSStreamClient):
    """Streams Capital ``marketData`` quotes for a set of epics → QuoteTicks."""

    venue = "capital"

    def __init__(
        self,
        *,
        epics: Iterable[str],
        session: _SessionLike,
        on_quote: Callable[[QuoteTick], None],
        **kw: object,
    ) -> None:
        kw.setdefault("ping_interval_sec", CAPITAL_PING_INTERVAL_SEC)
        super().__init__(on_quote=on_quote, **kw)  # type: ignore[arg-type]
        self._epics = list(dict.fromkeys(epics))
        self._session = session
        # Last time we observed WS activity (connect / message). monotonic, M6.
        # 0.0 means "never connected yet" → first connect is treated as a long gap.
        self._last_activity_monotonic = 0.0

    @property
    def ws_url(self) -> str:
        return CAPITAL_WS_URL

    def set_epics(self, epics: Iterable[str]) -> None:
        """Replace the subscribed epic set (universe change → re-subscribe)."""
        self._epics = list(dict.fromkeys(epics))

    # ------------------------------------------------------------------
    # Token freshness (M4)
    # ------------------------------------------------------------------

    def _mark_activity(self) -> None:
        self._last_activity_monotonic = time.monotonic()

    async def _ensure_session_fresh(self) -> None:
        """Re-auth ONLY before a (re)connect after a >10-min idle gap (M4).

        Steady-state keepalive pings keep the token warm, so we skip
        ``ensure_tokens`` unless the last activity is older than the inactivity
        window (or we never connected).
        """
        idle = time.monotonic() - self._last_activity_monotonic
        if self._last_activity_monotonic == 0.0 or idle >= CAPITAL_LONG_GAP_SEC:
            await self._session.ensure_tokens()

    async def _open(self) -> ClientConnection:
        # Long-gap reconnects re-auth here (steady-state reconnects do not).
        await self._ensure_session_fresh()
        self._mark_activity()
        # ping_timeout=None: keep keepalive PING, never drop on a slow/blocked
        # PONG (the loop is occasionally blocked >20s by the bar pipeline → 1011
        # drops that starved the tick engine). The idle watchdog owns dead-socket
        # detection.
        return await websockets.connect(self.ws_url, ping_timeout=None)

    # ------------------------------------------------------------------
    # Subscribe / keepalive
    # ------------------------------------------------------------------

    def _auth(self) -> tuple[str, str]:
        toks = self._session.tokens
        if toks is None:
            return "", ""
        return toks.cst, toks.security_token

    def subscribe_messages(self) -> Iterable[str]:
        cst, sec = self._auth()
        out: list[str] = []
        for i in range(0, len(self._epics), CAPITAL_EPIC_CHUNK):
            chunk = self._epics[i : i + CAPITAL_EPIC_CHUNK]
            out.append(
                json.dumps(
                    {
                        "destination": "marketData.subscribe",
                        "correlationId": uuid.uuid4().hex,
                        "cst": cst,
                        "securityToken": sec,
                        "payload": {"epics": chunk},
                    }
                )
            )
        return out

    def ping_message(self) -> str | None:
        cst, sec = self._auth()
        return json.dumps(
            {
                "destination": "ping",
                "correlationId": uuid.uuid4().hex,
                "cst": cst,
                "securityToken": sec,
            }
        )

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def parse_message(self, raw: str | bytes) -> QuoteTick | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(msg, dict):
            return None
        # Activity for token-freshness purposes (any inbound frame).
        self._mark_activity()
        if msg.get("destination") != "quote":
            return None  # ping ack / subscribe ack / OHLC etc.
        payload = msg.get("payload")
        if not isinstance(payload, dict) or "epic" not in payload:
            return None
        # Capital WS uses ``ofr`` for the ask; the REST converter expects
        # ``offer``. Normalize before delegating to the pure converter.
        norm = dict(payload)
        if "offer" not in norm and "ofr" in norm:
            norm["offer"] = norm["ofr"]
        try:
            tick = capital_market_to_quote_tick(norm, ts=int(time.time()))
        except (KeyError, ValueError):
            return None
        return _retag(tick, "capital_ws")

    # ------------------------------------------------------------------
    # Gating (weekend / off-session) — caller wires this to ``is_gated``.
    # ------------------------------------------------------------------

    @staticmethod
    def _is_weekend(*, ts: int | float | None = None) -> bool:
        """True on Sat/Sun UTC (FX/CFD closed). Coarse weekend gate."""
        t = ts if ts is not None else time.time()
        wd = dt.datetime.fromtimestamp(int(t), tz=dt.UTC).weekday()
        return wd >= 5  # 5 = Sat, 6 = Sun


def _retag(tick: QuoteTick, source: str) -> QuoteTick:
    return dataclasses.replace(tick, source=source)
