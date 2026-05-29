"""Layer 0 — Alpaca (Track C / US-equity) universe discovery.

Mirrors the Capital fetcher contract (``_capital.py``): env creds, returns
``[]`` when credentials are missing (smoke-safety), builds
``UniverseInstrument`` rows via a row→instrument helper. Track C is **additive**
— OKX (track A) and Capital (track B) paths are untouched.

US equities are paper-traded via the Alpaca demo (paper) REST API. The fetcher
lists tradable, active ``us_equity`` assets from ``GET /v2/assets``. Liquidity
proxies (vol/spread/ATR) are coarse placeholders here — the dashboards/learners
refine them downstream (same posture as Capital's nav-tree zeros).

Spec source: vault/30_components/layer-0-universe-discovery.md.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.universe._helpers import REST_TIMEOUT_SEC
from polaris.core.universe.schema import UniverseInstrument

logger = logging.getLogger(__name__)

ALPACA_PAPER_BASE = "https://paper-api.alpaca.markets"
ALPACA_ASSETS_PATH = "/v2/assets"

# env: bare name first, ARCHIVE_* fallback (T9 convention — reuse it).
ALPACA_API_KEY_ENV = "ALPACA_PAPER_API_KEY"
ALPACA_SECRET_ENV = "ALPACA_PAPER_SECRET"
ALPACA_API_KEY_ENV_FALLBACK = "ARCHIVE_ALPACA_PAPER_API_KEY"
ALPACA_SECRET_ENV_FALLBACK = "ARCHIVE_ALPACA_PAPER_SECRET"

# Coarse class-level liquidity placeholder. US large/mid caps clear the
# continuous-rank cut comfortably; per-symbol bars refine this later. This is a
# rank input, never a sizing multiplier (9-stack invariant untouched).
_PLACEHOLDER_VOL_24H_USD = 50_000_000.0
_PLACEHOLDER_SPREAD_BPS = 2.0
_PLACEHOLDER_ATR_24H_PCT = 1.5


def _resolve_creds(
    api_key: str | None, secret_key: str | None
) -> tuple[str | None, str | None]:
    """Resolve Alpaca paper creds: explicit args → bare env → ARCHIVE_* env."""
    api_key = (
        api_key
        or os.environ.get(ALPACA_API_KEY_ENV)
        or os.environ.get(ALPACA_API_KEY_ENV_FALLBACK)
    )
    secret_key = (
        secret_key
        or os.environ.get(ALPACA_SECRET_ENV)
        or os.environ.get(ALPACA_SECRET_ENV_FALLBACK)
    )
    return api_key, secret_key


async def fetch_alpaca_instruments(
    *,
    base_url: str = ALPACA_PAPER_BASE,
    api_key: str | None = None,
    secret_key: str | None = None,
    now_ts: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[UniverseInstrument]:
    """Fetch active, tradable US-equity assets from the Alpaca paper API.

    ``GET /v2/assets?status=active&asset_class=us_equity`` — each row becomes a
    ``UniverseInstrument`` (venue=alpaca, asset_class=equity, state=live).
    Returns ``[]`` when credentials are missing (smoke-friendly, mirrors
    Capital).
    """
    ts = now_ts if now_ts is not None else int(time.time())
    api_key, secret_key = _resolve_creds(api_key, secret_key)
    if not (api_key and secret_key):
        logger.info("[universe] Alpaca fetch skipped — credentials missing")
        return []

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    own_client = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        resp = await cli.get(
            ALPACA_ASSETS_PATH,
            params={
                "status": "active",
                "asset_class": "us_equity",
            },
            headers=headers,
        )
        resp.raise_for_status()
        rows = resp.json()
    finally:
        if own_client:
            await cli.aclose()

    out: list[UniverseInstrument] = []
    for row in rows or []:
        inst = _alpaca_asset_row_to_instrument(row, now_ts=ts)
        if inst is not None:
            out.append(inst)
    logger.info(
        "[universe] Alpaca fetched: raw=%d tradable_equity=%d",
        len(rows or []),
        len(out),
    )
    return out


def _alpaca_asset_row_to_instrument(
    row: dict[str, Any], *, now_ts: int
) -> UniverseInstrument | None:
    """Convert one Alpaca ``/v2/assets`` row → UniverseInstrument (or None)."""
    symbol = str(row.get("symbol", "")).strip().upper()
    if not symbol:
        return None
    if str(row.get("class", "")) != "us_equity":
        return None
    if not bool(row.get("tradable", False)):
        return None
    if str(row.get("status", "")) != "active":
        return None

    return UniverseInstrument(
        venue="alpaca",
        symbol=symbol,
        instrument_id=f"alpaca:{symbol}",
        underlying_group_id=compute_underlying_group_id("alpaca", symbol, "equity"),
        asset_class="equity",
        quote_ccy="USD",
        state="live",
        vol_24h_usd=_PLACEHOLDER_VOL_24H_USD,
        spread_bps=_PLACEHOLDER_SPREAD_BPS,
        atr_24h_pct=_PLACEHOLDER_ATR_24H_PCT,
        depth_10bps_usd=0.0,  # refined by dashboards/learners (P1 bars)
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=now_ts,
    )


async def refresh_alpaca_universe(
    now_ts: int,
    *,
    base_url: str = ALPACA_PAPER_BASE,
    api_key: str | None = None,
    secret_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[UniverseInstrument]:
    """Spec-named alias for `fetch_alpaca_instruments` (Track C, additive)."""
    return await fetch_alpaca_instruments(
        base_url=base_url,
        api_key=api_key,
        secret_key=secret_key,
        now_ts=now_ts,
        client=client,
    )
