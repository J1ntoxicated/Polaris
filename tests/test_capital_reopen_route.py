"""Capital market-closed reject → delay-route consumer (DEMO/PAPER paper bot).

``seconds_to_next_open`` (opening_hours.py) was a library/primitive that
computed the right countdown but had ZERO production consumers — a Capital
open reject during a closed window (SG25-style '16/16 reject') just kept
re-submitting and re-rejecting every tick with no delay-route. This module
stamps the reject with the computed next-open instant (reusing the
reject-anchor pattern — no new table-family invented) and gates the NEXT
submit attempt on that key until the stamped instant has passed.

flow_not_block: this is a DEFERRAL (never a permanent block) — the SAME
signal resumes submitting the instant the window opens; nothing is skipped
forever, no size/strength is touched. DEMO/PAPER only.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from unittest.mock import AsyncMock

import pytest

from polaris.storage.schema import ALL_DDL
from polaris.venues.capital.constraint_translator import CapitalMarketConstraint
from polaris.venues.capital.reopen_route import (
    clear_reopen_stamp,
    is_market_closed_reject,
    maybe_stamp_delayed_reopen,
    submit_suppressed_until_reopen,
)


def _ts(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.UTC).timestamp())


NOW = _ts(2026, 6, 22, 8, 0)  # Monday 08:00 UTC


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        c.execute(stmt)
    c.commit()
    return c


def _constraint(**overrides: object) -> CapitalMarketConstraint:
    base: dict[str, object] = {
        "epic": "SG25", "instrument_type": "INDICES", "min_deal_size": 0.1,
        "step_size": 0.1, "decimal_places": 1, "leverage": 20.0,
        "pip_value_usd": 0.0, "base_ccy": "", "quote_ccy": "USD",
        "opening_hours": None,
    }
    base.update(overrides)
    return CapitalMarketConstraint(**base)  # type: ignore[arg-type]


# A week that is CLOSED at NOW but opens NOW+3600 (Mon 09:00-...): index-style.
_CLOSED_NOW_OPENS_IN_1H: tuple[tuple[tuple[int, int], ...], ...] = (
    ((9 * 3600, 21 * 3600),),  # Mon
    (), (), (), (), (), (),
)


# ---------------------------------------------------------------------------
# (a) reject-code scoping — ONLY market-closed family triggers the delay-route
# ---------------------------------------------------------------------------


def test_market_closed_code_is_recognized() -> None:
    assert is_market_closed_reject("MARKET_CLOSED")
    assert is_market_closed_reject("MARKET_OFFLINE")


def test_non_market_closed_codes_are_not_recognized() -> None:
    """A compliance / generic reject must NEVER trigger the delay-route —
    retrying at 'next open' would not fix a compliance/size reject and could
    misfire the mechanism onto an unrelated failure class."""
    assert not is_market_closed_reject("INSTRUMENT_NOT_TRADEABLE")
    assert not is_market_closed_reject("REJECTED")
    assert not is_market_closed_reject(None)
    assert not is_market_closed_reject("MINIMUM_DEAL_SIZE")


# ---------------------------------------------------------------------------
# (b) stamping — computes + persists the next-open instant, best-effort
# ---------------------------------------------------------------------------


def test_maybe_stamp_delayed_reopen_writes_row_for_market_closed() -> None:
    conn = _conn()
    constraint = _constraint(opening_hours=_CLOSED_NOW_OPENS_IN_1H)
    stamped = maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="session_breakout",
        side="long", reject_code="MARKET_CLOSED", constraint=constraint,
        now_ts=NOW,
    )
    assert stamped is True
    row = conn.execute(
        "SELECT reopen_at_ts FROM capital_reopen_pending WHERE venue='capital' "
        "AND symbol='SG25' AND strategy_id='session_breakout' AND side='long'"
    ).fetchone()
    assert row is not None
    assert row[0] == NOW + 3600
    conn.close()


def test_maybe_stamp_skips_non_market_closed_reject() -> None:
    conn = _conn()
    constraint = _constraint(opening_hours=_CLOSED_NOW_OPENS_IN_1H)
    stamped = maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="INSTRUMENT_NOT_TRADEABLE", constraint=constraint, now_ts=NOW,
    )
    assert stamped is False
    assert conn.execute(
        "SELECT COUNT(*) FROM capital_reopen_pending"
    ).fetchone()[0] == 0
    conn.close()


def test_maybe_stamp_no_opening_hours_degrades_to_noop() -> None:
    """No parsed table (None) → nothing to compute from — never fabricates a
    countdown (degrade, don't guess)."""
    conn = _conn()
    constraint = _constraint(opening_hours=None)
    stamped = maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW,
    )
    assert stamped is False
    conn.close()


def test_maybe_stamp_fx_instrument_type_never_stamps() -> None:
    """FX is 24/5-exempt by type — seconds_to_next_open returns None for it,
    so the stamp must be a no-op (no fabricated forever-block)."""
    conn = _conn()
    constraint = _constraint(
        instrument_type="CURRENCIES", opening_hours=_CLOSED_NOW_OPENS_IN_1H,
    )
    stamped = maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="EURUSD", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW,
    )
    assert stamped is False
    conn.close()


def test_maybe_stamp_already_inside_open_window_is_noop() -> None:
    """seconds_to_next_open returns None while ALREADY open — nothing to
    route (the reject must have another cause; don't stamp)."""
    conn = _conn()
    always_open: tuple[tuple[tuple[int, int], ...], ...] = (
        ((0, 86400),), ((0, 86400),), ((0, 86400),), ((0, 86400),),
        ((0, 86400),), ((0, 86400),), ((0, 86400),),
    )
    constraint = _constraint(opening_hours=always_open)
    stamped = maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW,
    )
    assert stamped is False
    conn.close()


def test_maybe_stamp_is_idempotent_upsert() -> None:
    """A second reject on the same key updates (not duplicates) the row."""
    conn = _conn()
    constraint = _constraint(opening_hours=_CLOSED_NOW_OPENS_IN_1H)
    maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW,
    )
    maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW + 10,
    )
    n = conn.execute(
        "SELECT COUNT(*) FROM capital_reopen_pending"
    ).fetchone()[0]
    assert n == 1
    conn.close()


# ---------------------------------------------------------------------------
# (c) submit-gate — suppress until the stamped instant, then clear on resume
# ---------------------------------------------------------------------------


def test_submit_suppressed_while_before_reopen_instant() -> None:
    conn = _conn()
    constraint = _constraint(opening_hours=_CLOSED_NOW_OPENS_IN_1H)
    maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW,
    )
    assert submit_suppressed_until_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        now_ts=NOW + 1800,  # halfway to the 1h reopen
    )
    conn.close()


def test_submit_not_suppressed_once_reopen_instant_has_passed() -> None:
    conn = _conn()
    constraint = _constraint(opening_hours=_CLOSED_NOW_OPENS_IN_1H)
    maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW,
    )
    assert not submit_suppressed_until_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        now_ts=NOW + 3601,
    )
    conn.close()


def test_submit_not_suppressed_when_no_stamp_exists() -> None:
    conn = _conn()
    assert not submit_suppressed_until_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        now_ts=NOW,
    )
    conn.close()


def test_clear_reopen_stamp_removes_row() -> None:
    conn = _conn()
    constraint = _constraint(opening_hours=_CLOSED_NOW_OPENS_IN_1H)
    maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW,
    )
    clear_reopen_stamp(conn, venue="capital", symbol="SG25", strategy_id="s1", side="long")
    assert conn.execute(
        "SELECT COUNT(*) FROM capital_reopen_pending"
    ).fetchone()[0] == 0
    conn.close()


def test_different_key_not_suppressed_by_unrelated_stamp() -> None:
    """A stamp on one (venue, symbol, strategy_id, side) must never suppress a
    DIFFERENT key — no cross-symbol/cross-strategy aliasing."""
    conn = _conn()
    constraint = _constraint(opening_hours=_CLOSED_NOW_OPENS_IN_1H)
    maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="long",
        reject_code="MARKET_CLOSED", constraint=constraint, now_ts=NOW,
    )
    assert not submit_suppressed_until_reopen(
        conn, venue="capital", symbol="US500", strategy_id="s1", side="long",
        now_ts=NOW + 10,
    )
    assert not submit_suppressed_until_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="s1", side="short",
        now_ts=NOW + 10,
    )
    conn.close()


# ---------------------------------------------------------------------------
# (d) SG25 16/16-reject regression fixture — realistic weekend-window replay
# ---------------------------------------------------------------------------


def test_sg25_style_weekend_reject_never_churns_after_first_stamp() -> None:
    """Simulates the SG25 '16/16 reject' incident: the first reject stamps
    the delay-route; every subsequent tick during the closed window is
    suppressed (no repeat venue reject spam); the tick AT/AFTER the reopen
    instant is allowed through again exactly once (flow resumes)."""
    conn = _conn()
    sat_closed_sun_2200_open: tuple[tuple[tuple[int, int], ...], ...] = (
        (), (), (), (), (), (), ((22 * 3600, 24 * 3600),),  # Sun 22:00-24:00
    )
    constraint = _constraint(opening_hours=sat_closed_sun_2200_open)

    stamped = maybe_stamp_delayed_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="session_breakout",
        side="long", reject_code="MARKET_CLOSED", constraint=constraint,
        now_ts=NOW,
    )
    assert stamped is True
    reopen_ts = conn.execute(
        "SELECT reopen_at_ts FROM capital_reopen_pending WHERE symbol='SG25'"
    ).fetchone()[0]

    # 15 more ticks across the closed window — every one suppressed.
    for i in range(1, 16):
        probe_ts = NOW + i * 60
        if probe_ts >= reopen_ts:
            break
        assert submit_suppressed_until_reopen(
            conn, venue="capital", symbol="SG25",
            strategy_id="session_breakout", side="long", now_ts=probe_ts,
        )

    # At/after reopen: no longer suppressed.
    assert not submit_suppressed_until_reopen(
        conn, venue="capital", symbol="SG25", strategy_id="session_breakout",
        side="long", now_ts=reopen_ts,
    )
    conn.close()


# ---------------------------------------------------------------------------
# (e) live firing wire — reserve_and_submit actually consults the stamp
# before submitting a fresh Capital order (registered != fired otherwise)
# ---------------------------------------------------------------------------


def _sig(signal_id: str, symbol: str, strategy_id: str = "session_breakout") -> object:
    from polaris.strategies.base import RawSignal

    return RawSignal(
        signal_id=signal_id, strategy_id=strategy_id, symbol=symbol,
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="cfd_intraday_event",
    )


@pytest.mark.asyncio
async def test_reserve_and_submit_skips_when_reopen_suppressed(
    memdb: sqlite3.Connection,
) -> None:
    """A stamped, still-suppressed Capital key must be skipped BEFORE any
    fence reservation / venue submit — mirrors the blocklist skip gate."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import reserve_and_submit

    reset_process_fence()
    from polaris.scripts.production_paper_loop import ProdLoopState

    reopen_at_ts = int(time.time()) + 3600
    memdb.execute(
        "INSERT INTO capital_reopen_pending "
        "(venue, symbol, strategy_id, side, reopen_at_ts, created_ts) "
        "VALUES ('capital', 'SG25', 'session_breakout', 'long', ?, ?)",
        (reopen_at_ts, int(time.time())),
    )
    memdb.commit()
    state = ProdLoopState()
    session = AsyncMock()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("s1", "SG25"), venue="capital",
        symbol="SG25", asset_class="index", underlying_group_id="cfd:XAU_INDICES",
        notional_usd=100.0, last_price=8000.0, now_ts=int(time.time()),
        real_roundtrip=True, capital_session=session,
    )
    assert trade is None
    session.request.assert_not_awaited()
    res = memdb.execute(
        "SELECT COUNT(*) FROM allocator_reservations WHERE strategy_id='session_breakout'"
    ).fetchone()[0]
    assert int(res) == 0
    assert state.fault_events == 0


@pytest.mark.asyncio
async def test_reserve_and_submit_proceeds_once_reopen_instant_passed(
    memdb: sqlite3.Connection,
) -> None:
    """Once the stamped instant has passed, the SAME key must submit
    normally again (flow resumes — deferral, not a permanent block)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import reserve_and_submit

    reset_process_fence()
    from polaris.scripts.production_paper_loop import ProdLoopState

    past_reopen_ts = int(time.time()) - 10
    memdb.execute(
        "INSERT INTO capital_reopen_pending "
        "(venue, symbol, strategy_id, side, reopen_at_ts, created_ts) "
        "VALUES ('capital', 'SG25', 'session_breakout', 'long', ?, ?)",
        (past_reopen_ts, int(time.time())),
    )
    memdb.commit()
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("s2", "SG25"), venue="capital",
        symbol="SG25", asset_class="index", underlying_group_id="cfd:XAU_INDICES",
        notional_usd=100.0, last_price=8000.0, now_ts=int(time.time()),
        real_roundtrip=False,  # simulated fill path — no venue I/O needed
    )
    assert trade is not None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
