"""Incremental bar-fetch + skip-if-fresh + FX-major focus seat.

Root cause (Alpaca 429 storm, 2026-06-24): the bar ingest re-fetched the FULL
bar window for EVERY focus symbol on EVERY cadence-due tick. With ~99 Alpaca
equity symbols force-seated into the 1m regime bucket (5s cadence), each tick
issued ~99 separate ``/v2/stocks/{symbol}/bars`` requests -> Alpaca free-tier
rate limit -> HTTP 429 -> bars went 9-50h stale -> the whole US-equity track
went data-stale (``bars/recency-stale``) and could not trade.

Bars are a CACHE layer (Jin T2): "have it -> price fast; missing -> fetch the
multi-resolution history and store." These tests pin the cache semantics that
stop the 429 storm WITHOUT blocking any instrument (flow_not_block -- pure
fetch-efficiency, no throttle):

1. ``last_stored_bar_ts`` -- MAX(ts) per (instrument, interval) from the bars table.
2. ``current_period_open_ts`` -- the open ts of the in-progress bar for an interval.
3. skip-if-fresh -- ``ingest_bars_for_focus`` skips the network fetch for a
   (venue, interval) in ``skip_if_current`` when the current period's bar is
   already stored (the regime-only Alpaca 1m bucket has no strategy consumer, so
   re-fetching the in-progress equity 1m bar adds zero value but storms the API).
4. incremental ``start`` -- when history exists, ``fetch_alpaca_bars`` lower-bounds
   the window at the last stored bar instead of the fixed 330d/2d lookback, so a
   re-fetch returns a few new bars (small payload), not the full 240.
5. FX-major force-seat -- curated live Capital FX majors (USDJPY etc.) are seated
   into the focus union regardless of their rank tier, so they always get bars.

DEMO/PAPER only. OKX + Capital fetch paths stay byte-identical (the new params
default to "off" -> legacy behaviour for every caller that does not opt in).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from polaris.core.data.ingest import persist_bars
from polaris.core.data.schema import Bar
from polaris.scripts._production_bars import (
    current_period_open_ts,
    fetch_alpaca_bars,
    ingest_bars_for_focus,
    last_stored_bar_ts,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _equity_session_open() -> Generator[None]:
    """Pin ``ingest_bars_for_focus``'s wall-clock to a weekday RTH instant.

    These tests pin the EXCHANGE incremental / skip-if-current semantics for
    Alpaca equity and assume the equity fetch path runs (they predate the #84
    equity data-fetch session gate). The gate reads ``time.time`` once per call,
    so a run on a weekend / out-of-hours would (correctly) skip the equity fetch
    and mask the behaviour under test. Freeze the module clock to Wed 2026-06-24
    15:00 UTC (= 11:00 ET, RTH in EDT) so the gate is open and these tests stay
    deterministic regardless of the actual run-day. (The gate itself is tested
    independently in ``test_session_map`` / ``test_static_ground``.)
    """
    import datetime as _dt

    import polaris.scripts._production_bars as pbars

    rth_ts = int(_dt.datetime(2026, 6, 24, 15, 0, tzinfo=_dt.UTC).timestamp())
    with patch.object(pbars.time, "time", lambda: rth_ts):
        yield


@pytest.fixture(autouse=True)
def _yahoo_primary_off() -> Generator[None]:
    """Neutralize the Yahoo-PRIMARY layer for the EXCHANGE-routing tests here.

    Yahoo Finance is now the primary bar-history source (Alpaca 429 root fix).
    These tests pin the exchange-side incremental/cache semantics, so we stub
    ``fetch_yahoo_bars`` → [] (Yahoo has no bars) and clear the fallback cooldown
    so the exchange branch is always reached. flow_not_block unchanged.
    """
    import polaris.scripts._production_bars as pbars
    import polaris.scripts._yahoo_bars as ybars

    async def _no_yahoo(*args: Any, **kwargs: Any) -> list[Bar]:
        return []

    ybars._FALLBACK_LAST_MONO.clear()
    with patch.object(pbars, "fetch_yahoo_bars", new=_no_yahoo):
        yield
    ybars._FALLBACK_LAST_MONO.clear()


@pytest.fixture
def memdb() -> Generator[sqlite3.Connection]:
    # The 1m ingest path runs the full baseline pipeline (ticker_baseline_*),
    # so the fixture provisions those tables too.
    from polaris.storage.schema_ddl_core import (
        DDL_BARS,
        DDL_BARS_INDEX,
        DDL_TICKER_BASELINE_SAMPLES,
        DDL_TICKER_BASELINE_STATE,
    )

    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL_BARS)
    conn.executescript(DDL_BARS_INDEX)
    conn.executescript(DDL_TICKER_BASELINE_SAMPLES)
    conn.executescript(DDL_TICKER_BASELINE_STATE)
    try:
        yield conn
    finally:
        conn.close()


def _bar(venue: str, symbol: str, interval: str, ts: int, close: float = 100.0) -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"equity:{symbol}",
        venue=venue,
        symbol=symbol,
        bar_interval=interval,
        ts=ts,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000.0,
        notional_usd=close * 1000.0,
        trade_count=10,
        vwap=close,
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="test",
    )


class _RecordingAlpacaAdapter:
    """Stand-in exposing ``fetch_bars`` like ``AlpacaAdapter`` -- records calls."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.calls: list[dict[str, Any]] = []

    async def fetch_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Min",
        limit: int = 300,
        start: str | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "limit": limit, "start": start}
        )
        return self._rows


def _raw_alpaca_bar(day: int) -> dict[str, Any]:
    return {
        "t": f"2024-01-{day:02d}T05:00:00Z",
        "o": 100.0,
        "h": 101.0,
        "l": 99.0,
        "c": 100.5,
        "v": 1_000_000,
        "n": 5_000,
        "vw": 100.4,
    }


# ---------------------------------------------------------------------------
# 1. last_stored_bar_ts
# ---------------------------------------------------------------------------


def test_last_stored_bar_ts_none_when_empty(memdb: sqlite3.Connection) -> None:
    assert last_stored_bar_ts(memdb, "alpaca:AAPL", "1D") is None


def test_last_stored_bar_ts_returns_max(memdb: sqlite3.Connection) -> None:
    persist_bars(
        memdb,
        [
            _bar("alpaca", "AAPL", "1D", 1_000),
            _bar("alpaca", "AAPL", "1D", 3_000),
            _bar("alpaca", "AAPL", "1D", 2_000),
        ],
    )
    assert last_stored_bar_ts(memdb, "alpaca:AAPL", "1D") == 3_000


def test_last_stored_bar_ts_partitioned_by_interval(memdb: sqlite3.Connection) -> None:
    persist_bars(
        memdb,
        [
            _bar("alpaca", "AAPL", "1D", 5_000),
            _bar("alpaca", "AAPL", "1m", 9_000),
        ],
    )
    assert last_stored_bar_ts(memdb, "alpaca:AAPL", "1D") == 5_000
    assert last_stored_bar_ts(memdb, "alpaca:AAPL", "1m") == 9_000


# ---------------------------------------------------------------------------
# 2. current_period_open_ts
# ---------------------------------------------------------------------------


def test_current_period_open_floors_1m() -> None:
    # 1m bar opens at the start of the current minute (floor to 60s).
    now = 1_700_000_037
    assert now % 60 == 57  # 57s past the boundary
    assert current_period_open_ts("1m", now) == now - 57


def test_current_period_open_floors_1h() -> None:
    now = 1_700_001_800  # 30min past the hour
    open_ts = current_period_open_ts("1H", now)
    assert open_ts <= now
    assert now - open_ts < 3600
    assert open_ts % 3600 == 0


def test_current_period_open_1d_returns_now_never_skips() -> None:
    # 1D is intentionally unmapped: Alpaca daily bars are stamped at the session
    # open (not UTC midnight), so a daily skip gate would be unsound. The helper
    # returns ``now`` for 1D → a stored daily bar's ts is always < now → the skip
    # gate never fires for daily (flow_not_block; daily uses incremental window
    # only). Same conservative fall-through as any unmapped interval.
    now = 1_700_001_800
    assert current_period_open_ts("1D", now) == now
    assert current_period_open_ts("4h", now) == now  # unmapped → now


# ---------------------------------------------------------------------------
# 3. skip-if-fresh -- the 429 fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_if_current_skips_when_bar_already_stored(
    memdb: sqlite3.Connection,
) -> None:
    """A (venue, interval) in ``skip_if_current`` whose current-period bar is
    already stored is NOT re-fetched (kills the Alpaca 1m re-fetch storm)."""
    now = int(time.time())
    period_open = current_period_open_ts("1m", now)
    persist_bars(memdb, [_bar("alpaca", "AAPL", "1m", period_open)])
    adapter = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])

    result = await ingest_bars_for_focus(
        memdb,
        [("alpaca", "AAPL", "equity", "equity:AAPL")],
        alpaca_adapter=adapter,
        bar_interval="1m",
        skip_if_current={("alpaca", "1m")},
    )
    assert adapter.calls == [], "must NOT fetch -- current-period bar already held"
    assert result["bars"] == 0
    assert result["symbols"] == 0
    assert result["skipped_fresh"] == 1


@pytest.mark.asyncio
async def test_skip_if_current_fetches_when_period_rolled(
    memdb: sqlite3.Connection,
) -> None:
    """When the stored bar belongs to a PRIOR period, the new period is fetched
    (the skip auto-clears the instant the minute rolls -- flow_not_block)."""
    now = int(time.time())
    prior = current_period_open_ts("1m", now) - 60  # last minute's bar
    persist_bars(memdb, [_bar("alpaca", "AAPL", "1m", prior)])
    adapter = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])

    result = await ingest_bars_for_focus(
        memdb,
        [("alpaca", "AAPL", "equity", "equity:AAPL")],
        alpaca_adapter=adapter,
        bar_interval="1m",
        skip_if_current={("alpaca", "1m")},
    )
    assert len(adapter.calls) == 1, "must fetch -- current period not yet stored"
    assert result["bars"] >= 1


@pytest.mark.asyncio
async def test_skip_if_current_fetches_when_missing(
    memdb: sqlite3.Connection,
) -> None:
    """No stored bar at all -> always fetch (missing data, flow_not_block)."""
    adapter = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])
    result = await ingest_bars_for_focus(
        memdb,
        [("alpaca", "AAPL", "equity", "equity:AAPL")],
        alpaca_adapter=adapter,
        bar_interval="1m",
        skip_if_current={("alpaca", "1m")},
    )
    assert len(adapter.calls) == 1
    assert result["bars"] >= 1


@pytest.mark.asyncio
async def test_skip_if_current_does_not_skip_other_venue_interval(
    memdb: sqlite3.Connection,
) -> None:
    """A (venue, interval) NOT in ``skip_if_current`` is ALWAYS fetched even with
    a current-period bar stored -- protects volume_burst (okx 1m) intra-minute
    freshness. Only the regime-only Alpaca 1m is in the skip set."""
    now = int(time.time())
    period_open = current_period_open_ts("1m", now)
    persist_bars(memdb, [_bar("okx", "BTC-USDT", "1m", period_open)])

    captured: dict[str, Any] = {}

    async def _fake_okx(*args: Any, **kwargs: Any) -> list[Bar]:
        captured["called"] = True
        return [_bar("okx", "BTC-USDT", "1m", period_open)]

    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake_okx):
        await ingest_bars_for_focus(
            memdb,
            [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            bar_interval="1m",
            skip_if_current={("alpaca", "1m")},  # okx NOT in set
        )
    assert captured.get("called") is True, "okx 1m must NOT be skipped"


@pytest.mark.asyncio
async def test_no_skip_set_preserves_legacy_behaviour(
    memdb: sqlite3.Connection,
) -> None:
    """``skip_if_current=None`` (default) -> every focus symbol fetched as before
    (byte-identical legacy behaviour for callers that do not opt in)."""
    now = int(time.time())
    period_open = current_period_open_ts("1m", now)
    persist_bars(memdb, [_bar("alpaca", "AAPL", "1m", period_open)])
    adapter = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])
    await ingest_bars_for_focus(
        memdb,
        [("alpaca", "AAPL", "equity", "equity:AAPL")],
        alpaca_adapter=adapter,
        bar_interval="1m",
    )
    assert len(adapter.calls) == 1, "no skip set -> legacy full fetch"


# ---------------------------------------------------------------------------
# 3b. #84 equity data-fetch session gate on the HOT PATH (ingest_bars_for_focus)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_focus_skips_closed_equity_fetches_crypto(
    memdb: sqlite3.Connection,
) -> None:
    """On a US-equity-closed instant the hot path skips Alpaca equity, fetches OKX.

    Covers EVERY timeframe incl. the 1m regime bucket. OKX crypto is 24/7 and
    must keep fetching at the SAME instant (crypto 무게이팅). flow_not_block:
    only the fetch is skipped; the focus list / WS / dashboard are untouched.
    """
    import datetime as _dt

    import polaris.scripts._production_bars as pbars

    # Sat 2026-06-27 12:00 UTC — US equity shut all day, OKX never closes.
    sat_ts = int(_dt.datetime(2026, 6, 27, 12, 0, tzinfo=_dt.UTC).timestamp())

    adapter = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])
    okx_called: dict[str, Any] = {}

    async def _fake_okx(*args: Any, **kwargs: Any) -> list[Bar]:
        okx_called["called"] = True
        return [_bar("okx", "BTC-USDT", "1m", sat_ts)]

    with (
        patch.object(pbars.time, "time", lambda: sat_ts),
        patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake_okx),
    ):
        result = await ingest_bars_for_focus(
            memdb,
            [("alpaca", "AAPL", "equity", "equity:AAPL"),
             ("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            alpaca_adapter=adapter,
            bar_interval="1m",
        )

    assert adapter.calls == [], "closed-equity Alpaca fetch must be SKIPPED"
    assert okx_called.get("called") is True, "OKX crypto must STILL fetch (24/7)"
    # The equity skip is counted in skipped_fresh (flow_not_block telemetry).
    assert result["skipped_fresh"] == 1


# ---------------------------------------------------------------------------
# 4. incremental start -- payload reduction + incremental startup ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_start_uses_last_ts_when_history_exists() -> None:
    """``since_ts`` (last stored bar, RECENT — within the lookback) tightens the
    Alpaca window so a re-fetch returns only NEW bars, not the full 240."""
    adapter = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])
    # A recent last bar (5 days ago) → the incremental window starts ~5-7 days
    # ago (last_ts - 2d overlap), MUCH tighter than the fixed 330d lookback.
    recent = int(time.time()) - 5 * 86400
    await fetch_alpaca_bars(
        adapter, "AAPL", bar_interval="1D", limit=240, since_ts=recent,
    )
    incr_start = adapter.calls[0]["start"]

    fresh = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])
    await fetch_alpaca_bars(fresh, "AAPL", bar_interval="1D", limit=240)
    fixed_start = fresh.calls[0]["start"]

    assert incr_start is not None and fixed_start is not None
    # Incremental window is strictly more recent than the fixed 330d lookback.
    assert incr_start > fixed_start, (
        f"incremental start {incr_start} should be later than fixed {fixed_start}"
    )


@pytest.mark.asyncio
async def test_full_lookback_on_first_fetch_no_history() -> None:
    """``since_ts=None`` (no history) -> the full fixed lookback window (warmup)."""
    adapter = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])
    await fetch_alpaca_bars(adapter, "AAPL", bar_interval="1D", limit=240)
    start = adapter.calls[0]["start"]
    assert start is not None and len(start) == 10  # YYYY-MM-DD fixed lookback


@pytest.mark.asyncio
async def test_incremental_start_never_widens_window() -> None:
    """A ``since_ts`` OLDER than the fixed lookback must NOT widen the window past
    the fixed lookback (warmup floor preserved -- never fetch more than needed)."""
    adapter = _RecordingAlpacaAdapter([_raw_alpaca_bar(2)])
    ancient = 1_000_000_000  # 2001 -- far older than the 330d fixed lookback
    await fetch_alpaca_bars(
        adapter, "AAPL", bar_interval="1D", limit=240, since_ts=ancient,
    )
    start = adapter.calls[0]["start"]
    # Window stays at the fixed ~330d lookback, not 2001.
    assert start is not None and not start.startswith("2001")


# ---------------------------------------------------------------------------
# 5. FX-major force-seat into focus
# ---------------------------------------------------------------------------


def _universe_conn() -> sqlite3.Connection:
    from polaris.storage.schema_ddl_core import DDL_UNIVERSE

    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL_UNIVERSE)
    return conn


def _ins_universe(
    conn: sqlite3.Connection, venue: str, symbol: str, asset_class: str, state: str
) -> None:
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        "asset_class, quote_ccy, state, last_seen_ts, is_active) "
        "VALUES (?,?,?,?,?,?,?,?,1)",
        (
            venue,
            symbol,
            f"{venue}:{symbol}",
            f"{asset_class}:{symbol}",
            asset_class,
            "USD",
            state,
            0,
        ),
    )


def test_fx_major_focus_targets_seats_live_majors() -> None:
    """Curated live Capital FX majors are returned as focus-shaped targets so the
    bar ingest always fetches them regardless of rank tier (USDJPY ranked T-1178
    in the live DB never accumulated bars otherwise)."""
    conn = _universe_conn()
    _ins_universe(conn, "capital", "USDJPY", "forex", "live")
    _ins_universe(conn, "capital", "EURGBP", "forex", "live")  # not a curated major
    from polaris.scripts._production_layers import fx_major_focus_targets

    targets = fx_major_focus_targets(conn)
    symbols = {s for _v, s, _ac, _g in targets}
    assert "USDJPY" in symbols
    assert "EURGBP" not in symbols
    usdjpy = next(t for t in targets if t[1] == "USDJPY")
    assert usdjpy[0] == "capital"
    assert usdjpy[2] == "forex"
    conn.close()


def test_fx_major_focus_targets_seats_suffixed_audusd_zero() -> None:
    """REGRESSION: Capital lists the AUD/USD major as ``AUDUSD_ZERO`` (there is NO
    bare ``AUDUSD`` row in the live universe). An exact ``symbol IN (majors)``
    filter silently dropped it; the suffix-normalizing predicate must seat it."""
    conn = _universe_conn()
    _ins_universe(conn, "capital", "AUDUSD_ZERO", "forex", "live")
    _ins_universe(conn, "capital", "EURUSD_W", "forex", "closed")  # weekend → not live
    _ins_universe(conn, "capital", "AUDZAR", "forex", "live")  # exotic → not a major
    from polaris.scripts._production_layers import fx_major_focus_targets

    targets = fx_major_focus_targets(conn)
    symbols = {s for _v, s, _ac, _g in targets}
    assert "AUDUSD_ZERO" in symbols, "the suffixed AUD/USD major must be seated"
    assert "EURUSD_W" not in symbols, "closed weekend variant excluded by state guard"
    assert "AUDZAR" not in symbols, "exotic cross is not a curated major"
    conn.close()


def test_fx_major_focus_targets_skips_non_live() -> None:
    """A suspended/expired FX major is NOT seated (only live epics get bars)."""
    conn = _universe_conn()
    _ins_universe(conn, "capital", "EURUSD", "forex", "suspended")
    from polaris.scripts._production_layers import fx_major_focus_targets

    targets = fx_major_focus_targets(conn)
    assert targets == []
    conn.close()


def test_get_focus_targets_force_seats_fx_majors() -> None:
    """``get_focus_targets`` unions FX majors into the focus the same way it unions
    held positions -- additive, never truncated by ``max_n`` (flow_not_block)."""
    from polaris.storage.schema_ddl_core import (
        DDL_POSITIONS,
        DDL_UNIVERSE,
        DDL_WATCHLIST_FOCUS,
    )

    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL_UNIVERSE)
    conn.executescript(DDL_WATCHLIST_FOCUS)
    conn.executescript(DDL_POSITIONS)  # get_focus_targets unions held positions
    _ins_universe(conn, "capital", "USDJPY", "forex", "live")
    # A focus cycle whose single top pick is NOT USDJPY.
    cyc = 1_000
    conn.execute(
        "INSERT INTO watchlist_focus (cycle_ts, venue, symbol, focus_score, "
        "focus_rank, target_bucket, tier) VALUES (?,?,?,?,?,?,?)",
        (cyc, "okx", "BTC-USDT", 0.9, 1, "core", "S"),
    )
    from polaris.scripts._production_layers import get_focus_targets

    focus = get_focus_targets(conn, cycle_ts=cyc, max_n=1)
    symbols = {s for _v, s, _ac, _g in focus}
    assert "BTC-USDT" in symbols
    assert "USDJPY" in symbols, "FX major must be force-seated even outside max_n"
    conn.close()
