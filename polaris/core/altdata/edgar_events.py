"""SEC EDGAR filing-events collector (EVIDENCE only) — 8-K/10-Q/10-K provenance.

DEMO/PAPER. Reads SEC EDGAR's ``submissions/CIK##########.json`` feed for every
symbol in our ACTIVE Alpaca equity universe (``universe`` table, venue='alpaca',
is_active=1) and surfaces the LATEST qualifying (8-K/10-Q/10-K) filing per
symbol. Ticker->CIK resolution uses SEC's own bulk ``company_tickers.json``
index (fetched once, cached ~24h — "1회 캐시" per the sourcing plan).

Timestamp provenance (frontgate-scan invariant #2): each stored filing keeps
SEC's own ``acceptanceDateTime`` (A-grade — the moment SEC accepted the
filing) in ``acceptance_ts``, separate from ``ingestion_ts`` (when Polaris
pulled it) — never a single blended timestamp, so no downstream reader can
look ahead of when Polaris actually observed the event.

Bulk-index tradeoff: EDGAR's full-text-search backend (``efts.sec.gov``) DOES
accept a multi-CIK bulk query, but its hits carry only a day-level
``file_date`` — no ``acceptanceDateTime``. Since the A-grade acceptance
timestamp is a hard requirement here, this collector uses the documented
per-CIK ``data.sec.gov/submissions`` endpoint instead, paced well under SEC's
10 req/s fair-access ceiling (https://www.sec.gov/os/accessing-edgar-data).

SIGNAL/EVIDENCE only: on a missing symbol source / network / parse error this
returns ``{}`` (graceful fallback, NOT a defensive halt). It NEVER raises.

Ref pattern: ``news_sentiment.py`` (collector contract + shadow_conn wiring),
``okx_funding.py`` (sequential paced per-instrument fetch loop).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import sqlite3
import time
from typing import Any, Final

import httpx

from polaris.core.altdata._universe_symbols import active_alpaca_symbols
from polaris.core.altdata.filing_proximity_shadow import log_filing_proximity_shadow
from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

SEC_SUBMISSIONS_BASE: Final[str] = "https://data.sec.gov"
SEC_TICKERS_URL: Final[str] = "https://www.sec.gov/files/company_tickers.json"

_UA_ENV: Final[str] = "POLARIS_SEC_USER_AGENT"
_UA_DEFAULT: Final[str] = "Polaris DEMO/PAPER bot hjyoon85@gmail.com"

DEFAULT_FORMS: Final[frozenset[str]] = frozenset({"8-K", "10-Q", "10-K"})

# SEC fair-access ceiling is 10 req/s; paced well under it (~6.7 req/s) so the
# collector never needs a token bucket. Env-tunable (no_hardcode_in_plans).
_PACE_ENV: Final[str] = "POLARIS_EDGAR_REQUEST_PACE_SEC"
_PACE_DEFAULT: Final[float] = 0.15

# Ticker->CIK bulk map: fetched once, refreshed at most ~daily (the mapping
# barely churns intraday).
_CIK_MAP_TTL_SEC: Final[float] = 24 * 3600.0


def _pace_sec() -> float:
    raw = os.environ.get(_PACE_ENV)
    if not raw or not raw.strip():
        return _PACE_DEFAULT
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _PACE_DEFAULT
    return val if val >= 0.0 else _PACE_DEFAULT


def _cik_key(ticker: str) -> str:
    return ticker.strip().upper()


def _parse_iso(raw: Any) -> _dt.datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.UTC)
    return ts


def _days_since(acceptance_ts: int | None, now: _dt.datetime) -> float | None:
    if acceptance_ts is None:
        return None
    return round(max(0.0, now.timestamp() - acceptance_ts) / 86400.0, 3)


def parse_recent_filings(
    body: dict[str, Any], forms: frozenset[str]
) -> list[dict[str, Any]]:
    """Pure: ``submissions/CIK*.json`` body -> qualifying filings, newest-first.

    SEC already returns ``filings.recent`` newest-first (parallel arrays); this
    just zips + filters to ``forms`` and normalises ``acceptanceDateTime`` to
    epoch seconds. Any malformed/short array degrades to skipping that index
    (never raises — degrade-never-crash).
    """
    recent = ((body.get("filings") or {}).get("recent")) or {}
    forms_arr = recent.get("form") or []
    dates_arr = recent.get("filingDate") or []
    accepts_arr = recent.get("acceptanceDateTime") or []
    accessions_arr = recent.get("accessionNumber") or []
    primary_arr = recent.get("primaryDocument") or []
    out: list[dict[str, Any]] = []
    for i, form in enumerate(forms_arr):
        if form not in forms:
            continue
        acc_dt = _parse_iso(accepts_arr[i] if i < len(accepts_arr) else None)
        out.append(
            {
                "form_type": str(form),
                "filing_date": str(dates_arr[i]) if i < len(dates_arr) else "",
                "accession_number": (
                    str(accessions_arr[i]) if i < len(accessions_arr) else ""
                ),
                "primary_document": (
                    str(primary_arr[i]) if i < len(primary_arr) else ""
                ),
                "acceptance_ts": int(acc_dt.timestamp()) if acc_dt is not None else None,
            }
        )
    return out


def build_cik_map(body: Any) -> dict[str, str]:
    """Pure: ``company_tickers.json`` body -> ``{TICKER: cik_str}`` (unpadded)."""
    mapping: dict[str, str] = {}
    if not isinstance(body, dict):
        return mapping
    for row in body.values():
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker")
        cik = row.get("cik_str")
        if not ticker or cik is None:
            continue
        try:
            mapping[_cik_key(str(ticker))] = str(int(cik))
        except (TypeError, ValueError):
            continue
    return mapping


class EdgarEventsCollector:
    """SEC EDGAR 8-K/10-Q/10-K latest-filing-per-symbol (EVIDENCE only)."""

    name = "edgar_events"
    ttl_sec = 900  # 15min — mirrors news_sentiment's event-driven cadence
    asset_classes = ("equity",)

    def __init__(
        self,
        *,
        conn: sqlite3.Connection | None = None,
        symbols_override: tuple[str, ...] | None = None,
        base_url: str = SEC_SUBMISSIONS_BASE,
        tickers_url: str = SEC_TICKERS_URL,
        user_agent: str | None = None,
        forms: frozenset[str] = DEFAULT_FORMS,
    ) -> None:
        self._conn = conn
        self._symbols_override = symbols_override
        self._base_url = base_url
        self._tickers_url = tickers_url
        self._user_agent = user_agent or os.environ.get(_UA_ENV) or _UA_DEFAULT
        self._forms = forms
        self._cik_map: dict[str, str] | None = None
        self._cik_map_ts: float = 0.0

    def _resolve_symbols(self) -> tuple[str, ...]:
        if self._symbols_override is not None:
            return self._symbols_override
        return active_alpaca_symbols(self._conn)

    async def fetch(
        self, *, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any] | None:
        symbols = self._resolve_symbols()
        if not symbols:
            return {}
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        own = client is None
        cli = client or httpx.AsyncClient(timeout=REST_TIMEOUT_SEC, headers=headers)
        out: dict[str, Any] = {}
        pace = _pace_sec()
        try:
            cik_map = await self._cik_map_for(cli, headers)
            if not cik_map:
                return {}
            now = _dt.datetime.now(_dt.UTC)
            for sym in symbols:
                cik = cik_map.get(_cik_key(sym))
                if not cik:
                    continue
                try:
                    filings = await self._recent_filings(cli, cik, headers)
                except (httpx.HTTPError, ValueError) as exc:
                    logger.info(
                        "[altdata] edgar_events %s fetch failed (skip): %s", sym, exc
                    )
                    await asyncio.sleep(pace)
                    continue
                await asyncio.sleep(pace)
                if not filings:
                    continue
                self._persist(sym, cik, filings, now)
                latest = filings[0]
                out[sym] = {
                    "form_type": latest["form_type"],
                    "filing_date": latest["filing_date"],
                    "accession_number": latest["accession_number"],
                    "acceptance_ts": latest["acceptance_ts"],
                    "days_since": _days_since(latest["acceptance_ts"], now),
                }
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.info("[altdata] edgar_events fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
        return out

    async def _cik_map_for(
        self, cli: httpx.AsyncClient, headers: dict[str, str]
    ) -> dict[str, str]:
        now = time.monotonic()
        if self._cik_map is not None and (now - self._cik_map_ts) < _CIK_MAP_TTL_SEC:
            return self._cik_map
        resp = await cli.get(self._tickers_url, headers=headers)
        resp.raise_for_status()
        mapping = build_cik_map(resp.json())
        self._cik_map = mapping
        self._cik_map_ts = now
        return mapping

    async def _recent_filings(
        self, cli: httpx.AsyncClient, cik: str, headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        url = f"{self._base_url}/submissions/CIK{int(cik):010d}.json"
        resp = await cli.get(url, headers=headers)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return parse_recent_filings(resp.json(), self._forms)

    def _persist(
        self,
        symbol: str,
        cik: str,
        filings: list[dict[str, Any]],
        now: _dt.datetime,
    ) -> None:
        if self._conn is None:
            return
        ingestion_ts = int(now.timestamp())
        try:
            for f in filings:
                self._conn.execute(
                    "INSERT OR IGNORE INTO edgar_filings "
                    "(symbol, cik, accession_number, form_type, filing_date, "
                    " acceptance_ts, ingestion_ts, primary_document, created_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        symbol, cik, f["accession_number"], f["form_type"],
                        f["filing_date"], f["acceptance_ts"], ingestion_ts,
                        f["primary_document"], ingestion_ts,
                    ),
                )
        except sqlite3.Error as exc:
            logger.warning(
                "[altdata] edgar_filings persist dropped for %s: %r", symbol, exc
            )
            return
        latest = filings[0]
        try:
            log_filing_proximity_shadow(
                self._conn,
                symbol=symbol,
                cycle_ts=ingestion_ts,
                form_type=latest["form_type"],
                accession_number=latest["accession_number"],
                acceptance_ts=latest["acceptance_ts"],
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 — instrumentation must never break fetch()
            logger.warning(
                "[altdata] filing_proximity_shadow dropped for %s: %r", symbol, exc
            )
