"""Coinglass collector — graceful-skip stub (EVIDENCE only).

DEMO/PAPER. Coinglass needs ``COINGLASS_API_KEY``. In this environment the key
is ABSENT (``.env`` has ``COINGLASS_API_KEY=`` empty), so ``fetch`` returns
``{}`` immediately with NO network call. When a key is added later, wire the
liquidation/long-short/funding-aggregate parse here — the activation point is
already in place. Returning ``{}`` is a no-evidence fallback, never a throttle.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CoinglassCollector:
    """Coinglass alt-data (crypto). Skips gracefully without an API key."""

    name = "coinglass"
    ttl_sec = 300
    asset_classes = ("crypto",)

    def __init__(self, *, api_key: str | None = None) -> None:
        # Empty-string env key counts as absent.
        self._api_key = (
            api_key if api_key is not None else os.environ.get("COINGLASS_API_KEY", "")
        ) or ""

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        if not self._api_key:
            logger.debug("[altdata] coinglass: no key (graceful skip)")
            return {}
        # Key present → real parse goes here when Coinglass is activated.
        # (No live code path yet: keep keyless behavior the only behavior.)
        return {}
