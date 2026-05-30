"""Layer 0 — Capital CFD universe discovery (REST nav-tree walk, P0 categories).

Split out of ``discovery.py`` to keep each module ≤500 LOC. ``discovery``
re-exports the public names (``fetch_capital_instruments``,
``_capital_name_matches``, ``CAPITAL_*`` constants) so existing import paths
keep working. Spec source: vault/30_components/layer-0-universe-discovery.md.
"""

from __future__ import annotations

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
)


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
