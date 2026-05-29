"""FRED macro collector (EVIDENCE only) — VIX / HY spread / MOVE / yield curve.

DEMO/PAPER. Reads slow-moving macro series from the St. Louis Fed (FRED) using
``FRED_API_KEY`` (present in .env). Elevated VIX / HY spread = risk-off /
crisis evidence for the regime fuser of non-crypto (forex/index/commodity)
groups. Graceful ``{}`` if the key is missing — no network call, no throttle.

Series:
  vix          = VIXCLS       (CBOE VIX, EOD)
  hy_spread    = BAMLH0A0HYM2 (ICE BofA US High Yield OAS, percent → bps)
  move         = MOVE         (ICE BofAML MOVE bond-vol index)
  yield_curve  = T10Y2Y       (10Y-2Y Treasury spread)

Ref: ~/Projects/auto_invasion_mk1-main/invasion/data/collectors/fred_macro.py
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

FRED_BASE: Final[str] = "https://api.stlouisfed.org"
_OBS_PATH: Final[str] = "/fred/series/observations"

# Output key → FRED series id.
_SERIES: Final[dict[str, str]] = {
    "vix": "VIXCLS",
    "hy_spread": "BAMLH0A0HYM2",
    "move": "MOVE",
    "yield_curve": "T10Y2Y",
}


class FredMacroCollector:
    """FRED macro indicators (requires ``FRED_API_KEY``)."""

    name = "fred_macro"
    ttl_sec = 3600
    asset_classes = ("forex", "index", "commodity")

    def __init__(self, *, api_key: str | None = None, base_url: str = FRED_BASE) -> None:
        # Empty-string env keys count as absent.
        self._api_key = (api_key if api_key is not None else os.environ.get("FRED_API_KEY", "")) or ""
        self._base_url = base_url

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        if not self._api_key:
            logger.info("[altdata] fred_macro: no FRED_API_KEY (graceful skip)")
            return {}
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        out: dict[str, float | None] = {}
        try:
            for key, series_id in _SERIES.items():
                val = await self._latest(cli, series_id)
                # BAMLH0A0HYM2 is in percent; system-wide unit is bps.
                if key == "hy_spread" and val is not None:
                    val = val * 100
                out[key] = val
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.info("[altdata] fred_macro fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
        return out

    async def _latest(self, cli: httpx.AsyncClient, series_id: str) -> float | None:
        resp = await cli.get(
            _OBS_PATH,
            params={
                "series_id": series_id,
                "api_key": self._api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": "5",
            },
        )
        if resp.status_code in (400, 403, 404):
            return None
        resp.raise_for_status()
        body = resp.json()
        for obs in body.get("observations") or []:
            raw = obs.get("value", ".")
            if raw and raw != ".":
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None
        return None
