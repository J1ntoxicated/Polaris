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


# ---------------------------------------------------------------------------
# STEP① charge-rate tune (live probe 2026-06-26) — bars/ground producer split
# ---------------------------------------------------------------------------


def test_parallel_default_widened_for_charge_rate() -> None:
    """Bulk-fill width is widened (16→32) to ~2× the per-cycle reach.

    Live probe: at 16-wide a cycle reached only ~435/1882 before the 600s ceiling,
    so the sweep scored on partial bars. Still bounded (IP-block guard) + env-tunable.
    """
    assert sg.STATIC_GROUND_PARALLEL_DEFAULT == 32
    # Ground refresh cadence is decoupled + FASTER than the bar re-walk so the
    # EdgeScore fills while bars are still charging.
    assert sg.TICKER_GROUND_REFRESH_SEC < sg.STATIC_GROUND_REFRESH_SEC


@pytest.mark.asyncio
async def test_ticker_ground_producer_runs_independent_of_bars(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ground producer materializes EdgeScore WITHOUT waiting on the bar walk.

    Regression (live probe 2026-06-26): ground refresh sat behind a ~600s bar walk,
    so ``ticker_ground`` was 0 during the first walk and the sweep had no direction.
    The split producer must call ``refresh_ticker_ground`` on its own cadence — even
    if the bar producer never finishes a cycle.
    """
    from polaris.scripts import production_paper_loop as ppl

    _seat_active(conn, [("okx", "BTC-USDT", "crypto", "crypto:BTC")])

    calls: list[int] = []

    def _fake_refresh(_conn: Any, *, cache: Any, now_ts: int | None = None) -> int:
        calls.append(1)
        return 7

    monkeypatch.setattr(ppl, "refresh_ticker_ground", _fake_refresh)

    state = ppl.ProdLoopState()
    stop_evt = asyncio.Event()

    async def _stop_after_first() -> None:
        # Let the producer's first immediate refresh run, then stop it.
        await asyncio.sleep(0.02)
        stop_evt.set()

    await asyncio.gather(
        ppl._ticker_ground_producer(
            conn, state=state, stop_evt=stop_evt, refresh_sec=999.0,
        ),
        _stop_after_first(),
    )

    # First refresh ran immediately (no dependency on any bar walk completing).
    assert calls, "ticker-ground producer must refresh on its first cycle"
    assert state.static_ground_tickers == 7


@pytest.mark.asyncio
async def test_static_ground_producer_no_longer_refreshes_ticker_ground(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bar producer is BARS-ONLY — ground refresh moved to its own producer.

    Pins the split: the slow bar walk must NOT carry the ground refresh anymore
    (that coupling is exactly what starved the EdgeScore during the first walk).
    """
    from polaris.scripts import production_paper_loop as ppl

    async def _fake_bars(_conn: Any, **_kw: Any) -> dict[str, Any]:
        return {"instruments": 3, "bars": 12, "timed_out": False}

    ground_calls: list[int] = []

    def _spy_refresh(_conn: Any, *, cache: Any, now_ts: int | None = None) -> int:
        ground_calls.append(1)
        return 0

    monkeypatch.setattr(ppl, "ingest_static_ground_bars", _fake_bars)
    monkeypatch.setattr(ppl, "refresh_ticker_ground", _spy_refresh)

    state = ppl.ProdLoopState()
    stop_evt = asyncio.Event()

    async def _stop_after_first() -> None:
        await asyncio.sleep(0.02)
        stop_evt.set()

    await asyncio.gather(
        ppl._static_ground_producer(
            conn, state=state, stop_evt=stop_evt, refresh_sec=999.0,
        ),
        _stop_after_first(),
    )

    # Bars charged on the bar producer; ground refresh was NOT invoked by it.
    assert state.static_ground_bars == 12
    assert state.static_ground_instruments == 3
    assert ground_calls == [], "bar producer must not call refresh_ticker_ground"


@pytest.mark.asyncio
async def test_static_ground_producer_forwards_parallel(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bar producer forwards its ``parallel`` width to the bulk fill.

    Pins the live-tunable charge-rate knob (``POLARIS_STATIC_GROUND_PARALLEL`` is
    wired at the production call site → producer ``parallel`` → ingest).
    """
    from polaris.scripts import production_paper_loop as ppl

    seen: dict[str, Any] = {}

    async def _fake_bars(_conn: Any, *, parallel: int = -1, **_kw: Any) -> dict[str, Any]:
        seen["parallel"] = parallel
        return {"instruments": 0, "bars": 0, "timed_out": False}

    monkeypatch.setattr(ppl, "ingest_static_ground_bars", _fake_bars)

    state = ppl.ProdLoopState()
    stop_evt = asyncio.Event()

    async def _stop_after_first() -> None:
        await asyncio.sleep(0.02)
        stop_evt.set()

    await asyncio.gather(
        ppl._static_ground_producer(
            conn, state=state, stop_evt=stop_evt, refresh_sec=999.0, parallel=48,
        ),
        _stop_after_first(),
    )
    assert seen["parallel"] == 48


# ---------------------------------------------------------------------------
# #66 session-predictive pre-warm — pre-open 1m bar warming (Jin "장 열기 전부터
# 거래가능 바 알아서 채워야"). DATA WARMING ONLY: the bulk-fill additionally pulls
# the warm resolutions (1m) for symbols whose cash session is about to open so the
# recency gate sees fresh bars at the open. crypto/FX/commodity/unmapped are never
# warmed. Entry/sizing/exit are UNTOUCHED — this only pre-fills stored bars.
# ---------------------------------------------------------------------------


def _utc_warm(hour: int, minute: int = 0) -> int:
    """A known weekday (Wed 2026-06-24) UTC epoch at the given hour/minute."""
    import datetime as dt

    return int(dt.datetime(2026, 6, 24, hour, minute, tzinfo=dt.UTC).timestamp())


@pytest.mark.asyncio
async def test_warm_adds_1m_for_pre_open_index_only(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the US pre-open window a US index ALSO gets a 1m warm fetch; others don't.

    The base resolutions still walk EVERY active row (unchanged); the warm
    resolution (1m) is ADDED only for the symbol whose cash session is about to
    open / is open (US100 at 13:10 UTC, 20 min before the 13:30 open). An Asia
    index (J225, window 00:00-08:00 UTC) is long closed by 13:10 → no 1m.
    """
    _seat_active(
        conn,
        [("capital", "US100", "index", "index:US100"),   # US pre-open → +1m
         ("capital", "J225", "index", "index:J225"),      # Asia long closed → no 1m
         ("okx", "BTC-USDT", "crypto", "crypto:BTC")],    # crypto → never warmed
    )
    seen: list[tuple[str, str]] = []

    async def _fake(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        seen.append((symbol, bar_interval))
        return [_mk_bar(venue, symbol, bar_interval, int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    result = await sg.ingest_static_ground_bars(
        conn, resolutions=("1D",), warm_resolutions=("1m",),
        now_ts=_utc_warm(13, 10),
    )
    # 1m was fetched ONLY for the pre-open US index (Asia closed, crypto exempt).
    warmed_1m = {sym for sym, iv in seen if iv == "1m"}
    assert warmed_1m == {"US100"}
    # Base 1D still walked every active row (coverage unchanged).
    base_1d = {sym for sym, iv in seen if iv == "1D"}
    assert base_1d == {"US100", "J225", "BTC-USDT"}
    # Result reports the warm fan-out.
    assert result["warm_instruments"] == 1
    assert result["warm_bars"] >= 1
    # The pre-open index now has a stored 1m bar → fresh at the open.
    n = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE symbol='US100' AND bar_interval='1m'"
    ).fetchone()[0]
    assert n == 1


@pytest.mark.asyncio
async def test_warm_alpaca_equity_pre_open(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Alpaca US equity (the 미장 gap) gets a 1m warm before the US open."""
    _seat_active(conn, [("alpaca", "AAPL", "equity", "equity:AAPL")])
    seen: list[tuple[str, str]] = []

    async def _fake(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        seen.append((symbol, bar_interval))
        return [_mk_bar(venue, symbol, bar_interval, int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    result = await sg.ingest_static_ground_bars(
        conn, resolutions=("1D",), warm_resolutions=("1m",),
        now_ts=_utc_warm(13, 10),
    )
    assert ("AAPL", "1m") in seen
    assert result["warm_instruments"] == 1


@pytest.mark.asyncio
async def test_warm_off_when_no_warm_resolutions(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``warm_resolutions=()`` (kill-switch / default) = NO 1m warm at all.

    Pins the kill-switch path: with warming disabled the fill is byte-identical
    to the pre-#66 behavior (base resolutions only, no extra 1m fetch).
    """
    _seat_active(conn, [("capital", "US100", "index", "index:US100")])
    seen: list[str] = []

    async def _fake(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        seen.append(bar_interval)
        return [_mk_bar(venue, symbol, bar_interval, int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    result = await sg.ingest_static_ground_bars(
        conn, resolutions=("1D",), warm_resolutions=(),  # disabled
        now_ts=_utc_warm(13, 10),  # would be a US warm window if enabled
    )
    assert seen == ["1D"]  # base only, no 1m
    assert result["warm_instruments"] == 0
    assert result["warm_bars"] == 0


@pytest.mark.asyncio
async def test_warm_skips_when_not_in_window(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside every pre-open window NO symbol is warmed (1m fetch count = 0)."""
    _seat_active(conn, [("capital", "US100", "index", "index:US100")])
    seen: list[str] = []

    async def _fake(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        seen.append(bar_interval)
        return [_mk_bar(venue, symbol, bar_interval, int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    result = await sg.ingest_static_ground_bars(
        conn, resolutions=("1D",), warm_resolutions=("1m",),
        now_ts=_utc_warm(3),  # 03:00 UTC — US far from open, Europe/Asia not US100
    )
    assert "1m" not in seen
    assert result["warm_instruments"] == 0


@pytest.mark.asyncio
async def test_warm_fetch_failure_degrades_gracefully(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warm 1m fetch that raises never aborts the walk (degrade-never-crash).

    The base resolution still persists for the symbol even if the warm pull blows
    up — warming is best-effort, the bot loop is untouched.
    """
    _seat_active(conn, [("capital", "US100", "index", "index:US100")])

    async def _fake(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        if bar_interval == "1m":
            raise RuntimeError("yahoo 1m blew up")
        return [_mk_bar(venue, symbol, bar_interval, int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    result = await sg.ingest_static_ground_bars(
        conn, resolutions=("1D",), warm_resolutions=("1m",),
        now_ts=_utc_warm(13, 10),
    )
    # Base 1D survived; warm 1m failed but was swallowed (no crash).
    assert result["instruments"] == 1
    assert result["warm_instruments"] == 0  # 1m never persisted
    base = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE symbol='US100' AND bar_interval='1D'"
    ).fetchone()[0]
    assert base == 1


@pytest.mark.asyncio
async def test_warm_does_not_double_fetch_overlapping_resolution(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If 1m is already a base resolution the warm pass must not re-fetch it."""
    _seat_active(conn, [("capital", "US100", "index", "index:US100")])
    count_1m = 0

    async def _fake(
        venue: str, symbol: str, asset_class: str, *, bar_interval: str = "1m",
        **_kw: Any,
    ) -> list[Bar]:
        nonlocal count_1m
        if bar_interval == "1m":
            count_1m += 1
        return [_mk_bar(venue, symbol, bar_interval, int(time.time()))]

    monkeypatch.setattr(sg, "fetch_bars_one", _fake)
    await sg.ingest_static_ground_bars(
        conn, resolutions=("1D", "1m"), warm_resolutions=("1m",),
        now_ts=_utc_warm(13, 10),
    )
    assert count_1m == 1  # fetched once (base), not twice (base + warm)


@pytest.mark.asyncio
async def test_producer_short_cadence_when_warm_active(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the fill warms ≥1 symbol the producer uses the SHORT warm cadence.

    Open-imminent precision: a cycle that warmed a pre-open symbol sleeps the
    short ``warm_cadence_sec`` (default 60) instead of the slow 900s re-walk, so
    the next 1m pull lands close to the open. With 0 warm symbols it falls back
    to the normal refresh cadence (idle degrade).
    """
    from polaris.scripts import production_paper_loop as ppl

    waits: list[float] = []

    async def _warm_bars(_conn: Any, **_kw: Any) -> dict[str, Any]:
        return {"instruments": 1, "bars": 4, "timed_out": False,
                "warm_instruments": 1, "warm_bars": 1}

    monkeypatch.setattr(ppl, "ingest_static_ground_bars", _warm_bars)

    state = ppl.ProdLoopState()
    stop_evt = asyncio.Event()
    real_wait_for = asyncio.wait_for

    async def _spy_wait_for(awaitable: Any, timeout: float) -> Any:
        waits.append(timeout)
        stop_evt.set()  # end the loop after observing the first sleep
        return await real_wait_for(awaitable, timeout=0.001)

    monkeypatch.setattr(ppl.asyncio, "wait_for", _spy_wait_for)

    await ppl._static_ground_producer(
        conn, state=state, stop_evt=stop_evt,
        refresh_sec=900.0, warm_cadence_sec=60.0, warm_resolutions=("1m",),
    )
    assert waits and waits[0] == 60.0  # short cadence after a warm cycle
    assert state.static_ground_warm_bars == 1


@pytest.mark.asyncio
async def test_producer_normal_cadence_when_no_warm(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With 0 warm symbols the producer keeps the normal (slow) refresh cadence."""
    from polaris.scripts import production_paper_loop as ppl

    waits: list[float] = []

    async def _no_warm_bars(_conn: Any, **_kw: Any) -> dict[str, Any]:
        return {"instruments": 5, "bars": 20, "timed_out": False,
                "warm_instruments": 0, "warm_bars": 0}

    monkeypatch.setattr(ppl, "ingest_static_ground_bars", _no_warm_bars)

    state = ppl.ProdLoopState()
    stop_evt = asyncio.Event()
    real_wait_for = asyncio.wait_for

    async def _spy_wait_for(awaitable: Any, timeout: float) -> Any:
        waits.append(timeout)
        stop_evt.set()
        return await real_wait_for(awaitable, timeout=0.001)

    monkeypatch.setattr(ppl.asyncio, "wait_for", _spy_wait_for)

    await ppl._static_ground_producer(
        conn, state=state, stop_evt=stop_evt,
        refresh_sec=900.0, warm_cadence_sec=60.0, warm_resolutions=("1m",),
    )
    assert waits and waits[0] == 900.0  # normal cadence — nothing to warm


@pytest.mark.asyncio
async def test_producer_forwards_warm_resolutions(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The producer forwards its configured warm resolutions to the bulk fill."""
    from polaris.scripts import production_paper_loop as ppl

    seen: dict[str, Any] = {}

    async def _fake_bars(_conn: Any, *, warm_resolutions: Any = None, **_kw: Any
                         ) -> dict[str, Any]:
        seen["warm_resolutions"] = warm_resolutions
        return {"instruments": 0, "bars": 0, "timed_out": False,
                "warm_instruments": 0, "warm_bars": 0}

    monkeypatch.setattr(ppl, "ingest_static_ground_bars", _fake_bars)

    state = ppl.ProdLoopState()
    stop_evt = asyncio.Event()

    async def _stop_after_first() -> None:
        await asyncio.sleep(0.02)
        stop_evt.set()

    await asyncio.gather(
        ppl._static_ground_producer(
            conn, state=state, stop_evt=stop_evt, refresh_sec=999.0,
            warm_resolutions=("1m",),
        ),
        _stop_after_first(),
    )
    assert seen["warm_resolutions"] == ("1m",)
