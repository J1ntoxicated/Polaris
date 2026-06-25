"""Alpaca SIP/IEX market-data WebSocket client (P4 — Track C).

Design SSOT: ``.claude/plans/p4_ws_realtime_price_2026-06-01.md``.

Endpoint: ``wss://stream.data.alpaca.markets/v2/{feed}`` where ``feed`` is the
configured Alpaca data feed (``POLARIS_ALPACA_FEED``, default ``sip``). SIP is the
paid real-time consolidated feed and has NO per-symbol subscription cap; IEX is
the free fallback and hard-caps at 30 symbols. Auth is a post-connect
``{"action":"auth","key":...,"secret":...}`` frame, then a
``{"action":"subscribe","quotes":[...]}`` frame.

SIP→IEX graceful fallback: a SIP key without the entitlement is accepted at the
WS handshake but rejected with an ``{"T":"error","msg":"insufficient
subscription"}`` control frame (verified live). On that frame the client downgrades
the live feed to IEX (and logs ONE warning); the idle watchdog then reconnects
onto the IEX URL. The downgrade is permanent for the client's lifetime (an
entitlement does not appear mid-session), which avoids flapping. A plain connect
failure is NOT a downgrade trigger — it is a transient (same host for both feeds),
handled by the base reconnect backoff. Degrade, never halt (AGGRESSIVE).

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
from polaris.core.universe.schema import ALPACA_IEX_SYMBOL_CAP, alpaca_feed_token
from polaris.venues.alpaca.equity_session_gate import us_equity_session_state
from polaris.venues.ws_common import WSStreamClient

logger = logging.getLogger(__name__)

ALPACA_WS_IEX: str = "wss://stream.data.alpaca.markets/v2/iex"
ALPACA_WS_SIP: str = "wss://stream.data.alpaca.markets/v2/sip"
_FEED_URL: dict[str, str] = {"sip": ALPACA_WS_SIP, "iex": ALPACA_WS_IEX}

# Alpaca control-error substrings that signal the key lacks the SIP entitlement
# (auth rejected / not subscribed to this feed). Matched case-insensitively on the
# error frame's ``msg`` — a SIP-only failure that the IEX free feed will satisfy.
_SIP_ENTITLEMENT_ERROR_HINTS: tuple[str, ...] = (
    "auth failed",
    "not authenticated",
    "insufficient subscription",
    "subscription",
)


class AlpacaQuoteWS(WSStreamClient):
    """Streams Alpaca SIP/IEX quotes for a set of symbols → QuoteTicks."""

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
        # Configured feed (sip default); ``_active_feed`` is the LIVE feed, which a
        # runtime entitlement failure downgrades to iex. ``_downgraded`` guards the
        # one-shot warning so we do not log on every reconnect.
        self._active_feed = alpaca_feed_token()
        self._downgraded = False

    @property
    def ws_url(self) -> str:
        return _FEED_URL[self._active_feed]

    def _effective_symbols(self) -> list[str]:
        """Desired symbols, hard-capped to the IEX 30-symbol ceiling on the IEX feed.

        SIP has no cap → the full desired set is returned. On IEX (the downgrade or
        an explicit ``POLARIS_ALPACA_FEED=iex``) the set is capped so a >30-symbol
        subscribe frame never trips "symbol limit exceeded" → reconnect churn. The
        focus order is preserved (Tier-S leads), so the cap keeps the best names.
        flow_not_block: a name beyond the cap still bar-ingests via REST.
        """
        if self._active_feed == "iex" and len(self._symbols) > ALPACA_IEX_SYMBOL_CAP:
            return self._symbols[:ALPACA_IEX_SYMBOL_CAP]
        return self._symbols

    def _downgrade_to_iex(self, reason: str) -> None:
        """Permanently switch the live feed to IEX (one-shot warn). No-op on IEX.

        Called from ``parse_message`` on a SIP auth/entitlement error frame. Alpaca
        closes the socket after that error, so the base recv-loop-end path drives
        the reconnect (the idle watchdog is the backstop); either way the next
        connect reads ``ws_url`` → the IEX URL. No new reconnect plumbing is needed.
        """
        if self._active_feed == "iex":
            return
        self._active_feed = "iex"
        if not self._downgraded:
            self._downgraded = True
            logger.warning(
                "[alpaca ws] SIP feed unavailable (%s) — falling back to IEX "
                "(free, 30-symbol cap). Set POLARIS_ALPACA_FEED=iex to silence, or "
                "verify the key's SIP entitlement.",
                reason,
            )

    def set_symbols(self, symbols: Iterable[str]) -> None:
        """Replace the subscribed symbol set (universe change → re-subscribe).

        Desired set only; the LIVE delta is sent by ``apply_subscription_delta``.
        """
        self._symbols = list(dict.fromkeys(symbols))

    def current_subscription(self) -> set[str]:
        # The effective (IEX-capped) set — so the incremental-resubscribe delta
        # diffs against what the socket can actually carry (no >30 on IEX).
        return set(self._effective_symbols())

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
            # the quote_writer must persist (easing DB-lock contention). The set is
            # IEX-capped (no-op on SIP, where there is no cap).
            json.dumps(
                {
                    "action": "subscribe",
                    "quotes": self._effective_symbols(),
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
                emsg = str(item.get("msg", ""))
                logger.warning("[alpaca ws] error: %s", emsg)
                # SIP entitlement failure (auth rejected / not subscribed to SIP)
                # → downgrade to IEX; the reconnect lands on the IEX URL. Guarded by
                # ``_active_feed == "sip"`` (only the configured-SIP path downgrades)
                # and the one-shot ``_downgrade_to_iex``, so the broad "subscription"
                # hint can never mis-fire on an already-IEX socket.
                if self._active_feed == "sip" and any(
                    h in emsg.lower() for h in _SIP_ENTITLEMENT_ERROR_HINTS
                ):
                    self._downgrade_to_iex(f"error frame: {emsg}")
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
