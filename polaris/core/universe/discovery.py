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
    liquidity_floor_for_venue,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CAPITAL_BASE_DEMO",
    "CAPITAL_NAV_PATH",
    "CAPITAL_P0_CATEGORY_TOKENS",
    "CAPITAL_SESSION_PATH",
    "apply_active_filters",
    "deactivate_stale_active_rows",
    "detect_listing_changes",
    "fetch_alpaca_instruments",
    "fetch_capital_instruments",
    "fetch_okx_instruments",
    "merge_listing_timestamps",
    "parse_okx_tickers",
    "persist_universe",
    "rank_active_universe",
    "refresh_alpaca_universe",
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

    Sets `x-simulated-trading: 1` (demo). STEP 2 scope-widen: admits USD-equivalent
    quotes (USDT/USDC/USD) + USD-normalizable crypto-quoted pairs (see
    ``parse_okx_tickers``), not USDT-only.
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
        "[universe] OKX tickers fetched: raw=%d admitted=%d",
        len(rows),
        len(parsed),
    )
    return parsed


def _build_okx_quote_usd_index(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Map each base currency → its USD price from the USD-equivalent tickers.

    A USD-equivalent-quoted pair (quote ∈ {USDT, USDC, USD}) gives its BASE's USD
    price directly via ``last`` (e.g. ``ETH-USDT.last = 3000`` → ETH ≈ $3000). The
    USD-equivalent quote currencies themselves map to 1.0. This index is what lets
    a crypto-quoted pair (e.g. ``BTC-ETH``, quote=ETH) convert its quote-denominated
    24h notional to USD (``× index["ETH"]``). Built once per payload (pure, no
    network). When several USD-equiv pairs exist for a base, the first wins (they
    agree to ~peg precision; the rank is robust to the small spread).
    """
    index: dict[str, float] = {q: 1.0 for q in ALLOWED_QUOTE_CCY_OKX}
    for row in rows:
        inst_id = str(row.get("instId", ""))
        if "-" not in inst_id:
            continue
        base, quote = inst_id.split("-", 1)
        if quote not in ALLOWED_QUOTE_CCY_OKX or base in index:
            continue
        last = _to_float(row.get("last"))
        if last > 0.0:
            index[base] = last
    return index


def parse_okx_tickers(rows: list[dict[str, Any]], *, now_ts: int) -> list[UniverseInstrument]:
    """Convert OKX `/api/v5/market/tickers` rows → UniverseInstrument list.

    Admits USD-equivalent-quoted pairs (USDT/USDC/USD) directly, plus crypto-quoted
    pairs (e.g. ``BTC-ETH``) whose quote currency has a USD reference in the same
    payload — those have their quote-denominated vol/depth NORMALIZED to USD via
    ``_build_okx_quote_usd_index`` so the vol-dominant active rank compares
    like-for-like (a wrong normalization would be a price error; pinned by tests).
    A pair whose quote has NO USD reference is excluded (un-normalizable → not a
    rankable/priceable watch candidate, NOT a defensive block). flow_not_block:
    every name with a derivable USD datum flows. Pure function (no network).
    """
    quote_usd = _build_okx_quote_usd_index(rows)
    out: list[UniverseInstrument] = []
    for row in rows:
        inst_id = str(row.get("instId", ""))
        if "-" not in inst_id:
            continue
        base, quote = inst_id.split("-", 1)
        # Admit USD-equivalent quotes, OR a crypto quote that has a USD reference.
        quote_usd_price = quote_usd.get(quote, 0.0)
        if quote not in ALLOWED_QUOTE_CCY_OKX and quote_usd_price <= 0.0:
            continue

        last = _to_float(row.get("last"))
        bid = _to_float(row.get("bidPx"))
        ask = _to_float(row.get("askPx"))
        if last <= 0.0 or bid <= 0.0 or ask <= 0.0:
            continue

        mid = 0.5 * (bid + ask)
        spread_bps = ((ask - bid) / mid) * 10_000.0

        # 24h notional in the QUOTE currency (volCcyQuote24h, or volCcy24h × last).
        vol_quote = _to_float(row.get("volCcyQuote24h"))
        if vol_quote <= 0.0:
            vol_quote = _to_float(row.get("volCcy24h")) * last
        # Normalize quote-denominated notional/depth to USD. For USD-equiv quotes
        # the factor is 1.0 (no-op); for a crypto quote it is the quote's USD price
        # so an ETH-quoted vol becomes USD (the like-for-like ranking input).
        vol_usd = vol_quote * quote_usd_price

        high24 = _to_float(row.get("high24h"))
        low24 = _to_float(row.get("low24h"))
        atr_pct = ((high24 - low24) / last) * 100.0 if last > 0 else 0.0

        # Depth proxy: best-of-book volume × mid, then × quote→USD (top-of-book USD).
        # Real L2 depth arrives via WebSocket in P1; P0 keeps it conservative.
        depth_top = (
            (_to_float(row.get("bidSz")) + _to_float(row.get("askSz")))
            * mid
            * quote_usd_price
        )

        out.append(
            UniverseInstrument(
                venue="okx",
                symbol=inst_id,
                instrument_id=f"okx:{inst_id}",
                underlying_group_id=compute_underlying_group_id("okx", inst_id, "crypto"),
                asset_class="crypto",
                quote_ccy=quote,
                state="live",
                vol_24h_usd=vol_usd,
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

    ``is_active_set`` = set of `instrument_id` that survived the active-set
    selection (``rank_active_universe``); rows not in the set are written with
    ``is_active=0`` and an ``active_reason`` that MIRRORS THE REAL SELECTION PATH
    (``_active_exclusion_reason``): ``off_venue_class:*`` (stream-whitelist drop),
    ``session_wait:*`` / ``state=*`` (validity), ``quote_ccy=*`` (OKX quote),
    ``liqfloor:<axis>`` (eligibility liquidity floor), or ``below_rank_topN``
    (valid + cleared the floor but fell below the continuous-rank cut) — NOT the
    legacy hard 4-axis labels, which no longer govern selection.

    ``is_active_set=None`` means "no selection ran" → every row is marked active
    (legacy seed/test path). An **empty** set is honored as "nothing active"
    (STEP 3: off-session Capital → all rows persist with ``is_active=0`` and a
    ``session_wait`` reason, reviving next refresh) — it is NOT treated as None.
    """
    # ``thresholds`` is retained for the public spec-API signature but no longer
    # read here: ``active_reason`` now mirrors the real rank/floor path
    # (``_active_exclusion_reason``), which keys on the per-venue liquidity floor,
    # not the legacy 4-axis hard thresholds.
    _ = thresholds
    active_ids = (
        {ins.instrument_id for ins in instruments}
        if is_active_set is None
        else is_active_set
    )
    # Observability (log only): snapshot the PRIOR active instrument_ids for the
    # venues this refresh touches so we can report the 0↔1 transition counts
    # below. Pure read — never gates the upsert or the active selection.
    refreshed_venues = {ins.venue for ins in instruments}
    prior_active_ids = _read_active_instrument_ids(conn, refreshed_venues)
    rows = []
    for ins in instruments:
        is_active = ins.instrument_id in active_ids
        reason = None if is_active else _active_exclusion_reason(ins)
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
                ins.last_price,
                ins.name,
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
            signal_density_7d, last_price, name, listing_ts, last_seen_ts,
            is_active, active_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            last_price=excluded.last_price,
            name=COALESCE(NULLIF(excluded.name, ''), universe.name),
            listing_ts=COALESCE(universe.listing_ts, excluded.listing_ts),
            last_seen_ts=excluded.last_seen_ts,
            is_active=excluded.is_active,
            active_reason=excluded.active_reason
        """,
        rows,
    )
    off_venue_deactivated = _deactivate_off_venue_active_rows(conn, refreshed_venues)
    # Universe active 0↔1 transition (INFO): the per-refresh delta of which
    # instruments newly entered / left the active watchlist, plus how many stale
    # off-venue (whitelist-violating) active rows this refresh swept. Computed
    # from the post-upsert active set vs the prior snapshot — log only, no gate.
    new_active_ids = _read_active_instrument_ids(conn, refreshed_venues)
    activated = new_active_ids - prior_active_ids
    deactivated = prior_active_ids - new_active_ids
    if activated or deactivated or off_venue_deactivated:
        logger.info(
            "[L0/universe] persist venues=%s active=%d activated=%d "
            "deactivated=%d off_venue_swept=%d",
            ",".join(sorted(refreshed_venues)) or "-",
            len(new_active_ids), len(activated), len(deactivated),
            off_venue_deactivated,
        )


def deactivate_stale_active_rows(
    conn: sqlite3.Connection,
    *,
    venue: str,
    fetched_ids: set[str],
) -> int:
    """Flip a venue's ``is_active=1`` rows that are ABSENT from this fetch to inactive.

    Live-audit gap (2026-06-23): ``persist_universe``'s upsert only touches the
    rows present in the current fetch's ``instruments`` list. Alpaca's ~13k
    ``/v2/assets`` set churns, so a name that was ``is_active=1`` in a prior cycle
    but drops OUT of the current fetch is never re-evaluated and lingers active
    forever — verified ABVE ($54k vol) sat ``is_active=1`` for 21 days, below the
    5M Alpaca $vol floor, leading equity focus. OKX/Capital fetch a small stable
    universe so the ghost does not manifest there; this is an Alpaca-shaped hygiene
    correction, NOT a defensive throttle: a name that LEFT the venue is no longer
    eligible (universe membership), not "too risky".

    A name still in ``fetched_ids`` is NEVER swept here — ``persist_universe``
    already assigned it the correct ``is_active`` (the selection's verdict stands,
    flow_not_block). Swept rows are tagged ``active_reason='stale_unselected'``.

    Guard: an EMPTY ``fetched_ids`` means "no fetch happened this cycle"
    (credentials missing / API down) — it is a NO-OP (never zero the book on a
    non-event). The production caller only invokes this after a non-empty fetch.

    Returns the number of rows flipped (observability for the refresh INFO line).
    """
    if not fetched_ids:
        return 0
    active_ids = {
        str(r[0])
        for r in conn.execute(
            "SELECT instrument_id FROM universe WHERE venue = ? AND is_active = 1",
            (venue,),
        ).fetchall()
    }
    stale = active_ids - fetched_ids
    if not stale:
        return 0
    swept = 0
    # Chunk the IN-list (SQLite caps bound parameters ~999); Alpaca stale sets are
    # tiny in practice but stay safe.
    stale_list = sorted(stale)
    for start in range(0, len(stale_list), 500):
        chunk = stale_list[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        cur = conn.execute(
            f"UPDATE universe SET is_active = 0, active_reason = 'stale_unselected' "
            f"WHERE venue = ? AND is_active = 1 AND instrument_id IN ({placeholders})",
            (venue, *chunk),
        )
        swept += max(0, cur.rowcount)
    return swept


def _read_active_instrument_ids(
    conn: sqlite3.Connection, venues: set[str]
) -> set[str]:
    """Active (``is_active=1``) instrument_ids for ``venues`` (observability read).

    Used by :func:`persist_universe` to compute the 0↔1 transition delta around
    the upsert. Pure read; an empty ``venues`` or any sqlite error degrades to an
    empty set (no log line) — never affects the active selection or the upsert.
    """
    if not venues:
        return set()
    placeholders = ",".join("?" for _ in venues)
    try:
        return {
            str(r[0])
            for r in conn.execute(
                f"SELECT instrument_id FROM universe "
                f"WHERE is_active = 1 AND venue IN ({placeholders})",
                tuple(venues),
            ).fetchall()
        }
    except sqlite3.Error:
        return set()


def _deactivate_off_venue_active_rows(
    conn: sqlite3.Connection, venues: set[str]
) -> int:
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

    Returns the total number of rows flipped to ``is_active=0`` across all
    venues (observability for ``persist_universe``'s INFO line). The sweep logic
    is unchanged — only the rowcount is now surfaced.
    """
    swept = 0
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
        cur = conn.execute(
            f"UPDATE universe SET is_active = 0, active_reason = 'off_venue_class' "
            f"WHERE venue = ? AND is_active = 1 AND asset_class IN ({placeholders})",
            (venue, *off_venue),
        )
        swept += max(0, cur.rowcount)
    return swept


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_exclusion_reason(ins: UniverseInstrument) -> str:
    """Why ``ins`` is NOT in the active set, mirroring ``rank_active_universe``.

    Observability fix (B3 / audit 2026-06-23): selection is now
    ``rank_active_universe`` (stream whitelist → validity + liquidity floor →
    continuous-rank top-N), but ``active_reason`` used to be written by the LEGACY
    hard 4-axis ``_filter_failure_reason`` (atr<2.0 / vol<30M …) which no longer
    governs anything — so live G1 forensics reported an axis that does not decide
    membership (verified ``below_rank`` = 0 rows, ``atr_pct=1.50<2.0`` = 13,149
    placeholder rows). This walks the ACTUAL exclusion path in order:

    1. ``off_venue_class:<class>`` — dropped by the stream asset-class whitelist
       (``apply_stream_asset_class_filter``; crypto-CFD → OKX track A).
    2. validity (``_is_valid_candidate``): non-live state →
       ``session_wait:<state>`` for Capital (revives next refresh) else
       ``state=<state>``; OKX non-USDT quote → ``quote_ccy=<q>``.
    3. ``below_rank_topN`` — basic-valid but fell below the continuous-rank
       WATCH-cut (the ONLY honest active-exclusion "ranking" reason).

    WATCH/TRADE DECOUPLE (Jin 2026-06-24): the liquidity floor NO LONGER decides
    active membership — it moved to the curator TRADE gate, so a sub-floor name is
    now ACTIVE/WATCHED (not excluded). The ``liqfloor:<axis>`` label is therefore
    NOT an active-exclusion reason here; it is a TRADE-eligibility annotation
    exposed separately via :func:`liqfloor_trade_annotation`. A basic-valid row
    that fell below the WATCH-cut reports ``below_rank_topN``.

    Pure observability: zero behavior change (the active SELECTION is unchanged —
    ``is_active`` is decided upstream by ``is_active_set``). flow_not_block — this
    only LABELS why a non-selected row is out; it never blocks/cuts/vetoes.
    """
    if not asset_class_allowed_for_venue(ins.venue, ins.asset_class):
        return f"off_venue_class:{ins.asset_class}"
    if ins.state != "live":
        if ins.venue == "capital":
            return f"session_wait:{ins.state}"
        return f"state={ins.state}"
    # NOTE: no OKX quote_ccy exclusion here. STEP 2 scope-widen made
    # ``parse_okx_tickers`` the admission SSOT — it admits USD-equivalent quotes
    # AND crypto-quoted pairs that normalize to USD, so any OKX row that reached
    # the universe is validly quoted. A crypto-quoted name (quote=ETH) is no
    # longer an exclusion; if not selected it fell below the rank cut, below.
    # Basic-valid but below the continuous-rank WATCH-cut. (The liquidity floor is
    # NO LONGER an active-exclusion — it is a trade-eligibility annotation now.)
    return "below_rank_topN"


def liqfloor_trade_annotation(ins: UniverseInstrument) -> str | None:
    """Trade-eligibility annotation: ``liqfloor:<axis>`` iff ``ins`` is sub-floor.

    WATCH/TRADE DECOUPLE (Jin 2026-06-24): a sub-floor name is now WATCHED but its
    curator ``trade_eligible`` is forced FALSE (slippage protection). This labels
    WHICH floor axis a watched row trips for the TRADE gate — NOT an active-set
    exclusion (the row IS active/watched). Returns None when the row clears the
    floor (trade-eligible on the liquidity axis). Pure observability.
    """
    axis = _liqfloor_failed_axis(ins)
    return None if axis is None else f"liqfloor:{axis}"


def _liqfloor_failed_axis(ins: UniverseInstrument) -> str | None:
    """First eligibility-liquidity-floor axis ``ins`` fails, or None if it passes.

    Mirrors ``schema.passes_liquidity_floor`` axis-for-axis (same known-bad-only
    contract: a missing datum — 0.0 vol/depth/price — is never a failure; only
    spread applies on any value). Returns the axis name so ``active_reason`` can
    say WHICH floor a row tripped (spread/vol/depth/price), never a bare pass/fail.
    """
    floor = liquidity_floor_for_venue(ins.venue)
    if floor.max_spread_bps > 0.0 and ins.spread_bps > floor.max_spread_bps:
        return "spread"
    if (
        floor.min_vol_24h_usd > 0.0
        and ins.vol_24h_usd > 0.0
        and ins.vol_24h_usd < floor.min_vol_24h_usd
    ):
        return "vol"
    if (
        floor.min_depth_10bps_usd > 0.0
        and ins.depth_10bps_usd > 0.0
        and ins.depth_10bps_usd < floor.min_depth_10bps_usd
    ):
        return "depth"
    if (
        floor.min_price > 0.0
        and ins.last_price > 0.0
        and ins.last_price < floor.min_price
    ):
        return "price"
    return None


