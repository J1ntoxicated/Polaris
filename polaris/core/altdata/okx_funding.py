"""OKX public funding-rate + open-interest collector (EVIDENCE only).

DEMO/PAPER. Reads OKX SWAP perpetual funding rate and open interest from the
public endpoints (no auth, ``x-simulated-trading`` not required for public
market data). Positive funding = longs paying shorts (crowded long, bullish
positioning); deeply negative = crowded short (bearish positioning). This is
context for the regime fuser — never an action.

Per-symbol p10 (weekend_funding_capitulation_maker feed): the collector also
reads ``funding-rate-history`` per instrument and computes a per-symbol p10 (the
10th-percentile of the historical funding rates = the "extreme-negative /
crowded-short capitulation" threshold the weekend positioning edge keys on). The
history settles on the 8h funding cycle, so p10 is CACHED per-instId with a long
TTL and is NOT refetched on the 5min current-rate cadence (rate-limit / 429
safety — the only new requests fire at most ~every 8h). degrade-never-crash: a
history HTTP error leaves the row's p10 absent (the strategy reads None -> no
emit), never a crash; the current funding-rate row is unaffected.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

OKX_BASE: Final[str] = "https://us.okx.com"
_FUNDING_PATH: Final[str] = "/api/v5/public/funding-rate"
_OI_PATH: Final[str] = "/api/v5/public/open-interest"
_FUNDING_HISTORY_PATH: Final[str] = "/api/v5/public/funding-rate-history"

DEFAULT_INSTRUMENTS: Final[tuple[str, ...]] = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")

# OKX history page cap (100 rows/page = ~33 days at the 8h funding cadence —
# the broadest single-request window the public endpoint serves).
_HISTORY_LIMIT: Final[int] = 100
# p10 cache TTL: funding settles every 8h, so the historical distribution barely
# moves between settlements. Refresh the per-instId p10 at most ~once per cycle
# (8h) so the 5min current-rate loop never re-pulls history (429 safety).
_P10_TTL_SEC: Final[float] = 8 * 3600.0
# p10 percentile rank (10th = the extreme-negative crowded-short tail).
_P10_RANK: Final[float] = 0.10


def compute_funding_p10(rates: list[float]) -> float | None:
    """The 10th-percentile (linear interpolation) of a funding-rate sample.

    Pure, total. ``None`` for an empty sample; the single value for a 1-point
    sample (no manufactured tail). The crowded-short capitulation threshold: a
    current funding at/below this p10 sits in the extreme-negative tail.
    """
    vals = sorted(r for r in rates if isinstance(r, (int, float)))
    n = len(vals)
    if n == 0:
        return None
    if n == 1:
        return float(vals[0])
    # Linear-interpolation percentile (NumPy "linear" method) on the sorted tail.
    pos = _P10_RANK * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return float(vals[lo])
    return float(vals[lo] + (vals[lo + 1] - vals[lo]) * frac)


class OKXFundingCollector:
    """OKX public funding-rate + open-interest + per-symbol p10 (no key)."""

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
        # Per-instId p10 cache: instId -> (p10, fetched_monotonic). Long TTL so
        # the history leg fires at most ~once per 8h funding cycle.
        self._p10_cache: dict[str, tuple[float, float]] = {}

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
                # Per-symbol p10 (cached ~8h) — the crowded-short capitulation
                # threshold. A history failure leaves p10 absent (no emit), never
                # a crash: the current-rate row above is already populated.
                p10 = await self._p10_for(cli, inst)
                if p10 is not None:
                    row["p10"] = p10
                if row:
                    out[inst] = row
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.info("[altdata] okx_funding fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
        return out

    async def _p10_for(
        self, cli: httpx.AsyncClient, inst_id: str
    ) -> float | None:
        """Cached per-instId funding p10 — fetch history only when cache stale.

        Returns the cached p10 while fresh (TTL ~8h). On a cold/stale entry it
        pulls ``funding-rate-history`` once and recomputes; a history HTTP error
        is swallowed (returns None) so the current-rate row is never lost — the
        strategy simply reads no p10 (no emit) until the next refresh.
        """
        now = time.monotonic()
        cached = self._p10_cache.get(inst_id)
        if cached is not None and (now - cached[1]) < _P10_TTL_SEC:
            return cached[0]
        try:
            rates = await self._funding_history(cli, inst_id)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.info(
                "[altdata] okx_funding history skip %s (graceful): %s", inst_id, exc
            )
            return cached[0] if cached is not None else None
        p10 = compute_funding_p10(rates)
        if p10 is not None:
            self._p10_cache[inst_id] = (p10, now)
        return p10

    @staticmethod
    async def _funding_history(
        cli: httpx.AsyncClient, inst_id: str
    ) -> list[float]:
        """The historical funding-rate sample for ``inst_id`` (newest page)."""
        resp = await cli.get(
            _FUNDING_HISTORY_PATH,
            params={"instId": inst_id, "limit": str(_HISTORY_LIMIT)},
        )
        resp.raise_for_status()
        body = resp.json()
        if str(body.get("code", "0")) != "0":
            return []
        out: list[float] = []
        for row in body.get("data") or []:
            raw = row.get("fundingRate")
            if raw is None or raw == "":
                continue
            try:
                out.append(float(raw))
            except (TypeError, ValueError):
                continue
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
