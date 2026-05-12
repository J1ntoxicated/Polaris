"""Layer 1 — Bar/tick ingest tests (OKX + Capital + baseline samples).

Spec source:
- vault/30_components/layer-1-canonical-baseline.md (Q1 + Q2)
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.data.ingest import (
    BAR_BASELINE_METRICS,
    ingest_bars,
    persist_bars,
    update_baseline_from_bars,
)
from polaris.core.data.schema import Bar
from polaris.venues.capital.adapter import capital_price_row_to_bar
from polaris.venues.okx.adapter import fetch_okx_bars

NOW = 1_780_000_000


def _bar(symbol: str = "BTC-USDT", ts: int = NOW, *, venue: str = "okx") -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id="crypto:BTC",
        venue=venue,
        symbol=symbol,
        bar_interval="1m",
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        notional_usd=1005.0,
        trade_count=20,
    )


def test_persist_bars_idempotent(memdb: sqlite3.Connection) -> None:
    bars = [_bar(ts=NOW + i * 60) for i in range(5)]
    n1 = persist_bars(memdb, bars)
    n2 = persist_bars(memdb, bars)  # second run — same PK collisions ok.
    assert n1 == 5
    assert n2 == 5
    rows = memdb.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert rows == 5


def test_baseline_sample_update_every_closed_bar(memdb: sqlite3.Connection) -> None:
    bars = [_bar(ts=NOW + i * 60) for i in range(10)]
    persist_bars(memdb, bars)
    n_samples = update_baseline_from_bars(memdb, bars, asset_class="crypto")
    # 3 metrics (atr/size/volume) × 10 bars
    assert n_samples == 3 * 10
    # baseline state should be populated for each metric.
    states = memdb.execute(
        "SELECT metric, sample_count FROM ticker_baseline_state WHERE instrument_id=?",
        ("okx:BTC-USDT",),
    ).fetchall()
    metrics = {m for m, _ in states}
    for m in BAR_BASELINE_METRICS:
        assert m in metrics


def test_ingest_bars_combined(memdb: sqlite3.Connection) -> None:
    bars = [_bar(ts=NOW + i * 60) for i in range(3)]
    out = ingest_bars(memdb, bars, asset_class="crypto")
    assert out["bars"] == 3
    assert out["baseline_samples"] == 9


def test_capital_bar_parse_round_trip() -> None:
    row = {
        "snapshotTimeUTC": "2026-05-07T03:00:00",
        "openPrice": {"bid": 100.0, "ask": 100.2},
        "highPrice": {"bid": 101.0, "ask": 101.2},
        "lowPrice": {"bid": 99.0, "ask": 99.2},
        "closePrice": {"bid": 100.5, "ask": 100.7},
        "lastTradedVolume": 50.0,
    }
    bar = capital_price_row_to_bar(
        row, epic="EURUSD", bar_interval="1m", underlying_group_id="forex:EURUSD"
    )
    assert bar is not None
    assert bar.venue == "capital"
    assert bar.symbol == "EURUSD"
    assert bar.bar_interval == "1m"
    # mid of close = (100.5 + 100.7) / 2 = 100.6
    assert bar.close == pytest.approx(100.6)
    assert bar.volume == 50.0


def test_capital_bar_missing_close_returns_none() -> None:
    row = {
        "snapshotTimeUTC": "2026-05-07T03:00:00",
        "openPrice": {"bid": 100.0, "ask": 100.2},
        # no closePrice
    }
    bar = capital_price_row_to_bar(
        row, epic="EURUSD", bar_interval="1m", underlying_group_id="forex:EURUSD"
    )
    assert bar is None


def test_capital_bar_unsupported_interval_returns_none() -> None:
    row = {
        "snapshotTimeUTC": "2026-05-07T03:00:00",
        "openPrice": {"bid": 100.0, "ask": 100.2},
        "highPrice": {"bid": 101.0, "ask": 101.2},
        "lowPrice": {"bid": 99.0, "ask": 99.2},
        "closePrice": {"bid": 100.5, "ask": 100.7},
    }
    bar = capital_price_row_to_bar(
        row, epic="EURUSD", bar_interval="2m", underlying_group_id="forex:EURUSD"
    )
    assert bar is None


# ---------------------------------------------------------------------------
# OKX adapter — mock httpx network, exercise fetch_okx_bars
# ---------------------------------------------------------------------------


class _MockResponse:
    def __init__(self, body: dict) -> None:
        self._body = body
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _MockClient:
    def __init__(self, body: dict) -> None:
        self._body = body
        self.calls: list[dict] = []

    async def get(self, path: str, **kwargs):  # noqa: ANN001
        self.calls.append({"path": path, **kwargs})
        return _MockResponse(self._body)

    async def aclose(self) -> None:  # pragma: no cover
        return None


async def test_okx_bar_fetch_persist(memdb: sqlite3.Connection) -> None:
    body = {
        "code": "0",
        "msg": "",
        "data": [
            # ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm
            [str(int((NOW - i * 60) * 1000)), "100.0", "101.0", "99.0", "100.5",
             "10.0", "10.0", "1005.0", "1"]
            for i in range(5)
        ],
    }
    client = _MockClient(body)
    bars = await fetch_okx_bars("BTC-USDT", limit=5, client=client)  # type: ignore[arg-type]
    assert len(bars) == 5
    assert all(b.venue == "okx" for b in bars)
    n = persist_bars(memdb, bars)
    assert n == 5
    row = memdb.execute("SELECT instrument_id, bar_interval FROM bars LIMIT 1").fetchone()
    assert row[0] == "okx:BTC-USDT"
    assert row[1] == "1m"


async def test_okx_bar_fetch_error_code_raises() -> None:
    body = {"code": "51000", "msg": "Parameter error", "data": []}
    client = _MockClient(body)
    with pytest.raises(RuntimeError):
        await fetch_okx_bars("BTC-USDT", limit=5, client=client)  # type: ignore[arg-type]
