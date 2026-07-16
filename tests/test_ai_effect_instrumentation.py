"""AI-effect instrumentation tests (BUILD_SCHEMA).

Pure-additive measurement instrumentation — zero trading-behavior change.
DEMO/PAPER only. Aggressive bias untouched (no throttle / size dampen /
block-filter). Covers the three audit gaps:

1. signal_id → position linkage: ``positions.signal_id`` column exists,
   default-backfills ('') on legacy DBs, and is written by the entry path.
2. Token accounting: ``gate_events.input_tokens`` / ``output_tokens`` columns
   exist, default-backfill (0) on legacy DBs, and ``log_gate_event`` persists
   the GPT per-call token usage threaded onto ``GateResult``.
3. G3 raw GPT reason: a ``validator_kill`` mirrors G4's raw-reason capture.

These assert the additive columns + populate paths; existing gate_events
writes stay unaffected.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.gate_event_log import log_gate_event
from polaris.core.pipeline.gate_state import (
    GateContext,
    GateDecision,
    GateResult,
    SignalLifecycle,
)
from polaris.storage.schema import _apply_post_migrations, init_db

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _coldef(conn: sqlite3.Connection, table: str, col: str) -> tuple[int, str | None]:
    for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if row[1] == col:
            return row[3], row[4]
    raise AssertionError(f"{table}.{col} missing")


class _TokenGPTClient:
    """Mock client whose response carries usage tokens (for accounting test)."""

    def __init__(self, *, response_text: str, in_tok: int, out_tok: int) -> None:
        self.response_text = response_text
        outer = self

        class _Usage:
            input_tokens = in_tok
            output_tokens = out_tok
            cache_read_input_tokens = 0

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001, ANN003
                class _Block:
                    text = outer.response_text

                class _Resp:
                    content = [_Block()]
                    usage = _Usage()

                return _Resp()

        self.messages = _Messages()


def _ctx(payload: dict, *, gate_id: int) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-test",
        position_id=None,
        gate_id=gate_id,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.RAW,
    )


# ---------------------------------------------------------------------------
# 1. positions.signal_id linkage column
# ---------------------------------------------------------------------------


def test_positions_signal_id_column_exists_fresh(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        assert "signal_id" in _cols(conn, "positions")
        notnull, dflt = _coldef(conn, "positions", "signal_id")
        assert notnull == 1
        assert dflt == "''"
    finally:
        conn.close()


def test_positions_signal_id_legacy_backfill(tmp_path: Path) -> None:
    """A positions table created before the signal_id column gets an
    idempotent ALTER and existing rows default-backfill to ''."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE positions ("
        "position_id TEXT PRIMARY KEY, venue TEXT NOT NULL, symbol TEXT NOT NULL, "
        "strategy_id TEXT NOT NULL, entry_strategy_id TEXT NOT NULL, "
        "active_strategy_id TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, "
        "status TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status) VALUES "
        "('p1', 'okx', 'BTC-USDT', 's1', 's1', 's1', 'long', 1.0, 'open')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        assert "signal_id" in _cols(conn, "positions")
        row = conn.execute(
            "SELECT signal_id FROM positions WHERE position_id = 'p1'"
        ).fetchone()
        assert row == ("",)
    finally:
        conn.close()


def test_positions_signal_id_insert_roundtrip(tmp_path: Path) -> None:
    """The originating signal_id can be written + joined back to the row."""
    conn = init_db(tmp_path / "rt.sqlite")
    try:
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, "
            "underlying_group_id, signal_id, strategy_id, entry_strategy_id, "
            "active_strategy_id, side, qty, status, opened_ts, swap_count) "
            "VALUES ('pos_x', 'okx', 'BTC-USDT', 'crypto:BTC', 'sig-abc', "
            "'vb', 'vb', 'vb', 'long', 1.0, 'open', 100, 0)"
        )
        row = conn.execute(
            "SELECT position_id FROM positions WHERE signal_id = 'sig-abc'"
        ).fetchone()
        assert row == ("pos_x",)
    finally:
        conn.close()


def test_positions_signal_id_idempotent_reinit(tmp_path: Path) -> None:
    db = tmp_path / "reinit.sqlite"
    init_db(db).close()
    conn = init_db(db)
    try:
        _apply_post_migrations(conn)  # second invocation must be a no-op
        assert "signal_id" in _cols(conn, "positions")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. gate_events token-accounting columns
# ---------------------------------------------------------------------------


def test_gate_events_token_columns_exist_fresh(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        cols = _cols(conn, "gate_events")
        for col in ("input_tokens", "output_tokens"):
            assert col in cols
            notnull, dflt = _coldef(conn, "gate_events", col)
            assert notnull == 1
            assert dflt == "0"
    finally:
        conn.close()


def test_gate_events_token_columns_legacy_backfill(tmp_path: Path) -> None:
    """A gate_events table created before the token columns gets an idempotent
    ALTER and existing rows default-backfill to 0."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE gate_events ("
        "event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, signal_id TEXT, "
        "position_id TEXT, gate_id INTEGER NOT NULL, phase TEXT NOT NULL, "
        "decision TEXT, model_used TEXT, latency_ms INTEGER, payload_json TEXT, "
        "error_text TEXT, created_ts INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO gate_events (event_id, run_id, gate_id, phase, created_ts) "
        "VALUES ('e1', 'r1', 3, 'success', 100)"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        cols = _cols(conn, "gate_events")
        assert "input_tokens" in cols
        assert "output_tokens" in cols
        row = conn.execute(
            "SELECT input_tokens, output_tokens FROM gate_events WHERE event_id = 'e1'"
        ).fetchone()
        assert row == (0, 0)
    finally:
        conn.close()


def test_log_gate_event_persists_tokens(tmp_path: Path) -> None:
    """log_gate_event writes the GateResult token fields onto gate_events."""
    conn = init_db(tmp_path / "log.sqlite")
    try:
        ctx = _ctx({}, gate_id=3)
        result = GateResult(
            decision=GateDecision.PASS,
            next_gate=4,
            payload={"ok": True},
            model_used="gpt",
            latency_ms=42,
            input_tokens=123,
            output_tokens=45,
        )
        log_gate_event(conn, ctx, result)
        row = conn.execute(
            "SELECT input_tokens, output_tokens, model_used FROM gate_events "
            "WHERE signal_id = ?",
            (ctx.signal_id,),
        ).fetchone()
        assert row == (123, 45, "gpt")
    finally:
        conn.close()


def test_log_gate_event_python_gate_zero_tokens(tmp_path: Path) -> None:
    """A Python (non-GPT) gate logs 0 tokens — existing writes unaffected."""
    conn = init_db(tmp_path / "py.sqlite")
    try:
        ctx = _ctx({}, gate_id=5)
        result = GateResult(
            decision=GateDecision.SIZED,
            next_gate=6,
            payload={},
            model_used="python",
        )
        log_gate_event(conn, ctx, result)
        row = conn.execute(
            "SELECT input_tokens, output_tokens FROM gate_events WHERE signal_id = ?",
            (ctx.signal_id,),
        ).fetchone()
        assert row == (0, 0)
    finally:
        conn.close()


def test_signal_validator_deterministic_reports_zero_tokens() -> None:
    """P2a group B: G3 never calls GPT now — a supplied client's token usage
    is never threaded onto the result (always 0 for the deterministic gate)."""
    client = _TokenGPTClient(
        response_text='{"decision": "PASS", "strength_scalar": 1.0}',
        in_tok=200, out_tok=30,
    )
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = asyncio.run(signal_validator_gate(ctx, client=client))
    assert result.model_used == "python"
    assert result.input_tokens == 0
    assert result.output_tokens == 0


# ---------------------------------------------------------------------------
# 3. G3 raw GPT reason capture (mirror G4)
# ---------------------------------------------------------------------------


def test_signal_validator_only_kill_path_is_missing_raw_signal() -> None:
    """P2a group B: the GPT-driven ``validator_kill`` raw-reason capture is
    gone with the legacy branch — a supplied KILL-shaped client response is
    never read, and the ONLY G3 KILL left is ``missing_raw_signal``."""
    raw = '{"decision": "KILL", "thesis": "weak burst, no follow-through"}'
    client = _TokenGPTClient(response_text=raw, in_tok=180, out_tok=20)
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 0.3}}, gate_id=3)
    result = asyncio.run(signal_validator_gate(ctx, client=client))
    assert result.decision != GateDecision.KILL  # technical rule never KILLs here
    assert result.input_tokens == 0
    assert result.output_tokens == 0

    empty_ctx = _ctx({}, gate_id=3)
    kill_result = asyncio.run(signal_validator_gate(empty_ctx, client=client))
    assert kill_result.decision == GateDecision.KILL
    assert kill_result.payload["reason"] == "missing_raw_signal"
    assert "raw" not in kill_result.payload


def test_g3_deterministic_result_end_to_end_logged(tmp_path: Path) -> None:
    """The G3 deterministic decision + zero tokens survive into the
    persisted gate_events payload_json (GPT threading is gone)."""
    client = _TokenGPTClient(
        response_text='{"decision": "KILL", "thesis": "spread too wide"}',
        in_tok=10, out_tok=5,
    )
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 0.2}}, gate_id=3)
    result = asyncio.run(signal_validator_gate(ctx, client=client))
    conn = init_db(tmp_path / "e2e.sqlite")
    try:
        log_gate_event(conn, ctx, result)
        row = conn.execute(
            "SELECT payload_json, input_tokens, output_tokens FROM gate_events "
            "WHERE signal_id = ?",
            (ctx.signal_id,),
        ).fetchone()
        payload = json.loads(row[0])
        assert payload["reason"] != "validator_kill"
        assert (row[1], row[2]) == (0, 0)
    finally:
        conn.close()
