"""TDD — cadence phase-offset stagger (forensic wf_1f586d0a tick-body fix #2).

The per-timeframe bar-fetch cadences share a common multiple (5m=30s,
15m=60s, 1H=300s — LCM 300s), so at every LCM boundary all three buckets
become "due" on the SAME epoch tick, stacking their DB writes into one
instant and contributing to write-lock contention. A per-timeframe phase
offset (5m:+0 / 15m:+10 / 1H:+20s), checked via epoch-mod, spreads the
due-instants apart WITHOUT changing any fetch frequency — flow_not_block,
not a throttle: every timeframe still fires exactly once per its own
cadence, just at a different phase within the period.

DEMO/PAPER only — data-layer scheduling change, no entry/size/exit touched.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import patch

import pytest

from polaris.core.data.schema import Bar
from polaris.scripts._production_bars import (
    TIMEFRAME_FETCH_CADENCE_SEC,
    TIMEFRAME_FETCH_PHASE_OFFSET_SEC,
    ingest_bars_per_timeframe,
    is_timeframe_due_epoch,
)


def test_phase_offset_table_matches_forensic_spec() -> None:
    """5m:+0 (reference/untouched) / 15m:+10 / 1H:+20 per the forensic brief."""
    assert TIMEFRAME_FETCH_PHASE_OFFSET_SEC.get("5m", 0.0) == 0.0
    assert TIMEFRAME_FETCH_PHASE_OFFSET_SEC.get("15m", 0.0) == 10.0
    assert TIMEFRAME_FETCH_PHASE_OFFSET_SEC.get("1H", 0.0) == 20.0


def test_due_epochs_never_collide_across_5m_15m_1h() -> None:
    """Over a full LCM(30, 60, 300) = 300s period, at most ONE of {5m, 15m,
    1H} is ever due at any given epoch — the exact collision this fix
    eliminates."""
    lcm = 300
    for epoch in range(0, lcm * 3):  # 3 full periods, catch any edge wraparound
        due = [tf for tf in ("5m", "15m", "1H") if is_timeframe_due_epoch(tf, epoch)]
        assert len(due) <= 1, f"epoch={epoch} simultaneously due: {due}"


def test_each_timeframe_still_fires_at_its_own_cadence_frequency() -> None:
    """Frequency is UNCHANGED (not a throttle) — each tf is due exactly once
    per its own cadence period, over a full LCM window."""
    lcm = 300
    for tf in ("5m", "15m", "1H"):
        cadence = int(TIMEFRAME_FETCH_CADENCE_SEC[tf])
        due_count = sum(
            1 for epoch in range(lcm) if is_timeframe_due_epoch(tf, epoch)
        )
        assert due_count == lcm // cadence, (
            f"{tf}: expected {lcm // cadence} due epochs in one LCM window, "
            f"got {due_count} — frequency must stay identical, only phase shifts"
        )


def test_unmapped_timeframe_defaults_to_zero_offset() -> None:
    """A timeframe absent from the offset table (1m/4H/1D) behaves exactly
    like offset=0 — no accidental stagger for buckets the forensic fix
    doesn't target."""
    assert TIMEFRAME_FETCH_PHASE_OFFSET_SEC.get("1m", 0.0) == 0.0
    # offset=0 means due iff epoch % cadence == 0 (same as un-staggered).
    cadence = int(TIMEFRAME_FETCH_CADENCE_SEC["1m"])
    for epoch in range(0, cadence * 3):
        assert is_timeframe_due_epoch("1m", epoch) == (epoch % cadence == 0)


# ---------------------------------------------------------------------------
# Wiring — ingest_bars_per_timeframe's opt-in ``now_epoch`` bootstrap gate.
# ---------------------------------------------------------------------------


def _bar(*, ts: int, symbol: str = "BTC-USDT") -> Bar:
    return Bar(
        instrument_id=f"okx:{symbol}", underlying_group_id="crypto:BTC",
        venue="okx", symbol=symbol, bar_interval="1H", ts=ts,
        open=100.0, high=101.0, low=99.0, close=100.5,
        volume=10.0, notional_usd=1005.0, trade_count=20,
    )


@pytest.fixture(autouse=True)
def _no_yahoo() -> Any:
    import polaris.scripts._production_bars as pbars

    async def _empty(*_a: Any, **_k: Any) -> list[Bar]:
        return []

    with patch.object(pbars, "fetch_yahoo_bars", new=_empty):
        yield


@pytest.mark.asyncio
async def test_now_epoch_omitted_keeps_legacy_immediate_first_fetch(
    memdb: sqlite3.Connection,
) -> None:
    """Regression guard: existing callers that don't pass ``now_epoch`` (every
    pinned cadence test in test_production_paper_loop_timeframe.py) keep the
    old byte-identical behaviour — a first-ever fetch fires immediately."""

    async def _fake(*_a: Any, **_k: Any) -> list[Bar]:
        return [_bar(ts=1_700_000_000)]

    last_fetch: dict[str, float] = {}
    bars_by_tf: dict[str, int] = {}
    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        await ingest_bars_per_timeframe(
            memdb, [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            timeframe_to_venues={"1H": {"okx"}},
            last_fetch_monotonic_by_tf=last_fetch,
            bars_persisted_by_tf=bars_by_tf,
            now_mono=100.0,  # now_epoch NOT passed
        )
    assert last_fetch.get("1H:okx") == 100.0


@pytest.mark.asyncio
async def test_now_epoch_defers_first_fetch_until_its_due_slot(
    memdb: sqlite3.Connection,
) -> None:
    """1H (offset 20, cadence 300) at an off-slot epoch defers the cold-start
    fetch; the SAME call at its due epoch fires."""

    async def _fake(*_a: Any, **_k: Any) -> list[Bar]:
        return [_bar(ts=1_700_000_000)]

    last_fetch: dict[str, float] = {}
    bars_by_tf: dict[str, int] = {}
    off_slot_epoch = 21  # (21 - 20) % 300 = 1 != 0 -> not due
    assert not is_timeframe_due_epoch("1H", off_slot_epoch)
    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        await ingest_bars_per_timeframe(
            memdb, [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            timeframe_to_venues={"1H": {"okx"}},
            last_fetch_monotonic_by_tf=last_fetch,
            bars_persisted_by_tf=bars_by_tf,
            now_mono=100.0, now_epoch=off_slot_epoch,
        )
    assert "1H:okx" not in last_fetch, (
        "an off-slot epoch must defer the cold-start fetch (retryable next tick)"
    )

    due_epoch = 20  # (20 - 20) % 300 == 0 -> due
    assert is_timeframe_due_epoch("1H", due_epoch)
    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        await ingest_bars_per_timeframe(
            memdb, [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            timeframe_to_venues={"1H": {"okx"}},
            last_fetch_monotonic_by_tf=last_fetch,
            bars_persisted_by_tf=bars_by_tf,
            now_mono=200.0, now_epoch=due_epoch,
        )
    assert last_fetch.get("1H:okx") == 200.0


@pytest.mark.asyncio
async def test_now_epoch_only_gates_the_cold_start_not_steady_state(
    memdb: sqlite3.Connection,
) -> None:
    """Once a (timeframe, venue) key has fired once, subsequent ticks are
    governed ONLY by the normal since-last-fetch cadence — an off-slot
    epoch on a LATER tick must not re-defer an already-warm key."""

    async def _fake(*_a: Any, **_k: Any) -> list[Bar]:
        return [_bar(ts=1_700_000_000)]

    last_fetch: dict[str, float] = {"1H:okx": 0.0}  # already warm
    bars_by_tf: dict[str, int] = {}
    off_slot_epoch = 99
    assert not is_timeframe_due_epoch("1H", off_slot_epoch)
    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        await ingest_bars_per_timeframe(
            memdb, [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            timeframe_to_venues={"1H": {"okx"}},
            last_fetch_monotonic_by_tf=last_fetch,
            bars_persisted_by_tf=bars_by_tf,
            now_mono=1000.0, now_epoch=off_slot_epoch,  # mono - 0 = 1000 >= 300 cadence
        )
    assert last_fetch.get("1H:okx") == 1000.0, (
        "a warm key must fire on the normal since-last-fetch cadence "
        "regardless of the epoch-mod slot"
    )


def test_absent_timeframe_is_due_at_any_epoch() -> None:
    """Review MAJOR fix: 4H/1D (absent from the offset table) must be due at
    EVERY epoch — their 3600s cadence would otherwise defer the first
    post-restart fetch up to ~1h (daily-bar starvation class). 1m/5m stay
    gated (max defer 5s/30s, negligible)."""
    from polaris.scripts._production_bars import is_timeframe_due_epoch

    for tf in ("4H", "1D"):
        for epoch in (1, 1783660112, 3599, 3600, 86399):
            assert is_timeframe_due_epoch(tf, epoch), (tf, epoch)
