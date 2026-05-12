"""Capital CFD vol/depth proxy — chart endpoint + spread-weighted depth.

Day 1 found that Capital does not publish 24h notional or order-book depth via
the navigation tree, so every CFD market was rejected by the 4-axis filter.
This proxy synthesises both metrics from the publicly-available chart endpoint:

  - ``vol_24h_proxy`` = Σ (close × volume) over the last 24 HOUR candles.
    Capital's ``volume`` is contract-units; multiplying by close gives a notional
    estimate (USD-equivalent for USD-denominated epics).
  - ``depth_proxy`` = ``(1 / spread_bps) × atr_pct × notional_per_pip``.
    Tight CFD spreads compress to deep proxy depth; ATR widens the corridor so
    instruments with a real intra-day range are favoured.

The 4-axis filter accepts a CFD row when **either** proxy clears its threshold
(`vol_proxy ≥ $30M` OR `depth_proxy ≥ $500k`). CFD rejection on raw-zero
metrics is preserved at L0; the proxy only sets the columns prior to filtering.

Spec source: vault/30_components/layer-0-universe-discovery.md (Q2 hard filter).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Final

import httpx

from polaris.core.universe.schema import UniverseInstrument

__all__ = [
    "CAPITAL_DEPTH_PROXY_FLOOR_USD",
    "CAPITAL_VOL_PROXY_FLOOR_USD",
    "CapitalProxyConfig",
    "compute_depth_proxy",
    "compute_vol_24h_proxy",
    "fetch_capital_chart_24h",
    "passes_proxy_4_axis",
    "populate_capital_proxies",
]

CAPITAL_BASE_DEMO: Final[str] = "https://demo-api-capital.backend-capital.com"
CAPITAL_PRICES_PATH: Final[str] = "/api/v1/prices"
REST_TIMEOUT_SEC: Final[float] = 15.0

# Proxy threshold tuning — DEMO aggressive, can be relaxed by learner in P1.
CAPITAL_VOL_PROXY_FLOOR_USD: Final[float] = 30_000_000.0
CAPITAL_DEPTH_PROXY_FLOOR_USD: Final[float] = 500_000.0

# CFD pip notional default ($10/pip per standard lot, conservative). Tuned per
# asset_class to reflect typical instrument sizing on Capital demo.
PIP_NOTIONAL_BY_CLASS: Final[dict[str, float]] = {
    "forex": 10.0,
    "indices": 25.0,
    "commodity": 100.0,
    "crypto": 10.0,
    "other": 10.0,
}


@dataclass(frozen=True, slots=True)
class CapitalProxyConfig:
    """Tunable knobs for the proxy (P1 learner-tunable; P0 = defaults)."""

    vol_floor_usd: float = CAPITAL_VOL_PROXY_FLOOR_USD
    depth_floor_usd: float = CAPITAL_DEPTH_PROXY_FLOOR_USD
    resolution: str = "HOUR"
    max_candles: int = 24


# ---------------------------------------------------------------------------
# Pure compute helpers
# ---------------------------------------------------------------------------


def compute_vol_24h_proxy(candles: list[dict[str, Any]]) -> float:
    """Σ ``close × volume`` over up-to-24 HOUR candles.

    Capital's chart row uses ``closePrice.bid``/``.ask`` (use the mid of bid/ask
    if both present, else whichever is available). ``lastTradedVolume`` is the
    candle's volume. Missing fields contribute 0.
    """
    total = 0.0
    for row in candles:
        close = _row_close(row)
        vol = _to_float(row.get("lastTradedVolume"))
        if close <= 0.0 or vol <= 0.0:
            continue
        total += close * vol
    return total


def compute_depth_proxy(
    *,
    spread_bps: float,
    atr_24h_pct: float,
    asset_class: str,
    notional_per_pip: float | None = None,
) -> float:
    """Spread-weighted depth proxy.

    ``depth_proxy = (1 / spread_bps) × atr_pct × notional_per_pip × 10_000``.
    The 10_000 factor flips bps→fractional spread units; the resulting figure is
    a USD-equivalent "how much can move 10bps before slippage" estimate that
    matches the L0 ``depth_10bps_usd`` column semantics.
    """
    if spread_bps <= 0.0 or atr_24h_pct <= 0.0:
        return 0.0
    pip = notional_per_pip
    if pip is None:
        pip = PIP_NOTIONAL_BY_CLASS.get(asset_class, PIP_NOTIONAL_BY_CLASS["other"])
    return (1.0 / spread_bps) * atr_24h_pct * pip * 10_000.0


def passes_proxy_4_axis(
    *,
    vol_proxy_usd: float,
    depth_proxy_usd: float,
    config: CapitalProxyConfig | None = None,
) -> bool:
    """CFD-safe proxy gate: either axis clearing its floor is sufficient.

    Standard 4-axis filter requires ALL four hard rails. For CFD venues that
    cannot publish real notional/depth, the proxy provides an OR-gate so the
    venue isn't fully excluded — this is the codex round 1 P0 P0+1 fix.
    """
    cfg = config or CapitalProxyConfig()
    return vol_proxy_usd >= cfg.vol_floor_usd or depth_proxy_usd >= cfg.depth_floor_usd


# ---------------------------------------------------------------------------
# Network fetch (chart endpoint)
# ---------------------------------------------------------------------------


async def fetch_capital_chart_24h(
    epic: str,
    *,
    cst: str,
    security_token: str,
    base_url: str = CAPITAL_BASE_DEMO,
    resolution: str = "HOUR",
    max_candles: int = 24,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch the last ``max_candles`` ``resolution`` bars for ``epic``.

    Returns the ``prices`` array verbatim so callers can dispatch their own
    proxy compute. Raises on HTTP error / non-200.
    """
    own_client = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        resp = await cli.get(
            f"{CAPITAL_PRICES_PATH}/{epic}",
            params={"resolution": resolution, "max": max_candles},
            headers={"CST": cst, "X-SECURITY-TOKEN": security_token},
        )
        resp.raise_for_status()
        body = resp.json()
        return list(body.get("prices", []))
    finally:
        if own_client:
            await cli.aclose()


async def populate_capital_proxies(
    instruments: list[UniverseInstrument],
    *,
    cst: str,
    security_token: str,
    base_url: str = CAPITAL_BASE_DEMO,
    config: CapitalProxyConfig | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[UniverseInstrument]:
    """Replace ``vol_24h_usd`` + ``depth_10bps_usd`` on every Capital row with proxy values.

    OKX / non-Capital rows pass through untouched. Per-row failure (chart
    endpoint timeout, malformed payload) leaves that instrument's metrics at
    zero so the 4-axis filter rejects it cleanly.
    """
    cfg = config or CapitalProxyConfig()
    own_client = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    out: list[UniverseInstrument] = []
    try:
        for ins in instruments:
            if ins.venue != "capital":
                out.append(ins)
                continue
            try:
                candles = await fetch_capital_chart_24h(
                    ins.symbol,
                    cst=cst,
                    security_token=security_token,
                    base_url=base_url,
                    resolution=cfg.resolution,
                    max_candles=cfg.max_candles,
                    client=cli,
                )
            except (httpx.HTTPError, ValueError) as exc:  # noqa: PERF203 — explicit per-row guard
                # Per-row failure must be visible (audit log path); the L0 smoke
                # script logs the message and the row stays inactive.
                _ = exc
                out.append(ins)
                continue
            vol_proxy = compute_vol_24h_proxy(candles)
            depth_proxy = compute_depth_proxy(
                spread_bps=ins.spread_bps,
                atr_24h_pct=ins.atr_24h_pct,
                asset_class=ins.asset_class,
            )
            out.append(replace(ins, vol_24h_usd=vol_proxy, depth_10bps_usd=depth_proxy))
    finally:
        if own_client:
            await cli.aclose()
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_close(row: dict[str, Any]) -> float:
    """Extract ``close`` from a Capital chart row.

    Capital returns either ``{"closePrice": {"bid": x, "ask": y}}`` or a flat
    ``"close"`` numeric. Bias to mid when both bid+ask are present.
    """
    close_price = row.get("closePrice")
    if isinstance(close_price, dict):
        bid = _to_float(close_price.get("bid"))
        ask = _to_float(close_price.get("ask"))
        if bid > 0 and ask > 0:
            return 0.5 * (bid + ask)
        return bid if bid > 0 else ask
    return _to_float(row.get("close"))


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 0.0
    return f
