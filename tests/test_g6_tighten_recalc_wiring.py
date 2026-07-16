"""End-to-end: probe TIGHTEN sidecar → G6 → G7 → persisted tighter stop (recalc).

DEMO/PAPER · aggressive · flow_not_block. Drives the FULL live wiring through
``recalc_active_positions``: an adverse HOLD-band position with a freshest probe
TIGHTEN row in the ``data/probes.sqlite`` sidecar (wired to ``state.probe_conn``) gets
its persisted ATR-trailing stop TIGHTENED (pulled toward price) — precise exit TIMING,
never a close/block/size cut. Flag OFF → the stop is unchanged (byte-identical).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.probes.tuning_log import PROBE_DDL
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import recalc_active_positions
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000
ENTRY = 80_000.0
# Adverse long: last price below entry but well above the -1R stop band → HOLD band.
LAST = 79_980.0
# A WIDE current stop (far below price) the tighten pulls UP toward the peak.
WIDE_STOP = 79_000.0
PEAK = 80_050.0  # the position once ticked up — anchor for the tighter long stop.


def _seed_adverse_position(conn: sqlite3.Connection, *, position_id: str) -> None:
    venue, symbol = "okx", "BTC-USDT"
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " swap_count, stop_price, peak_price, trough_price, exit_state, "
        " entry_atr_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 0.001, 'open', ?, 0, ?, ?, ?, "
        "        'open', ?)",
        (
            position_id, venue, symbol, "crypto:BTC", "vb", "vb", "vb", NOW,
            WIDE_STOP, PEAK, LAST, 0.001,
        ),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, ?, ?, ?, 'long', 0.001, ?, 80.0, 0.05, 1.0, 0.0, 0, ?, ?, "
        "        'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, "vb", f"{venue}:{symbol}", venue,
            ENTRY, position_id, uuid.uuid4().hex,
        ),
    )
    instrument_id = f"{venue}:{symbol}"
    for i in range(20):
        ts = NOW - (20 - i) * 60
        close = LAST
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, 100.0, ?, 1, ?, ?, ?, 1.0, "
            "        'rest')",
            (
                instrument_id, "crypto:BTC", venue, symbol, ts,
                close, close + 60.0, close - 60.0, close, 100.0 * close,
                close, close, close,
            ),
        )


def _seed_probe_tighten(
    conn: sqlite3.Connection, *, position_id: str, action: str = "TIGHTEN",
) -> str:
    """Seed one OPEN probe decision row; returns its ``decision_id`` (P3 promotion —
    callers use this to assert the applied=1 write-back on the exact source row)."""
    decision_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO probe_decisions "
        "(decision_id, eval_id, ts, run_id, position_id, mode, composite_lean, "
        " action, trail_mult, applied, pnl_r_at_decision, pnl_r_truth, "
        " mark_source, mark_age_ms, exit_state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            decision_id, uuid.uuid4().hex, NOW, "run", position_id, "observe",
            -0.49, action, None, 0, -0.05, -0.05, "bar", 0, "open",
        ),
    )
    return decision_id


def _probe_conn() -> sqlite3.Connection:
    pc = sqlite3.connect(":memory:")
    for stmt in PROBE_DDL:
        pc.execute(stmt)
    return pc


def _trade(position_id: str) -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
        strategy_id="vb", side="long", entry_price=ENTRY, notional_usd=80.0,
        open_ts=NOW, position_id=position_id, correlation_group="crypto:BTC",
        underlying_group_id="crypto:BTC",
    )


def _lookup_regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


async def _run(memdb: sqlite3.Connection, *, flag_env: str | None) -> float | None:
    pid = "pos-tighten"
    _seed_adverse_position(memdb, position_id=pid)
    state = ProdLoopState()
    state.open_trades = [_trade(pid)]
    state.probe_conn = _probe_conn()
    _seed_probe_tighten(state.probe_conn, position_id=pid)
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW + 1, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
    )
    row = memdb.execute(
        "SELECT stop_price, status FROM positions WHERE position_id = ?", (pid,),
    ).fetchone()
    assert row is not None
    assert row[1] == "open"  # flow_not_block — never closed by the tighten
    return None if row[0] is None else float(row[0])


@pytest.mark.asyncio
async def test_flag_on_persists_tighter_stop(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON: the wide stop (79000) is pulled UP toward the peak — a tighter stop."""
    monkeypatch.setenv("POLARIS_G6_PROBE_TIGHTEN", "1")
    stop = await _run(memdb, flag_env="1")
    assert stop is not None
    assert stop > WIDE_STOP  # tightened (long stop moved up toward price)


@pytest.mark.asyncio
async def test_flag_off_matches_no_probe_baseline(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF (default) → byte-identical: the persisted stop equals the no-probe
    baseline (the precise-exit FSM's own ratchet), i.e. the probe TIGHTEN row had ZERO
    effect on the stop. A second adverse position with NO probe row in the SAME recalc
    must land on the identical stop."""
    monkeypatch.delenv("POLARIS_G6_PROBE_TIGHTEN", raising=False)
    # Position WITH a probe TIGHTEN row, flag OFF.
    with_probe = await _run(memdb, flag_env=None)

    # Baseline: a fresh DB + identical adverse position but NO probe row at all.
    base_db = sqlite3.connect(":memory:")
    from polaris.storage.schema import ALL_DDL

    for stmt in ALL_DDL:
        base_db.execute(stmt)
    pid = "pos-baseline"
    _seed_adverse_position(base_db, position_id=pid)
    state = ProdLoopState()
    state.open_trades = [
        SimulatedTrade(
            signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
            strategy_id="vb", side="long", entry_price=ENTRY, notional_usd=80.0,
            open_ts=NOW, position_id=pid, correlation_group="crypto:BTC",
            underlying_group_id="crypto:BTC",
        )
    ]
    state.probe_conn = _probe_conn()  # empty — no TIGHTEN row
    await recalc_active_positions(
        base_db, state=state, now_ts=NOW + 1, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
    )
    base_row = base_db.execute(
        "SELECT stop_price FROM positions WHERE position_id = ?", (pid,),
    ).fetchone()
    baseline = None if base_row is None or base_row[0] is None else float(base_row[0])
    base_db.close()

    assert with_probe == baseline  # probe TIGHTEN had ZERO effect with the flag OFF


@pytest.mark.asyncio
async def test_flag_on_marks_source_probe_row_applied(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3 promotion evidence: once the tighten actually lands on the live
    position (persisted stop), the EXACT source probe_decisions row is
    stamped applied=1 — a before/after promotion read can isolate it."""
    monkeypatch.setenv("POLARIS_G6_PROBE_TIGHTEN", "1")
    pid = "pos-tighten-applied"
    _seed_adverse_position(memdb, position_id=pid)
    state = ProdLoopState()
    state.open_trades = [_trade(pid)]
    state.probe_conn = _probe_conn()
    decision_id = _seed_probe_tighten(state.probe_conn, position_id=pid)
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW + 1, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
    )
    applied = state.probe_conn.execute(
        "SELECT applied FROM probe_decisions WHERE decision_id = ?", (decision_id,),
    ).fetchone()
    assert applied is not None
    assert applied[0] == 1


@pytest.mark.asyncio
async def test_flag_on_harvest_also_persists_tighter_stop(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HARVEST is a protect action too (P3 promotion) — the wiring pulls the
    stop tighter exactly like TIGHTEN."""
    monkeypatch.setenv("POLARIS_G6_PROBE_TIGHTEN", "1")
    pid = "pos-harvest"
    _seed_adverse_position(memdb, position_id=pid)
    state = ProdLoopState()
    state.open_trades = [_trade(pid)]
    state.probe_conn = _probe_conn()
    _seed_probe_tighten(state.probe_conn, position_id=pid, action="HARVEST")
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW + 1, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
    )
    row = memdb.execute(
        "SELECT stop_price, status FROM positions WHERE position_id = ?", (pid,),
    ).fetchone()
    assert row is not None
    assert row[1] == "open"
    assert row[0] is not None
    assert float(row[0]) > WIDE_STOP  # tightened, same as the TIGHTEN path


@pytest.mark.asyncio
async def test_flag_on_never_touches_qty_or_status(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """-1.0R rail / hard-MAX sizing untouched: the probe protect consumer only
    ever adjusts ``stop_price`` (exit TIMING). ``qty`` (sizing chain output)
    and ``status`` (never a stop-out / block) must be byte-identical
    before/after the tighten fires."""
    monkeypatch.setenv("POLARIS_G6_PROBE_TIGHTEN", "1")
    pid = "pos-rail-safety"
    _seed_adverse_position(memdb, position_id=pid)
    qty_before = memdb.execute(
        "SELECT qty FROM positions WHERE position_id = ?", (pid,),
    ).fetchone()[0]
    state = ProdLoopState()
    state.open_trades = [_trade(pid)]
    state.probe_conn = _probe_conn()
    _seed_probe_tighten(state.probe_conn, position_id=pid)
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW + 1, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
    )
    qty_after, status_after = memdb.execute(
        "SELECT qty, status FROM positions WHERE position_id = ?", (pid,),
    ).fetchone()
    assert qty_after == qty_before  # size chain untouched by exit-TIMING consumer
    assert status_after == "open"  # never a HOLD -> EXIT_NOW block
