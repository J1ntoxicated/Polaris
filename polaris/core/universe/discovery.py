"""Layer 0 — Dynamic Universe Discovery (OKX SPOT + Capital CFD).

P0 = REST poll; P1 = WebSocket stream.

Spec source: vault/30_components/layer-0-universe-discovery.md (Q1 + Q2 + Q5 + Q6).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import replace
from typing import Any

import httpx

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.universe.schema import (
    ALLOWED_QUOTE_CCY_OKX,
    ATR_FLOOR_BY_CLASS,
    FilterThresholds,
    UniverseInstrument,
    default_thresholds,
)

logger = logging.getLogger(__name__)

__all__ = [
    "apply_active_filters",
    "detect_listing_changes",
    "fetch_capital_instruments",
    "fetch_okx_instruments",
    "merge_listing_timestamps",
    "parse_okx_tickers",
    "persist_universe",
    "refresh_capital_universe",
    "refresh_okx_universe",
]

# ---------------------------------------------------------------------------
# Endpoint constants (P0)
# ---------------------------------------------------------------------------

OKX_BASE_DEMO = "https://us.okx.com"
OKX_TICKERS_PATH = "/api/v5/market/tickers"

CAPITAL_BASE_DEMO = "https://demo-api-capital.backend-capital.com"
CAPITAL_SESSION_PATH = "/api/v1/session"
CAPITAL_NAV_PATH = "/api/v1/marketnavigation"

# ADR-003 P0 categories (forex / indices / commodity / crypto). Shares = P2.
# Match by name token (case-insensitive substring), not by nav-tree position.
# Capital demo nav node names include underscores ("crypto_currencies_group",
# "oil_markets_group", "commodities_group") so we keep the token list lower-case
# and substring-only.
CAPITAL_P0_CATEGORY_TOKENS: tuple[str, ...] = (
    "forex",
    "currenc",  # "Currencies" / "crypto_currencies_group"
    "indic",  # "Indices"
    "commod",  # "Commodities"
    "metal",
    "energ",
    "oil",  # "oil_markets_group"
    "crypto",
)

REST_TIMEOUT_SEC = 15.0


# ---------------------------------------------------------------------------
# OKX
# ---------------------------------------------------------------------------


async def fetch_okx_instruments(
    *,
    base_url: str = OKX_BASE_DEMO,
    now_ts: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[UniverseInstrument]:
    """Fetch OKX SPOT tickers via REST and convert to UniverseInstrument list.

    Sets `x-simulated-trading: 1` (demo). USDT-quote only at P0.
    """
    ts = now_ts if now_ts is not None else int(time.time())
    headers = {"x-simulated-trading": "1"}

    own_client = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        resp = await cli.get(OKX_TICKERS_PATH, params={"instType": "SPOT"}, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own_client:
            await cli.aclose()

    if str(body.get("code", "0")) != "0":
        raise RuntimeError(f"OKX tickers error: code={body.get('code')} msg={body.get('msg')}")

    rows = body.get("data", [])
    parsed = parse_okx_tickers(rows, now_ts=ts)
    logger.info(
        "[universe] OKX tickers fetched: raw=%d usdt_quote=%d",
        len(rows),
        len(parsed),
    )
    return parsed


def parse_okx_tickers(rows: list[dict[str, Any]], *, now_ts: int) -> list[UniverseInstrument]:
    """Convert OKX `/api/v5/market/tickers` rows → UniverseInstrument list (USDT-quote only).

    Pure function; lifted out so tests can feed canned payloads (no network).
    """
    out: list[UniverseInstrument] = []
    for row in rows:
        inst_id = str(row.get("instId", ""))
        if "-" not in inst_id:
            continue
        base, quote = inst_id.split("-", 1)
        if quote not in ALLOWED_QUOTE_CCY_OKX:
            continue

        last = _to_float(row.get("last"))
        bid = _to_float(row.get("bidPx"))
        ask = _to_float(row.get("askPx"))
        if last <= 0.0 or bid <= 0.0 or ask <= 0.0:
            continue

        mid = 0.5 * (bid + ask)
        spread_bps = ((ask - bid) / mid) * 10_000.0

        # Notional = volCcy24h (base-volume) × last; or volCcyQuote24h directly if present.
        vol_quote = _to_float(row.get("volCcyQuote24h"))
        if vol_quote <= 0.0:
            vol_quote = _to_float(row.get("volCcy24h")) * last

        high24 = _to_float(row.get("high24h"))
        low24 = _to_float(row.get("low24h"))
        atr_pct = ((high24 - low24) / last) * 100.0 if last > 0 else 0.0

        # Depth proxy: best-of-book volume × mid (top-of-book USD). Real L2 depth
        # arrives via WebSocket in P1; P0 keeps it conservative and additive.
        depth_top = (_to_float(row.get("bidSz")) + _to_float(row.get("askSz"))) * mid

        out.append(
            UniverseInstrument(
                venue="okx",
                symbol=inst_id,
                instrument_id=f"okx:{inst_id}",
                underlying_group_id=compute_underlying_group_id("okx", inst_id, "crypto"),
                asset_class="crypto",
                quote_ccy=quote,
                state="live",
                vol_24h_usd=vol_quote,
                spread_bps=spread_bps,
                atr_24h_pct=atr_pct,
                depth_10bps_usd=depth_top,
                signal_density_7d=0.0,
                listing_ts=None,
                last_seen_ts=now_ts,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Capital
# ---------------------------------------------------------------------------


async def fetch_capital_instruments(
    *,
    base_url: str = CAPITAL_BASE_DEMO,
    api_key: str | None = None,
    email: str | None = None,
    password: str | None = None,
    now_ts: int | None = None,
    client: httpx.AsyncClient | None = None,
    category_tokens: tuple[str, ...] = CAPITAL_P0_CATEGORY_TOKENS,
) -> list[UniverseInstrument]:
    """Fetch Capital CFD universe restricted to ADR-003 P0 categories.

    Top-level nav nodes are matched by **name token** (forex / currencies /
    indices / commodities / metals / energy / crypto). Shares = P2 by name, so
    they never enter the P0 pool. Markets seen across multiple nodes are
    deduplicated by ``epic``.
    """
    ts = now_ts if now_ts is not None else int(time.time())
    api_key = api_key or os.environ.get("CAP_API_KEY")
    email = email or os.environ.get("CAP_EMAIL")
    password = password or os.environ.get("CAP_PASSWORD")
    if not (api_key and email and password):
        # No credentials → skip Capital fetch (smoke-friendly).
        logger.info("[universe] Capital fetch skipped — credentials missing")
        return []

    own_client = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        # 1. Session create.
        sess_resp = await cli.post(
            CAPITAL_SESSION_PATH,
            headers={"X-CAP-API-KEY": api_key, "Content-Type": "application/json"},
            json={"identifier": email, "password": password},
        )
        sess_resp.raise_for_status()
        cst = sess_resp.headers.get("CST", "")
        sec = sess_resp.headers.get("X-SECURITY-TOKEN", "")
        if not cst or not sec:
            raise RuntimeError("Capital session: missing CST / X-SECURITY-TOKEN headers")
        auth_headers = {"CST": cst, "X-SECURITY-TOKEN": sec}

        # 2. Top-level nav, then filter to P0 categories by name token.
        nav_resp = await cli.get(CAPITAL_NAV_PATH, headers=auth_headers)
        nav_resp.raise_for_status()
        nodes = nav_resp.json().get("nodes", [])
        p0_nodes = [n for n in nodes if _capital_name_matches(n, category_tokens)]

        seen_epics: set[str] = set()
        out: list[UniverseInstrument] = []
        for node in p0_nodes:
            node_id = str(node.get("id", ""))
            node_name = str(node.get("name", ""))
            if not node_id:
                continue
            child_resp = await cli.get(f"{CAPITAL_NAV_PATH}/{node_id}", headers=auth_headers)
            if child_resp.status_code != 200:
                continue
            child_body = child_resp.json()
            for market in child_body.get("markets", []) or []:
                inst = _capital_market_row_to_instrument(
                    market, asset_class_hint=node_name, now_ts=ts
                )
                if inst is None or inst.symbol in seen_epics:
                    continue
                seen_epics.add(inst.symbol)
                out.append(inst)
            for sub in child_body.get("nodes", []) or []:
                sub_id = str(sub.get("id", ""))
                if not sub_id:
                    continue
                sub_resp = await cli.get(f"{CAPITAL_NAV_PATH}/{sub_id}", headers=auth_headers)
                if sub_resp.status_code != 200:
                    continue
                for market in sub_resp.json().get("markets", []) or []:
                    inst = _capital_market_row_to_instrument(
                        market, asset_class_hint=node_name, now_ts=ts
                    )
                    if inst is None or inst.symbol in seen_epics:
                        continue
                    seen_epics.add(inst.symbol)
                    out.append(inst)
        logger.info(
            "[universe] Capital fetched: nodes=%d p0_nodes=%d unique_epics=%d",
            len(nodes),
            len(p0_nodes),
            len(out),
        )
        return out
    finally:
        if own_client:
            await cli.aclose()


def _capital_name_matches(node: dict[str, Any], tokens: tuple[str, ...]) -> bool:
    """Case-insensitive match of node name against P0 category tokens."""
    name = str(node.get("name", "")).lower()
    return any(tok in name for tok in tokens)


def _capital_market_row_to_instrument(
    row: dict[str, Any],
    *,
    asset_class_hint: str,
    now_ts: int,
) -> UniverseInstrument | None:
    """Convert one Capital `markets` row → UniverseInstrument (or None if unusable)."""
    epic = str(row.get("epic", ""))
    if not epic:
        return None
    bid = _to_float(row.get("bid"))
    ask = _to_float(row.get("offer"))
    if bid <= 0.0 or ask <= 0.0:
        return None

    mid = 0.5 * (bid + ask)
    spread_bps = ((ask - bid) / mid) * 10_000.0
    high = _to_float(row.get("high"))
    low = _to_float(row.get("low"))
    atr_pct = ((high - low) / mid) * 100.0 if mid > 0 else 0.0

    asset_class = _classify_capital_node(asset_class_hint)
    market_status = str(row.get("marketStatus", "TRADEABLE")).upper()
    state = "live" if market_status == "TRADEABLE" else market_status.lower()

    return UniverseInstrument(
        venue="capital",
        symbol=epic,
        instrument_id=f"capital:{epic}",
        underlying_group_id=compute_underlying_group_id("capital", epic, asset_class),
        asset_class=asset_class,
        quote_ccy="USD",  # CFD pricing currency is venue-side; P0 placeholder
        state=state,
        vol_24h_usd=0.0,  # Capital does not expose 24h notional via nav; P1 = chart endpoint
        spread_bps=spread_bps,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=0.0,  # CFD is dealt; depth proxy via P1 quote stream
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=now_ts,
    )


def _classify_capital_node(hint: str) -> str:
    h = hint.lower()
    # Crypto must precede the generic "currenc" check ("crypto_currencies_group"
    # contains both tokens; without ordering FX would absorb every crypto CFD).
    if "crypto" in h:
        return "crypto"
    if "forex" in h or "fx" in h:
        return "forex"
    if "currenc" in h:
        return "forex"
    if "indic" in h:
        return "indices"
    if "commod" in h or "metal" in h or "energ" in h or "oil" in h:
        return "commodity"
    return "other"


# ---------------------------------------------------------------------------
# 4-axis hard filter
# ---------------------------------------------------------------------------


def apply_active_filters(
    instruments: list[UniverseInstrument],
    thresholds: FilterThresholds | None = None,
    *,
    capital_proxy_or_gate: bool = True,
) -> list[UniverseInstrument]:
    """Filter instruments through 4-axis hard gate (Q2 of L0 spec).

    Default contract: all four axes (vol / spread / atr / depth) MUST pass.

    ``capital_proxy_or_gate`` (Day 2 patch): for ``venue=='capital'`` rows the
    gate accepts (vol_24h_usd OR depth_10bps_usd) clearing its threshold,
    because Capital does not publish both natively — proxies populate them via
    ``polaris.venues.capital.market_proxy``. State / spread / ATR axes still
    apply uniformly. Set to ``False`` to fall back to strict ALL-axis hard gate
    (preserves the legacy contract used by L0 unit tests).
    """
    th = thresholds or default_thresholds()
    use_class_floor = thresholds is None  # custom thresholds → caller takes over
    rejected: dict[str, int] = {}
    out: list[UniverseInstrument] = []
    for ins in instruments:
        if ins.state != "live":
            rejected["state"] = rejected.get("state", 0) + 1
            continue
        if ins.spread_bps > th.max_spread_bps:
            rejected["spread"] = rejected.get("spread", 0) + 1
            continue
        # Asset-class ATR floor (Day 2 patch). The default ``th.min_atr_24h_pct``
        # is calibrated for crypto (2%); FX/indices/commodity have lower native
        # ATR ranges and would be wholly excluded by a single floor. Custom
        # ``thresholds`` argument disables the per-class fallback (caller chose
        # the policy explicitly).
        if use_class_floor:
            atr_floor = ATR_FLOOR_BY_CLASS.get(ins.asset_class, th.min_atr_24h_pct)
        else:
            atr_floor = th.min_atr_24h_pct
        if ins.atr_24h_pct < atr_floor:
            rejected["atr"] = rejected.get("atr", 0) + 1
            continue
        vol_pass = ins.vol_24h_usd >= th.min_vol_24h_usd
        depth_pass = ins.depth_10bps_usd >= th.min_depth_10bps_usd
        if capital_proxy_or_gate and ins.venue == "capital":
            if not (vol_pass or depth_pass):
                rejected["vol_or_depth"] = rejected.get("vol_or_depth", 0) + 1
                continue
        else:
            if not vol_pass:
                rejected["vol"] = rejected.get("vol", 0) + 1
                continue
            if not depth_pass:
                rejected["depth"] = rejected.get("depth", 0) + 1
                continue
        out.append(ins)
    logger.info(
        "[universe] 4-axis filter: %d → %d (rejected=%s)",
        len(instruments),
        len(out),
        rejected,
    )
    return out


# ---------------------------------------------------------------------------
# Listing-change delta (Q1 + Q6)
# ---------------------------------------------------------------------------


def detect_listing_changes(
    prev: list[UniverseInstrument],
    curr: list[UniverseInstrument],
    *,
    now_ts: int | None = None,
) -> tuple[list[UniverseInstrument], list[str]]:
    """Diff prev vs curr; return (new_listings, delistings).

    Identity = ``instrument_id``. Delisting = present in prev, absent in curr.

    New rows are returned with ``listing_ts`` populated to ``now_ts`` so the
    listing watchdog (Q6) has a real first-seen timestamp downstream. If the
    incoming row already carries a ``listing_ts``, it is preserved.
    """
    ts = now_ts if now_ts is not None else int(time.time())
    prev_ids = {ins.instrument_id for ins in prev}
    curr_ids = {ins.instrument_id for ins in curr}
    new_listings: list[UniverseInstrument] = []
    for ins in curr:
        if ins.instrument_id in prev_ids:
            continue
        if ins.listing_ts is None:
            ins = replace(ins, listing_ts=ts)
        new_listings.append(ins)
    delisted = sorted(prev_ids - curr_ids)
    return new_listings, delisted


def merge_listing_timestamps(
    prev: list[UniverseInstrument],
    curr: list[UniverseInstrument],
    *,
    now_ts: int | None = None,
) -> list[UniverseInstrument]:
    """Return ``curr`` with ``listing_ts`` carried over from ``prev`` and stamped on new rows.

    P0 wiring path: cycle N's universe is fed in as ``prev`` (already stamped),
    cycle N+1 raw fetch is ``curr``; this returns curr with timestamps merged
    so persist + bucket assignment downstream see real watchdog state.
    """
    ts = now_ts if now_ts is not None else int(time.time())
    prev_ts = {ins.instrument_id: ins.listing_ts for ins in prev}
    out: list[UniverseInstrument] = []
    for ins in curr:
        existing = prev_ts.get(ins.instrument_id)
        if existing is not None:
            out.append(replace(ins, listing_ts=existing))
        elif ins.listing_ts is None:
            out.append(replace(ins, listing_ts=ts))
        else:
            out.append(ins)
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_universe(
    conn: sqlite3.Connection,
    instruments: list[UniverseInstrument],
    *,
    is_active_set: set[str] | None = None,
    thresholds: FilterThresholds | None = None,
) -> None:
    """Upsert UniverseInstrument rows into `universe` table.

    ``is_active_set`` = set of `instrument_id` that survived 4-axis filter; rows
    not in the set are written with ``is_active=0`` and an ``active_reason``
    explaining which axis failed (vol / spread / atr / depth / state).
    """
    th = thresholds or default_thresholds()
    active_ids = is_active_set or {ins.instrument_id for ins in instruments}
    rows = []
    for ins in instruments:
        is_active = ins.instrument_id in active_ids
        reason = None if is_active else _filter_failure_reason(ins, th)
        rows.append(
            (
                ins.venue,
                ins.symbol,
                ins.instrument_id,
                ins.underlying_group_id,
                ins.asset_class,
                ins.quote_ccy,
                ins.state,
                ins.vol_24h_usd,
                ins.spread_bps,
                ins.atr_24h_pct,
                ins.depth_10bps_usd,
                ins.signal_density_7d,
                ins.listing_ts,
                ins.last_seen_ts,
                1 if is_active else 0,
                reason,
            )
        )
    conn.executemany(
        """
        INSERT INTO universe (
            venue, symbol, instrument_id, underlying_group_id,
            asset_class, quote_ccy, state,
            vol_24h_usd, spread_bps, atr_24h_pct, depth_10bps_usd,
            signal_density_7d, listing_ts, last_seen_ts,
            is_active, active_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(venue, symbol) DO UPDATE SET
            instrument_id=excluded.instrument_id,
            underlying_group_id=excluded.underlying_group_id,
            asset_class=excluded.asset_class,
            quote_ccy=excluded.quote_ccy,
            state=excluded.state,
            vol_24h_usd=excluded.vol_24h_usd,
            spread_bps=excluded.spread_bps,
            atr_24h_pct=excluded.atr_24h_pct,
            depth_10bps_usd=excluded.depth_10bps_usd,
            signal_density_7d=excluded.signal_density_7d,
            listing_ts=COALESCE(universe.listing_ts, excluded.listing_ts),
            last_seen_ts=excluded.last_seen_ts,
            is_active=excluded.is_active,
            active_reason=excluded.active_reason
        """,
        rows,
    )


# ---------------------------------------------------------------------------
# Spec API aliases (vault/30_components/layer-0-universe-discovery.md)
# ---------------------------------------------------------------------------


async def refresh_okx_universe(
    now_ts: int,
    *,
    base_url: str = OKX_BASE_DEMO,
    client: httpx.AsyncClient | None = None,
) -> list[UniverseInstrument]:
    """Spec-named alias for `fetch_okx_instruments` (Q1 of L0 spec)."""
    return await fetch_okx_instruments(base_url=base_url, now_ts=now_ts, client=client)


async def refresh_capital_universe(
    now_ts: int,
    *,
    base_url: str = CAPITAL_BASE_DEMO,
    client: httpx.AsyncClient | None = None,
) -> list[UniverseInstrument]:
    """Spec-named alias for `fetch_capital_instruments` (Q1 of L0 spec)."""
    return await fetch_capital_instruments(base_url=base_url, now_ts=now_ts, client=client)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter_failure_reason(ins: UniverseInstrument, th: FilterThresholds) -> str:
    """First-failing axis name for the 4-axis hard filter (used in `active_reason`)."""
    if ins.state != "live":
        return f"state={ins.state}"
    if ins.spread_bps > th.max_spread_bps:
        return f"spread_bps={ins.spread_bps:.1f}>{th.max_spread_bps}"
    if ins.atr_24h_pct < th.min_atr_24h_pct:
        return f"atr_pct={ins.atr_24h_pct:.2f}<{th.min_atr_24h_pct}"
    if ins.vol_24h_usd < th.min_vol_24h_usd:
        return f"vol_usd={ins.vol_24h_usd:.0f}<{th.min_vol_24h_usd:.0f}"
    if ins.depth_10bps_usd < th.min_depth_10bps_usd:
        return f"depth_usd={ins.depth_10bps_usd:.0f}<{th.min_depth_10bps_usd:.0f}"
    return "unknown"


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
