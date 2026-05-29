"""AltDataCache — TTL-honoring, async/thread-safe alt-data store (read context).

DEMO/PAPER. Holds the latest payload per source with its own TTL. ``get`` returns
``None`` once stale; ``get_for_group`` returns only the FRESH sources relevant to
a given ``underlying_group_id`` by asset-class prefix:

  - ``crypto:*``                 → okx_funding + crypto_fg + coinglass
  - ``forex:* / index:* / commodity:*`` → fred_macro + myfxbook (forex only)

There is NO write path to positions / learner / risk state — this cache feeds
the regime fuser as read-only evidence context only.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# Asset-class prefix → source names that inform it.
_GROUP_SOURCES: dict[str, tuple[str, ...]] = {
    "crypto": ("okx_funding", "crypto_fg", "coinglass"),
    "forex": ("fred_macro", "myfxbook"),
    "index": ("fred_macro",),
    "commodity": ("fred_macro",),
}


class AltDataCache:
    """Thread/async-safe ``{source -> (ts, ttl, payload)}`` store."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, int, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def set(
        self, source: str, payload: dict[str, Any], *, ttl_sec: int, now_ts: float | None = None
    ) -> None:
        ts = time.time() if now_ts is None else now_ts
        with self._lock:
            self._store[source] = (ts, ttl_sec, dict(payload))

    def get(self, source: str, *, now_ts: float | None = None) -> dict[str, Any] | None:
        ts_now = time.time() if now_ts is None else now_ts
        with self._lock:
            entry = self._store.get(source)
            if entry is None:
                return None
            stored_ts, ttl, payload = entry
            if ts_now - stored_ts >= ttl:
                return None
            return dict(payload)

    def get_for_group(
        self, underlying_group_id: str, *, now_ts: float | None = None
    ) -> dict[str, dict[str, Any]]:
        """Return ``{source -> payload}`` of FRESH sources for this group.

        Group is matched by its asset-class prefix (text before ``:``). Sources
        with no entry or an expired entry are simply omitted (graceful — fewer
        evidence sources, never a block).
        """
        prefix = underlying_group_id.split(":", 1)[0]
        wanted = _GROUP_SOURCES.get(prefix, ())
        out: dict[str, dict[str, Any]] = {}
        for src in wanted:
            payload = self.get(src, now_ts=now_ts)
            if payload:
                out[src] = payload
        return out
