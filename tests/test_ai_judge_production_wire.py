"""#32 — AI JUDGE production-path info-completeness + activation wiring.

DEMO/PAPER paper bot. The adversarial review of #32 confirmed the judge's SAFETY
structure (no-KILL verdict type, shadow default, 9-stack sealed) but flagged a
REJECT gap: in PRODUCTION the judge received NO information (the production payload
builders never stamped the fused alt-data ``evidence`` / ``ticker_ground``) AND the
judge was DORMANT (``judge_client`` was never threaded into the orchestrator). These
tests pin the FIX over the REAL production path (``run_pipeline_for_signal`` /
``_evaluate_position`` — NOT a hand-built GateContext fixture):

  - the production G3/G4 payload carries the bot's OWN fused alt-data evidence +
    ground coverage (read from the already-materialized ``ticker_ground`` row);
  - the production G7 live-recalc payload carries the same;
  - ``state.judge_client`` is threaded into the orchestrator (entry) and the G7
    exit gate (recalc) so the judge actually RUNS in production;
  - a graceful no-op when no ground row exists / no judge client (byte-identical to
    the no-judge path — the deterministic decision still acts).

flow_not_block / shadow-byte-identical: stamping ``evidence``/``ticker_ground`` adds
JUDGE-ONLY payload keys (no deterministic gate branches on them) and threading the
client only makes the judge LOG in shadow mode — the live decision is unchanged.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

import pytest

import polaris.scripts._production_recalc as recalc_mod
import polaris.scripts._production_run_signal as run_signal_mod
from polaris.core.pipeline.gate_state import GateDecision, GateResult
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts._static_ground import _persist_ticker_ground
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.storage.schema import ALL_DDL
from polaris.strategies import RawSignal, SpotDonchianStrategy

NOW = 1_780_000_000


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        c.execute(stmt)
    return c


class _Inst:
    """Minimal active-instrument shape for ``_persist_ticker_ground``."""

    def __init__(self) -> None:
        self.instrument_id = "okx:BTC-USDT"
        self.venue = "okx"
        self.symbol = "BTC-USDT"
        self.underlying_group_id = "crypto:BTC"
        self.asset_class = "crypto"


# The fused alt-data evidence the ticker-ground producer ACTUALLY persists (the
# keys ``fuse_evidence`` emits — label / scores / news_* / raw signals).
_FUSED_EVIDENCE: dict[str, Any] = {
    "label": "bull_trend",
    "scores": {"bull_trend": 2.4, "bear_trend": 0.2, "chop": 0.0, "crisis": 0.0},
    "asset_class": "crypto",
    "news_sentiment": 0.55,
    "news_magnitude": 0.7,
    "news_n": 6,
    "crypto_fg": 79,
    "avg_funding": 0.0041,
}


def _seat_ground(conn: sqlite3.Connection) -> None:
    """Materialize the per-ticker ground row the production path reads."""
    _persist_ticker_ground(
        conn, inst=_Inst(), now_ts=NOW,
        has_sentiment=True, has_event=True, ground=_FUSED_EVIDENCE,
    )
    conn.commit()


def _sig(*, side: Literal["long", "short"] = "long") -> RawSignal:
    return RawSignal(
        signal_id="sig-j", strategy_id="tsmom", symbol="BTC-USDT", side=side,
        strength=0.9, sizing_hint=0.5, ttl_bars=24, thesis_tag="t",
        correlation_group="spot_cross_sectional_momo", created_at_bar=5000,
    )


# ===========================================================================
# 1. ENTRY production path (run_pipeline_for_signal) — info + activation wire.
# ===========================================================================


def _patch_entry_orch(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    """Replace GateOrchestrator with a fake that captures ctx.payload + judge_client.

    The fake is constructed by the REAL ``run_pipeline_for_signal`` body, so what
    it captures is exactly what production builds — not a hand-rolled fixture.
    """

    class _FakeOrch:
        def __init__(self, **kwargs: Any) -> None:
            captured["judge_client"] = kwargs.get("judge_client", "MISSING")

        async def run(self, ctx: Any, *, start_gate: int) -> list[GateResult]:
            captured["payload"] = dict(ctx.payload)
            # SIZED so the submit path proceeds (G6/G7 below are stubbed).
            return [
                GateResult(
                    decision=GateDecision.SIZED, next_gate=None,
                    payload={"sized": {"final_notional_usd": 80.0}},
                )
            ]

    async def _fake_monitor(_ctx: Any, *, client: Any) -> GateResult:
        return GateResult(decision=GateDecision.HOLD, next_gate=None)

    async def _fake_exit(_ctx: Any, *, client: Any) -> GateResult:
        return GateResult(decision=GateDecision.PROCEED, next_gate=None)

    monkeypatch.setattr(run_signal_mod, "GateOrchestrator", _FakeOrch)
    monkeypatch.setattr(run_signal_mod, "position_monitor_gate", _fake_monitor)
    monkeypatch.setattr(run_signal_mod, "adaptive_exit_gate", _fake_exit)
    monkeypatch.setattr(run_signal_mod, "log_gate_event", lambda *a, **k: None)


async def _run_entry(
    conn: sqlite3.Connection, state: ProdLoopState,
) -> None:
    async def _reserve_and_submit(**_: object) -> SimulatedTrade | None:
        return SimulatedTrade(
            signal_id="sig-j", venue="okx", symbol="BTC-USDT",
            strategy_id="tsmom", side="long", entry_price=100.0,
            notional_usd=80.0, open_ts=NOW, position_id="pos-j",
        )

    await run_signal_mod.run_pipeline_for_signal(
        conn=conn, haiku=None, state=state, strategy=SpotDonchianStrategy(), sig=_sig(),
        venue="okx", symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.01, last_price=100.0, universe_rows=[], now_ts=NOW,
        reserve_and_submit=_reserve_and_submit, phase="P0",
    )


@pytest.mark.asyncio
async def test_entry_production_payload_stamps_fused_evidence_and_ground(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The REAL entry path stamps the bot's own fused evidence + ground coverage."""
    _seat_ground(conn)
    captured: dict[str, Any] = {}
    _patch_entry_orch(monkeypatch, captured)
    state = ProdLoopState()
    await _run_entry(conn, state)
    payload = captured["payload"]
    # The judge's two info inputs are present, sourced from the ticker_ground row.
    assert payload["evidence"] == _FUSED_EVIDENCE
    assert payload["ticker_ground"] == {"has_sentiment": True, "has_event": True}
    # The fused evidence carries the REAL fuser keys (not the dead news_headline).
    assert "news_sentiment" in payload["evidence"]
    assert "news_headline" not in payload["evidence"]


@pytest.mark.asyncio
async def test_entry_threads_judge_client_into_orchestrator(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``state.judge_client`` reaches the orchestrator (judge actually runs)."""
    _seat_ground(conn)
    captured: dict[str, Any] = {}
    _patch_entry_orch(monkeypatch, captured)
    state = ProdLoopState()
    sentinel = object()
    state.judge_client = sentinel
    await _run_entry(conn, state)
    assert captured["judge_client"] is sentinel


@pytest.mark.asyncio
async def test_entry_graceful_when_no_ground_row(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ground row (name not yet covered) → no stamp, no crash (byte-identical)."""
    # No _seat_ground — the ticker_ground table is empty.
    captured: dict[str, Any] = {}
    _patch_entry_orch(monkeypatch, captured)
    state = ProdLoopState()  # judge_client defaults to None (dormant)
    await _run_entry(conn, state)
    payload = captured["payload"]
    assert "evidence" not in payload
    assert "ticker_ground" not in payload
    assert captured["judge_client"] is None


# ===========================================================================
# 2. EXIT production path (_evaluate_position live recalc) — info + activation.
# ===========================================================================


def _pos() -> dict[str, Any]:
    return {
        "venue": "okx", "symbol": "BTC-USDT", "side": "long",
        "strategy": "tsmom", "strategy_id": "tsmom", "position_id": "pos-j",
        "entry_price": 100.0, "last_price": 101.0, "atr_pct": 0.01,
        "opened_ts": NOW - 600, "qty": 0.8, "correlation_group": "g",
        "entry_atr_pct": 0.01,
    }


@pytest.mark.asyncio
async def test_exit_recalc_payload_stamps_evidence_and_threads_judge_client(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The REAL G7 live-recalc path stamps evidence/ground + threads judge_client."""
    _seat_ground(conn)
    captured: dict[str, Any] = {}

    async def _fake_monitor(ctx: Any, **_: Any) -> GateResult:
        # G6 ADJUST_EXIT routes the FSM into the G7 adaptive-exit gate branch —
        # the ONLY production path where the G7 judge runs (same surface as the
        # existing P1 GPT exit client). HOLD short-circuits before G7.
        return GateResult(
            decision=GateDecision.ADJUST_EXIT, next_gate=None, payload={}
        )

    async def _fake_exit(
        ctx: Any, *, judge_client: Any = "MISSING", **_: Any
    ) -> GateResult:
        captured["payload"] = dict(ctx.payload)
        captured["judge_client"] = judge_client
        return GateResult(decision=GateDecision.HOLD, next_gate=None, payload={})

    monkeypatch.setattr(recalc_mod, "position_monitor_gate", _fake_monitor)
    monkeypatch.setattr(recalc_mod, "adaptive_exit_gate", _fake_exit)
    monkeypatch.setattr(recalc_mod, "log_gate_event", lambda *a, **k: None)
    # Neutralize the deterministic precise-exit / session rails so the flow reaches
    # the G7 gate call (these are owned by the FSM and untouched by #32).
    monkeypatch.setattr(recalc_mod, "observe_probes", lambda **k: None)

    async def _no_session(**_: object) -> bool:
        return False

    monkeypatch.setattr(recalc_mod, "run_session_forced_exit", _no_session)

    async def _no_precise(*a: object, **k: object) -> bool:
        return False

    monkeypatch.setattr(recalc_mod, "run_precise_exit", _no_precise)

    sentinel = object()
    state = ProdLoopState()
    state.judge_client = sentinel

    async def _close_specific(**_: object) -> None:
        return None

    def _lookup_regime(_c: Any, _v: str, _s: str) -> str:
        return "bull_trend"

    await recalc_mod._evaluate_position(
        conn=conn, state=state, pos=_pos(), regime="bull_trend",
        gpt_client=None, now_ts=NOW, close_specific=_close_specific,
        lookup_regime=_lookup_regime, phase="P1",
    )
    payload = captured["payload"]
    assert payload["regime"] == "bull_trend"
    assert payload["evidence"] == _FUSED_EVIDENCE
    assert payload["ticker_ground"] == {"has_sentiment": True, "has_event": True}
    assert captured["judge_client"] is sentinel


# ===========================================================================
# 3. _resolve_judge_client — graceful no-op (no key / opt-out → None, no crash).
# ===========================================================================


def test_resolve_judge_client_none_without_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No OPENAI_API_KEY → judge client is None (dormant), NEVER a crash."""
    from polaris.scripts import production_paper_loop as loop_mod

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("POLARIS_AI_JUDGE", raising=False)
    assert loop_mod._resolve_judge_client() is None


def test_resolve_judge_client_opt_out_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POLARIS_AI_JUDGE=0 → judge fully off (None) even with a key present."""
    from polaris.scripts import production_paper_loop as loop_mod

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("POLARIS_AI_JUDGE", "0")
    assert loop_mod._resolve_judge_client() is None


def test_resolve_judge_client_builds_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Key present + not opted out → a real gpt-5-mini client is built."""
    from polaris.core.pipeline.agents._gpt_client import GPT_P0_MODEL
    from polaris.scripts import production_paper_loop as loop_mod

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("POLARIS_AI_JUDGE", raising=False)
    client = loop_mod._resolve_judge_client()
    assert client is not None
    assert getattr(client, "model", None) == GPT_P0_MODEL
