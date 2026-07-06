"""Quote-flush WAL-backpressure logging (observability fix).

DEMO/PAPER. Under the accepted WAL-checkpoint trade the 1 Hz quote flush can hit
``database is locked`` — EXPECTED backpressure, not a fault: ``quote_ticks`` is a
single-row LWW table, so a dropped batch only defers the latest price for those
instruments to the next flush (no integrity loss; the write must never halt the
bot — AGGRESSIVE). The old code logged every drop at ERROR+traceback ~per second,
which dominated the daily ops_alerts count and bloated the runtime log, burying
any genuine flush fault. These tests pin the corrected behaviour:

- a "database is locked/busy" drop is COUNTED and summarised at a RATE-LIMITED
  WARNING (never a per-drop ERROR+traceback);
- a GENUINE flush error (any other OperationalError) still logs at ERROR so a
  real bug is never hidden behind the expected lock drops.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import pytest

from polaris.core.data.quote_writer import QuoteTickWriter
from polaris.core.data.schema import QuoteTick

NOW = 1_900_000_000


def _qt(inst: str = "okx:BTC-USDT", venue: str = "okx") -> QuoteTick:
    return QuoteTick(
        instrument_id=inst, venue=venue, symbol=inst.split(":", 1)[-1],
        ts=NOW, bid=99.5, ask=100.5, mid=100.0, spread_bps=10.0,
        source=f"{venue}_ws",
    )


@pytest.mark.asyncio
async def test_lock_drop_is_counted_and_rate_limited(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Two consecutive lock drops → both counted, but only ONE WARNING summary
    (rate-limited) and NEVER an ERROR/traceback."""
    w = QuoteTickWriter(":memory:")

    def boom(ticks: object, inflow: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(w, "_flush_blocking", boom)
    loop = asyncio.get_running_loop()

    with caplog.at_level(logging.WARNING, logger="polaris.core.data.quote_writer"):
        w.on_quote(_qt())
        await w._flush_once(loop)
        w.on_quote(_qt())
        await w._flush_once(loop)

    assert w.lock_drops == 2
    assert w.lock_ticks_dropped == 2
    warns = [r for r in caplog.records if "WAL backpressure" in r.getMessage()]
    assert len(warns) == 1  # rate-limited to one summary within the interval
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_genuine_flush_error_still_logs_at_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A NON-lock OperationalError (a real bug) still logs at ERROR — it must not
    be swallowed into the backpressure counter."""
    w = QuoteTickWriter(":memory:")

    def boom(ticks: object, inflow: object) -> None:
        raise sqlite3.OperationalError("no such table: quote_ticks")

    monkeypatch.setattr(w, "_flush_blocking", boom)
    loop = asyncio.get_running_loop()

    with caplog.at_level(logging.ERROR, logger="polaris.core.data.quote_writer"):
        w.on_quote(_qt())
        await w._flush_once(loop)

    assert w.lock_drops == 0  # not a backpressure drop
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
