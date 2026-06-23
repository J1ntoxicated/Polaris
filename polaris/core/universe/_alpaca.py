"""Layer 0 — Alpaca (Track C / US-equity) universe discovery.

Mirrors the Capital fetcher contract (``_capital.py``): env creds, returns
``[]`` when credentials are missing (smoke-safety), builds
``UniverseInstrument`` rows via a row→instrument helper. Track C is **additive**
— OKX (track A) and Capital (track B) paths are untouched.

US equities are paper-traded via the Alpaca demo (paper) REST API. The fetcher
lists tradable, active ``us_equity`` assets from ``GET /v2/assets`` (~12.9k
rows), then injects REAL per-symbol liquidity (P1, forensic w9gq4ueep): the
old coarse placeholder stamped EVERY row with an identical ``vol_24h_usd=50e6``,
so in ``rank_active_universe`` all rows tied at z=0 and the stable sort seated
the first 40 ALPHABETICALLY — every megacap (AAPL/MSFT/NVDA/SPY/TSLA/...) ended
up ``is_active=0``. To discriminate WITHOUT fetching 12.9k symbols one-by-one,
a bounded enrichment maps real dollar-volume onto the liquid names via the
most-actives screener + batched snapshots (plus a curated megacap/top-ETF seed
so the megacaps are always probed). Symbols with no liquidity datum keep the
coarse placeholder and still flow (flow_not_block — nothing is removed; the
liquid names simply out-rank the rest). This is a rank input, never a sizing
multiplier (9-stack invariant untouched).

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
from polaris.core.universe.schema import LIQUID_EQUITY_SYMBOLS, UniverseInstrument

logger = logging.getLogger(__name__)

ALPACA_PAPER_BASE = "https://paper-api.alpaca.markets"
ALPACA_DATA_BASE = "https://data.alpaca.markets"
ALPACA_ASSETS_PATH = "/v2/assets"
# Liquidity-enrichment endpoints (data host). Both are bounded, batched reads —
# we never iterate the full us_equity list one symbol at a time.
ALPACA_MOST_ACTIVES_PATH = "/v1beta1/screener/stocks/most-actives"
ALPACA_SNAPSHOTS_PATH = "/v2/stocks/snapshots"

# Screener candidate cap (top liquid names by share-volume) and the per-request
# snapshot batch size (Alpaca caps the symbols list; chunk to stay well under).
_MOST_ACTIVES_TOP = 100
_SNAPSHOT_BATCH = 100

# Curated liquidity prior: megacaps + the most-traded ETFs. Always probed for a
# real snapshot even on a day they fall out of the share-volume most-actives,
# so the largest-cap names are never starved by the alphabetical-tie bug. Also
# the offline fallback liquidity seed when a live datum is unavailable.
# SSOT = ``schema.LIQUID_EQUITY_SYMBOLS`` (the same set also drives the equity
# focus-quota priority); sorted here for a deterministic probe order.
LIQUID_SEED_SYMBOLS: tuple[str, ...] = tuple(sorted(LIQUID_EQUITY_SYMBOLS))

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
    data_base_url: str = ALPACA_DATA_BASE,
    api_key: str | None = None,
    secret_key: str | None = None,
    now_ts: int | None = None,
    client: httpx.AsyncClient | None = None,
    data_client: httpx.AsyncClient | None = None,
) -> list[UniverseInstrument]:
    """Fetch active, tradable US-equity assets from the Alpaca paper API.

    ``GET /v2/assets?status=active&asset_class=us_equity`` — each row becomes a
    ``UniverseInstrument`` (venue=alpaca, asset_class=equity, state=live). Real
    per-symbol dollar-volume is then injected onto the liquid names (screener +
    batched snapshots) so the ranking discriminates instead of seating an
    alphabetical slab; rows without a datum keep the coarse placeholder and
    still flow. Returns ``[]`` when credentials are missing (smoke-friendly,
    mirrors Capital).

    ``client`` (when shared, e.g. a MockTransport) serves both the trade-host
    assets call and the data-host enrichment calls; otherwise a separate
    data-host client is opened for the screener/snapshots reads.
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
    # Reuse a caller-supplied client for the data host too (shared MockTransport
    # in tests / a single live client); else open a dedicated data-host client.
    own_data_client = data_client is None and client is None
    data_cli = data_client or client or httpx.AsyncClient(
        base_url=data_base_url, timeout=REST_TIMEOUT_SEC
    )
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

        out: list[UniverseInstrument] = []
        for row in rows or []:
            inst = _alpaca_asset_row_to_instrument(row, now_ts=ts)
            if inst is not None:
                out.append(inst)

        liquidity = await _fetch_alpaca_liquidity(
            data_cli,
            headers=headers,
            symbols={ins.symbol for ins in out},
        )
    finally:
        if own_client:
            await cli.aclose()
        if own_data_client:
            await data_cli.aclose()

    out = _apply_liquidity(out, liquidity)
    enriched = sum(1 for ins in out if ins.symbol in liquidity)
    logger.info(
        "[universe] Alpaca fetched: raw=%d tradable_equity=%d liquidity_enriched=%d",
        len(rows or []),
        len(out),
        enriched,
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


async def _fetch_alpaca_liquidity(
    data_cli: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    symbols: set[str],
) -> dict[str, dict[str, float]]:
    """Real per-symbol liquidity for a BOUNDED candidate set (never all 12.9k).

    Candidates = (most-actives screener ∩ universe) ∪ (curated seed ∩ universe).
    Batched snapshots give the daily bar → real ``vol_24h_usd`` (close × volume)
    and an intraday-range ATR proxy. Any failure returns ``{}`` (smoke-safe — the
    rows then keep their coarse placeholder and still flow). Maps to
    ``{symbol: {"vol_24h_usd", "atr_24h_pct"}}``.
    """
    if not symbols:
        return {}
    try:
        actives = await _fetch_most_actives(data_cli, headers=headers)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("[universe] Alpaca most-actives fetch failed: %r", exc)
        actives = []
    # Bound the snapshot set to names actually in our universe: top liquid
    # screener names + the curated seed (so megacaps are always probed).
    candidates = [s for s in actives if s in symbols]
    seed = [s for s in LIQUID_SEED_SYMBOLS if s in symbols and s not in set(candidates)]
    candidates.extend(seed)
    if not candidates:
        return {}
    try:
        return await _fetch_snapshots_liquidity(
            data_cli, headers=headers, symbols=candidates
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("[universe] Alpaca snapshots fetch failed: %r", exc)
        return {}


async def _fetch_most_actives(
    data_cli: httpx.AsyncClient, *, headers: dict[str, str]
) -> list[str]:
    """Top liquid symbols (by share-volume) from the most-actives screener."""
    resp = await data_cli.get(
        ALPACA_MOST_ACTIVES_PATH,
        params={"by": "volume", "top": str(_MOST_ACTIVES_TOP)},
        headers=headers,
    )
    resp.raise_for_status()
    body = resp.json()
    rows = body.get("most_actives", []) if isinstance(body, dict) else []
    out: list[str] = []
    for row in rows:
        sym = str(row.get("symbol", "")).strip().upper() if isinstance(row, dict) else ""
        if sym:
            out.append(sym)
    return out


async def _fetch_snapshots_liquidity(
    data_cli: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    symbols: list[str],
) -> dict[str, dict[str, float]]:
    """Batched ``/v2/stocks/snapshots`` → real dollar-volume + ATR proxy per sym."""
    out: dict[str, dict[str, float]] = {}
    for start in range(0, len(symbols), _SNAPSHOT_BATCH):
        chunk = symbols[start : start + _SNAPSHOT_BATCH]
        resp = await data_cli.get(
            ALPACA_SNAPSHOTS_PATH,
            params={"symbols": ",".join(chunk)},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        # Newer API nests under "snapshots"; older returns the map at top level.
        snaps = body.get("snapshots", body) if isinstance(body, dict) else {}
        if not isinstance(snaps, dict):
            continue
        for sym, snap in snaps.items():
            liq = _snapshot_to_liquidity(snap)
            if liq is not None:
                out[str(sym).strip().upper()] = liq
    return out


def _snapshot_to_liquidity(snap: Any) -> dict[str, float] | None:
    """Derive ``vol_24h_usd`` (close × volume) + ATR-% proxy from one snapshot."""
    if not isinstance(snap, dict):
        return None
    daily = snap.get("dailyBar")
    if not isinstance(daily, dict):
        return None
    raw_minute = snap.get("minuteBar")
    minute: dict[str, Any] = raw_minute if isinstance(raw_minute, dict) else {}
    close = _num(minute.get("c")) or _num(daily.get("c"))
    volume = _num(daily.get("v"))
    if close <= 0.0 or volume <= 0.0:
        return None
    dollar_vol = close * volume
    high = _num(daily.get("h"))
    low = _num(daily.get("l"))
    atr_pct = ((high - low) / close * 100.0) if (high > low and close > 0.0) else 0.0
    # ``price`` (last close) feeds the universe min_price eligibility floor — it
    # was previously computed and discarded; pennies (TNON $0.59, ADTX $0.017)
    # gap through stops, so the floor needs the price plumbed onto the row.
    return {"vol_24h_usd": dollar_vol, "atr_24h_pct": atr_pct, "price": close}


def _num(value: Any) -> float:
    """Coerce a snapshot numeric field to float (0.0 on missing/garbage)."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _apply_liquidity(
    instruments: list[UniverseInstrument],
    liquidity: dict[str, dict[str, float]],
) -> list[UniverseInstrument]:
    """Overwrite vol/atr on rows that have a real datum; others keep placeholder.

    flow_not_block: no row is dropped — symbols without a liquidity datum retain
    the coarse placeholder and still flow, they simply rank below the enriched
    liquid names. ATR is only overwritten when the snapshot yields a positive
    intraday range (a 0-range datum keeps the placeholder so the name still
    carries a realized-vol signal).
    """
    if not liquidity:
        return instruments
    out: list[UniverseInstrument] = []
    for ins in instruments:
        liq = liquidity.get(ins.symbol)
        if liq is None:
            out.append(ins)
            continue
        atr = liq["atr_24h_pct"] if liq["atr_24h_pct"] > 0.0 else ins.atr_24h_pct
        out.append(
            UniverseInstrument(
                venue=ins.venue,
                symbol=ins.symbol,
                instrument_id=ins.instrument_id,
                underlying_group_id=ins.underlying_group_id,
                asset_class=ins.asset_class,
                quote_ccy=ins.quote_ccy,
                state=ins.state,
                vol_24h_usd=liq["vol_24h_usd"],
                spread_bps=ins.spread_bps,
                atr_24h_pct=atr,
                depth_10bps_usd=ins.depth_10bps_usd,
                signal_density_7d=ins.signal_density_7d,
                listing_ts=ins.listing_ts,
                last_seen_ts=ins.last_seen_ts,
                last_price=liq.get("price", ins.last_price),
            )
        )
    return out


async def refresh_alpaca_universe(
    now_ts: int,
    *,
    base_url: str = ALPACA_PAPER_BASE,
    data_base_url: str = ALPACA_DATA_BASE,
    api_key: str | None = None,
    secret_key: str | None = None,
    client: httpx.AsyncClient | None = None,
    data_client: httpx.AsyncClient | None = None,
) -> list[UniverseInstrument]:
    """Spec-named alias for `fetch_alpaca_instruments` (Track C, additive)."""
    return await fetch_alpaca_instruments(
        base_url=base_url,
        data_base_url=data_base_url,
        api_key=api_key,
        secret_key=secret_key,
        now_ts=now_ts,
        client=client,
        data_client=data_client,
    )
