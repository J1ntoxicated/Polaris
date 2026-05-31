"""End-to-end: a real replay result -> baselines -> 3-tier gate verdict.

Completion criterion (spec): run an actual replay and feed it through the gate
so a 3-tier verdict is produced + persisted to the read-model. Offline /
behaviour 0 — synthetic in-memory sandbox + synthetic bars (no live DB).
"""

from __future__ import annotations

import sqlite3

from polaris.core.benchmark.baselines import (
    bollinger_baseline,
    buy_and_hold_baseline,
    tsmom_baseline,
)
from polaris.core.benchmark.gate import evaluate_gate
from polaris.core.data.schema import Bar
from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.models import ReplayConfig
from polaris.scripts.run_replay import build_gate, persist_run
from polaris.storage.schema import ALL_DDL

BASE_TS = 1_700_000_000
HOUR = 3600


def _sandbox() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _uptrend(n: int = 60) -> list[Bar]:
    bars: list[Bar] = []
    price = 100.0
    for i in range(n):
        nxt = price * 1.01
        bars.append(
            Bar(
                instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
                venue="okx", symbol="BTC-USDT", bar_interval="1H", ts=BASE_TS + i * HOUR,
                open=price, high=max(price, nxt), low=min(price, nxt), close=nxt,
                volume=1000.0 + i, notional_usd=price * 1000.0, spread_bps_close=4.0,
                source="test",
            )
        )
        price = nxt
    return bars


def _run() -> tuple:
    bars = {"okx:BTC-USDT": _uptrend()}
    cfg = ReplayConfig(instrument_ids=("okx:BTC-USDT",), bar_interval="1H")
    result = ReplayEngine(cfg).run_with_bars(_sandbox(), {k: list(v) for k, v in bars.items()})
    flat = bars["okx:BTC-USDT"]
    baselines = {
        "bh_btc": buy_and_hold_baseline(flat, starting_equity=10_000.0),
        "tsmom": tsmom_baseline(flat, starting_equity=10_000.0, lookback=20),
        "bollinger": bollinger_baseline(flat, starting_equity=10_000.0),
    }
    return result, baselines


def test_replay_produces_gate_verdict() -> None:
    result, baselines = _run()
    assert result.trades  # the replay actually traded
    gate = evaluate_gate(replay=result, baselines=baselines, trials=7)
    assert set(gate.tiers) == {"relative", "risk_adjusted", "statistical"}
    assert isinstance(gate.passed, bool)
    # Spreads computed against every baseline.
    assert set(gate.sharpe_spreads) == {"bh_btc", "tsmom", "bollinger"}
    assert 0.0 <= gate.psr <= 1.0
    assert gate.deflated_sharpe <= gate.psr + 1e-9


def test_build_gate_and_persist_round_trip() -> None:
    result, baselines = _run()
    gate, verdict = build_gate(result, baselines, trials=7)
    assert verdict in {"PASS", "EDGE_WIDE_CI", "FAIL"}
    conn = _sandbox()
    cfg = ReplayConfig(instrument_ids=("okx:BTC-USDT",), bar_interval="1H")
    run_id = persist_run(conn, config=cfg, result=result, gate=gate, verdict=verdict, created_ts=BASE_TS)
    # The read-model row is written and reads back.
    row = conn.execute(
        "SELECT verdict, n_trades, psr, deflated_sharpe FROM replay_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == verdict
    assert row[1] == len(result.trades)
    # benchmark_results has one row per tier (+ per relative baseline).
    tiers = {r[0] for r in conn.execute(
        "SELECT DISTINCT tier FROM benchmark_results WHERE run_id=?", (run_id,)
    )}
    assert tiers == {"relative", "risk_adjusted", "statistical"}
