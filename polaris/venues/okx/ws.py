"""OKX public ``tickers`` WebSocket client (P4 — M3).

Design SSOT: ``.claude/plans/p4_ws_realtime_price_2026-06-01.md`` (M3).

The OKX REST resolver (``resolve_okx_base_url``) only yields a REST host. WS uses
a *different* host family, so ``resolve_okx_ws_url`` maps the same env value to
the matching public WS endpoint:

    REST host             → public WS host
    us.okx.com (default)  → wss://wsuspap.okx.com:8443/ws/v5/public  (US demo)
    wspap.okx.com (demo)  → wss://wspap.okx.com:8443/ws/v5/public    (global demo)
    www.okx.com (prod)    → wss://ws.okx.com:8443/ws/v5/public        (prod)

Endpoint reconciliation (Jin official US reference): the US-region demo WS host is
``wsuspap.okx.com`` — it EXISTS and is live-verified (BTC-USDT ticker received;
region-consistent with the ``us.okx.com`` REST host, prices match). The earlier
``ws.us.okx.com`` guess does not resolve, but that does NOT mean OKX has no
US-region WS — ``wsuspap.okx.com`` is the correct one. So the US-region demo
*trading* keys map to the **US demo public WS** (``wsuspap.okx.com``), region-
matching the REST endpoint and the simulated-trading context. The GLOBAL demo WS
(``wspap.okx.com``) is reserved for an explicit global-demo REST host only.
``ws.okx.com`` (prod) was also verified live. OKX drops a connection after 30 s
of silence and expects a literal text ``"ping"`` keepalive (replies ``"pong"``);
we send it every <25 s. Connection success itself is the availability check (no
separate smoke) — if connects fail ``max_reconnect_attempts`` in a row the base
client transitions to ``rest_only`` and REST bar ingest remains the fallback.
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

OKX_WS_US_DEMO: str = "wss://wsuspap.okx.com:8443/ws/v5/public"
OKX_WS_DEMO: str = "wss://wspap.okx.com:8443/ws/v5/public"
OKX_WS_PROD: str = "wss://ws.okx.com:8443/ws/v5/public"

# OKX disconnects after 30 s idle and wants a keepalive well under that. We ping
# every 20 s (the base-client default) which is < 25 s.
OKX_PING_INTERVAL_SEC: float = 20.0


def resolve_okx_ws_url(rest_env_value: str | None) -> str:
    """Map the OKX REST base-URL env value to the matching public WS host.

    Region-consistent with the REST resolver (``resolve_okx_base_url``): the
    US-region demo REST host (default / ``us.okx.com`` / any ``*.us.okx.com``)
    maps to the **US demo** WS (``wsuspap.okx.com`` — Jin official, live-verified
    BTC-USDT ticker). The GLOBAL demo WS (``wspap.okx.com``) is reserved for an
    explicit global-demo REST host, and only the international prod host
    (``www.okx.com``) maps to the prod WS (``ws.okx.com``).
    """
    if not rest_env_value:
        return OKX_WS_US_DEMO
    host = (urlparse(rest_env_value).netloc or "").lower().split(":")[0]
    if host == "www.okx.com":
        # Explicit international prod host → prod public WS.
        return OKX_WS_PROD
    if host == "wspap.okx.com":
        # Explicit GLOBAL demo host → global demo public WS.
        return OKX_WS_DEMO
    # us.okx.com (default US demo) and any *.us.okx.com sub-domain → US demo WS,
    # region-matching the us.okx.com REST endpoint.
    return OKX_WS_US_DEMO


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
        """Replace the subscribed instId set (universe change → re-subscribe).

        Updates the desired set ONLY; the LIVE delta is sent by
        ``apply_subscription_delta`` (called from the loop's resubscribe pass) so
        a focus churn reaches a HEALTHY socket without waiting for a reconnect.
        """
        self._symbols = list(dict.fromkeys(symbols))

    def current_subscription(self) -> set[str]:
        return set(self._symbols)

    def subscribe_messages(self) -> Iterable[str]:
        args = [{"channel": "tickers", "instId": s} for s in self._symbols]
        return [json.dumps({"op": "subscribe", "args": args})]

    def subscribe_delta_frames(
        self, added: set[str], removed: set[str]
    ) -> list[str]:
        """OKX op:subscribe (added instIds) + op:unsubscribe (removed instIds).

        Reuses the per-instId ``tickers`` args shape of ``subscribe_messages`` for
        the delta. A FLOW INCREASE (the socket follows focus), AI-free.
        """
        frames: list[str] = []
        if added:
            frames.append(
                json.dumps(
                    {
                        "op": "subscribe",
                        "args": [
                            {"channel": "tickers", "instId": s} for s in sorted(added)
                        ],
                    }
                )
            )
        if removed:
            frames.append(
                json.dumps(
                    {
                        "op": "unsubscribe",
                        "args": [
                            {"channel": "tickers", "instId": s}
                            for s in sorted(removed)
                        ],
                    }
                )
            )
        return frames

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
