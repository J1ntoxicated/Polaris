"""CFTC Commitment-of-Traders collector (EVIDENCE only) — commodity positioning.

DEMO/PAPER. The CFTC publishes the weekly Commitment of Traders (COT) report
every Friday (data as of the prior Tuesday) — a scheduled, high-impact commodity
EVENT. This reads the legacy futures-only report from the CFTC public Socrata API
(``publicreporting.cftc.gov`` — FREE, NO API KEY) and derives, per commodity, the
large-speculator (non-commercial) NET positioning normalised by open interest:

    net_spec_pct = (noncomm_long - noncomm_short) / open_interest

then ranks THIS week's reading against the contract's OWN ~3yr distribution:

    net_spec_pctile = fraction of trailing weeks with a lower net_spec_pct

Per-contract normalisation matters: net-spec is STRUCTURALLY net-long for storable
/ precious commodities (gold is net-long ~100% of weeks), so a flat cross-commodity
cut on the raw level would just measure WHICH commodity it is, not whether
positioning is extreme NOW. A contract sitting at its own median scores ~0.5
(neutral); only an extreme vs its OWN range fires. Speculators unusually net-long
FOR THIS CONTRACT are trend-confirming BULL evidence; unusually net-short BEAR.
This is a directional CONVICTION SIGNAL (alt-data = signal, per the Aggressive
mandate) fed into the regime fuser for the matching ``commodity:<SYM>`` group —
NEVER a size dampen, entry block, or halt. Graceful ``{}`` on any network error.

Keyed by our universe symbol (the fuser indexes per ``commodity:<SYM>`` group);
markets with no CFTC contract (e.g. NATURALGAS Henry-Hub naming, ICE GASOIL) are
simply absent → those groups keep the macro-only evidence (no-op).

Signal interpretation (momentum-confirmation, the standard COT trend reading) is
FLAGGED for /debate calibration alongside the thresholds.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

CFTC_BASE: Final[str] = "https://publicreporting.cftc.gov"
# Legacy COT, futures-only (combined report would double-count options-equivalent).
_COT_PATH: Final[str] = "/resource/6dca-aqww.json"
# ~3yr of weekly reports: enough trailing history to rank THIS week's positioning
# against each contract's OWN distribution (per-contract normalisation — net-spec
# is structurally net-long for storables, so a flat cross-commodity cut would just
# measure which commodity it is, not whether positioning is extreme NOW).
_LOOKBACK_WEEKS: Final[int] = 160
# Minimum trailing weeks (besides the latest) for a credible percentile; thinner
# contracts (a newly listed market) yield no COT signal → macro stands (graceful).
_MIN_HISTORY_WEEKS: Final[int] = 26

# Our universe symbol → EXACT CFTC ``contract_market_name`` (highest-OI main
# contract; MICRO/MINI/DIFF variants deliberately excluded). Verified live
# against the Socrata feed. Unmapped commodities (NATURALGAS, GASOIL, MCU3) get
# no COT evidence and fall back to the macro scorer (graceful).
_MARKET_TO_SYMBOL: Final[dict[str, str]] = {
    "CRUDE OIL, LIGHT SWEET-WTI": "OIL_CRUDE",
    "BRENT LAST DAY": "OIL_BRENT",
    "GOLD": "GOLD",
    "SILVER": "SILVER",
    "COPPER- #1": "COPPER",
    "CORN": "CORN",
    "SOYBEANS": "SOYBEAN",
    "SOYBEAN OIL": "SOYBEANOIL",
    "SOYBEAN MEAL": "SOYBEANMEAL",
    "COFFEE C": "COFFEEARABICA",
    "COCOA": "USCOCOA",
    "COTTON NO. 2": "USCOTTON",
    "GASOLINE RBOB": "GASOLINE",
    "NY HARBOR ULSD": "HEATINGOIL",
    "PLATINUM": "PLATINUM",
    "LEAN HOGS": "LEANHOGS",
    "LIVE CATTLE": "LIVECATTLE",
    "FEEDER CATTLE": "FEEDERCATTLE",
    "OATS": "OATS",
}


class CFTCCotCollector:
    """Weekly commodity speculative positioning (no API key)."""

    name = "cftc_cot"
    ttl_sec = 21600  # 6h — COT only changes weekly; refresh a few times a day.
    asset_classes = ("commodity",)

    def __init__(self, *, base_url: str = CFTC_BASE) -> None:
        self._base_url = base_url

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        try:
            rows = await self._recent_rows(cli)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.info("[altdata] cftc_cot fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
        return _derive_signals(rows)

    async def _recent_rows(self, cli: httpx.AsyncClient) -> list[dict[str, Any]]:
        # Filter server-side to OUR mapped markets only (≈19), newest-first, up
        # to _LOOKBACK_WEEKS of history each — a bounded ~19×160 query. We then
        # build each contract's own trailing distribution in Python. NOTE: the
        # $limit is load-bearing — it must stay >> markets×_MIN_HISTORY_WEEKS so
        # the global date-DESC window still covers ≥26 weeks for every market
        # (current margin ≈ 160 vs the 26 floor); bump it if the mapping grows.
        in_clause = "contract_market_name in (" + ",".join(
            f"'{name}'" for name in _MARKET_TO_SYMBOL
        ) + ")"
        resp = await cli.get(
            _COT_PATH,
            params={
                "$select": (
                    "contract_market_name,report_date_as_yyyy_mm_dd,"
                    "noncomm_positions_long_all,noncomm_positions_short_all,"
                    "open_interest_all"
                ),
                "$where": in_clause,
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": str(len(_MARKET_TO_SYMBOL) * _LOOKBACK_WEEKS),
            },
        )
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, list) else []


def _derive_signals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per mapped symbol: latest positioning ranked against its OWN history.

    ``rows`` arrive newest-first. For each contract we build the full trailing
    series of ``net_spec_pct`` and emit ``net_spec_pctile`` — the fraction of
    prior weeks BELOW the latest reading (0=most net-short ever, 1=most net-long
    ever vs its own ~3yr range). This per-contract normalisation is the fix for
    the structural net-long bias: a commodity sitting at its own median scores
    ~0.5 (neutral), and a contract that is normally net-flat can still register a
    bearish extreme. ``net_spec_pct`` (raw level) + ``net_spec_chg`` (1-week
    delta) are surfaced as evidence. Contracts with < ``_MIN_HISTORY_WEEKS`` of
    history, non-positive OI, or unparsable fields yield no signal (graceful).
    """
    by_symbol: dict[str, list[float]] = {}
    report_date: dict[str, str] = {}
    for row in rows:
        sym = _MARKET_TO_SYMBOL.get(str(row.get("contract_market_name", "")))
        if sym is None:
            continue
        pct = _net_spec_pct(row)
        if pct is None:
            continue
        by_symbol.setdefault(sym, []).append(pct)
        report_date.setdefault(sym, str(row.get("report_date_as_yyyy_mm_dd", ""))[:10])

    out: dict[str, Any] = {}
    for sym, series in by_symbol.items():
        latest = series[0]
        history = series[1:]
        if len(history) < _MIN_HISTORY_WEEKS:
            continue
        if max(history) == min(history):
            # No dispersion (a feed glitch repeating an identical row) → the
            # strict-`<` rank would spuriously read 0.0 (BEAR extreme). Skip —
            # never reached with real data (float ratios over ~160 distinct
            # weeks don't collide), a defensive guard only.
            continue
        pctile = sum(1 for h in history if h < latest) / len(history)
        out[sym] = {
            "net_spec_pctile": pctile,
            "net_spec_pct": latest,
            "net_spec_chg": latest - history[0],
            "report_date": report_date.get(sym, ""),
        }
    return out


def _net_spec_pct(row: dict[str, Any]) -> float | None:
    raw_long = row.get("noncomm_positions_long_all")
    raw_short = row.get("noncomm_positions_short_all")
    raw_oi = row.get("open_interest_all")
    if raw_long is None or raw_short is None or raw_oi is None:
        return None
    try:
        long_ = float(raw_long)
        short = float(raw_short)
        oi = float(raw_oi)
    except (TypeError, ValueError):
        return None
    if oi <= 0.0:
        return None
    return (long_ - short) / oi


__all__ = ["CFTCCotCollector"]
