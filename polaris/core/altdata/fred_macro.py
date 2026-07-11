"""FRED macro collector (EVIDENCE only) — comprehensive macro panel.

DEMO/PAPER. Reads slow-moving macro series from the St. Louis Fed (FRED) using
``FRED_API_KEY`` (present in .env). Elevated VIX / HY spread = risk-off /
crisis evidence for the regime fuser of non-crypto (forex/index/commodity)
groups. Graceful ``{}`` if the key is missing — no network call, no throttle.

This collector was expanded from the original 4-series probe (vix / hy_spread /
move / yield_curve) to a curated ~30-series macro panel spanning rates / prices
/ employment / growth / money / credit / housing / dollar / consumer /
commodities / volatility. Every series is FREE-tier and live-200 verified. The
4 original output keys (``vix`` / ``hy_spread`` / ``move`` / ``yield_curve``)
keep IDENTICAL names + units so the existing fuser scorer + tests are
unaffected; the new series are PURE additive EVIDENCE surfaced to the AI judge
(no scorer change required — they enrich ``evidence_json`` context).

Unit normalisation: ICE BofA OAS series (``hy_spread`` / ``ig_spread``) are
published by FRED in PERCENT; the system-wide spread unit is bps, so those two
are ×100. Every other series passes through raw (FRED's native unit).

Currency (#69): each emitted value carries its FRED OBSERVATION date as
``<key>_asof`` (the day FRED actually printed the value) so a weekend / holiday
/ monthly-lag read served as 'current' is age-labelable downstream. Monthly /
quarterly series (CPI, payrolls, GDP, M2 …) are observation-dated weeks-to-months
in the past BY NATURE — the asof lets the judge age-discount, never a block
(flow_not_block).

Rate-limit: FRED allows 120 req/min/key. The full panel is ~30 GET calls fired
once per ``ttl_sec`` (3600s) cadence — a single hourly batch, far under the
limit. No token bucket needed; the AltDataCache TTL is the throttle.

MOVE (ICE BofAML bond-vol) is NOT redistributed by FRED (proprietary) and
returns HTTP 400 'series does not exist'; ``_latest`` swallows that to None so
``move`` is permanently absent (no crash). It is retained as a key for output
stability — the bond-vol axis is simply unfilled.

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

# Output key → FRED series id. Curated, FREE-tier, live-200-verified panel.
# ── ORIGINAL 4 (unchanged names + units — fuser scorer + tests depend on these)
# ── plus the additive macro panel (pure EVIDENCE for the AI judge).
_SERIES: Final[dict[str, str]] = {
    # — Original 4 (scored by the fuser macro scorer) —
    "vix": "VIXCLS",  # CBOE VIX (EOD)
    "hy_spread": "BAMLH0A0HYM2",  # ICE BofA US HY OAS (percent → bps)
    "move": "MOVE",  # ICE BofAML MOVE (proprietary — FRED 400, stays None)
    "yield_curve": "T10Y2Y",  # 10Y-2Y Treasury spread
    # — Rates (daily) —
    "fed_funds": "DFF",  # effective Fed funds rate
    "ust_10y": "DGS10",  # 10Y Treasury yield
    "ust_2y": "DGS2",  # 2Y Treasury yield
    "yield_curve_10y3m": "T10Y3M",  # 10Y-3M spread (standard recession signal)
    "sofr": "SOFR",  # secured overnight financing rate
    # — Inflation —
    "cpi": "CPIAUCSL",  # CPI all items (monthly)
    "core_cpi": "CPILFESL",  # CPI ex food & energy (monthly)
    "pce": "PCEPI",  # PCE price index (monthly)
    "breakeven_5y": "T5YIE",  # 5Y breakeven inflation (daily, market expectation)
    "breakeven_10y": "T10YIE",  # 10Y breakeven inflation (daily)
    # — Employment —
    "unemployment": "UNRATE",  # unemployment rate (monthly)
    "nonfarm_payrolls": "PAYEMS",  # total nonfarm payrolls (monthly)
    "initial_claims": "ICSA",  # initial jobless claims (weekly)
    # — Growth —
    "real_gdp": "GDPC1",  # real GDP (quarterly)
    "industrial_production": "INDPRO",  # industrial production (monthly)
    # — Money / liquidity —
    "m2": "M2SL",  # M2 money stock (monthly)
    "fed_balance_sheet": "WALCL",  # Fed total assets (weekly)
    # — Credit —
    "ig_spread": "BAMLC0A0CM",  # ICE BofA US IG OAS (percent → bps)
    # — Housing —
    "housing_starts": "HOUST",  # housing starts (monthly)
    "home_price_index": "CSUSHPISA",  # Case-Shiller national HPI (monthly)
    # — Dollar / FX —
    "dollar_index": "DTWEXBGS",  # broad trade-weighted USD index (daily)
    "eur_usd": "DEXUSEU",  # USD per EUR (daily, ~1wk lag)
    "usd_jpy": "DEXJPUS",  # JPY per USD (daily)
    # — Consumer —
    "consumer_sentiment": "UMCSENT",  # U-Mich consumer sentiment (monthly)
    # — Commodities —
    "wti_crude": "DCOILWTICO",  # WTI crude spot (daily)
    "brent_crude": "DCOILBRENTEU",  # Brent crude spot (daily)
    # — Financial-stress composites —
    "stl_stress": "STLFSI4",  # St. Louis Fed financial stress (weekly)
    "nfci": "NFCI",  # Chicago Fed national financial conditions (weekly)
    # — Volatility family —
    "vix_3m": "VXVCLS",  # 3M VIX (term structure)
    "oil_vol": "OVXCLS",  # CBOE crude-oil VIX
    "gold_vol": "GVZCLS",  # CBOE gold VIX
    "dfii10": "DFII10",  # 10Y TIPS real yield (frontgate item #5 — gold conviction)
}

# Series published by FRED in PERCENT that the system uses in bps (×100).
_PERCENT_TO_BPS: Final[frozenset[str]] = frozenset({"hy_spread", "ig_spread"})

# Output key → observation-date key. ``hy_spread`` shortens to ``hy_asof`` (the
# name the fuser / judge prompt expect); every other key is ``<key>_asof``.
_ASOF_SPECIAL: Final[dict[str, str]] = {"hy_spread": "hy_asof"}


def _asof_key(key: str) -> str:
    """Observation-date key for an output key (``hy_spread`` → ``hy_asof``)."""
    return _ASOF_SPECIAL.get(key, f"{key}_asof")


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
        out: dict[str, float | str | None] = {}
        try:
            for key, series_id in _SERIES.items():
                val, obs_date = await self._latest(cli, series_id)
                # ICE BofA OAS series are in percent; system-wide unit is bps.
                if key in _PERCENT_TO_BPS and val is not None:
                    val = val * 100
                out[key] = val
                # Currency: preserve the OBSERVATION date (the day FRED actually
                # printed this value) so a weekend / holiday / monthly-lag read
                # served as 'current' is age-labelable downstream. Surfaced ONLY
                # when a real value was found (no value → no fake date).
                if val is not None and obs_date:
                    out[_asof_key(key)] = obs_date
        except (httpx.HTTPError, RuntimeError) as exc:
            # Surface the failure TYPE (+ HTTP status) — httpx timeouts carry an
            # EMPTY message, so the old %s logged a bare "... skip): " that hid what
            # silenced the macro feed. NB: log the class name / status, NEVER
            # str(exc)/repr(exc): on a raise_for_status error HTTPStatusError embeds
            # the full request URL incl. ``?api_key=<FRED_KEY>`` → a secret leak.
            detail = type(exc).__name__
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None:
                detail = f"{detail} HTTP {status}"
            logger.info("[altdata] fred_macro fetch failed (graceful skip): %s", detail)
            return {}
        finally:
            if own:
                await cli.aclose()
        return out

    async def _latest(
        self, cli: httpx.AsyncClient, series_id: str
    ) -> tuple[float | None, str | None]:
        """Latest valid (value, observation-date) for a series; ``(None, None)`` if none."""
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
            return None, None
        resp.raise_for_status()
        body = resp.json()
        for obs in body.get("observations") or []:
            raw = obs.get("value", ".")
            if raw and raw != ".":
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    return None, None
                raw_date = obs.get("date")
                obs_date = str(raw_date) if raw_date else None
                return value, obs_date
        return None, None
