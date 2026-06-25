"""Tests for the per-symbol activation accumulator (#39 Capital activation).

DEMO/PAPER. The candidate sweep needs PER-SYMBOL live motion (tick rate / short
move / intraday range) to surface active majors (EURUSD 9s tick, US100) over
calm wide-daily agriculturals. ``quote_ticks`` is single-row LWW (no per-symbol
history) and ``tick_inflow`` is per-VENUE (identical micro pulse for every Capital
symbol). So the writer carries a NEW bounded per-symbol accumulator.

🚨 ABSOLUTE invariants these tests pin (debate-converged spec):
- IN-MEMORY ONLY — the accumulator NEVER writes to disk (the flush SQL is the
  quote rows + per-venue tick_inflow ONLY; the per-symbol accumulator adds no SQL).
- bounded per SYMBOL — buckets older than the 600s window are pruned (no growth
  in time); ``ticks_600s`` reflects only the trailing window.
- bounded per MAP — distinct-instrument growth is capped (LRU-by-last_ts
  eviction) so a long run can never grow the map without bound.
- keyed on ``instrument_id`` — the SAME symbol on two venues is two distinct
  accumulators (no cross-venue collision).
"""

from __future__ import annotations

import asyncio
import sqlite3

from polaris.core.data.quote_writer import (
    ACTIVATION_MAX_SYMBOLS,
    QuoteTickWriter,
)
from polaris.core.data.schema import QuoteTick
from polaris.storage.schema import init_db

NOW = 1_900_000_000


def _qt(
    inst: str, venue: str = "okx", *, ts: int = NOW, mid: float = 100.0,
    spread_bps: float = 10.0,
) -> QuoteTick:
    return QuoteTick(
        instrument_id=inst,
        venue=venue,
        symbol=inst.split(":", 1)[-1],
        ts=ts,
        bid=mid - 0.5,
        ask=mid + 0.5,
        mid=mid,
        spread_bps=spread_bps,
        source=f"{venue}_ws",
    )


# ---------------------------------------------------------------------------
# Metrics — ticks_600s / short_move / intraday_range computed per symbol.
# ---------------------------------------------------------------------------


def test_activation_metrics_tick_rate_counts_window() -> None:
    """``ticks_600s`` counts ticks within the trailing 600s window."""
    w = QuoteTickWriter(":memory:")
    for i in range(5):
        w.on_quote(_qt("okx:BTC-USDT", ts=NOW + i, mid=100.0 + i))
    m = w.activation_metrics("okx:BTC-USDT")
    assert m is not None
    assert m["ticks_600s"] == 5
    assert m["last_mid"] == 104.0
    assert m["last_ts"] == NOW + 4


def test_activation_metrics_none_for_unseen_symbol() -> None:
    """An instrument with no ticks yet → None (caller degrades to neutral)."""
    w = QuoteTickWriter(":memory:")
    assert w.activation_metrics("okx:NEVER-USDT") is None


def test_activation_metrics_intraday_range_high_low() -> None:
    """``mid_high_600s`` / ``mid_low_600s`` track the rolling window extremes."""
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt("okx:ETH-USDT", ts=NOW, mid=100.0))
    w.on_quote(_qt("okx:ETH-USDT", ts=NOW + 1, mid=108.0))
    w.on_quote(_qt("okx:ETH-USDT", ts=NOW + 2, mid=95.0))
    w.on_quote(_qt("okx:ETH-USDT", ts=NOW + 3, mid=101.0))
    m = w.activation_metrics("okx:ETH-USDT")
    assert m is not None
    assert m["mid_high_600s"] == 108.0
    assert m["mid_low_600s"] == 95.0


def test_activation_metrics_short_move_reference() -> None:
    """``mid_120s_ago`` references a mid ~120s before the latest tick (short move)."""
    w = QuoteTickWriter(":memory:")
    # A tick 130s ago at 100, then a recent tick at 110 → short move references the
    # older mid so |last - ref| is a real recent displacement.
    w.on_quote(_qt("okx:SOL-USDT", ts=NOW - 130, mid=100.0))
    w.on_quote(_qt("okx:SOL-USDT", ts=NOW, mid=110.0))
    m = w.activation_metrics("okx:SOL-USDT")
    assert m is not None
    assert m["mid_120s_ago"] == 100.0
    assert m["last_mid"] == 110.0


# ---------------------------------------------------------------------------
# Bounded — buckets pruned to 600s; the map is LRU-capped (no infinite growth).
# ---------------------------------------------------------------------------


def test_activation_buckets_prune_outside_600s() -> None:
    """A tick older than 600s before the latest does NOT count in ticks_600s."""
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt("okx:BTC-USDT", ts=NOW, mid=100.0))
    w.on_quote(_qt("okx:BTC-USDT", ts=NOW + 700, mid=101.0))  # 700s later → old drops
    m = w.activation_metrics("okx:BTC-USDT")
    assert m is not None
    assert m["ticks_600s"] == 1  # only the recent tick survives the window prune


def test_activation_map_lru_capped_no_infinite_growth() -> None:
    """Distinct-instrument growth is bounded — the map never exceeds the cap.

    Feeding far more distinct instruments than ``ACTIVATION_MAX_SYMBOLS`` must
    evict the oldest (by last_ts) — the in-mem state is bounded for a long run
    (retention guard / no infinite growth, the debate's hard requirement).
    """
    w = QuoteTickWriter(":memory:")
    n = ACTIVATION_MAX_SYMBOLS + 50
    for i in range(n):
        w.on_quote(_qt(f"okx:SYM{i}-USDT", ts=NOW + i, mid=100.0))
    assert len(w._activation) <= ACTIVATION_MAX_SYMBOLS
    # The OLDEST instruments are evicted; the newest are retained.
    assert w.activation_metrics(f"okx:SYM{n - 1}-USDT") is not None
    assert w.activation_metrics("okx:SYM0-USDT") is None


# ---------------------------------------------------------------------------
# instrument_id keyed — cross-venue collision safety.
# ---------------------------------------------------------------------------


def test_activation_keyed_on_instrument_id_no_cross_venue_collision() -> None:
    """The same symbol on two venues is two distinct accumulators."""
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt("okx:BTC-USDT", venue="okx", ts=NOW, mid=100.0))
    w.on_quote(_qt("okx:BTC-USDT", venue="okx", ts=NOW + 1, mid=101.0))
    w.on_quote(_qt("capital:BTC-USDT", venue="capital", ts=NOW, mid=50.0))
    okx = w.activation_metrics("okx:BTC-USDT")
    cap = w.activation_metrics("capital:BTC-USDT")
    assert okx is not None and cap is not None
    assert okx["ticks_600s"] == 2
    assert cap["ticks_600s"] == 1  # the capital row did not absorb the okx ticks
    assert okx["last_mid"] == 101.0
    assert cap["last_mid"] == 50.0


# ---------------------------------------------------------------------------
# Disk-safe — the accumulator NEVER writes to disk (no per-symbol SQL).
# ---------------------------------------------------------------------------


async def test_activation_accumulator_writes_no_disk_rows(tmp_path) -> None:
    """The accumulator is pure in-mem — a flush persists ONLY quote_ticks +
    tick_inflow, never a per-symbol activation table/row."""
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    for i in range(10):
        w.on_quote(_qt("okx:BTC-USDT", ts=NOW + i, mid=100.0 + i))

    await w._flush_once(asyncio.get_running_loop())
    w.close()

    conn = sqlite3.connect(db)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    # No activation/accumulator table was created — it lives only in memory.
    assert not any("activation" in t.lower() for t in tables)
    # The accumulator still holds the per-symbol state in memory after the flush.
    assert w.activation_metrics("okx:BTC-USDT") is not None
