"""ADR-012 Slice 1 — the observe-mode attach is PROVABLY byte-identical.

DEMO/PAPER. The probe → engine → tuning-log attach in ``_evaluate_position``
runs the bus + engine + log writers in observe mode AROUND ``run_precise_exit``
but threads NOTHING into it. These tests drive the SAME seeded scenarios as
``test_precise_exit_recalc`` through ``recalc_active_positions`` twice — once
with a probe sidecar wired on ``state``, once without — and assert the live
exit outcome (positions row + recalc_precise_exit) is IDENTICAL. They also prove
fault isolation (a raising probe / a fail-open writer never breaks the tick).
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.core.probes import ProbeBus, ProbeContext, ProbeKind, ProbeReading
from polaris.core.probes.engine import ExitEngine
from polaris.core.probes.tuning_log import open_probe_db
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import recalc_active_positions
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


# --- harness cloned from test_precise_exit_recalc ---------------------------


def _seed(
    conn: sqlite3.Connection, *, position_id: str, side: str = "long",
    entry_price: float = 100.0, last_price: float = 100.0, opened_ts: int = NOW,
    strategy: str = "vb", band: float = 0.5,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', ?, ?, ?, ?, 0.001, "
        " 'open', ?, 0)",
        (position_id, strategy, strategy, strategy, side, opened_ts),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, 'okx:BTC-USDT', 'okx', ?, 0.001, ?, 80.0, 0.05, "
        " 1.0, 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, strategy, side, entry_price,
         position_id, uuid.uuid4().hex),
    )
    for i in range(20):
        ts = NOW - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
            " ?, ?, ?, ?, 100.0, 10000.0, 1, ?, ?, ?, 1.0, 'rest')",
            (ts, entry_price, last_price + band, last_price - band,
             last_price, last_price, last_price, last_price),
        )


def _set_last_price(
    conn: sqlite3.Connection, *, last_price: float, band: float = 0.5,
    base_ts: int = NOW,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
        " ts, open, high, low, close, volume, notional_usd, trade_count, "
        " vwap, bid_close, ask_close, spread_bps_close, source) "
        "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
        " ?, ?, ?, ?, 100.0, 10000.0, 1, ?, ?, ?, 1.0, 'rest')",
        (base_ts + 60, last_price, last_price + band, last_price - band,
         last_price, last_price, last_price, last_price),
    )


def _trade(position_id: str, strategy: str = "vb") -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
        strategy_id=strategy, side="long", entry_price=100.0, notional_usd=80.0,
        open_ts=NOW, position_id=position_id, correlation_group="crypto:BTC",
        underlying_group_id="crypto:BTC",
    )


def _regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


class _MockGPT:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                outer.calls.append(kwargs)

                class _Block:
                    text = '{"decision":"HOLD"}'

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


async def _recalc(conn: sqlite3.Connection, state: ProdLoopState) -> None:
    await recalc_active_positions(
        conn, state=state, now_ts=NOW, gpt_client=_MockGPT(), phase="P1",
        lookup_regime=_regime, close_specific=close_specific_position,
    )


def _pos_row(conn: sqlite3.Connection, position_id: str) -> tuple[Any, ...]:
    row: tuple[Any, ...] = conn.execute(
        "SELECT status, stop_price, peak_price, trough_price, mfe_r, mae_r, "
        "exit_state, pnl_r, closed_ts FROM positions WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return row


def _wire_probes(state: ProdLoopState, probe_db: str) -> None:
    """Attach the observe-mode probe sidecar onto state (as boot does)."""
    from polaris.core.probes.catalog import (
        LossDefenseProbe,
        ProfitTakingProbe,
        SessionHoursProbe,
        TechnicalProbe,
    )

    state.probe_conn = open_probe_db(probe_db)
    state.probe_bus = ProbeBus(
        [ProfitTakingProbe(), LossDefenseProbe(), TechnicalProbe(),
         SessionHoursProbe()]
    )
    state.probe_engine = ExitEngine()


# --- byte-identical proofs ---------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "first_last", "second_last", "band"),
    [
        ("winner_holds", 100.4, None, 0.5),
        ("round_trip_closes", 100.3, 99.7, 0.05),
    ],
)
def test_observe_attach_byte_identical_exit(  # type: ignore[no-untyped-def]
    tmp_path, scenario, first_last, second_last, band,
) -> None:
    # Two independent DBs seeded identically; one run has the probe sidecar
    # wired, the other does not. The live exit outcome must be IDENTICAL.
    # Synchronous test: each run owns a fresh asyncio.run loop.
    import asyncio

    def _run(with_probes: bool) -> tuple[tuple[Any, ...], int]:
        from polaris.storage.schema import init_db

        db_path = f"{tmp_path}/{scenario}_{with_probes}.sqlite"
        conn = init_db(db_path)
        _seed(conn, position_id="p", last_price=first_last, band=band)
        state = ProdLoopState()
        state.open_trades = [_trade("p")]
        if with_probes:
            _wire_probes(state, f"{tmp_path}/probes_{scenario}_{with_probes}.sqlite")
        asyncio.run(_recalc(conn, state))
        if second_last is not None:
            _set_last_price(conn, last_price=second_last, band=band)
            asyncio.run(_recalc(conn, state))
        row = _pos_row(conn, "p")
        precise = state.recalc_precise_exit
        if with_probes:
            state.probe_conn.close()
        conn.close()
        return (row, precise)

    baseline = _run(with_probes=False)
    with_probe = _run(with_probes=True)
    assert baseline == with_probe, (
        f"observe-mode attach changed the live exit for {scenario}: "
        f"baseline={baseline} with_probe={with_probe}"
    )


@pytest.mark.asyncio
async def test_attach_raising_probe_does_not_break_tick(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from polaris.storage.schema import init_db

    class _Boom:
        probe_id: str = "boom"
        kind: ProbeKind = "technical"

        def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
            raise RuntimeError("kaboom")

    conn = init_db(f"{tmp_path}/boom.sqlite")
    _seed(conn, position_id="pb", last_price=100.4)
    state = ProdLoopState()
    state.open_trades = [_trade("pb")]
    state.probe_conn = open_probe_db(f"{tmp_path}/probes_boom.sqlite")
    state.probe_bus = ProbeBus([_Boom()])
    state.probe_engine = ExitEngine()
    # Tick must complete; the position is evaluated; the exit is unaffected.
    await _recalc(conn, state)
    row = _pos_row(conn, "pb")
    assert row[0] == "open"  # winner held — exit unaffected by the raising probe
    assert state.fault_events == 0  # the probe fault was isolated inside the bus
    conn.close()


@pytest.mark.asyncio
async def test_attach_fail_open_writer_missing_table(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # probe_conn points at a DB WITHOUT the probe tables → writers fail-open,
    # the tick proceeds, the exit is unaffected.
    from polaris.storage.schema import init_db

    conn = init_db(f"{tmp_path}/nowrite.sqlite")
    _seed(conn, position_id="pn", last_price=100.4)
    state = ProdLoopState()
    state.open_trades = [_trade("pn")]
    state.probe_conn = sqlite3.connect(":memory:")  # no DDL
    from polaris.core.probes.catalog import ProfitTakingProbe
    state.probe_bus = ProbeBus([ProfitTakingProbe()])
    state.probe_engine = ExitEngine()
    await _recalc(conn, state)
    assert _pos_row(conn, "pn")[0] == "open"
    conn.close()


@pytest.mark.asyncio
async def test_attach_logs_a_decision_row_in_observe(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The attach DID log an observe-mode decision (applied=0) — the sidecar
    # records the would-be action without touching the live exit.
    from polaris.storage.schema import init_db

    conn = init_db(f"{tmp_path}/logrow.sqlite")
    _seed(conn, position_id="pl", last_price=100.4)
    state = ProdLoopState()
    state.open_trades = [_trade("pl")]
    probe_db = f"{tmp_path}/probes_logrow.sqlite"
    _wire_probes(state, probe_db)
    await _recalc(conn, state)
    pconn = state.probe_conn
    n_dec = pconn.execute(
        "SELECT COUNT(*) FROM probe_decisions WHERE position_id='pl' "
        "AND mode='observe' AND applied=0"
    ).fetchone()[0]
    assert n_dec == 1
    pconn.close()
    conn.close()
