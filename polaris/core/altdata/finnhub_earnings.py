"""Finnhub earnings-calendar collector (EVIDENCE only) — forward 14d + trailing
7d earnings prints for our ACTIVE Alpaca equity universe.

DEMO/PAPER. Reads Finnhub's free-tier ``/api/v1/calendar/earnings`` (60
calls/min) over a single date-range window and intersects the result with our
active Alpaca universe (``universe`` table, venue='alpaca', is_active=1).
Stores every matched row (upserted on ``(symbol, earnings_date)`` so
``eps_actual``/``surprise_pct`` fill in once the print lands) and surfaces the
NEAREST print per symbol (soonest-upcoming, or most-recent-past if none
upcoming) as the collector's EVIDENCE entry.

``surprise_pct`` is a plain ``(actual-estimate)/|estimate|`` proxy, NOT a real
SUE (no consensus-revision/std-dev) — see
vault/50_research/frontgate-scan/scan-event-models.md R1.

Keyless graceful skip: absent ``FINNHUB_API_KEY`` returns ``{}`` with NO
network call (mirrors ``fred_macro.py``'s FRED_API_KEY gate).

SIGNAL/EVIDENCE only: on a missing key / network / parse error this returns
``{}`` (graceful fallback, NOT a defensive halt). It NEVER raises.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import sqlite3
from typing import Any, Final

import httpx

from polaris.core.altdata._universe_symbols import active_alpaca_symbols
from polaris.core.altdata.earnings_proximity_shadow import log_earnings_proximity_shadow
from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

FINNHUB_BASE: Final[str] = "https://finnhub.io"
_CALENDAR_PATH: Final[str] = "/api/v1/calendar/earnings"
_API_KEY_ENV: Final[str] = "FINNHUB_API_KEY"

_LOOKAHEAD_DAYS: Final[int] = 14
_LOOKBACK_DAYS: Final[int] = 7


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def surprise_pct(eps_estimate: float | None, eps_actual: float | None) -> float | None:
    """Pure: plain EPS-surprise proxy (NOT a real SUE — see module docstring)."""
    if eps_estimate is None or eps_actual is None or eps_estimate == 0.0:
        return None
    return round((eps_actual - eps_estimate) / abs(eps_estimate) * 100.0, 4)


def _days_from(date_str: str, now: _dt.datetime) -> float:
    try:
        d = _dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_dt.UTC)
    except ValueError:
        return 0.0
    return round((d - now).total_seconds() / 86400.0, 3)


class FinnhubEarningsCollector:
    """Finnhub earnings-calendar, active-universe-intersected (EVIDENCE only)."""

    name = "finnhub_earnings"
    ttl_sec = 21600  # 6h — sourcing plan item #3
    asset_classes = ("equity",)

    def __init__(
        self,
        *,
        api_key: str | None = None,
        conn: sqlite3.Connection | None = None,
        symbols_override: tuple[str, ...] | None = None,
        base_url: str = FINNHUB_BASE,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get(_API_KEY_ENV, "")
        ) or ""
        self._conn = conn
        self._symbols_override = symbols_override
        self._base_url = base_url

    def _resolve_symbols(self) -> tuple[str, ...]:
        if self._symbols_override is not None:
            return self._symbols_override
        return active_alpaca_symbols(self._conn)

    async def fetch(
        self, *, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any] | None:
        if not self._api_key:
            logger.info("[altdata] finnhub_earnings: no FINNHUB_API_KEY (graceful skip)")
            return {}
        symbols = self._resolve_symbols()
        if not symbols:
            return {}
        now = _dt.datetime.now(_dt.UTC)
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        try:
            rows = await self._fetch_calendar(cli, now)
        except httpx.HTTPError as exc:
            # NEVER log str(exc)/repr(exc) — the request URL embeds
            # ``?token=<FINNHUB_KEY>`` (mirrors fred_macro's secret-leak guard).
            detail = type(exc).__name__
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None:
                detail = f"{detail} HTTP {status}"
            logger.info("[altdata] finnhub_earnings fetch failed (graceful skip): %s", detail)
            return {}
        except ValueError as exc:
            logger.info("[altdata] finnhub_earnings parse failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()
        symset = set(symbols)
        matched: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            sym = str(row.get("symbol", "")).strip().upper()
            date_str = str(row.get("date", "")).strip()
            if not sym or sym not in symset or not date_str:
                continue
            matched.setdefault(sym, []).append(row)
        ingestion_ts = int(now.timestamp())
        out: dict[str, Any] = {}
        for sym, sym_rows in matched.items():
            for row in sym_rows:
                self._persist(sym, row, ingestion_ts)
            nearest = min(
                sym_rows, key=lambda r: abs(_days_from(str(r.get("date", "")), now))
            )
            eps_est = _to_float(nearest.get("epsEstimate"))
            eps_act = _to_float(nearest.get("epsActual"))
            entry = {
                "earnings_date": str(nearest.get("date", "")),
                "hour": str(nearest.get("hour", "") or ""),
                "eps_estimate": eps_est,
                "eps_actual": eps_act,
                "surprise_pct": surprise_pct(eps_est, eps_act),
                "days_from_now": _days_from(str(nearest.get("date", "")), now),
            }
            out[sym] = entry
            self._log_shadow(sym, entry, ingestion_ts)
        return out

    async def _fetch_calendar(
        self, cli: httpx.AsyncClient, now: _dt.datetime
    ) -> list[dict[str, Any]]:
        start = (now - _dt.timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        end = (now + _dt.timedelta(days=_LOOKAHEAD_DAYS)).strftime("%Y-%m-%d")
        resp = await cli.get(
            _CALENDAR_PATH,
            params={"from": start, "to": end, "token": self._api_key},
        )
        resp.raise_for_status()
        body = resp.json()
        rows = body.get("earningsCalendar") if isinstance(body, dict) else None
        return [r for r in (rows or []) if isinstance(r, dict)]

    def _persist(self, symbol: str, row: dict[str, Any], ingestion_ts: int) -> None:
        if self._conn is None:
            return
        eps_est = _to_float(row.get("epsEstimate"))
        eps_act = _to_float(row.get("epsActual"))
        try:
            self._conn.execute(
                "INSERT INTO earnings_calendar "
                "(symbol, earnings_date, hour, eps_estimate, eps_actual, "
                " revenue_estimate, revenue_actual, surprise_pct, ingestion_ts, "
                " created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol, earnings_date) DO UPDATE SET "
                " hour=excluded.hour, eps_estimate=excluded.eps_estimate, "
                " eps_actual=excluded.eps_actual, "
                " revenue_estimate=excluded.revenue_estimate, "
                " revenue_actual=excluded.revenue_actual, "
                " surprise_pct=excluded.surprise_pct, "
                " ingestion_ts=excluded.ingestion_ts",
                (
                    symbol, str(row.get("date", "")), str(row.get("hour", "") or ""),
                    eps_est, eps_act, _to_float(row.get("revenueEstimate")),
                    _to_float(row.get("revenueActual")), surprise_pct(eps_est, eps_act),
                    ingestion_ts, ingestion_ts,
                ),
            )
        except sqlite3.Error as exc:
            logger.warning(
                "[altdata] earnings_calendar persist dropped for %s: %r", symbol, exc
            )

    def _log_shadow(self, symbol: str, entry: dict[str, Any], cycle_ts: int) -> None:
        if self._conn is None:
            return
        try:
            log_earnings_proximity_shadow(
                self._conn,
                symbol=symbol,
                cycle_ts=cycle_ts,
                days_to_earnings=entry["days_from_now"],
                hour=entry["hour"],
                surprise_pct=entry["surprise_pct"],
            )
        except Exception as exc:  # noqa: BLE001 — instrumentation must never break fetch()
            logger.warning(
                "[altdata] earnings_proximity_shadow dropped for %s: %r", symbol, exc
            )
