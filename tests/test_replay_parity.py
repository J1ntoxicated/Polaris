"""Replay <-> live parity + sandbox read-only isolation (divergence guard).

Parity: on a fixed fixture, the SignalIntent + notional the replay engine feeds
its fill model are byte-for-byte the same as a direct live ``compute_size`` call
on the same inputs (the engine imports the live seam — zero fork).

Isolation: ``seed_sandbox`` opens the live DB strictly read-only; writing to the
seeded in-memory conn must not propagate to the source file.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

import polaris.core.replay.engine as engine_mod
from polaris.core.data.schema import Bar
from polaris.core.live_recalc.exit_engine import evaluate_exit as live_evaluate_exit
from polaris.core.pipeline._sizer_payload import build_sizer_payload
from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.models import ReplayConfig
from polaris.core.replay.sandbox_db import SEED_TABLES, seed_sandbox
from polaris.core.sizing import compute_size
from polaris.scripts._production_recalc_exit import _loser_timeout_for_strategy
from polaris.storage.schema import ALL_DDL
from polaris.strategies.base import RawSignal

BASE_TS = 1_700_000_000
HOUR = 3600


def _mk_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def test_replay_sizing_matches_live_compute_size() -> None:
    """Fixed fixture: build the payload exactly as the engine does, run
    compute_size twice (once 'live', once 'replay') -> identical notional."""
    conn = _mk_db_conn()
    raw = RawSignal(
        signal_id="fixed-sig-id",  # fixed so the call is deterministic
        strategy_id="tsmom", symbol="BTC-USDT", side="long",
        strength=0.8, sizing_hint=0.8, ttl_bars=4, thesis_tag="t",
        correlation_group="spot_cross_sectional_momo", created_at_bar=BASE_TS,
    )
    kwargs = dict(
        raw_signal=raw, venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
        asset_class="spot", regime="bull_trend", track="A",
        listing_age_hours=10_000.0, leverage=1.0, equity_usd=10_000.0,
        now_ts=BASE_TS,
    )
    live_payload = build_sizer_payload(conn=conn, **kwargs)  # type: ignore[arg-type]
    replay_payload = build_sizer_payload(conn=conn, **kwargs)  # type: ignore[arg-type]
    assert live_payload["signal_intent"] == replay_payload["signal_intent"]

    live = compute_size(
        conn, intent=live_payload["signal_intent"],
        risk_state=live_payload["risk_state"],
        portfolio=live_payload["portfolio"], now_ts=BASE_TS,
    )
    replay = compute_size(
        conn, intent=replay_payload["signal_intent"],
        risk_state=replay_payload["risk_state"],
        portfolio=replay_payload["portfolio"], now_ts=BASE_TS,
    )
    assert live.final_notional_usd == replay.final_notional_usd
    assert live.final_risk_pct == replay.final_risk_pct
    assert live.binding_cap == replay.binding_cap
    assert live.final_notional_usd > 0.0


def test_seed_sandbox_is_read_only_on_live_db() -> None:
    """Writes to the seeded :memory: conn never reach the source live file."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "fake_live.sqlite")
        live = sqlite3.connect(db_path)
        for stmt in ALL_DDL:
            live.execute(stmt)
        live.execute(
            "INSERT INTO cell_matrix_p0 (exchange, strategy, ticker, regime, n_eff, "
            "wins_eff, pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts) "
            "VALUES ('okx','tsmom','BTC-USDT','bull_trend',5.0,3.0,2.0,0.4,0.4,?)",
            (BASE_TS,),
        )
        live.commit()
        live_before = live.execute(
            "SELECT n_eff, score FROM cell_matrix_p0 WHERE ticker='BTC-USDT'"
        ).fetchone()

        sandbox = seed_sandbox(db_path)
        try:
            # The snapshot was copied in.
            seeded = sandbox.execute(
                "SELECT n_eff, score FROM cell_matrix_p0 WHERE ticker='BTC-USDT'"
            ).fetchone()
            assert seeded == live_before
            # Mutate the sandbox hard.
            sandbox.execute("UPDATE cell_matrix_p0 SET n_eff = 999.0")
            sandbox.execute(
                "INSERT INTO cell_matrix_p0 (exchange, strategy, ticker, regime) "
                "VALUES ('okx','x','Y','chop')"
            )
        finally:
            sandbox.close()

        # Re-open the live file from scratch — unchanged.
        live2 = sqlite3.connect(db_path)
        live_after = live2.execute(
            "SELECT n_eff, score FROM cell_matrix_p0 WHERE ticker='BTC-USDT'"
        ).fetchone()
        n_rows = live2.execute("SELECT COUNT(*) FROM cell_matrix_p0").fetchone()[0]
        live2.close()
        live.close()
        assert live_after == live_before
        assert n_rows == 1  # the sandbox INSERT did not leak


def test_seed_tables_cover_cell_and_learner_reads() -> None:
    """Snapshot must include every table the sizing/exit read seams consult."""
    for must in ("cell_matrix_p0", "regime_state", "learner_state", "learner_blocks"):
        assert must in SEED_TABLES


# --- exit-FSM parity: replay feeds the SAME loser_timeout_sec as live ------
def _sandbox() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _uptrend_then_drop_bars(n: int = 130) -> list[Bar]:
    """1H bars: a long base, a dip, a late trend re-acceleration that fires a 1H
    trend long (e.g. supertrend — the surviving registered OKX 1H trend strategy
    after spot_donchian, the prior 1H vehicle, was un-registered 2026-06-27 in the
    #56 stop-bleeders KILL), then a drop so the open position is ticked through
    ``_tick_exit`` (the FSM is exercised at the 1H capped loser-timeout)."""
    bars: list[Bar] = []
    price = 100.0
    for i in range(n):
        # Slow base (warmup), a dip, a strong late rally (flips a 1H trend long),
        # then a decline so the freshly-opened 1H position is ticked as a loser.
        if i < 60:
            step = 0.0008
        elif i < 78:
            step = -0.012
        elif i < 95:
            step = 0.020
        else:
            step = -0.012
        nxt = price * (1.0 + step)
        bars.append(
            Bar(
                instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
                venue="okx", symbol="BTC-USDT", bar_interval="1H",
                ts=BASE_TS + i * HOUR,
                open=price, high=max(price, nxt), low=min(price, nxt),
                close=nxt, volume=1000.0 + i, notional_usd=price * 1000.0,
                spread_bps_close=4.0, source="test",
            )
        )
        price = nxt
    return bars


def test_replay_exit_passes_live_loser_timeout_for_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Divergence guard: every ``evaluate_exit`` the replay engine fires must be
    passed ``loser_timeout_sec`` == the LIVE recalc value for that position's
    strategy (a 1H strategy -> capped drift backstop, NOT the flat 900s default).

    We spy on BOTH seams the engine imports: ``_loser_timeout_for_strategy``
    (records which strategy_id the engine resolved) and ``evaluate_exit``
    (records the loser_timeout_sec it was actually fed). Each FSM call's kwarg
    must equal the LIVE helper's value for the strategy_id resolved immediately
    before it — byte-for-byte parity with ``run_precise_exit``.
    """
    pairs: list[tuple[str, float]] = []
    last_resolved: dict[str, str] = {}

    def _timeout_spy(strategy_id: str) -> float:
        last_resolved["sid"] = strategy_id
        return _loser_timeout_for_strategy(strategy_id)  # the LIVE helper

    def _exit_spy(**kwargs: object) -> object:
        timeout = float(kwargs["loser_timeout_sec"])  # type: ignore[arg-type]
        pairs.append((last_resolved["sid"], timeout))
        return live_evaluate_exit(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_mod, "_loser_timeout_for_strategy", _timeout_spy)
    monkeypatch.setattr(engine_mod, "evaluate_exit", _exit_spy)
    cfg = ReplayConfig(
        instrument_ids=("okx:BTC-USDT",), bar_interval="1H",
        starting_equity=10_000.0,
    )
    ReplayEngine(cfg).run_with_bars(
        _sandbox(), {"okx:BTC-USDT": _uptrend_then_drop_bars()}
    )

    assert pairs, "the exit FSM must be ticked at least once"
    # Every FSM call used the LIVE per-strategy timeout for the strategy the
    # engine resolved (parity with run_precise_exit).
    for sid, fed_timeout in pairs:
        assert fed_timeout == _loser_timeout_for_strategy(sid)
    # And a 1H strategy (supertrend/etc.) was actually exercised at its
    # drift-backstop value — capped at the named LOSER_TIMEOUT_CAP_SEC (3600s),
    # NOT the flat 900s (the OLD broken wiring) and NOT the uncapped 7200s floor.
    from polaris.scripts._production_recalc_exit import LOSER_TIMEOUT_CAP_SEC

    assert any(fed == LOSER_TIMEOUT_CAP_SEC for _sid, fed in pairs), pairs
