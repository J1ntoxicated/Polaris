"""Stream-coverage P0 (Alpaca) — daily equity bar ingest wiring tests.

Forensic ``w9gq4ueep``: the Alpaca stream ingested **0** bars because
``fetch_bars_one`` had no ``venue == 'alpaca'`` branch (fell through to
``return []``) and ``'1D'`` was not a registered timeframe. The three equity
strategies (all ``timeframe='1D'``) therefore never saw a usable canvas and
could not trade at the US RTH open.

These tests pin the A1 + A2 contract:
- ``fetch_bars_one(venue='alpaca', bar_interval='1D')`` normalizes raw Alpaca
  bar dicts (keys ``t/o/h/l/c/v/n/vw``) into canonical newest-last ``Bar``s.
- ``'1D'`` is a first-class canonical interval (BAR_INTERVALS) with a fetch
  cadence + an Alpaca timeframe-token map (``'1D' -> '1Day'``).
- daily routing reaches the Alpaca venue end-to-end.

DEMO/PAPER only. These fixes INCREASE coverage (flow_not_block) — no throttle,
no sizing/gating touched. OKX + Capital bar paths stay byte-identical.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from polaris.core.data.schema import BAR_INTERVALS, Bar
from polaris.scripts._production_bars import (
    ALPACA_TIMEFRAME_BY_INTERVAL,
    TIMEFRAME_FETCH_CADENCE_SEC,
    fetch_alpaca_bars,
    fetch_bars_one,
    ingest_bars_for_focus,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _raw_alpaca_bars(n: int = 3) -> list[dict[str, Any]]:
    """Raw Alpaca ``/v2/stocks/{symbol}/bars`` rows (newest LAST, as the API
    returns chronological ascending). Keys: t/o/h/l/c/v/n/vw."""
    out: list[dict[str, Any]] = []
    for i in range(n):
        # 2024-01-02, 03, 04 ... at the RTH open boundary (UTC).
        day = 2 + i
        out.append(
            {
                "t": f"2024-01-{day:02d}T05:00:00Z",
                "o": 100.0 + i,
                "h": 101.0 + i,
                "l": 99.0 + i,
                "c": 100.5 + i,
                "v": 1_000_000 + i,
                "n": 5_000 + i,
                "vw": 100.4 + i,
            }
        )
    return out


class _FakeAlpacaAdapter:
    """Minimal stand-in exposing ``fetch_bars`` like ``AlpacaAdapter``."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[dict[str, Any]] = []

    async def fetch_bars(
        self, symbol: str, *, timeframe: str = "1Min", limit: int = 300
    ) -> list[dict[str, Any]]:
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "limit": limit})
        return self._rows


@pytest.fixture
def memdb() -> Generator[sqlite3.Connection]:
    from polaris.storage.schema_ddl_core import DDL_BARS, DDL_BARS_INDEX

    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL_BARS)
    conn.executescript(DDL_BARS_INDEX)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A2 — '1D' is a first-class canonical interval
# ---------------------------------------------------------------------------


def test_1D_in_bar_intervals() -> None:
    assert "1D" in BAR_INTERVALS


def test_1D_has_fetch_cadence() -> None:
    assert "1D" in TIMEFRAME_FETCH_CADENCE_SEC
    assert TIMEFRAME_FETCH_CADENCE_SEC["1D"] > 0


def test_alpaca_timeframe_map_covers_1D() -> None:
    assert ALPACA_TIMEFRAME_BY_INTERVAL["1D"] == "1Day"


# ---------------------------------------------------------------------------
# A1 — fetch_alpaca_bars normalizes raw dicts -> canonical Bars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_alpaca_bars_normalizes_raw_dicts() -> None:
    adapter = _FakeAlpacaAdapter(_raw_alpaca_bars(3))
    bars = await fetch_alpaca_bars(
        adapter,
        "AAPL",
        bar_interval="1D",
        limit=240,
    )
    assert len(bars) == 3
    assert all(isinstance(b, Bar) for b in bars)
    first = bars[0]
    assert first.venue == "alpaca"
    assert first.symbol == "AAPL"
    assert first.instrument_id == "alpaca:AAPL"
    assert first.underlying_group_id == "equity:AAPL"
    assert first.bar_interval == "1D"
    # OHLCV mapped from t/o/h/l/c/v/n/vw.
    assert first.open == 100.0
    assert first.high == 101.0
    assert first.low == 99.0
    assert first.close == 100.5
    assert first.volume == 1_000_000
    assert first.trade_count == 5_000
    assert first.vwap == 100.4
    # ISO-8601 't' parsed to seconds-epoch UTC (2024-01-02T05:00:00Z).
    assert first.ts == 1_704_171_600
    # token forwarded to the adapter as the Alpaca daily token.
    assert adapter.calls[0]["timeframe"] == "1Day"
    assert adapter.calls[0]["limit"] == 240


@pytest.mark.asyncio
async def test_fetch_alpaca_bars_newest_last() -> None:
    adapter = _FakeAlpacaAdapter(_raw_alpaca_bars(3))
    bars = await fetch_alpaca_bars(
        adapter,
        "AAPL",
        bar_interval="1D",
    )
    ts_seq = [b.ts for b in bars]
    assert ts_seq == sorted(ts_seq), "bars must be newest-last (ascending ts)"


@pytest.mark.asyncio
async def test_fetch_alpaca_bars_fetch_failure_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import httpx

    class _Boom:
        async def fetch_bars(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
            raise httpx.HTTPError("boom")

    with caplog.at_level("DEBUG"):
        bars = await fetch_alpaca_bars(
            _Boom(),
            "AAPL",
            bar_interval="1D",
        )
    assert bars == []


# ---------------------------------------------------------------------------
# A1 — fetch_bars_one routes venue == 'alpaca' to the normalizer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_bars_one_alpaca_branch() -> None:
    adapter = _FakeAlpacaAdapter(_raw_alpaca_bars(2))
    bars = await fetch_bars_one(
        "alpaca",
        "AAPL",
        "equity",
        alpaca_adapter=adapter,
        limit=240,
        bar_interval="1D",
    )
    assert len(bars) == 2
    assert all(b.venue == "alpaca" for b in bars)
    assert adapter.calls[0]["timeframe"] == "1Day"


@pytest.mark.asyncio
async def test_fetch_bars_one_alpaca_no_adapter_returns_empty() -> None:
    bars = await fetch_bars_one(
        "alpaca", "AAPL", "equity", bar_interval="1D",
    )
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_bars_one_alpaca_unsupported_interval_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _FakeAlpacaAdapter(_raw_alpaca_bars(2))
    with caplog.at_level("WARNING"):
        bars = await fetch_bars_one(
            "alpaca", "AAPL", "equity",
            alpaca_adapter=adapter,
            bar_interval="4h",  # not a registered Alpaca interval
        )
    assert bars == []


# ---------------------------------------------------------------------------
# End-to-end: daily ingest persists Alpaca bars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_bars_for_focus_alpaca_daily_persists(
    memdb: sqlite3.Connection,
) -> None:
    adapter = _FakeAlpacaAdapter(_raw_alpaca_bars(3))
    result = await ingest_bars_for_focus(
        memdb,
        [("alpaca", "AAPL", "equity", "equity:AAPL")],
        alpaca_adapter=adapter,
        bar_interval="1D",
    )
    assert result["bars"] == 3
    # non-1m path → no baseline mutation.
    assert result["baseline_samples"] == 0
    persisted = {
        r[0] for r in memdb.execute("SELECT DISTINCT bar_interval FROM bars").fetchall()
    }
    assert persisted == {"1D"}
    venues = {
        r[0] for r in memdb.execute("SELECT DISTINCT venue FROM bars").fetchall()
    }
    assert venues == {"alpaca"}


# ---------------------------------------------------------------------------
# Daily routing reaches alpaca via strategy metadata
# ---------------------------------------------------------------------------


def test_daily_routing_maps_1D_to_alpaca() -> None:
    """The production loop buckets strategies by metadata.timeframe with NO
    BAR_INTERVALS filter — the three equity strategies (1D/alpaca) therefore
    produce a ``timeframe_to_venues['1D'] == {'alpaca'}`` route."""
    from polaris.scripts.production_paper_loop import (
        _all_strategies,
        _strategies_by_timeframe,
    )

    by_tf = _strategies_by_timeframe(_all_strategies())
    assert "1D" in by_tf
    venues_1d = {s.metadata.venue for s in by_tf["1D"]}
    assert venues_1d == {"alpaca"}


# ---------------------------------------------------------------------------
# Behavior preservation — OKX + Capital fetch_bars_one paths byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_okx_branch_unchanged_by_alpaca_kwarg() -> None:
    captured: dict[str, Any] = {}

    async def _fake(*args: Any, **kwargs: Any) -> list[Bar]:
        captured.update(kwargs)
        return []

    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        await fetch_bars_one("okx", "BTC-USDT", "crypto", bar_interval="15m")
    assert captured["bar_interval"] == "15m"
