"""OKX public ``tickers`` WebSocket client (P4 — M3).

Design SSOT: ``.claude/plans/p4_ws_realtime_price_2026-06-01.md`` (M3).

The OKX REST resolver (``resolve_okx_base_url``) only yields a REST host. WS uses
a *different* host family, so ``resolve_okx_ws_url`` maps the same env value to
the matching public WS endpoint:

    REST host             → public WS host
    us.okx.com (default)  → wss://wspap.okx.com:8443/ws/v5/public   (demo)
    wspap.okx.com (demo)  → wss://wspap.okx.com:8443/ws/v5/public   (demo)
    www.okx.com (prod)    → wss://ws.okx.com:8443/ws/v5/public      (prod)

M3 verification (2026-06-01 live DNS + handshake): the design's assumed
``ws.us.okx.com`` host **does NOT resolve** — OKX publishes no US-region WS
subdomain. The ``tickers`` channel is **unauthenticated public market data**
(no signing, region-agnostic), so the US-region demo *trading* keys map to the
**demo public WS** (``wspap.okx.com``), which serves live BTC-USDT ticks and
matches the simulated-trading context. ``ws.okx.com`` (prod) was also verified
live. OKX drops a connection after 30 s of silence and expects a literal text
``"ping"`` keepalive (replies ``"pong"``); we send it every <25 s. Connection
success itself is the availability check (no separate smoke) — if connects fail
``max_reconnect_attempts`` in a row the base client transitions to ``rest_only``
and REST bar ingest remains the fallback.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from collections.abc import Callable, Iterable
from urllib.parse import urlparse

from polaris.core.data.canonical import okx_ticker_to_quote_tick
from polaris.core.data.schema import QuoteTick
from polaris.venues.ws_common import WSStreamClient

logger = logging.getLogger(__name__)

OKX_WS_DEMO: str = "wss://wspap.okx.com:8443/ws/v5/public"
OKX_WS_PROD: str = "wss://ws.okx.com:8443/ws/v5/public"

# OKX disconnects after 30 s idle and wants a keepalive well under that. We ping
# every 20 s (the base-client default) which is < 25 s.
OKX_PING_INTERVAL_SEC: float = 20.0


def resolve_okx_ws_url(rest_env_value: str | None) -> str:
    """Map the OKX REST base-URL env value to the matching public WS host.

    The public ``tickers`` feed is unauthenticated + region-agnostic and OKX has
    NO US WS subdomain (``ws.us.okx.com`` does not resolve — M3 verified). So the
    US-region demo REST host (default / ``us.okx.com``) maps to the **demo** WS
    (``wspap.okx.com``), matching the simulated-trading context. Only an explicit
    prod REST host (``www.okx.com``) maps to the prod WS (``ws.okx.com``).
    """
    if not rest_env_value:
        return OKX_WS_DEMO
    host = (urlparse(rest_env_value).netloc or "").lower().split(":")[0]
    if host == "www.okx.com":
        # Explicit international prod host → prod public WS.
        return OKX_WS_PROD
    # us.okx.com (US demo) and wspap.okx.com (demo) both → demo public WS.
    return OKX_WS_DEMO


class OKXTickerWS(WSStreamClient):
    """Streams OKX ``tickers`` for a set of instIds → canonical QuoteTicks."""

    venue = "okx"

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        on_quote: Callable[[QuoteTick], None],
        rest_env_value: str | None = None,
        **kw: object,
    ) -> None:
        # OKX keepalive must be < 25 s; honour an explicit override but default low.
        kw.setdefault("ping_interval_sec", OKX_PING_INTERVAL_SEC)
        super().__init__(on_quote=on_quote, **kw)  # type: ignore[arg-type]
        self._symbols = list(dict.fromkeys(symbols))
        self._ws_url = resolve_okx_ws_url(rest_env_value)

    @property
    def ws_url(self) -> str:
        return self._ws_url

    def set_symbols(self, symbols: Iterable[str]) -> None:
        """Replace the subscribed instId set (universe change → re-subscribe)."""
        self._symbols = list(dict.fromkeys(symbols))

    def subscribe_messages(self) -> Iterable[str]:
        args = [{"channel": "tickers", "instId": s} for s in self._symbols]
        return [json.dumps({"op": "subscribe", "args": args})]

    def ping_message(self) -> str | None:
        # OKX expects the literal text frame "ping" (it replies "pong").
        return "ping"

    def parse_message(self, raw: str | bytes) -> QuoteTick | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        if raw == "pong":  # keepalive reply
            return None
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(msg, dict):
            return None
        # Control frames: {"event": "subscribe"|"error", ...}
        if "event" in msg:
            if msg.get("event") == "error":
                logger.warning("[okx ws] error frame: %s", msg.get("msg"))
            return None
        data = msg.get("data")
        if not isinstance(data, list) or not data:
            return None
        row = data[0]
        if not isinstance(row, dict):
            return None
        try:
            tick = okx_ticker_to_quote_tick(row, ts=int(time.time()))
        except (KeyError, ValueError):
            return None
        # Tag the source as the WS path (canonical converter hardcodes _rest).
        return _retag(tick, "okx_ws")


def _retag(tick: QuoteTick, source: str) -> QuoteTick:
    """Return a copy of ``tick`` with the ``source`` field set (frozen slots)."""
    return dataclasses.replace(tick, source=source)
