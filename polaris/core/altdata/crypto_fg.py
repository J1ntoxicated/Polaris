"""Alternative.me Crypto Fear & Greed collector (EVIDENCE only).

DEMO/PAPER. Free public API, no key. 0 = Extreme Fear, 100 = Extreme Greed.
Extreme fear is read by the fuser as bearish/crisis evidence (and, in an
aggressive contrarian framing, a potential buy-the-fear context) — it is a
SIGNAL only, never a throttle.

Ref: ~/Projects/auto_invasion_mk1-main/invasion/data/collectors/alternative_me.py
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

ALT_ME_BASE: Final[str] = "https://api.alternative.me"
_FNG_PATH: Final[str] = "/fng/"


class CryptoFearGreedCollector:
    """Alternative.me crypto Fear & Greed index (no key required)."""

    name = "crypto_fg"
    ttl_sec = 1800
    asset_classes = ("crypto",)

    def __init__(self, *, base_url: str = ALT_ME_BASE) -> None:
        self._base_url = base_url

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        try:
            resp = await cli.get(
                _FNG_PATH,
                params={"limit": "7"},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            body = resp.json()
            entries = body.get("data") or []
            if not entries:
                return {}
            current = entries[0]
            value = int(current.get("value", 50))
            label = current.get("value_classification", "Neutral")
            one_week_ago = int(entries[6]["value"]) if len(entries) > 6 else None
            return {"value": value, "label": label, "one_week_ago": one_week_ago}
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.info("[altdata] crypto_fg fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
