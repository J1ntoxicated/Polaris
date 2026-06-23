"""Alpaca IEX market-data WebSocket client (P4 — Track C).

Design SSOT: ``.claude/plans/p4_ws_realtime_price_2026-06-01.md``.

Endpoint: ``wss://stream.data.alpaca.markets/v2/iex`` — the IEX feed is free on
paper accounts and is real-time (the SIP feed needs a paid plan). Auth is a
post-connect ``{"action":"auth","key":...,"secret":...}`` frame, then a
``{"action":"subscribe","quotes":[...],"trades":[...]}`` frame.

Alpaca delivers an **array** of messages per frame; each item has a ``T`` type
discriminator (``q`` quote, ``t`` trade, ``success`` / ``subscription`` /
``error`` control). We map the first quote in the array to a QuoteTick.

Gating: RTH only — outside US regular trading hours the venue rejects orders, so
we do not even hold a socket open. Wired via ``equity_session_gate``
(``us_equity_session_state`` → RTH check) so it matches the entry-hold gate.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable

from polaris.core.data.canonical import alpaca_quote_to_quote_tick
from polaris.core.data.schema import QuoteTick
from polaris.venues.alpaca.equity_session_gate import us_equity_session_state
from polaris.venues.ws_common import WSStreamClient

logger = logging.getLogger(__name__)

ALPACA_WS_IEX: str = "wss://stream.data.alpaca.markets/v2/iex"


class AlpacaQuoteWS(WSStreamClient):
    """Streams Alpaca IEX quotes/trades for a set of symbols → QuoteTicks."""

    venue = "alpaca"

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        api_key: str,
        api_secret: str,
        on_quote: Callable[[QuoteTick], None],
        **kw: object,
    ) -> None:
        # RTH gate by default (off-session → no connect). Caller may override.
        kw.setdefault("is_gated", lambda: self._gated_at())
        super().__init__(on_quote=on_quote, **kw)  # type: ignore[arg-type]
        self._symbols = list(dict.fromkeys(symbols))
        self._api_key = api_key
        self._api_secret = api_secret

    @property
    def ws_url(self) -> str:
        return ALPACA_WS_IEX

    def set_symbols(self, symbols: Iterable[str]) -> None:
        """Replace the subscribed symbol set (universe change → re-subscribe).

        Desired set only; the LIVE delta is sent by ``apply_subscription_delta``.
        """
        self._symbols = list(dict.fromkeys(symbols))

    def current_subscription(self) -> set[str]:
        return set(self._symbols)

    def subscribe_delta_frames(
        self, added: set[str], removed: set[str]
    ) -> list[str]:
        """Alpaca subscribe(added quotes) + unsubscribe(removed quotes) frames.

        Quotes-only (mirrors ``subscribe_messages`` — trades are discarded). A
        FLOW INCREASE (the socket follows focus), AI-free.
        """
        frames: list[str] = []
        if added:
            frames.append(
                json.dumps({"action": "subscribe", "quotes": sorted(added)})
            )
        if removed:
            frames.append(
                json.dumps({"action": "unsubscribe", "quotes": sorted(removed)})
            )
        return frames

    def subscribe_messages(self) -> Iterable[str]:
        return [
            json.dumps(
                {"action": "auth", "key": self._api_key, "secret": self._api_secret}
            ),
            # Quotes ONLY. parse_message discards every non-"q" frame (trades
            # carry no bid/ask), so a "trades" subscription was pure waste — and
            # it DOUBLED the IEX subscription count (16 symbols → 32 channels)
            # past the free-feed 30-symbol cap, tripping "symbol limit exceeded"
            # → reconnect churn → idle-forced reconnects → tick-loop stalls. One
            # channel halves both the cap pressure and the inbound quote volume
            # the quote_writer must persist (easing DB-lock contention).
            json.dumps(
                {
                    "action": "subscribe",
                    "quotes": self._symbols,
                }
            ),
        ]

    def parse_message(self, raw: str | bytes) -> QuoteTick | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return None
        # Alpaca frames are arrays of messages.
        items = msg if isinstance(msg, list) else [msg]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("T")
            if t == "error":
                logger.warning("[alpaca ws] error: %s", item.get("msg"))
                continue
            if t != "q":  # only quotes carry bid/ask; trades/control ignored here
                continue
            try:
                return alpaca_quote_to_quote_tick(item, ts=int(time.time()))
            except (KeyError, ValueError):
                continue
        return None

    # ------------------------------------------------------------------
    # Gating — RTH only.
    # ------------------------------------------------------------------

    @staticmethod
    def _gated_at(*, ts: int | float | None = None) -> bool:
        """True outside US regular trading hours (gate = hold, never a halt)."""
        t = ts if ts is not None else time.time()
        return us_equity_session_state(t) != "rth"
