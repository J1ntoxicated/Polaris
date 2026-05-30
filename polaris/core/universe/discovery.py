"""Layer 0 — Dynamic Universe Discovery (OKX SPOT + Capital CFD).

P0 = REST poll; P1 = WebSocket stream.

Spec source: vault/30_components/layer-0-universe-discovery.md (Q1 + Q2 + Q5 + Q6).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import replace
from typing import Any

import httpx

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.streams import asset_class_allowed_for_venue
from polaris.core.universe._alpaca import (
    fetch_alpaca_instruments,
    refresh_alpaca_universe,
)
from polaris.core.universe._capital import (
    CAPITAL_BASE_DEMO,
    CAPITAL_NAV_PATH,
    CAPITAL_P0_CATEGORY_TOKENS,
    CAPITAL_SESSION_PATH,
    fetch_capital_instruments,
)
from polaris.core.universe._helpers import REST_TIMEOUT_SEC, _to_float
from polaris.core.universe._ranking import rank_active_universe
from polaris.core.universe.schema import (
    ALLOWED_QUOTE_CCY_OKX,
    ATR_FLOOR_BY_CLASS,
    FilterThresholds,
    UniverseInstrument,
    default_thresholds,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CAPITAL_BASE_DEMO",
    "CAPITAL_NAV_PATH",
    "CAPITAL_P0_CATEGORY_TOKENS",
    "CAPITAL_SESSION_PATH",
    "apply_active_filters",
    "detect_listing_changes",
    "fetch_alpaca_instruments",
    "fetch_capital_instruments",
    "fetch_okx_instruments",
    "merge_listing_timestamps",
    "parse_okx_tickers",
    "persist_universe",
    "rank_active_universe",
    "refresh_alpaca_universe",
    "refresh_capital_universe",
    "refresh_okx_universe",
]

# ---------------------------------------------------------------------------
# Endpoint constants (P0)
# ---------------------------------------------------------------------------

OKX_BASE_DEMO = "https://us.okx.com"
OKX_TICKERS_PATH = "/api/v5/market/tickers"


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

    ``is_active_set=None`` means "no selection ran" → every row is marked active
    (legacy seed/test path). An **empty** set is honored as "nothing active"
    (STEP 3: off-session Capital → all rows persist with ``is_active=0`` and a
    ``session_wait`` reason, reviving next refresh) — it is NOT treated as None.
    """
    th = thresholds or default_thresholds()
    active_ids = (
        {ins.instrument_id for ins in instruments}
        if is_active_set is None
        else is_active_set
    )
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
    _deactivate_off_venue_active_rows(conn, {ins.venue for ins in instruments})


def _deactivate_off_venue_active_rows(
    conn: sqlite3.Connection, venues: set[str]
) -> None:
    """Sweep each refreshed venue's stale whitelist-violating active rows.

    Live-audit gap (2026-05-30): STEP 2 filters off-venue asset_classes out of a
    *new* fetch, but a row persisted ``is_active=1`` by an earlier build (before
    fetch-time filtering) is absent from the current ``instruments`` list, so
    ``persist_universe``'s upsert never touches it and it lingers active —
    e.g. a Capital crypto-CFD focus candidate routed to the wrong stream.

    This is a **full-venue** sweep (independent of the fetched instrument set):
    any ``is_active=1`` row whose ``asset_class`` is NOT on its venue's stream
    whitelist (:func:`asset_class_allowed_for_venue`) is flipped to
    ``is_active=0, active_reason='off_venue_class'``. It is an asset-class
    routing correction (crypto edge belongs on OKX track A), NOT a defensive
    throttle and NOT a session gate.

    Invariants held by the SSOT whitelist:
    * OKX (crypto-only) / Alpaca (equity-only) carry no violation → no-op.
    * STEP 3 session-wait rows (off-session FX/index/commodity, ``is_active=0``,
      ``session_wait:*``) keep a *whitelisted* asset_class → never matched here
      (and already inactive), so their reason is preserved.
    """
    for venue in venues:
        active_classes = {
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT asset_class FROM universe "
                "WHERE venue = ? AND is_active = 1",
                (venue,),
            ).fetchall()
        }
        off_venue = [
            ac for ac in active_classes if not asset_class_allowed_for_venue(venue, ac)
        ]
        if not off_venue:
            continue
        placeholders = ",".join("?" for _ in off_venue)
        conn.execute(
            f"UPDATE universe SET is_active = 0, active_reason = 'off_venue_class' "
            f"WHERE venue = ? AND is_active = 1 AND asset_class IN ({placeholders})",
            (venue, *off_venue),
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
    """First-failing axis name for the 4-axis hard filter (used in `active_reason`).

    STEP 2 (asset-class routing): a row whose ``asset_class`` is off its venue's
    stream whitelist (e.g. a Capital crypto-CFD) is tagged ``off_venue_class:...``
    — it never enters the active set (crypto → OKX track A).

    STEP 3 (session asymmetry): a non-live CFD (Capital) row is a **session-wait**
    — it is persisted (``is_active=0``) and revives automatically the next refresh
    once the venue reports it TRADEABLE again, so the reason is tagged
    ``session_wait:<state>`` (not a permanent ``state=...`` reject). Crypto (OKX,
    24/7) and any genuinely halted/off venue keep the literal state reason.
    """
    if not asset_class_allowed_for_venue(ins.venue, ins.asset_class):
        return f"off_venue_class:{ins.asset_class}"
    if ins.state != "live":
        if ins.venue == "capital":
            return f"session_wait:{ins.state}"
        return f"state={ins.state}"
    if ins.spread_bps > th.max_spread_bps:
        return f"spread_bps={ins.spread_bps:.1f}>{th.max_spread_bps}"
    if ins.atr_24h_pct < th.min_atr_24h_pct:
        return f"atr_pct={ins.atr_24h_pct:.2f}<{th.min_atr_24h_pct}"
    if ins.vol_24h_usd < th.min_vol_24h_usd:
        return f"vol_usd={ins.vol_24h_usd:.0f}<{th.min_vol_24h_usd:.0f}"
    if ins.depth_10bps_usd < th.min_depth_10bps_usd:
        return f"depth_usd={ins.depth_10bps_usd:.0f}<{th.min_depth_10bps_usd:.0f}"
    # Valid + clears every soft axis but fell below the continuous-rank cut.
    return "below_rank"
