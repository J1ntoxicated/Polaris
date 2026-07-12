"""DefiLlama stablecoin-liquidity collector (EVIDENCE only) — total circulating
market cap + 7d net-mint, total and per-tracked-symbol (USDT/USDC/DAI).

DEMO/PAPER. Reads DefiLlama's free, keyless ``stablecoins.llama.fi/stablecoins``
snapshot. Net 7d minting = ``circulating - circulatingPrevWeek`` (positive =
new stablecoin supply entering crypto = a liquidity-inflow / risk-on context
signal; negative = net redemption). No pub-vs-ingest lag to audit here — this
is a state SNAPSHOT (B-grade per the sourcing plan), ``ts`` IS the observation
time.

SIGNAL/EVIDENCE only: on a network / parse error this returns ``{}`` (graceful
fallback, NOT a defensive halt). It NEVER raises.

Ref pattern: ``fred_macro.py`` (keyless-friendly snapshot collector contract).
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC
from polaris.storage.db_writer import DBWriter, dbwriter_enabled

logger = logging.getLogger(__name__)

# Shared by both the direct-conn and db_writer-submitted paths so the two
# branches are STRUCTURALLY guaranteed to issue the identical statement.
_STABLECOIN_LIQUIDITY_SQL = (
    "INSERT OR REPLACE INTO stablecoin_liquidity "
    "(ts, symbol, mcap_usd, mcap_prev_day_usd, mcap_prev_week_usd, "
    " net_mint_7d_usd, net_mint_7d_pct, created_ts) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

DEFILLAMA_BASE: Final[str] = "https://stablecoins.llama.fi"
_STABLES_PATH: Final[str] = "/stablecoins"

# Universe quote-stable filter (sourcing plan item #2) — the stables our own
# crypto universe actually quotes/settles against.
TRACKED_SYMBOLS: Final[tuple[str, ...]] = ("USDT", "USDC", "DAI")
_TOTAL_KEY: Final[str] = "TOTAL"


def _pegged_usd(block: Any) -> float | None:
    if not isinstance(block, dict):
        return None
    val = block.get("peggedUSD")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build_entry(
    cur: float | None, prev_day: float | None, prev_week: float | None
) -> dict[str, Any]:
    """Pure: (current, prevDay, prevWeek) mcap -> the emitted evidence entry."""
    net_mint_7d = (cur - prev_week) if (cur is not None and prev_week is not None) else None
    net_mint_7d_pct = (
        round(net_mint_7d / prev_week * 100.0, 4)
        if net_mint_7d is not None and prev_week
        else None
    )
    return {
        "mcap_usd": round(cur, 2) if cur is not None else None,
        "mcap_prev_day_usd": round(prev_day, 2) if prev_day is not None else None,
        "mcap_prev_week_usd": round(prev_week, 2) if prev_week is not None else None,
        "net_mint_7d_usd": round(net_mint_7d, 2) if net_mint_7d is not None else None,
        "net_mint_7d_pct": net_mint_7d_pct,
    }


class DefiLlamaStablesCollector:
    """DefiLlama stablecoin total + per-symbol liquidity snapshot."""

    name = "defillama_stables"
    ttl_sec = 3600  # sourcing plan item #2: "ttl 3600s+"
    asset_classes = ("crypto",)

    def __init__(
        self,
        *,
        conn: sqlite3.Connection | None = None,
        base_url: str = DEFILLAMA_BASE,
        symbols: tuple[str, ...] = TRACKED_SYMBOLS,
        db_writer: DBWriter | None = None,
    ) -> None:
        self._conn = conn
        self._base_url = base_url
        self._symbols = symbols
        # db-writer-reader-split (opt-in, 2026-07-12): None is byte-identical
        # to the pre-migration direct-write path — see ``_persist``.
        self._db_writer = db_writer

    async def fetch(
        self, *, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any] | None:
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        try:
            resp = await cli.get(_STABLES_PATH)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info(
                "[altdata] defillama_stables fetch failed (graceful skip): %s", exc
            )
            return {}
        finally:
            if own:
                await cli.aclose()
        assets = body.get("peggedAssets") if isinstance(body, dict) else None
        if not assets:
            return {}
        ts = int(_dt.datetime.now(_dt.UTC).timestamp())
        wanted = set(self._symbols)
        out: dict[str, Any] = {}
        tot_now = tot_prev_day = tot_prev_week = 0.0
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            sym = str(asset.get("symbol", "")).strip().upper()
            cur = _pegged_usd(asset.get("circulating"))
            prev_day = _pegged_usd(asset.get("circulatingPrevDay"))
            prev_week = _pegged_usd(asset.get("circulatingPrevWeek"))
            tot_now += cur or 0.0
            tot_prev_day += prev_day or 0.0
            tot_prev_week += prev_week or 0.0
            if sym not in wanted:
                continue
            entry = build_entry(cur, prev_day, prev_week)
            out[sym] = entry
            self._persist(sym, ts, entry)
        total_entry = build_entry(tot_now, tot_prev_day, tot_prev_week)
        out[_TOTAL_KEY] = total_entry
        self._persist(_TOTAL_KEY, ts, total_entry)
        return out

    def _persist(self, symbol: str, ts: int, entry: dict[str, Any]) -> None:
        if self._conn is None:
            return
        args = (
            ts, symbol, entry["mcap_usd"], entry["mcap_prev_day_usd"],
            entry["mcap_prev_week_usd"], entry["net_mint_7d_usd"],
            entry["net_mint_7d_pct"], ts,
        )
        try:
            # db-writer-reader-split (opt-in, 2026-07-12): fire-and-forget
            # submit to the shared single-RW-conn writer instead of running
            # on the caller's own ``conn`` directly. Safe to defer — pure
            # liquidity snapshot with no same-tick reader. Default ``None``
            # is byte-identical to the pre-migration direct-write path.
            if self._db_writer is not None and dbwriter_enabled():
                def _job(
                    c: sqlite3.Connection,
                    sql: str = _STABLECOIN_LIQUIDITY_SQL, a: tuple[object, ...] = args,
                ) -> None:
                    c.execute(sql, a)

                self._db_writer.submit(_job, label="stablecoin_liquidity")
            else:
                self._conn.execute(_STABLECOIN_LIQUIDITY_SQL, args)
        except sqlite3.Error as exc:
            logger.warning(
                "[altdata] stablecoin_liquidity persist dropped for %s: %r", symbol, exc
            )
