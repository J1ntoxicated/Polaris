"""G7 divergence SHADOW (instrumentation, behavior 0) — rails vs GPT.

DEMO/PAPER only — virtual funds; aggressive bias preserved. The GPT decision
ALWAYS drives the live pipeline; this only proves the gate_shadow_events row
records (a) the deterministic Q9 rail decision for the same proposal, (b) the
raw GPT label (rubber-stamp HOLD-rate numerator — B4), (c) the call-site tag,
and that the returned decision is byte-identical with the shadow on/off.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate
from polaris.core.pipeline.agents.shadow_log import fetch_shadow_events
from polaris.core.pipeline.gate_orchestrator import GateOrchestrator
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000



@pytest.fixture(autouse=True)
def _legacy_gpt_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """W3 AI-free cutover adaptation (NOT a behavior change): this module pins
    the LEGACY GPT path, so force POLARIS_AI_FREE=0 explicitly (the flag now
    defaults ON). The flag=1 deterministic-primary path is covered by
    tests/test_ai_free_cutover.py."""
    monkeypatch.setenv("POLARIS_AI_FREE", "0")

class _MockGPTClient:
    def __init__(self, response_text: str = "{}") -> None:
        self.response_text = response_text
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001, ANN202
                outer.calls.append(kwargs)

                class _Block:
                    text = outer.response_text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _proposal(*, pnl_r: float = 1.2) -> dict:
    # Long, proposed farther than current → the Q9 rail ALLOWS the widen when
    # pnl_r clears the widen window (rails decision = ADJUST_EXIT).
    return {
        "side": "long",
        "current_stop_price": 79_000.0,
        "proposed_stop_price": 78_000.0,
        "entry_price": 80_000.0,
        "unrealized_pnl_r": pnl_r,
        "max_loss_r": 1.0,
        "overrides_used": 0,
        "seconds_since_last_override": 60,
    }


def _ctx(proposal: dict, *, site: str | None = None) -> GateContext:
    payload: dict = {
        "widen_proposal": proposal,
        "current_stop_price": proposal["current_stop_price"],
        "regime": "bull_trend",
    }
    if site is not None:
        payload["g7_shadow_site"] = site
    return GateContext(
        run_id=uuid.uuid4().hex, signal_id="sig-g7", position_id="pos-g7",
        gate_id=GATE_ADAPTIVE_EXIT, venue="okx", symbol="BTC-USDT",
        strategy_id="vb", payload=payload, started_ts=NOW,
        state=SignalLifecycle.MONITORED,
    )


def _g7_rows(conn: sqlite3.Connection) -> list[dict]:
    return fetch_shadow_events(conn, gate_id=GATE_ADAPTIVE_EXIT)


async def test_gpt_hold_vs_rails_widen_is_mismatch_with_raw_label(
    memdb: sqlite3.Connection,
) -> None:
    haiku = _MockGPTClient(json.dumps({"decision": "HOLD", "reason": "free text"}))
    result = await adaptive_exit_gate(
        _ctx(_proposal(), site="live_recalc"), client=haiku, shadow_conn=memdb,
    )
    assert result.decision == GateDecision.HOLD  # GPT still drives
    rows = _g7_rows(memdb)
    assert len(rows) == 1
    row = rows[0]
    assert row["technical_decision"] == "ADJUST_EXIT"  # rail would widen
    assert row["gpt_decision"] == "HOLD"
    assert row["mismatch"] == 1
    assert "site:live_recalc" in row["technical_flags"]
    assert "gpt_raw:HOLD" in row["technical_flags"]  # B4: raw label survives
    assert row["regime"] == "bull_trend"


async def test_gpt_widen_accepted_is_agreement(memdb: sqlite3.Connection) -> None:
    haiku = _MockGPTClient(json.dumps({"decision": "WIDEN", "reason": "runner"}))
    result = await adaptive_exit_gate(
        _ctx(_proposal()), client=haiku, shadow_conn=memdb,
    )
    assert result.decision == GateDecision.ADJUST_EXIT
    row = _g7_rows(memdb)[0]
    assert row["technical_decision"] == "ADJUST_EXIT"
    assert row["gpt_decision"] == "ADJUST_EXIT"
    assert row["mismatch"] == 0
    assert "gpt_raw:WIDEN" in row["technical_flags"]
    # No explicit site payload → orchestrator default tag.
    assert "site:orchestrator" in row["technical_flags"]


async def test_gpt_exit_now_vs_rails_hold_is_mismatch(
    memdb: sqlite3.Connection,
) -> None:
    haiku = _MockGPTClient(json.dumps({"decision": "EXIT_NOW", "reason": "flip"}))
    # Below the widen window → the rail would HOLD; GPT acts alone.
    result = await adaptive_exit_gate(
        _ctx(_proposal(pnl_r=0.5)), client=haiku, shadow_conn=memdb,
    )
    assert result.decision == GateDecision.EXIT_NOW
    row = _g7_rows(memdb)[0]
    assert row["technical_decision"] == "HOLD"
    assert row["gpt_decision"] == "EXIT_NOW"
    assert row["mismatch"] == 1
    assert "gpt_raw:EXIT_NOW" in row["technical_flags"]


async def test_gpt_error_fallback_logs_err_raw_label(
    memdb: sqlite3.Connection,
) -> None:
    haiku = _MockGPTClient("this is not json")
    result = await adaptive_exit_gate(
        _ctx(_proposal()), client=haiku, shadow_conn=memdb,
    )
    # Fallback = the deterministic rail itself (fail-open contract unchanged).
    assert result.decision in (GateDecision.ADJUST_EXIT, GateDecision.HOLD)
    row = _g7_rows(memdb)[0]
    assert "gpt_raw:ERR" in row["technical_flags"]


async def test_p0_client_none_logs_no_shadow_row(memdb: sqlite3.Connection) -> None:
    """P0 rails ARE the live decision — a shadow row would be mismatch=0 noise."""
    result = await adaptive_exit_gate(
        _ctx(_proposal()), client=None, shadow_conn=memdb,
    )
    assert result.decision == GateDecision.ADJUST_EXIT
    assert _g7_rows(memdb) == []


async def test_decision_byte_identical_with_and_without_shadow(
    memdb: sqlite3.Connection,
) -> None:
    for text in (
        '{"decision":"HOLD","reason":"x"}',
        '{"decision":"WIDEN","reason":"x"}',
        '{"decision":"TIGHTEN","reason":"x"}',
        '{"decision":"EXIT_NOW","reason":"x"}',
        "garbage",
    ):
        bare = await adaptive_exit_gate(
            _ctx(_proposal()), client=_MockGPTClient(text),
        )
        shadowed = await adaptive_exit_gate(
            _ctx(_proposal()), client=_MockGPTClient(text), shadow_conn=memdb,
        )
        assert shadowed.decision == bare.decision
        assert shadowed.payload == bare.payload
        assert shadowed.model_used == bare.model_used


async def test_missing_shadow_table_never_crashes(memdb: sqlite3.Connection) -> None:
    memdb.execute("DROP TABLE gate_shadow_events")
    haiku = _MockGPTClient('{"decision":"HOLD","reason":"x"}')
    result = await adaptive_exit_gate(
        _ctx(_proposal()), client=haiku, shadow_conn=memdb,
    )
    assert result.decision == GateDecision.HOLD


async def test_orchestrator_p1_wrap_exit_logs_shadow(
    memdb: sqlite3.Connection,
) -> None:
    """GateOrchestrator phase=P1 G7 passes its conn as shadow_conn."""
    haiku = _MockGPTClient('{"decision":"HOLD","reason":"x"}')
    orch = GateOrchestrator(conn=memdb, haiku_client=haiku, phase="P1")
    ctx = _ctx(_proposal())
    results = await orch.run(ctx, start_gate=GATE_ADAPTIVE_EXIT)
    assert results and results[0].decision == GateDecision.HOLD
    rows = _g7_rows(memdb)
    assert len(rows) == 1
    assert rows[0]["gpt_decision"] == "HOLD"
    assert "gpt_raw:HOLD" in rows[0]["technical_flags"]


async def test_orchestrator_p0_wrap_exit_no_shadow(
    memdb: sqlite3.Connection,
) -> None:
    orch = GateOrchestrator(conn=memdb, haiku_client=None, phase="P0")
    ctx = _ctx(_proposal())
    await orch.run(ctx, start_gate=GATE_ADAPTIVE_EXIT)
    assert _g7_rows(memdb) == []


async def test_live_recalc_site_passes_shadow_conn_and_site_tag(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_production_recalc wires shadow_conn=conn + g7_shadow_site=live_recalc."""
    import polaris.scripts._production_recalc as recalc_mod
    from polaris.core.pipeline.gate_state import GateResult
    from polaris.scripts._production_recalc import recalc_active_positions
    from polaris.scripts.production_paper_loop import ProdLoopState

    captured: dict = {}

    async def _capture_exit(ctx: GateContext, **kwargs: object) -> GateResult:
        captured["shadow_conn"] = kwargs.get("shadow_conn")
        captured["site"] = ctx.payload.get("g7_shadow_site")
        return GateResult(
            decision=GateDecision.HOLD, next_gate=None,
            payload={"stop_price": 1.0, "widening_applied": False},
        )

    async def _g6_adjust(ctx: GateContext, **_: object) -> GateResult:
        return GateResult(
            decision=GateDecision.ADJUST_EXIT, next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "test"},
        )

    async def _no_precise_exit(**_: object) -> bool:
        return False

    async def _no_session_exit(**_: object) -> bool:
        return False

    monkeypatch.setattr(recalc_mod, "adaptive_exit_gate", _capture_exit)
    monkeypatch.setattr(recalc_mod, "position_monitor_gate", _g6_adjust)
    monkeypatch.setattr(recalc_mod, "run_precise_exit", _no_precise_exit)
    monkeypatch.setattr(recalc_mod, "run_session_forced_exit", _no_session_exit)
    # Seed one open position + its entry fill + a recent bar.
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, strategy_id, entry_strategy_id, "
        "active_strategy_id, side, qty, status, opened_ts) "
        "VALUES ('pos-rc', 'okx', 'BTC-USDT', 'crypto:BTC', 'tsmom', 'tsmom', "
        "'tsmom', 'long', 1.0, 'open', ?)",
        (NOW,),
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, ts_ms, strategy_id, instrument_id, venue, "
        "side, base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        "is_close, contribution_id, order_id, state) "
        "VALUES ('f1', ?, 'tsmom', 'okx:BTC-USDT', 'okx', 'long', 1.0, 100.0, "
        "100.0, 0.0, 0.0, 0.0, 0, 'pos-rc', 'o1', 'filled')",
        (NOW * 1000,),
    )
    memdb.execute(
        "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
        "venue, symbol, bar_interval, ts, open, high, low, close, volume) "
        "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
        "100.0, 101.0, 99.0, 100.5, 10.0)",
        (NOW - 60,),
    )
    state = ProdLoopState()
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=object(), phase="P1",
        lookup_regime=lambda _c, _v, _s: "bull_trend",
        close_specific=lambda **_: None,
    )
    assert captured["shadow_conn"] is memdb
    assert captured["site"] == "live_recalc"


async def test_live_recalc_wires_cell_routing_into_g7_payload(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#32 axis-B fix: the live-recalc G7 payload now carries a real
    ``cell_routing`` summary (was always absent -> warmth stuck at 0.0,
    capping ``evidence_robustness`` at 0.41 < robust_min 0.50)."""
    import polaris.scripts._production_recalc as recalc_mod
    from polaris.core.pipeline.gate_state import GateResult
    from polaris.scripts._production_recalc import recalc_active_positions
    from polaris.scripts.production_paper_loop import ProdLoopState

    captured: dict = {}

    async def _capture_exit(ctx: GateContext, **kwargs: object) -> GateResult:
        captured["cell_routing"] = ctx.payload.get("cell_routing")
        return GateResult(
            decision=GateDecision.HOLD, next_gate=None,
            payload={"stop_price": 1.0, "widening_applied": False},
        )

    async def _g6_adjust(ctx: GateContext, **_: object) -> GateResult:
        return GateResult(
            decision=GateDecision.ADJUST_EXIT, next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "test"},
        )

    async def _no_precise_exit(**_: object) -> bool:
        return False

    async def _no_session_exit(**_: object) -> bool:
        return False

    monkeypatch.setattr(recalc_mod, "adaptive_exit_gate", _capture_exit)
    monkeypatch.setattr(recalc_mod, "position_monitor_gate", _g6_adjust)
    monkeypatch.setattr(recalc_mod, "run_precise_exit", _no_precise_exit)
    monkeypatch.setattr(recalc_mod, "run_session_forced_exit", _no_session_exit)
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, strategy_id, entry_strategy_id, "
        "active_strategy_id, side, qty, status, opened_ts) "
        "VALUES ('pos-rc2', 'okx', 'BTC-USDT', 'crypto:BTC', 'tsmom', 'tsmom', "
        "'tsmom', 'long', 1.0, 'open', ?)",
        (NOW,),
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, ts_ms, strategy_id, instrument_id, venue, "
        "side, base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        "is_close, contribution_id, order_id, state) "
        "VALUES ('f2', ?, 'tsmom', 'okx:BTC-USDT', 'okx', 'long', 1.0, 100.0, "
        "100.0, 0.0, 0.0, 0.0, 0, 'pos-rc2', 'o2', 'filled')",
        (NOW * 1000,),
    )
    memdb.execute(
        "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
        "venue, symbol, bar_interval, ts, open, high, low, close, volume) "
        "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
        "100.0, 101.0, 99.0, 100.5, 10.0)",
        (NOW - 60,),
    )
    memdb.execute(
        "INSERT INTO cell_matrix_p0 "
        "(exchange, strategy, ticker, regime, n_eff, wins_eff, "
        "pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts) "
        "VALUES ('okx', 'tsmom', 'BTC-USDT', 'bull_trend', 22.0, 13.0, "
        "4.0, 0.18, 1.3, ?)",
        (NOW,),
    )
    state = ProdLoopState()
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=object(), phase="P1",
        lookup_regime=lambda _c, _v, _s: "bull_trend",
        close_specific=lambda **_: None,
    )
    assert captured["cell_routing"] is not None
    assert captured["cell_routing"]["n_eff"] == 22.0
