"""Structural hardening (2026-06-23) — exit-hygiene cluster (#7/#9/#12).

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block. Exit-timing winner
protection ONLY — never a throttle / block / size-cut / entry-veto:

  * #7 bar-vs-tick CUT-rate telemetry — a ``positions.exit_cadence`` tag
    (bar / tick) on the close record + a close_reason × cadence split on the
    since-reset rollup. MEASUREMENT-FIRST: no streak threading until the
    asymmetry is confirmed. The default-bar / explicit-tick close paths only
    stamp a column; no exit decision changes.
  * #9 Slice-2 compose dict<->MfeProtectSchedule adapter — round-trips the
    ``EngineDecision.mfe_protect`` dict into ``run_precise_exit``'s typed
    schedule. Pure plumbing while the engine is sealed (every mode returns
    None); pins the future Slice-2 flip to a one-liner.
  * #12 thread caller ``base_mfe_protect`` through the non-HOLD re-map modes in
    ``mode_to_exit_params`` so a caller-supplied (equity / tick) schedule is
    PRESERVED instead of clobbered by the bar default. Give-back protect in the
    winner-protection direction.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.live_recalc.exit_engine import (
    EXIT_BAR_MFE_BEP_R,
    EXIT_BAR_MFE_LOCK_R,
    EXIT_BAR_MFE_PROTECT_R,
    Bucket,
    ManagementMode,
    MfeProtectSchedule,
    ThesisGivebackParams,
    mfe_protect_from_dict,
    mfe_protect_to_dict,
    mode_to_exit_params,
)
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import ActivePositionRow
from polaris.scripts._production_recalc_exit import run_precise_exit
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.dashboard.snapshot_queries import (
    _cadence_reason_split,
    _since_reset_rollup,
)
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.storage.measurement_reset import stamp_measurement_reset

NOW = 1_780_000_000

_GIVEBACK = ThesisGivebackParams(arm_r=1.0, frac=0.5, hard_frac=0.8)


# ===========================================================================
# #9 — dict<->MfeProtectSchedule wire-point adapter
# ===========================================================================


def test_mfe_protect_round_trips_schedule_to_dict_and_back() -> None:
    sched = MfeProtectSchedule(bep_at_r=0.30, protect_at_r=0.45, lock_r=0.20)
    payload = mfe_protect_to_dict(sched)
    assert payload == {"bep_at_r": 0.30, "protect_at_r": 0.45, "lock_r": 0.20}
    assert mfe_protect_from_dict(payload) == sched


def test_mfe_protect_from_none_is_none() -> None:
    # The observe-mode default (no schedule threaded) → None at the wire point,
    # so a sealed compose stays byte-identical.
    assert mfe_protect_from_dict(None) is None


def test_compose_mfe_protect_dict_threads_into_run_precise_exit_type() -> None:
    # A future Slice-2 compose hands run_precise_exit a dict; the adapter must
    # produce the exact MfeProtectSchedule type the exit pass consumes.
    engine_dict = {"bep_at_r": 0.35, "protect_at_r": 0.50, "lock_r": 0.25}
    adapted = mfe_protect_from_dict(engine_dict)
    assert isinstance(adapted, MfeProtectSchedule)
    assert adapted.bep_at_r == 0.35
    assert adapted.protect_at_r == 0.50
    assert adapted.lock_r == 0.25


def test_mfe_protect_from_partial_dict_raises_loudly() -> None:
    # A partial dict is a programming error — raised, never silently floored.
    with pytest.raises(ValueError, match="missing rungs"):
        mfe_protect_from_dict({"bep_at_r": 0.3})


# ===========================================================================
# #12 — thread caller base_mfe_protect through non-HOLD modes
# ===========================================================================

_BAR_DEFAULT = MfeProtectSchedule(
    bep_at_r=EXIT_BAR_MFE_BEP_R,
    protect_at_r=EXIT_BAR_MFE_PROTECT_R,
    lock_r=EXIT_BAR_MFE_LOCK_R,
)


@pytest.mark.parametrize(
    "mode", [ManagementMode.LET_RUN, ManagementMode.HARVEST, ManagementMode.REMODE]
)
def test_caller_equity_schedule_preserved_in_non_hold_modes(
    mode: ManagementMode,
) -> None:
    # A caller-supplied equity/tick schedule (later-arming, more aggressive)
    # must SURVIVE the re-map instead of being clobbered by the bar default.
    equity = MfeProtectSchedule(bep_at_r=0.35, protect_at_r=0.50, lock_r=0.25)
    tp = mode_to_exit_params(
        mode, bucket=Bucket.TREND, mfe_r=0.6, pnl_r=0.4,
        giveback=_GIVEBACK, base_mfe_protect=equity, base_profit_target_r=None,
    )
    assert tp.mfe_protect == equity  # NOT the bar default


@pytest.mark.parametrize(
    "mode", [ManagementMode.LET_RUN, ManagementMode.HARVEST, ManagementMode.REMODE]
)
def test_none_base_falls_back_to_bar_default(mode: ManagementMode) -> None:
    # The bar-pipeline caller passes None → the bar default is synthesised
    # (byte-identical to the prior inline schedule).
    tp = mode_to_exit_params(
        mode, bucket=Bucket.TREND, mfe_r=0.6, pnl_r=0.4,
        giveback=_GIVEBACK, base_mfe_protect=None, base_profit_target_r=None,
    )
    assert tp.mfe_protect == _BAR_DEFAULT


def test_tick_cfg_schedule_now_survives_remap_behavior_change() -> None:
    # BEHAVIOR CHANGE (documented): the tick momentum schedule (0.35/0.50/0.25 —
    # later-arming, more aggressive let-run) was previously CLOBBERED to the bar
    # default (0.30/0.45/0.20) whenever a re-map mode engaged on the tick path.
    # #12 now PRESERVES it — winner protection in the let-run direction, NOT a
    # throttle. (The bar path is byte-identical because its base == bar default.)
    tick_cfg = MfeProtectSchedule(bep_at_r=0.35, protect_at_r=0.50, lock_r=0.25)
    tp = mode_to_exit_params(
        ManagementMode.HARVEST, bucket=Bucket.TREND, mfe_r=0.6, pnl_r=0.4,
        giveback=_GIVEBACK, base_mfe_protect=tick_cfg, base_profit_target_r=None,
    )
    assert tp.mfe_protect == tick_cfg
    assert tp.mfe_protect != _BAR_DEFAULT


def test_cut_mode_unaffected_by_base_schedule() -> None:
    # CUT closes now (broken+red) — it carries no protect schedule regardless.
    tp = mode_to_exit_params(
        ManagementMode.CUT, bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.5,
        giveback=_GIVEBACK,
        base_mfe_protect=MfeProtectSchedule(0.35, 0.50, 0.25),
        base_profit_target_r=None,
    )
    assert tp.thesis_cut is True
    assert tp.mfe_protect is None


# ===========================================================================
# #7 — bar-vs-tick exit-cadence telemetry
# ===========================================================================


def _seed_closeable(
    conn: sqlite3.Connection, *, position_id: str, opened_ts: int = NOW
) -> None:
    """A live position priced INTO its stop so run_precise_exit closes it."""
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, stop_price, peak_price, trough_price, "
        " exit_state) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', 'vb', 'vb', 'vb', "
        " 'long', 0.001, 'open', ?, 0, 101.0, 104.0, 99.9, 'touched')",
        (position_id, opened_ts),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, 'vb', 'okx:BTC-USDT', 'okx', 'buy', 0.001, 100.0, "
        " 80.0, 0.05, 1.0, 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, opened_ts * 1000, position_id, uuid.uuid4().hex),
    )


def _trade(position_id: str) -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
        strategy_id="vb", side="long", entry_price=100.0, notional_usd=80.0,
        open_ts=NOW, position_id=position_id, correlation_group="crypto:BTC",
        underlying_group_id="crypto:BTC",
    )


def _regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


def _cadence(conn: sqlite3.Connection, position_id: str) -> str | None:
    r = conn.execute(
        "SELECT exit_cadence FROM positions WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return None if r is None else r[0]


async def _close_via_precise_exit(
    conn: sqlite3.Connection, position_id: str, *, cadence: str
) -> bool:
    state = ProdLoopState()
    state.open_trades = [_trade(position_id)]
    pos = ActivePositionRow()
    pos.update({
        "position_id": position_id, "venue": "okx", "symbol": "BTC-USDT",
        "side": "long", "active_strategy_id": "vb", "strategy": "vb",
        "stop_price": 101.0, "peak_price": 104.0, "trough_price": 99.9,
        "exit_state": "touched",
    })
    return await run_precise_exit(
        conn=conn, state=state, pos=pos, side="long",
        entry_price=100.0, last_price=100.5, atr_pct=0.019, pnl_r=0.1,
        held_seconds=60, now_ts=NOW, close_specific=close_specific_position,
        lookup_regime=_regime, gpt_client=None, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        cadence=cadence,
    )


@pytest.mark.asyncio
async def test_bar_close_stamps_cadence_bar(memdb: sqlite3.Connection) -> None:
    _seed_closeable(memdb, position_id="pos-bar")
    closed = await _close_via_precise_exit(memdb, "pos-bar", cadence="bar")
    assert closed is True
    assert _cadence(memdb, "pos-bar") == "bar"


@pytest.mark.asyncio
async def test_tick_close_stamps_cadence_tick(memdb: sqlite3.Connection) -> None:
    _seed_closeable(memdb, position_id="pos-tick")
    closed = await _close_via_precise_exit(memdb, "pos-tick", cadence="tick")
    assert closed is True
    assert _cadence(memdb, "pos-tick") == "tick"


@pytest.mark.asyncio
async def test_run_precise_exit_defaults_to_bar_cadence(
    memdb: sqlite3.Connection,
) -> None:
    # No cadence kwarg → 'bar' (the ~5s recalc is the default caller). A close
    # path that forgot to tag would regress this.
    _seed_closeable(memdb, position_id="pos-default")
    state = ProdLoopState()
    state.open_trades = [_trade("pos-default")]
    pos = ActivePositionRow()
    pos.update({
        "position_id": "pos-default", "venue": "okx", "symbol": "BTC-USDT",
        "side": "long", "active_strategy_id": "vb", "strategy": "vb",
        "stop_price": 101.0, "peak_price": 104.0, "trough_price": 99.9,
        "exit_state": "touched",
    })
    closed = await run_precise_exit(
        conn=memdb, state=state, pos=pos, side="long",
        entry_price=100.0, last_price=100.5, atr_pct=0.019, pnl_r=0.1,
        held_seconds=60, now_ts=NOW, close_specific=close_specific_position,
        lookup_regime=_regime, gpt_client=None, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
    )
    assert closed is True
    assert _cadence(memdb, "pos-default") == "bar"


def test_cadence_reason_split_groups_bar_vs_tick(
    memdb: sqlite3.Connection,
) -> None:
    # Two closed positions over the same since-reset window — one bar, one tick,
    # both thesis_cut — split into distinct cadence cells.
    for pid, cadence, pnl in (("c-bar", "bar", -3.0), ("c-tick", "tick", 5.0)):
        memdb.execute(
            "INSERT INTO positions "
            "(position_id, venue, symbol, underlying_group_id, strategy_id, "
            " entry_strategy_id, active_strategy_id, side, qty, status, "
            " opened_ts, swap_count, exit_cadence) "
            "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', 'vb', 'vb', 'vb', "
            " 'long', 0.001, 'closed', ?, 0, ?)",
            (pid, NOW + 10, cadence),
        )
        memdb.execute(
            "INSERT INTO fills "
            "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
            " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
            " is_close, contribution_id, order_id, state) "
            "VALUES (?, ?, 'vb', 'okx:BTC-USDT', 'okx', 'sell', 0.001, 100.0, "
            " 80.0, 0.10, 1.0, ?, 1, ?, ?, 'filled')",
            (uuid.uuid4().hex, (NOW + 20) * 1000, pnl, pid, uuid.uuid4().hex),
        )
        memdb.execute(
            "INSERT INTO position_strategy_segments "
            "(position_id, segment_id, strategy_id, started_ts, exit_reason) "
            "VALUES (?, ?, 'vb', ?, 'thesis_cut')",
            (pid, uuid.uuid4().hex, NOW + 10),
        )

    split = _cadence_reason_split(memdb, reset_ts=NOW)
    cells = {(c.close_reason, c.cadence): c for c in split}
    assert ("thesis_cut", "bar") in cells
    assert ("thesis_cut", "tick") in cells
    assert cells[("thesis_cut", "bar")].n == 1
    assert cells[("thesis_cut", "tick")].n == 1
    # fee-net = gross - fee (0.10 each).
    assert cells[("thesis_cut", "bar")].net_usd == pytest.approx(-3.10)
    assert cells[("thesis_cut", "tick")].net_usd == pytest.approx(4.90)


def test_since_reset_rollup_carries_cadence_split(
    memdb: sqlite3.Connection,
) -> None:
    stamp_measurement_reset(memdb, reset_ts=NOW, label="t", git_sha="abc",
                            equity_baseline_usd=1000.0)
    memdb.execute(
        "INSERT INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, exit_cadence) "
        "VALUES ('r1', 'okx', 'BTC-USDT', 'crypto:BTC', 'vb', 'vb', 'vb', "
        " 'long', 0.001, 'closed', ?, 0, 'tick')",
        (NOW + 10,),
    )
    memdb.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, 'vb', 'okx:BTC-USDT', 'okx', 'sell', 0.001, 100.0, "
        " 80.0, 0.10, 1.0, 2.0, 1, 'r1', ?, 'filled')",
        (uuid.uuid4().hex, (NOW + 20) * 1000, uuid.uuid4().hex),
    )
    rollup = _since_reset_rollup(memdb)
    assert rollup is not None
    assert any(c.cadence == "tick" for c in rollup.cadence_split)
