"""④ #12 technical store — write-after-compute persistence + judge evidence.

DEMO/PAPER only. Aggressive bias preserved (flow_not_block: the store is
EVIDENCE-ONLY/advisory — no entry/size/exit gated, no sizing multiplier touched).

Design = WRITE-AFTER-COMPUTE: the full technical set is EXTRACTED from the same
``MarketView`` the strategy already saw (no re-computation → no double source of
truth, zero rate-limit consumption — pure CPU + SQLite). Stored single-row LWW
(PK = instrument_id + bar_interval + indicator) so the table is bounded and never
grows unbounded (#5 retention guard). The value's currency is carried by
``computed_ts`` + ``source_bar_ts``.

Verifies:
1. ``extract_technicals_from_mv`` pulls the rich indicators (rsi/adx/bb/donchian/
   atr/ema/momentum) from a MarketView — exactly what the strategy saw.
2. ``upsert_technicals`` writes single-row LWW (a second upsert overwrites, never
   appends → bounded).
3. ``read_technicals`` round-trips the stored values with currency stamps.
4. ``technicals_summary`` renders a judge-ready dict that the evidence block reads.
5. A write into a missing table degrades to a no-op (degrade-never-crash).
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.data.technical_store import (
    extract_technicals_from_mv,
    read_technicals,
    technicals_summary,
    upsert_technicals,
)
from polaris.storage.schema import init_db
from polaris.strategies.base import BarView, MarketView


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:  # type: ignore[no-untyped-def]
    c = init_db(tmp_path / "t.sqlite")
    yield c
    c.close()


def _mv() -> MarketView:
    bars = [
        BarView(ts=1000 + i * 60, open=100.0, high=101.0, low=99.0,
                close=100.0 + i * 0.1, volume=10.0 + i)
        for i in range(60)
    ]
    return MarketView(
        symbol="BTC-USDT",
        venue="okx",
        timeframe="1H",
        bars=bars,
        last_price=106.0,
        spread_bps=5.0,
        atr_pct=0.012,
        volume_z=1.5,
        rsi_14=58.0,
        bb_lower=95.0,
        bb_upper=110.0,
        bb_middle=102.5,
        adx_14=27.0,
        momentum_20bar=0.04,
        donchian_high_40=109.0,
        donchian_low_40=98.0,
    )


def test_extract_pulls_rich_indicators() -> None:
    """The extractor reads the SAME numbers the strategy saw (no recompute)."""
    out = extract_technicals_from_mv(_mv())
    assert out["rsi_14"] == 58.0
    assert out["adx_14"] == 27.0
    assert out["atr_pct"] == 0.012
    assert out["bb_upper"] == 110.0
    assert out["donchian_high_40"] == 109.0
    assert out["momentum_20bar"] == 0.04
    # None-valued fields (ma_200 absent) are dropped, not stored as NULL noise.
    assert "ma_200" not in out


def test_upsert_is_single_row_lww(conn: sqlite3.Connection) -> None:
    """A second upsert OVERWRITES the same (instrument,interval,indicator) row.

    Bounded growth (#5): the table size is instruments × intervals × indicators,
    never an unbounded time series.
    """
    upsert_technicals(
        conn, instrument_id="okx:BTC-USDT", bar_interval="1H",
        values={"rsi_14": 58.0, "adx_14": 27.0},
        computed_ts=2000, source_bar_ts=1900,
    )
    upsert_technicals(
        conn, instrument_id="okx:BTC-USDT", bar_interval="1H",
        values={"rsi_14": 61.0, "adx_14": 30.0},
        computed_ts=2100, source_bar_ts=2050,
    )
    rows = conn.execute(
        "SELECT COUNT(*) FROM ticker_technicals "
        "WHERE instrument_id='okx:BTC-USDT' AND bar_interval='1H'"
    ).fetchone()
    assert rows[0] == 2  # 2 indicators, NOT 4 (LWW, no append)
    stored = read_technicals(conn, instrument_id="okx:BTC-USDT", bar_interval="1H")
    assert stored["rsi_14"]["value"] == 61.0
    assert stored["rsi_14"]["computed_ts"] == 2100
    assert stored["rsi_14"]["source_bar_ts"] == 2050


def test_read_missing_returns_empty(conn: sqlite3.Connection) -> None:
    """A read for an un-stored instrument returns {} (graceful judge n/a)."""
    assert read_technicals(conn, instrument_id="okx:NOPE", bar_interval="1H") == {}


def test_technicals_summary_judge_ready(conn: sqlite3.Connection) -> None:
    """The summary renders a compact judge-ready dict (value + age stamps)."""
    upsert_technicals(
        conn, instrument_id="okx:BTC-USDT", bar_interval="1H",
        values={"rsi_14": 58.0, "adx_14": 27.0, "donchian_high_40": 109.0},
        computed_ts=2000, source_bar_ts=1900,
    )
    summary = technicals_summary(
        conn, instrument_id="okx:BTC-USDT", bar_interval="1H"
    )
    assert summary["rsi_14"] == 58.0
    assert summary["adx_14"] == 27.0
    assert summary["donchian_high_40"] == 109.0
    assert summary["source_bar_ts"] == 1900


def test_upsert_missing_table_degrades(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A write against a DB with no technicals table is a no-op, never raises."""
    raw = sqlite3.connect(tmp_path / "bare.sqlite")
    # No init_db → table absent. degrade-never-crash: returns 0, no exception.
    n = upsert_technicals(
        raw, instrument_id="okx:BTC-USDT", bar_interval="1H",
        values={"rsi_14": 58.0}, computed_ts=2000, source_bar_ts=1900,
    )
    assert n == 0
    raw.close()


def test_extract_writes_full_set_round_trip(conn: sqlite3.Connection) -> None:
    """End-to-end: extract from a MarketView → upsert → read returns the set."""
    mv = _mv()
    values = extract_technicals_from_mv(mv)
    upsert_technicals(
        conn, instrument_id="okx:BTC-USDT", bar_interval=mv.timeframe,
        values=values, computed_ts=5000, source_bar_ts=mv.bars[-1].ts,
    )
    stored = read_technicals(
        conn, instrument_id="okx:BTC-USDT", bar_interval=mv.timeframe
    )
    assert stored["rsi_14"]["value"] == 58.0
    assert stored["adx_14"]["value"] == 27.0
    assert stored["bb_upper"]["value"] == 110.0
