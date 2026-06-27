"""Binance public derivatives collector (EVIDENCE only) — keyless.

DEMO/PAPER. Reads Binance USD-M futures positioning + order-flow from the PUBLIC
keyless endpoints (``fapi.binance.com`` + ``futures/data``). The live
``BINANCE_API_KEY`` in .env is mainnet-geo-blocked (401 -2015) and DEMO-mandate
irrelevant — this collector uses NO credentials at all, exactly like
``okx_funding`` (public market data needs no key). Wiring the dead signed key is
explicitly avoided.

This is a NET-NEW positioning / order-flow axis Polaris lacked: OKX's public
feed has no retail-vs-smart-money or taker-imbalance equivalent. Per symbol it
emits:

  funding_rate           premiumIndex.lastFundingRate (8h perp funding)
  mark_price             premiumIndex.markPrice
  open_interest          openInterest (contracts, current)
  oi_value_usd           openInterestHist.sumOpenInterestValue (latest, USD)
  oi_change_24h          (latest - 24h-ago) / 24h-ago of sumOpenInterestValue —
                         the delta okx_funding.py:116 hardcodes None
  global_long_short      globalLongShortAccountRatio.longShortRatio (retail crowd)
  top_long_short         topLongShortPositionRatio.longShortRatio (smart money)
  taker_buy_sell         takerlongshortRatio.buySellRatio (aggressive flow)

All values are EVIDENCE: the fuser tilts bull/bear from positioning, never
blocks/sizes/exits (flow_not_block). A missing field / HTTP error leaves that
row-field absent (degrade-never-crash); a whole-fetch error returns ``{}``.

Currency (#69): the futures/data history rows carry exchange ``timestamp`` (ms).
The OI 24h-delta is computed from those rows, and the newest OI value's
observation timestamp is surfaced as ``oi_asof`` (ISO-8601 UTC) so a stale read
is age-labelable downstream — never presented as 'current'.

Rate-limit: the futures/data endpoints are cheap; one batch of ~3 calls/symbol
over a small majors roster on the slow 5min cadence sits far under Binance's
~2400/min fapi weight budget. Like okx_funding, no token bucket is needed (the
AltDataCache TTL is the throttle); requests are issued sequentially.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

BINANCE_FAPI_BASE: Final[str] = "https://fapi.binance.com"
_PREMIUM_INDEX_PATH: Final[str] = "/fapi/v1/premiumIndex"
_OI_HIST_PATH: Final[str] = "/futures/data/openInterestHist"
_GLOBAL_LS_PATH: Final[str] = "/futures/data/globalLongShortAccountRatio"
_TOP_LS_PATH: Final[str] = "/futures/data/topLongShortPositionRatio"
_TAKER_LS_PATH: Final[str] = "/futures/data/takerlongshortRatio"

# Liquid Binance USD-M perp roster (mirrors the okx_funding majors set so the
# two venues cross-confirm positioning on the same symbols).
DEFAULT_SYMBOLS: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "AVAXUSDT",
    "LINKUSDT", "LTCUSDT", "BCHUSDT", "ADAUSDT", "DOTUSDT", "NEARUSDT",
    "TONUSDT", "TRXUSDT",
)

# futures/data period for the long/short + OI-history endpoints. "5m" is the
# finest grain; we read enough rows to also span 24h for the OI delta.
_PERIOD: Final[str] = "5m"
# 5m * 288 = 24h. Request a hair over so the oldest row brackets 24h-ago.
_OI_HIST_LIMIT: Final[int] = 289
# Long/short ratio endpoints: only the latest row is needed.
_LS_LIMIT: Final[int] = 1


def compute_oi_change_24h(
    rows: list[dict[str, Any]], *, field: str = "sumOpenInterestValue"
) -> tuple[float | None, float | None, str | None]:
    """24h fractional OI change + latest value + latest observation ISO date.

    ``rows`` are Binance ``openInterestHist`` entries (chronological — oldest
    first, as the endpoint returns). Returns
    ``(oi_change_24h, latest_value, latest_iso)`` where ``oi_change_24h`` is
    ``(latest - oldest) / oldest`` over the spanned window. Pure / total: any
    leg missing or a zero oldest value yields ``None`` for the change (no
    divide-by-zero, no manufactured signal); ``latest_value`` / ``latest_iso``
    are still returned when the newest row is parseable.
    """
    parsed: list[tuple[float, int]] = []
    for r in rows:
        raw = r.get(field)
        ts = r.get("timestamp")
        if raw is None or ts is None:
            continue
        try:
            parsed.append((float(raw), int(ts)))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None, None, None
    parsed.sort(key=lambda vt: vt[1])
    oldest_val, _ = parsed[0]
    latest_val, latest_ts = parsed[-1]
    latest_iso = _ms_to_iso(latest_ts)
    if len(parsed) < 2 or oldest_val == 0.0:
        return None, latest_val, latest_iso
    change = (latest_val - oldest_val) / oldest_val
    return change, latest_val, latest_iso


def _ms_to_iso(ms: int) -> str | None:
    """Epoch-ms → ISO-8601 UTC date-time (``None`` on overflow / bad value)."""
    try:
        return (
            _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.UTC)
            .replace(microsecond=0)
            .isoformat()
        )
    except (OverflowError, OSError, ValueError):
        return None


class BinanceDerivCollector:
    """Binance public futures positioning + order-flow (no key)."""

    name = "binance_deriv"
    ttl_sec = 300
    asset_classes = ("crypto",)

    def __init__(
        self,
        *,
        symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
        base_url: str = BINANCE_FAPI_BASE,
    ) -> None:
        self._symbols = symbols
        self._base_url = base_url

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        out: dict[str, dict[str, float | str | None]] = {}
        try:
            for sym in self._symbols:
                row: dict[str, float | str | None] = {}
                await self._add_funding(cli, sym, row)
                await self._add_oi(cli, sym, row)
                await self._add_long_short(cli, sym, row)
                if row:
                    out[sym] = row
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.info("[altdata] binance_deriv fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
        return out

    async def _add_funding(
        self, cli: httpx.AsyncClient, symbol: str, row: dict[str, float | str | None]
    ) -> None:
        """premiumIndex → funding_rate + mark_price (degrade: absent on error)."""
        body = await self._get_json(cli, _PREMIUM_INDEX_PATH, {"symbol": symbol})
        obj = body[0] if isinstance(body, list) and body else body
        if not isinstance(obj, dict):
            return
        fr = _as_float(obj.get("lastFundingRate"))
        if fr is not None:
            row["funding_rate"] = fr
        mark = _as_float(obj.get("markPrice"))
        if mark is not None:
            row["mark_price"] = mark

    async def _add_oi(
        self, cli: httpx.AsyncClient, symbol: str, row: dict[str, float | str | None]
    ) -> None:
        """openInterestHist → current USD OI + 24h delta + observation date.

        Fills the gap okx_funding.py:116 leaves (oi_change_24h hardcoded None).
        A history error leaves all three absent (no crash).
        """
        body = await self._get_json(
            cli,
            _OI_HIST_PATH,
            {"symbol": symbol, "period": _PERIOD, "limit": str(_OI_HIST_LIMIT)},
        )
        rows = body if isinstance(body, list) else []
        change, latest_val, latest_iso = compute_oi_change_24h(rows)
        if latest_val is not None:
            row["oi_value_usd"] = latest_val
        if change is not None:
            row["oi_change_24h"] = change
        if latest_iso is not None:
            row["oi_asof"] = latest_iso

    async def _add_long_short(
        self, cli: httpx.AsyncClient, symbol: str, row: dict[str, float | str | None]
    ) -> None:
        """The three positioning / order-flow ratios (latest row each).

        global_long_short = retail crowd ; top_long_short = smart money ;
        taker_buy_sell = aggressive flow imbalance. Each is independent — one
        endpoint failing leaves only that field absent.
        """
        glob = await self._latest_ratio(cli, _GLOBAL_LS_PATH, symbol, "longShortRatio")
        if glob is not None:
            row["global_long_short"] = glob
        top = await self._latest_ratio(cli, _TOP_LS_PATH, symbol, "longShortRatio")
        if top is not None:
            row["top_long_short"] = top
        taker = await self._latest_ratio(cli, _TAKER_LS_PATH, symbol, "buySellRatio")
        if taker is not None:
            row["taker_buy_sell"] = taker

    async def _latest_ratio(
        self, cli: httpx.AsyncClient, path: str, symbol: str, field: str
    ) -> float | None:
        body = await self._get_json(
            cli, path, {"symbol": symbol, "period": _PERIOD, "limit": str(_LS_LIMIT)}
        )
        if not isinstance(body, list) or not body:
            return None
        last = body[-1]
        if not isinstance(last, dict):
            return None
        return _as_float(last.get(field))

    @staticmethod
    async def _get_json(
        cli: httpx.AsyncClient, path: str, params: dict[str, str]
    ) -> Any:
        """GET → parsed JSON, or ``None`` on a non-2xx (degrade-never-crash)."""
        resp = await cli.get(path, params=params)
        if resp.status_code != 200:
            return None
        return resp.json()


def _as_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
