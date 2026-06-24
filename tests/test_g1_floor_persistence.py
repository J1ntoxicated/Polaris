"""G1 floor-persistence (B2) — last_price persistence + Alpaca stale-active sweep.

DEMO/PAPER virtual capital, AGGRESSIVE bias preserved. Every change here is
flow_not_block: the liquidity floor is an eligibility-MEMBERSHIP boundary, the
``last_price`` plumbing only restores the existing min_price floor on the DB-read
path, and the stale-active sweep is a universe-hygiene correction (a name that
left the venue fetch is no longer eligible) — none of it blocks an entry, cuts a
size, vetoes a signal, or introduces a ≤1 multiplier stack (no 9-stack). GPT=0.

Audit source: vault/50_research/g1_universe_gate_audit_2026-06-23.md (B2 cluster).
Live-DB verified: ABVE ($54k vol, last_seen 2026-06-02) sat is_active=1 for 21
days and SNBR ($47k) for hours — both BELOW the 5M Alpaca $vol floor — because
``persist_universe`` only UPDATEs rows present in THIS fetch, so a name that drops
out of the ~13k Alpaca ``/v2/assets`` list is never flipped to is_active=0. And
``last_price`` had no DB column, so ``read_active_universe`` left it 0.0 and the
min_price floor was inert on the path ``compute_dynamic_focus`` actually reads.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3

import httpx

from polaris.core.universe._alpaca import ALPACA_PAPER_BASE
from polaris.core.universe.discovery import (
    deactivate_stale_active_rows,
    persist_universe,
)
from polaris.core.universe.schema import UniverseInstrument
from polaris.scripts._production_layers import (
    read_active_universe,
    refresh_alpaca_universe_once,
)
from polaris.storage.schema import ALL_DDL, _apply_post_migrations

NOW = 1_780_000_000


def _inst(
    symbol: str,
    *,
    venue: str = "alpaca",
    asset_class: str = "equity",
    quote_ccy: str = "USD",
    vol: float = 5e7,
    spread_bps: float = 2.0,
    atr_pct: float = 1.5,
    depth: float = 0.0,
    last_price: float = 100.0,
    listing_ts: int | None = None,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy=quote_ccy,
        state="live",
        vol_24h_usd=vol,
        spread_bps=spread_bps,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=depth,
        listing_ts=listing_ts,
        last_seen_ts=NOW,
        last_price=last_price,
    )


def _memdb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for ddl in ALL_DDL:
        conn.execute(ddl)
    return conn


# ---------------------------------------------------------------------------
# B2a — last_price persisted through persist_universe → read_active_universe
# ---------------------------------------------------------------------------


def test_last_price_persists_round_trip() -> None:
    """A persisted active row's last_price survives the DB round-trip.

    The min_price eligibility floor reads UniverseInstrument.last_price; without
    a DB column read_active_universe left it 0.0, so the floor was inert on the
    exact (DB-read) path compute_dynamic_focus consumes."""
    conn = _memdb()
    aapl = _inst("AAPL", last_price=190.0)
    persist_universe(conn, [aapl], is_active_set={"alpaca:AAPL"})
    out = {ins.symbol: ins for ins in read_active_universe(conn)}
    assert out["AAPL"].last_price == 190.0


def test_last_price_fresh_ddl_has_column() -> None:
    """The fresh-DB DDL carries the last_price column (not only the migration)."""
    conn = _memdb()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(universe)").fetchall()}
    assert "last_price" in cols


def test_last_price_migration_idempotent_on_legacy_db() -> None:
    """A legacy universe table (no last_price) gains it via _apply_post_migrations,
    and re-running the migration is a no-op (idempotent ALTER guard).

    Build a full, migrated DB first (so the positions/etc. migration steps have
    their real tables), then simulate a LEGACY universe by recreating only that
    table WITHOUT last_price and asserting the migration re-adds it."""
    conn = sqlite3.connect(":memory:")
    for ddl in ALL_DDL:
        conn.execute(ddl)
    _apply_post_migrations(conn)
    # Simulate a pre-B2 universe table (drop last_price by recreating the table).
    conn.execute("DROP TABLE universe")
    conn.execute(
        """
        CREATE TABLE universe (
            venue TEXT NOT NULL, symbol TEXT NOT NULL, instrument_id TEXT NOT NULL,
            underlying_group_id TEXT NOT NULL, asset_class TEXT NOT NULL,
            product_class TEXT NOT NULL DEFAULT '', stream_id TEXT NOT NULL DEFAULT '',
            quote_ccy TEXT NOT NULL, state TEXT NOT NULL,
            vol_24h_usd REAL NOT NULL DEFAULT 0.0, spread_bps REAL NOT NULL DEFAULT 0.0,
            atr_24h_pct REAL NOT NULL DEFAULT 0.0, depth_10bps_usd REAL NOT NULL DEFAULT 0.0,
            signal_density_7d REAL NOT NULL DEFAULT 0.0, listing_ts INTEGER,
            last_seen_ts INTEGER NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
            active_reason TEXT, PRIMARY KEY (venue, symbol)
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(universe)").fetchall()}
    assert "last_price" not in cols  # legacy shape
    _apply_post_migrations(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(universe)").fetchall()}
    assert "last_price" in cols
    _apply_post_migrations(conn)  # second pass must not raise


def test_last_price_default_zero_for_venues_that_do_not_plumb_it() -> None:
    """OKX/Capital rows (no last_price) round-trip as 0.0 → min_price axis skipped
    (flow_not_block: never drop on a missing datum)."""
    conn = _memdb()
    btc = _inst("BTC-USDT", venue="okx", asset_class="crypto", quote_ccy="USDT",
                last_price=0.0)
    persist_universe(conn, [btc], is_active_set={"okx:BTC-USDT"})
    out = {ins.symbol: ins for ins in read_active_universe(conn)}
    assert out["BTC-USDT"].last_price == 0.0


# ---------------------------------------------------------------------------
# B2b — Alpaca stale-active deactivate sweep (the 21-day ghost)
# ---------------------------------------------------------------------------


def test_stale_active_row_absent_from_fetch_is_deactivated() -> None:
    """A name persisted is_active=1 in a prior cycle that is NOT in the current
    fetch's instrument set is flipped is_active=0 (the ABVE 21-day ghost)."""
    conn = _memdb()
    # Cycle N: AAPL + ABVE both fetched and active.
    persist_universe(
        conn, [_inst("AAPL"), _inst("ABVE", vol=5.4e4, last_price=4.0)],
        is_active_set={"alpaca:AAPL", "alpaca:ABVE"},
    )
    assert {i.symbol for i in read_active_universe(conn)} == {"AAPL", "ABVE"}
    # Cycle N+1: only AAPL fetched (ABVE dropped out of /v2/assets).
    fetched = [_inst("AAPL")]
    persist_universe(conn, fetched, is_active_set={"alpaca:AAPL"})
    swept = deactivate_stale_active_rows(
        conn, venue="alpaca", fetched_ids={ins.instrument_id for ins in fetched}
    )
    assert swept == 1
    active = {i.symbol for i in read_active_universe(conn)}
    assert "ABVE" not in active and "AAPL" in active


def test_stale_sweep_reason_is_stale_unselected() -> None:
    """The swept row carries an honest audit reason (not a legacy 4-axis label)."""
    conn = _memdb()
    persist_universe(
        conn, [_inst("SNBR", vol=4.7e4, last_price=20.0), _inst("AAPL")],
        is_active_set={"alpaca:SNBR", "alpaca:AAPL"},
    )
    # Next cycle: only AAPL fetched; SNBR dropped out → swept stale.
    deactivate_stale_active_rows(conn, venue="alpaca", fetched_ids={"alpaca:AAPL"})
    reason = conn.execute(
        "SELECT active_reason FROM universe WHERE venue='alpaca' AND symbol='SNBR'"
    ).fetchone()[0]
    assert reason == "stale_unselected"


def test_stale_sweep_keeps_names_in_current_fetch() -> None:
    """A still-fetched name (even if inactive this cycle) is NEVER swept — only
    names absent from the fetch are stale. flow_not_block: a present name keeps
    whatever is_active the selection assigned it."""
    conn = _memdb()
    persist_universe(conn, [_inst("AAPL"), _inst("MSFT")],
                     is_active_set={"alpaca:AAPL", "alpaca:MSFT"})
    fetched = [_inst("AAPL"), _inst("MSFT")]
    swept = deactivate_stale_active_rows(
        conn, venue="alpaca", fetched_ids={ins.instrument_id for ins in fetched}
    )
    assert swept == 0
    assert {i.symbol for i in read_active_universe(conn)} == {"AAPL", "MSFT"}


def test_stale_sweep_is_venue_scoped() -> None:
    """The Alpaca sweep never touches OKX/Capital active rows (venue-scoped)."""
    conn = _memdb()
    btc = _inst("BTC-USDT", venue="okx", asset_class="crypto", quote_ccy="USDT")
    persist_universe(conn, [btc], is_active_set={"okx:BTC-USDT"})
    persist_universe(
        conn, [_inst("ABVE", vol=5.4e4), _inst("AAPL")],
        is_active_set={"alpaca:ABVE", "alpaca:AAPL"},
    )
    # Alpaca sweep: only AAPL fetched → ABVE swept, BTC (other venue) untouched.
    deactivate_stale_active_rows(conn, venue="alpaca", fetched_ids={"alpaca:AAPL"})
    active = {(i.venue, i.symbol) for i in read_active_universe(conn)}
    assert ("okx", "BTC-USDT") in active
    assert ("alpaca", "ABVE") not in active


def test_stale_sweep_empty_fetch_is_noop() -> None:
    """An EMPTY fetched_ids means 'no fetch happened this cycle' (creds missing /
    API down) — it must NOT mark every active row stale and zero the book. The
    sweep no-ops on an empty fetch set (flow_not_block: never zero on a non-event).
    The other tests pass an explicit ``set()`` ONLY because the prior cycle's row
    is the sole row; here a populated book proves the empty-set guard."""
    conn = _memdb()
    persist_universe(conn, [_inst("AAPL"), _inst("MSFT")],
                     is_active_set={"alpaca:AAPL", "alpaca:MSFT"})
    swept = deactivate_stale_active_rows(conn, venue="alpaca", fetched_ids=set())
    assert swept == 0
    assert {i.symbol for i in read_active_universe(conn)} == {"AAPL", "MSFT"}


# ---------------------------------------------------------------------------
# B2 integration — refresh_alpaca_universe_once wires the sweep + listing_ts
# ---------------------------------------------------------------------------


def _assets_payload(symbols: list[str]) -> list[dict[str, object]]:
    return [
        {
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": s,
            "status": "active",
            "tradable": True,
        }
        for s in symbols
    ]


def _alpaca_mock_client(symbols: list[str]) -> httpx.AsyncClient:
    """MockTransport serving /v2/assets; screener/snapshots return empty (rows keep
    placeholder vol → all eligible, so the top-N seats them; enrichment is not the
    point of these tests — the sweep + listing_ts wiring is)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/assets":
            return httpx.Response(200, json=_assets_payload(symbols))
        if request.url.path.endswith("most-actives"):
            return httpx.Response(200, json={"most_actives": []})
        if request.url.path.endswith("snapshots"):
            return httpx.Response(200, json={"snapshots": {}})
        return httpx.Response(404, json={"path": request.url.path})

    return httpx.AsyncClient(
        base_url=ALPACA_PAPER_BASE, transport=httpx.MockTransport(handler), timeout=5.0
    )


def _refresh_alpaca(conn: sqlite3.Connection, symbols: list[str], *, ts: int) -> int:
    # These tests feed an ALL-PLACEHOLDER universe (screener/snapshots empty) on
    # purpose — they exercise the stale-sweep + listing_ts wiring, NOT enrichment
    # quality. The enrichment reliability guard (which bars an all-un-enriched slab
    # from active) is therefore disabled here so the placeholder rows seat as
    # before; its own behavior is covered by tests/test_universe_reliability_guard.
    prev = os.environ.get("POLARIS_RELIABILITY_MIN_ENRICHED_RATIO")
    os.environ["POLARIS_RELIABILITY_MIN_ENRICHED_RATIO"] = "0"

    async def run() -> int:
        async with _alpaca_mock_client(symbols) as cli:
            # client serves both trade + data hosts (shared MockTransport).
            from polaris.core.universe import _alpaca as alp

            async def _patched(**kw: object) -> list[UniverseInstrument]:
                return await alp.fetch_alpaca_instruments(
                    api_key="k", secret_key="s", now_ts=ts, client=cli,
                )

            import polaris.scripts._production_layers as pl

            orig = pl.fetch_alpaca_instruments
            pl.fetch_alpaca_instruments = _patched  # type: ignore[assignment]
            try:
                return await refresh_alpaca_universe_once(conn, now_ts=ts)
            finally:
                pl.fetch_alpaca_instruments = orig  # type: ignore[assignment]

    try:
        return asyncio.run(run())
    finally:
        if prev is None:
            os.environ.pop("POLARIS_RELIABILITY_MIN_ENRICHED_RATIO", None)
        else:
            os.environ["POLARIS_RELIABILITY_MIN_ENRICHED_RATIO"] = prev


def test_refresh_deactivates_ghost_when_name_leaves_fetch() -> None:
    """End-to-end: a name active in cycle N that drops out of cycle N+1's Alpaca
    fetch is flipped is_active=0 — the 21-day ABVE ghost can no longer survive."""
    conn = _memdb()
    # Cycle N: ABVE + AAPL fetched.
    _refresh_alpaca(conn, ["ABVE", "AAPL"], ts=NOW)
    assert "ABVE" in {i.symbol for i in read_active_universe(conn)}
    # Cycle N+1: ABVE absent from the fetch (delisted / fell off /v2/assets).
    _refresh_alpaca(conn, ["AAPL", "MSFT"], ts=NOW + 600)
    active = {i.symbol for i in read_active_universe(conn)}
    assert "ABVE" not in active, "ghost row must be deactivated when it leaves the fetch"
    assert {"AAPL", "MSFT"} <= active
    reason = conn.execute(
        "SELECT active_reason FROM universe WHERE venue='alpaca' AND symbol='ABVE'"
    ).fetchone()[0]
    assert reason == "stale_unselected"


def test_refresh_populates_listing_ts() -> None:
    """The Alpaca refresh stamps listing_ts (was NULL for every row → new-listing
    watchdog never fired). A first-seen name gets a non-null listing_ts."""
    conn = _memdb()
    _refresh_alpaca(conn, ["AAPL", "MSFT"], ts=NOW)
    rows = conn.execute(
        "SELECT symbol, listing_ts FROM universe WHERE venue='alpaca'"
    ).fetchall()
    ts_by_sym = {str(s): lt for s, lt in rows}
    assert ts_by_sym["AAPL"] is not None
    assert ts_by_sym["MSFT"] is not None


def test_refresh_listing_ts_stable_across_cycles() -> None:
    """A name's listing_ts is its FIRST-seen ts and does not advance on re-fetch
    (COALESCE keep), so the <24h watchdog measures true age."""
    conn = _memdb()
    _refresh_alpaca(conn, ["AAPL"], ts=NOW)
    first = conn.execute(
        "SELECT listing_ts FROM universe WHERE venue='alpaca' AND symbol='AAPL'"
    ).fetchone()[0]
    _refresh_alpaca(conn, ["AAPL"], ts=NOW + 10_000)
    second = conn.execute(
        "SELECT listing_ts FROM universe WHERE venue='alpaca' AND symbol='AAPL'"
    ).fetchone()[0]
    assert first == second == NOW
