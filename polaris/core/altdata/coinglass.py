"""Coinglass collector (EVIDENCE only) — activation-ready, key-gated.

DEMO/PAPER. Coinglass v4 aggregates derivatives data (funding / open-interest /
liquidations / long-short ratio) across 30+ exchanges. It needs ``COINGLASS_API_KEY``
(the ``CG-API-KEY`` header on EVERY call — Coinglass has NO keyless tier). In this
environment the key is ABSENT (``.env`` has ``COINGLASS_API_KEY=`` empty), so
``fetch`` returns ``{}`` immediately with NO network call — a no-evidence
fallback, never a throttle (flow_not_block).

When Jin pastes a key into ``.env``, the collector activates with no further code
change: it reads aggregated LIQUIDATION history (the positioning-flush axis the
bot has ZERO of today) + cross-exchange aggregated FUNDING — both NET-NEW over
the single-venue okx_funding feed. The fuser scores liquidation $-cascades +
cross-exchange funding into the crypto branch.

Currency (#69): Coinglass rows carry exchange timestamps (ms); the newest is
surfaced as ``<axis>_asof`` (ISO-8601 UTC) so a stale read is age-labelable.

degrade-never-crash: any HTTP / parse error returns ``{}`` (key present or not);
a single-axis failure leaves only that axis absent.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

COINGLASS_BASE: Final[str] = "https://open-api-v4.coinglass.com"
_LIQ_HIST_PATH: Final[str] = "/api/futures/liquidation/aggregated-history"
_FUNDING_LIST_PATH: Final[str] = "/api/futures/funding-rate/exchange-list"

# Symbols to aggregate (base assets — Coinglass keys on the coin, not a pair).
DEFAULT_COINS: Final[tuple[str, ...]] = ("BTC", "ETH", "SOL")
_LIQ_INTERVAL: Final[str] = "1h"
_LIQ_LIMIT: Final[int] = 24  # last 24h of liquidation buckets


def _ms_to_iso(ms: Any) -> str | None:
    """Epoch-ms → ISO-8601 UTC (``None`` on bad value)."""
    try:
        return (
            _dt.datetime.fromtimestamp(float(ms) / 1000.0, tz=_dt.UTC)
            .replace(microsecond=0)
            .isoformat()
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _as_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class CoinglassCollector:
    """Coinglass aggregated derivatives (crypto). Key-gated graceful skip."""

    name = "coinglass"
    ttl_sec = 300
    asset_classes = ("crypto",)

    def __init__(
        self,
        *,
        api_key: str | None = None,
        coins: tuple[str, ...] = DEFAULT_COINS,
        base_url: str = COINGLASS_BASE,
    ) -> None:
        # Empty-string env key counts as absent.
        self._api_key = (
            api_key if api_key is not None else os.environ.get("COINGLASS_API_KEY", "")
        ) or ""
        self._coins = coins
        self._base_url = base_url

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        if not self._api_key:
            logger.debug("[altdata] coinglass: no key (graceful skip)")
            return {}
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        headers = {"CG-API-KEY": self._api_key, "accept": "application/json"}
        out: dict[str, dict[str, float | str | None]] = {}
        try:
            for coin in self._coins:
                row: dict[str, float | str | None] = {}
                await self._add_liquidation(cli, coin, headers, row)
                await self._add_funding(cli, coin, headers, row)
                if row:
                    out[coin] = row
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.info("[altdata] coinglass fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
        return out

    async def _add_liquidation(
        self,
        cli: httpx.AsyncClient,
        coin: str,
        headers: dict[str, str],
        row: dict[str, float | str | None],
    ) -> None:
        """Aggregated long/short liquidation $ over the window + newest asof.

        The positioning-flush axis the bot has ZERO of today. A failure leaves
        the liquidation fields absent (no crash).
        """
        body = await self._get_json(
            cli,
            _LIQ_HIST_PATH,
            {"symbol": coin, "interval": _LIQ_INTERVAL, "limit": str(_LIQ_LIMIT)},
            headers,
        )
        rows = self._data_rows(body)
        long_liq = 0.0
        short_liq = 0.0
        seen = False
        newest_ts: float | None = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            lv = _as_float(r.get("aggregated_long_liquidation_usd"))
            sv = _as_float(r.get("aggregated_short_liquidation_usd"))
            if lv is None and sv is None:
                continue
            seen = True
            long_liq += lv or 0.0
            short_liq += sv or 0.0
            ts = _as_float(r.get("time"))
            if ts is not None and (newest_ts is None or ts > newest_ts):
                newest_ts = ts
        if not seen:
            return
        row["long_liquidation_usd"] = long_liq
        row["short_liquidation_usd"] = short_liq
        if newest_ts is not None:
            iso = _ms_to_iso(newest_ts)
            if iso is not None:
                row["liquidation_asof"] = iso

    async def _add_funding(
        self,
        cli: httpx.AsyncClient,
        coin: str,
        headers: dict[str, str],
        row: dict[str, float | str | None],
    ) -> None:
        """Cross-exchange aggregated funding mean + dispersion (NET-NEW vs OKX-only)."""
        body = await self._get_json(
            cli, _FUNDING_LIST_PATH, {"symbol": coin}, headers
        )
        rows = self._data_rows(body)
        rates: list[float] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            fr = _as_float(r.get("funding_rate"))
            if fr is not None:
                rates.append(fr)
        if not rates:
            return
        mean = sum(rates) / len(rates)
        row["funding_mean"] = mean
        if len(rates) > 1:
            var = sum((x - mean) ** 2 for x in rates) / len(rates)
            row["funding_dispersion"] = var ** 0.5

    @staticmethod
    def _data_rows(body: Any) -> list[Any]:
        """Coinglass v4 envelope → its ``data`` list (``[]`` on error/empty)."""
        if not isinstance(body, dict):
            return []
        # Coinglass returns code "0" on success; non-"0" carries an error msg.
        if str(body.get("code", "0")) not in ("0", "200"):
            return []
        data = body.get("data")
        return data if isinstance(data, list) else []

    @staticmethod
    async def _get_json(
        cli: httpx.AsyncClient,
        path: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> Any:
        resp = await cli.get(path, params=params, headers=headers)
        if resp.status_code != 200:
            return None
        return resp.json()
