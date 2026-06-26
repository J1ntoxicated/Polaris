"""Component B anti-churn — last-entry recording on actual submit.

DEMO/PAPER. Proves the novelty-tracking dict (``state.last_entry_by_key``) is
populated ONLY when an entry is actually submitted (``reserve_and_submit``
returned a trade), carrying the signal's ``(created_at_bar, side)``. This is the
state the next tick's novelty test reads to exempt a NEW bar / side flip but
block a same-bar same-side re-buy. Blocking the churn re-buy is PRECISION
(surgical-strike), not a defensive throttle / size dampen / P&L halt.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

import pytest

import polaris.scripts._production_run_signal as run_signal_mod
from polaris.core.pipeline.gate_state import GateDecision, GateResult
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.storage.schema import ALL_DDL
from polaris.strategies import RawSignal

# spot_donchian un-registered 2026-06-27 (#56 stop-bleeders KILL) — module
# preserved read-only; used here as a generic strategy vehicle, import direct.
from polaris.strategies.spot_donchian import SpotDonchianStrategy

NOW = 1_780_000_000


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        c.execute(stmt)
    return c


def _sig(
    *, created_at_bar: int, side: Literal["long", "short"] = "long",
) -> RawSignal:
    return RawSignal(
        signal_id="sig-b",
        strategy_id="tsmom",
        symbol="BTC-USDT",
        side=side,
        strength=0.9,  # high RAW strength — must NOT matter for recording
        sizing_hint=0.5,
        ttl_bars=24,
        thesis_tag="t",
        correlation_group="spot_cross_sectional_momo",
        created_at_bar=created_at_bar,
    )


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the orchestrator (SIZED) + G6/G7 gates so the submit path runs."""

    class _FakeOrch:
        def __init__(self, **_: object) -> None:
            pass

        async def run(self, _ctx: object, *, start_gate: int) -> list[GateResult]:
            return [
                GateResult(
                    decision=GateDecision.SIZED, next_gate=None,
                    payload={"sized": {"final_notional_usd": 80.0}},
                )
            ]

    async def _fake_monitor(_ctx: object, *, client: object) -> GateResult:
        return GateResult(decision=GateDecision.HOLD, next_gate=None)

    async def _fake_exit(_ctx: object, *, client: object) -> GateResult:
        return GateResult(decision=GateDecision.PROCEED, next_gate=None)

    monkeypatch.setattr(run_signal_mod, "GateOrchestrator", _FakeOrch)
    monkeypatch.setattr(run_signal_mod, "position_monitor_gate", _fake_monitor)
    monkeypatch.setattr(run_signal_mod, "adaptive_exit_gate", _fake_exit)
    monkeypatch.setattr(run_signal_mod, "log_gate_event", lambda *a, **k: None)


async def _run(
    conn: sqlite3.Connection, state: ProdLoopState, sig: RawSignal,
    *, trade: SimulatedTrade | None,
) -> None:
    async def _reserve_and_submit(**_: object) -> SimulatedTrade | None:
        return trade

    await run_signal_mod.run_pipeline_for_signal(
        conn=conn, haiku=None, state=state, strategy=SpotDonchianStrategy(), sig=sig,
        venue="okx", symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.01, last_price=100.0, universe_rows=[], now_ts=NOW,
        reserve_and_submit=_reserve_and_submit, phase="P0",
    )


def _trade() -> SimulatedTrade:
    return SimulatedTrade(
        signal_id="sig-b", venue="okx", symbol="BTC-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0,
        notional_usd=80.0, open_ts=NOW, position_id="pos-b",
    )


@pytest.mark.asyncio
async def test_records_last_entry_on_submit(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    state = ProdLoopState()
    await _run(conn, state, _sig(created_at_bar=5000), trade=_trade())
    # A trade was submitted → the key carries THIS entry's (bar, side).
    assert state.last_entry_by_key[("okx", "BTC-USDT", "tsmom")] == (5000, "long")


@pytest.mark.asyncio
async def test_no_record_when_submit_returns_none(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    state = ProdLoopState()
    await _run(conn, state, _sig(created_at_bar=5000), trade=None)
    # No submit → no recording (a blocked / rejected entry never sets novelty).
    assert ("okx", "BTC-USDT", "tsmom") not in state.last_entry_by_key


@pytest.mark.asyncio
async def test_record_advances_to_latest_submitted_bar(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    state = ProdLoopState()
    await _run(conn, state, _sig(created_at_bar=5000), trade=_trade())
    await _run(conn, state, _sig(created_at_bar=5100), trade=_trade())
    # The latest submitted entry's bar is what the next novelty test compares to.
    assert state.last_entry_by_key[("okx", "BTC-USDT", "tsmom")] == (5100, "long")
