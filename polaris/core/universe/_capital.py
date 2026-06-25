"""Layer 0 — Capital CFD universe discovery (REST nav-tree walk, P0 categories).

Split out of ``discovery.py`` to keep each module ≤500 LOC. ``discovery``
re-exports the public names (``fetch_capital_instruments``,
``_capital_name_matches``, ``CAPITAL_*`` constants) so existing import paths
keep working. Spec source: vault/30_components/layer-0-universe-discovery.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.universe._helpers import REST_TIMEOUT_SEC, _to_float
from polaris.core.universe.schema import UniverseInstrument

logger = logging.getLogger(__name__)

CAPITAL_BASE_DEMO = "https://demo-api-capital.backend-capital.com"
CAPITAL_SESSION_PATH = "/api/v1/session"
CAPITAL_NAV_PATH = "/api/v1/marketnavigation"

# ADR-003 P0 categories restricted to the Capital B-stream whitelist
# (forex / indices / commodity). Crypto is OWNED by OKX track A (Jin 2026-05-30
# STEP 0 (a) — intended asset-class routing, not a throttle), so the "crypto"
# token is removed here as an efficiency cut: the crypto_currencies_group walk
# (~290 CFDs) is no longer fetched. ``currenc`` is RETAINED for the FX
# "Currencies" node, but ``_classify_capital_node`` still tags crypto first, and
# any crypto row that slips through a shared node is dropped downstream by
# ``apply_stream_asset_class_filter`` (the SSOT enforcement in _ranking.py).
# Match by name token (case-insensitive substring), not by nav-tree position.
CAPITAL_P0_CATEGORY_TOKENS: tuple[str, ...] = (
    "forex",
    "currenc",  # "Currencies" (FX). crypto_currencies node skipped at walk time.
    "indic",  # "Indices"
    "commod",  # "Commodities"
    "metal",
    "energ",
    "oil",  # "oil_markets_group"
    # STEP 2 scope-widen (Jin 2026-06-24 "다 열어야지"): descend into the broader
    # Capital catalog. ``share`` opens the company-share CFD tree (~2086 epics →
    # classified ``equity``); ``etf`` / ``bond`` / ``rate`` open the remaining
    # categories. These are FETCHED + persisted (surfaced). Whether an ``equity``-
    # classed row enters the ACTIVE/TRADE set is decided downstream by the Capital
    # stream asset-class whitelist (today {forex,index,commodity}) — a separate
    # stream-config decision, NOT this walk's concern (flow_not_block: widen admit).
    "share",
    "etf",
    "bond",
    "rate",
)

# STEP 2 coverage protection (Jin 2026-06-24): the ~2086-share walk is ~10× the
# request volume of the forex/index/commodity walk. A small inter-request throttle
# (env ``POLARIS_CAPITAL_WALK_THROTTLE_SEC``, default 0.05s) paces the nav GETs so
# the wider walk does not hammer the demo API. A transient non-200 (429/5xx) is
# RETRIED with exponential backoff (``_CAPITAL_NAV_RETRIES`` attempts) instead of
# silently dropping the whole sub-tree (the prior coverage-loss bug). Both protect
# UNIVERSE COVERAGE only — never an order/trading path.
CAPITAL_WALK_THROTTLE_ENV = "POLARIS_CAPITAL_WALK_THROTTLE_SEC"
_CAPITAL_WALK_THROTTLE_DEFAULT = 0.05
_CAPITAL_NAV_RETRIES = 3
_CAPITAL_NAV_BACKOFF_BASE_SEC = 0.5


def _capital_walk_throttle_sec() -> float:
    """Inter-request nav-walk throttle (env-overridable, >= 0). Invalid → default."""
    raw = os.environ.get(CAPITAL_WALK_THROTTLE_ENV)
    if raw is None or raw == "":
        return _CAPITAL_WALK_THROTTLE_DEFAULT
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _CAPITAL_WALK_THROTTLE_DEFAULT


async def _capital_nav_get(
    cli: httpx.AsyncClient, path: str, auth_headers: dict[str, str]
) -> httpx.Response | None:
    """GET a nav node with retry-backoff on a transient non-200 (429/5xx).

    Returns the 200 response, or None when every attempt failed (the caller skips
    that sub-tree — best-effort, never raises, never halts). A 4xx other than 429
    is treated as permanent (no retry — the node genuinely does not exist). This
    protects COVERAGE only; it is not a trading/order path.
    """
    backoff = _CAPITAL_NAV_BACKOFF_BASE_SEC
    for attempt in range(_CAPITAL_NAV_RETRIES):
        try:
            resp = await cli.get(path, headers=auth_headers)
        except httpx.HTTPError:
            resp = None
        if resp is not None and resp.status_code == 200:
            return resp
        transient = resp is None or resp.status_code == 429 or resp.status_code >= 500
        if not transient or attempt == _CAPITAL_NAV_RETRIES - 1:
            return resp if (resp is not None and resp.status_code == 200) else None
        await asyncio.sleep(backoff)
        backoff *= 2.0
    return None

# C2b: the nav tree nests real commodities 3 levels deep
# (commodities_group → commodities → {precious_metals, energies, base_metals} →
# markets), so the prior fixed 2-level walk fetched ZERO Gold/Silver/Oil/NatGas
# CFDs (forensic). Descend recursively until markets are found; the depth cap
# bounds the request count and guards against a pathological cycle. Forex/indices
# (markets at depth 2) are reached identically — pure coverage increase.
CAPITAL_NAV_MAX_DEPTH = 4


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
        # Token match selects P0 categories; the crypto skip (Jin 2026-05-30
        # STEP 0 (a)) drops the crypto_currencies node that "currenc" would
        # otherwise re-admit — crypto is OKX track A only. Pure efficiency: the
        # SSOT enforcement (apply_stream_asset_class_filter) is still the hard
        # guarantee downstream.
        p0_nodes = [
            n
            for n in nodes
            if _capital_name_matches(n, category_tokens)
            and _classify_capital_node(str(n.get("name", ""))) != "crypto"
        ]

        seen_epics: set[str] = set()
        seen_nodes: set[str] = set()
        out: list[UniverseInstrument] = []
        for node in p0_nodes:
            node_id = str(node.get("id", ""))
            node_name = str(node.get("name", ""))
            if not node_id:
                continue
            await _walk_capital_node(
                cli,
                node_id=node_id,
                asset_class_hint=node_name,
                auth_headers=auth_headers,
                now_ts=ts,
                out=out,
                seen_epics=seen_epics,
                seen_nodes=seen_nodes,
                depth=0,
            )
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


async def _walk_capital_node(
    cli: httpx.AsyncClient,
    *,
    node_id: str,
    asset_class_hint: str,
    auth_headers: dict[str, str],
    now_ts: int,
    out: list[UniverseInstrument],
    seen_epics: set[str],
    seen_nodes: set[str],
    depth: int,
) -> None:
    """Recursively collect a nav node's markets, then descend into its sub-nodes.

    Bounded by ``CAPITAL_NAV_MAX_DEPTH`` (C2b: real commodities sit 3 levels deep;
    the prior fixed 2-level walk missed them). ``asset_class_hint`` stays the
    top-level P0 node name (unchanged from the prior walk — it is only a fallback
    now that the market row's ``instrumentType`` is authoritative, C2a). Dedupes
    by epic (markets seen across nodes) and by node id (cycle guard). A non-200
    response is skipped (best-effort, mirrors the prior walk).
    """
    if depth >= CAPITAL_NAV_MAX_DEPTH or node_id in seen_nodes:
        return
    seen_nodes.add(node_id)
    # Throttle the nav GETs (the wider STEP 2 walk is ~10× requests) + retry a
    # transient non-200 instead of silently dropping the sub-tree (coverage fix).
    throttle = _capital_walk_throttle_sec()
    if throttle > 0.0:
        await asyncio.sleep(throttle)
    resp = await _capital_nav_get(cli, f"{CAPITAL_NAV_PATH}/{node_id}", auth_headers)
    if resp is None:
        return  # exhausted retries — skip this sub-tree (best-effort, never halts)
    body = resp.json()
    for market in body.get("markets", []) or []:
        inst = _capital_market_row_to_instrument(
            market, asset_class_hint=asset_class_hint, now_ts=now_ts
        )
        if inst is None or inst.symbol in seen_epics:
            continue
        seen_epics.add(inst.symbol)
        out.append(inst)
    for sub in body.get("nodes", []) or []:
        sub_id = str(sub.get("id", ""))
        if not sub_id:
            continue
        await _walk_capital_node(
            cli,
            node_id=sub_id,
            asset_class_hint=asset_class_hint,
            auth_headers=auth_headers,
            now_ts=now_ts,
            out=out,
            seen_epics=seen_epics,
            seen_nodes=seen_nodes,
            depth=depth + 1,
        )


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

    # C2a: the market row's own ``instrumentType`` is authoritative; the node-name
    # hint mis-tags oil/energy COMPANY share CFDs (CVX/XOM/COP/SLB...) as
    # 'commodity' because their nav node carries an energ/oil token. SHARES →
    # 'equity' (off the Capital forex/index/commodity whitelist → routed out, so
    # the commodity bucket is not polluted with stocks); only a real COMMODITIES
    # CFD keeps 'commodity'. Falls back to the node hint when the type is absent.
    type_class = _classify_capital_instrument_type(str(row.get("instrumentType", "")))
    asset_class = type_class if type_class is not None else _classify_capital_node(asset_class_hint)
    market_status = str(row.get("marketStatus", "TRADEABLE")).upper()
    state = "live" if market_status == "TRADEABLE" else market_status.lower()
    # Human-readable name (display only): Capital market rows carry
    # ``instrumentName`` ("Gold", "EUR/USD"). "" when absent → UI falls back to epic.
    name = str(row.get("instrumentName", "")).strip()

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
        name=name,
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


# Capital ``markets[].instrumentType`` → polaris asset_class. SHARES are company
# equity (off the Capital forex/index/commodity whitelist → routed out of the
# Capital active set), so an oil/energy company under an energ/oil-named node is
# no longer mis-bucketed as 'commodity'. Mirrors the venue instrumentType space
# in venues/capital/constraint_translator.py.
_CAPITAL_INSTRUMENT_TYPE_TO_CLASS: dict[str, str] = {
    "SHARES": "equity",
    "COMMODITIES": "commodity",
    "CURRENCIES": "forex",
    "INDICES": "indices",
    "CRYPTOCURRENCIES": "crypto",
}


def _classify_capital_instrument_type(instrument_type: str) -> str | None:
    """Map the market row's ``instrumentType`` to an asset_class, or None if absent/unknown.

    None signals "fall back to the node-name hint" — the type is the authoritative
    market-level signal when present (C2a re-tag), but the nav-tree walk does not
    always carry it, so the node hint remains the default path.
    """
    return _CAPITAL_INSTRUMENT_TYPE_TO_CLASS.get(instrument_type.strip().upper())
