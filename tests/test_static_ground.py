"""STEP① static-ground expansion — full-universe bar coverage + per-ticker ground.

DEMO/PAPER. AGGRESSIVE / flow_not_block: the static-ground fill is a SEPARATE
background producer that widens OBSERVATION coverage from the focus subset to the
WHOLE active universe; it never gates an entry / size / exit. These tests pin:

  - coverage widens 276→ALL active (the fill walks read_active_universe, not focus);
  - concurrency is Semaphore-bounded + a per-cycle total-timeout (degrade-never-halt);
  - the Yahoo frame cache is reused (overlap with the hot path = no double fetch);
  - the fill is non-blocking (offloaded — never awaited inline in the tick body);
  - per-active-ticker sentiment/event ground is query-able via the existing fuser;
  - a ticker with NO covering source returns a graceful EMPTY ground row.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

import pytest

from polaris.core.data.schema import Bar
from polaris.scripts import _static_ground as sg
from polaris.storage.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Any) -> sqlite3.Connection:
    return init_db(tmp_path / "ground.sqlite")


def _seat_active(
    conn: sqlite3.Connection,
    rows: list[tuple[str, str, str, str]],
    *,
    is_active: int = 1,
) -> None:
    """Insert ``(venue, symbol, asset_class, group_id)`` rows into ``universe``."""
    now = int(time.time())
    for venue, symbol, ac, group in rows:
        conn.execute(
            "INSERT OR REPLACE INTO universe (venue, symbol, instrument_id, "
            "underlying_group_id, asset_class, quote_ccy, state, last_seen_ts, "
            "is_active) VALUES (?,?,?,?,?,?,?,?,?)",
            (venue, symbol, f"{venue}:{symbol}", group, ac, "USD", "live", now,
             is_active),
        )
    conn.commit()


def _mk_bar(venue: str, symbol: str, interval: str, ts: int) -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"crypto:{symbol}",
        venue=venue, symbol=symbol, bar_interval=interval, ts=ts,
        open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0,
        notional_usd=1.0, trade_count=1, vwap=1.0,
        bid_close=0.0, ask_close=0.0, spread_bps_close=0.0, source="yahoo",
    )


# ---------------------------------------------------------------------------
# STEP①.1 — bar coverage widens to the WHOLE active universe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fill_covers_every_active_instrument(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fill fetches bars for EVERY active row, not just a focus subset."""
    active = [
        ("okx", f"C{i}-USDT", "crypto", f"crypto:C{i}") for i in range(50)
    ]
    _seat_active(conn, active)

    fetched: list[str] = []

    async def _fake_fetch_bars_one(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        fetched.append(f"{venue}:{symbol}")
        return [_mk_bar(venue, symbol, bar_interval, int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake_fetch_bars_one)

    result = await sg.ingest_static_ground_bars(
        conn, resolutions=("1D",), parallel=8, total_timeout_sec=30.0,
    )

    # Every active instrument was fetched (276→all): 50 active × 1 resolution.
    covered = {f.split(":", 1)[0] + ":" + f.split(":", 1)[1].split("@", 1)[0]
               for f in fetched}
    assert len(covered) == 50
    assert result["instruments"] == 50
    assert result["bars"] >= 50
    # Persisted: every instrument now has at least one stored 1D bar.
    n = conn.execute(
        "SELECT COUNT(DISTINCT instrument_id) FROM bars WHERE bar_interval='1D'"
    ).fetchone()[0]
    assert n == 50


@pytest.mark.asyncio
async def test_fill_only_walks_active_rows(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inactive (is_active=0) rows are NOT fetched — coverage = active universe."""
    _seat_active(conn, [("okx", "BTC-USDT", "crypto", "crypto:BTC")], is_active=1)
    _seat_active(conn, [("okx", "DEAD-USDT", "crypto", "crypto:DEAD")], is_active=0)

    fetched: list[str] = []

    async def _fake(venue: str, symbol: str, asset_class: str, **_kw: Any) -> list[Bar]:
        fetched.append(symbol)
        return []

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    await sg.ingest_static_ground_bars(conn, resolutions=("1D",))
    assert fetched == ["BTC-USDT"]


@pytest.mark.asyncio
async def test_fill_multi_resolution(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each active instrument gets bars at EVERY requested resolution."""
    _seat_active(conn, [("okx", "BTC-USDT", "crypto", "crypto:BTC")])
    seen: list[str] = []

    async def _fake(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        seen.append(bar_interval)
        return [_mk_bar(venue, symbol, bar_interval, int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    await sg.ingest_static_ground_bars(conn, resolutions=("1D", "1H", "15m"))
    assert sorted(seen) == ["15m", "1D", "1H"]


@pytest.mark.asyncio
async def test_fill_bounded_concurrency(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrency never exceeds ``parallel`` (Yahoo IP-block guard)."""
    _seat_active(
        conn, [("okx", f"C{i}-USDT", "crypto", f"crypto:C{i}") for i in range(40)]
    )
    inflight = 0
    peak = 0

    async def _fake(venue: str, symbol: str, asset_class: str, **_kw: Any) -> list[Bar]:
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.005)
        inflight -= 1
        return []

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    await sg.ingest_static_ground_bars(conn, resolutions=("1D",), parallel=4)
    assert peak <= 4


@pytest.mark.asyncio
async def test_fill_total_timeout_degrades_never_halts(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow batch is bounded by the total-timeout; partial work survives."""
    _seat_active(
        conn, [("okx", f"C{i}-USDT", "crypto", f"crypto:C{i}") for i in range(20)]
    )

    async def _slow(venue: str, symbol: str, asset_class: str, **_kw: Any) -> list[Bar]:
        await asyncio.sleep(10.0)  # far longer than the timeout
        return [_mk_bar(venue, symbol, "1D", int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _slow)
    t0 = time.monotonic()
    result = await sg.ingest_static_ground_bars(
        conn, resolutions=("1D",), parallel=8, total_timeout_sec=0.1,
    )
    elapsed = time.monotonic() - t0
    # Returned promptly (did not block on the 10s sleeps) and reported a timeout.
    assert elapsed < 2.0
    assert result["timed_out"] is True


@pytest.mark.asyncio
async def test_fill_persist_error_does_not_abort_walk(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed Yahoo candle that fails persist (NOT NULL) never aborts the walk.

    Live-probe regression (2026-06-25): a real Yahoo frame carried a NULL/NaN
    close → IntegrityError in persist_bars → the unguarded raise aborted the whole
    gather. The SAVEPOINT guard must skip the bad batch and keep the rest.
    """
    _seat_active(
        conn,
        [("okx", "GOOD1-USDT", "crypto", "crypto:GOOD1"),
         ("okx", "BADBAR-USDT", "crypto", "crypto:BADBAR"),
         ("okx", "GOOD2-USDT", "crypto", "crypto:GOOD2")],
    )

    async def _fake(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        bar = _mk_bar(venue, symbol, bar_interval, int(time.time()))
        if symbol == "BADBAR-USDT":
            # Reproduce the live malformed candle: SQLite stores a NaN float binding
            # as NULL, so a NaN close trips the NOT NULL bars.close constraint inside
            # persist_bars (the exact IntegrityError the live probe surfaced).
            object.__setattr__(bar, "close", float("nan"))
        return [bar]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    result = await sg.ingest_static_ground_bars(conn, resolutions=("1D",))
    # Both GOOD instruments persisted; the BAD batch was skipped, not fatal.
    assert result["instruments"] == 2
    persisted = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM bars WHERE bar_interval='1D'"
        ).fetchall()
    }
    assert persisted == {"GOOD1-USDT", "GOOD2-USDT"}


@pytest.mark.asyncio
async def test_fill_per_symbol_error_is_swallowed(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One symbol raising never aborts the rest (degrade-never-halt)."""
    _seat_active(
        conn,
        [("okx", "GOOD-USDT", "crypto", "crypto:GOOD"),
         ("okx", "BAD-USDT", "crypto", "crypto:BAD")],
    )

    async def _fake(venue: str, symbol: str, asset_class: str, **_kw: Any) -> list[Bar]:
        if symbol == "BAD-USDT":
            raise RuntimeError("yahoo blew up")
        return [_mk_bar(venue, symbol, "1D", int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    result = await sg.ingest_static_ground_bars(conn, resolutions=("1D",))
    # GOOD still persisted; the batch did not crash.
    assert result["instruments"] == 1
    n = conn.execute("SELECT COUNT(*) FROM bars WHERE symbol='GOOD-USDT'").fetchone()[0]
    assert n == 1


# ---------------------------------------------------------------------------
# STEP①.2 — per-active-ticker sentiment/event ground (built on the fuser)
# ---------------------------------------------------------------------------


class _FakeCache:
    """Minimal AltDataCache stand-in: returns canned sources per group prefix."""

    def __init__(self, by_prefix: dict[str, dict[str, Any]]) -> None:
        self._by_prefix = by_prefix

    def get_for_group(
        self, group_id: str, *, now_ts: float | None = None
    ) -> dict[str, Any]:
        prefix = group_id.split(":", 1)[0]
        return self._by_prefix.get(prefix, {})


def test_ground_queryable_for_every_active_ticker(conn: sqlite3.Connection) -> None:
    """Every active ticker yields a ground row (covered → evidence, else empty)."""
    _seat_active(
        conn,
        [("okx", "BTC-USDT", "crypto", "crypto:BTC"),
         ("capital", "ZZZ", "equity", "equity:ZZZ")],
    )
    # Crypto F&G covers crypto; nothing covers the equity ticker.
    cache = _FakeCache({"crypto": {"crypto_fg": {"value": 80, "label": "Greed"}}})

    written = sg.refresh_ticker_ground(conn, cache=cache)
    assert written == 2  # one row per active ticker, covered or not

    rows = {
        r[0]: r
        for r in conn.execute(
            "SELECT instrument_id, has_sentiment, has_event, ground_json "
            "FROM ticker_ground"
        ).fetchall()
    }
    assert set(rows) == {"okx:BTC-USDT", "capital:ZZZ"}
    # Covered crypto ticker carries evidence; uncovered equity ticker is graceful-empty.
    assert rows["okx:BTC-USDT"][1] == 1  # has_sentiment/event evidence present
    assert rows["capital:ZZZ"][1] == 0
    assert rows["capital:ZZZ"][2] == 0


def test_ground_empty_source_is_graceful(conn: sqlite3.Connection) -> None:
    """No covering source → empty ground row, never raises (flow_not_block)."""
    _seat_active(conn, [("okx", "OBSCURE-USDT", "crypto", "crypto:OBSCURE")])
    cache = _FakeCache({})  # nothing fresh for any prefix
    written = sg.refresh_ticker_ground(conn, cache=cache)
    assert written == 1
    row = conn.execute(
        "SELECT has_sentiment, has_event, ground_json FROM ticker_ground"
    ).fetchone()
    assert row[0] == 0 and row[1] == 0
    assert row[2] == "{}"


def test_ground_no_cache_is_noop(conn: sqlite3.Connection) -> None:
    """A None cache (smoke/replay) writes nothing and never raises."""
    _seat_active(conn, [("okx", "BTC-USDT", "crypto", "crypto:BTC")])
    assert sg.refresh_ticker_ground(conn, cache=None) == 0
    assert conn.execute("SELECT COUNT(*) FROM ticker_ground").fetchone()[0] == 0


def test_read_ticker_ground_roundtrip(conn: sqlite3.Connection) -> None:
    """The candidate sweep (②) reads back per-ticker ground by instrument_id."""
    _seat_active(conn, [("okx", "BTC-USDT", "crypto", "crypto:BTC")])
    cache = _FakeCache({"crypto": {"crypto_fg": {"value": 80, "label": "Greed"}}})
    sg.refresh_ticker_ground(conn, cache=cache)
    ground = sg.read_ticker_ground(conn, "okx:BTC-USDT")
    assert ground is not None
    assert ground["has_sentiment"] is True
    assert isinstance(ground["ground"], dict)
    # Absent ticker → None (graceful).
    assert sg.read_ticker_ground(conn, "okx:NOPE") is None
