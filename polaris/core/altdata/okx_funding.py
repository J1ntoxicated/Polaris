"""OKX public funding-rate + open-interest collector (EVIDENCE only).

DEMO/PAPER. Reads OKX SWAP perpetual funding rate and open interest from the
public endpoints (no auth, ``x-simulated-trading`` not required for public
market data). Positive funding = longs paying shorts (crowded long, bullish
positioning); deeply negative = crowded short (bearish positioning). This is
context for the regime fuser — never an action.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

OKX_BASE: Final[str] = "https://us.okx.com"
_FUNDING_PATH: Final[str] = "/api/v5/public/funding-rate"
_OI_PATH: Final[str] = "/api/v5/public/open-interest"

DEFAULT_INSTRUMENTS: Final[tuple[str, ...]] = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")


class OKXFundingCollector:
    """OKX public funding-rate + open-interest (no key required)."""

    name = "okx_funding"
    ttl_sec = 300
    asset_classes = ("crypto",)

    def __init__(
        self,
        *,
        instruments: tuple[str, ...] = DEFAULT_INSTRUMENTS,
        base_url: str = OKX_BASE,
    ) -> None:
        self._instruments = instruments
        self._base_url = base_url

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        out: dict[str, dict[str, float | None]] = {}
        try:
            for inst in self._instruments:
                row: dict[str, float | None] = {}
                fr = await self._get_one(cli, _FUNDING_PATH, inst, "fundingRate")
                if fr is not None:
                    row["fundingRate"] = fr
                oi = await self._get_one(cli, _OI_PATH, inst, "oi")
                if oi is not None:
                    row["oi"] = oi
                    row["oi_change_24h"] = None  # OKX public OI has no 24h delta field
                if row:
                    out[inst] = row
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.info("[altdata] okx_funding fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
        return out

    @staticmethod
    async def _get_one(
        cli: httpx.AsyncClient, path: str, inst_id: str, field: str
    ) -> float | None:
        resp = await cli.get(path, params={"instId": inst_id})
        resp.raise_for_status()
        body = resp.json()
        if str(body.get("code", "0")) != "0":
            return None
        data = body.get("data") or []
        if not data:
            return None
        raw = data[0].get(field)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
