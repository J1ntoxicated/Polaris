"""G3 warm-pool-local-bottom discriminator — NEW shadow feature (behavior 0).

Pins the conductor_g3g4_cutover_2026-05-31 follow-up (#2): a NEW, explicitly
named shadow discriminator that makes a narrow G3 KILL reachable while the
GLOBAL quartile gate (CELL_MIN_POOL_SIZE=20 pool cardinality) holds every label
at ``cold`` — WITHOUT manufacturing a thin-sample kill.

Contract:
- ENGAGES only when quartile == 'cold' (global gate inactive) AND n_eff >= 5
  (genuinely warm) AND avg_pnl_r < 0 AND warm_pool_local_bottom is True →
  KILL scalar 0.0 reason 'warm_pool_local_bottom_losing'.
- warm_pool_local_bottom default False ⇒ byte-identical to the pre-feature rule.
- genuinely cold cell (n_eff < 5) = pass-through ALWAYS, even when flagged.
- a real quartile label (top/mid/bottom, global gate active) ⇒ existing
  Rules 2-4 own the decision — the new path does not run.
- helper: pool < 4 ⇒ False; cell not bottom ⇒ False; cell bottom+warm ⇒ True.
- behavior 0: shadow_conn=None ⇒ signal_validator_gate byte-identical; the
  production payload (build_validator_payload) is never touched.
"""

from __future__ import annotations

import sqlite3
import uuid

from polaris.core.pipeline.agents._shadow_rules import (
    WARM_POOL_LOCAL_BOTTOM_REASON,
    G3ShadowInputs,
    g3_shadow_inputs_from_payload,
    technical_validate_decision,
)
from polaris.core.pipeline.agents.signal_validator import (
    WARM_POOL_FLOOR_N_EFF,
    _warm_pool_local_bottom,
    signal_validator_gate,
)
from polaris.core.pipeline.gate_state import (
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000


class _MockGPTClient:
    def __init__(self, response_text: str = "{}") -> None:
        self.response_text = response_text
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                class _Block:
                    text = outer.response_text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _ctx(payload: dict, *, gate_id: int = 3) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-disc",
        position_id=None,
        gate_id=gate_id,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.RAW,
    )


def _seed_cell(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    regime: str,
    n_eff: float,
    score: float,
    avg_pnl_r: float = 0.0,
) -> None:
    conn.execute(
        """
        INSERT INTO cell_matrix_p0
            (exchange, strategy, ticker, regime, n_eff, wins_eff,
             pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts)
        VALUES ('okx', 'vb', ?, ?, ?, 0.0, 0.0, ?, ?, ?)
        """,
        (ticker, regime, n_eff, avg_pnl_r, score, NOW),
    )


# ===========================================================================
# Rule path — technical_validate_decision with warm_pool_local_bottom
# ===========================================================================


def test_warm_pool_local_bottom_kill_reachable_while_global_gate_cold() -> None:
    """quartile=cold (global gate inactive) + warm + losing + flagged → KILL."""
    inp = G3ShadowInputs(
        n_eff=8.0,  # genuinely warm
        quartile="cold",  # global cardinality gate inactive (<20 warm cells)
        avg_pnl_r=-0.4,  # losing
        warm_pool_local_bottom=True,
    )
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.KILL
    assert out.scalar == 0.0
    assert out.reason == WARM_POOL_LOCAL_BOTTOM_REASON


def test_flag_false_is_byte_identical_cold_quartile_losing_modify() -> None:
    """warm_pool_local_bottom=False ⇒ unchanged: cold-quartile losing → MODIFY."""
    base = dict(n_eff=8.0, quartile="cold", avg_pnl_r=-0.4)
    out = technical_validate_decision(G3ShadowInputs(**base))
    assert out.decision == GateDecision.MODIFY  # legacy cold-quartile trim
    # Explicit-False must match the default-omitted projection exactly.
    out_false = technical_validate_decision(
        G3ShadowInputs(**base, warm_pool_local_bottom=False)
    )
    assert out_false == out


def test_genuinely_cold_cell_passthrough_even_when_flagged() -> None:
    """n_eff < 5 = pass-through ALWAYS — the flag cannot KILL a cold cell."""
    inp = G3ShadowInputs(
        n_eff=3.0,  # genuinely cold (< CELL_WARM_MIN_N_EFF)
        quartile="cold",
        avg_pnl_r=-2.0,  # losing
        warm_pool_local_bottom=True,  # flagged, but cell is cold
    )
    out = technical_validate_decision(inp)
    assert out.decision != GateDecision.KILL


def test_flag_does_not_kill_when_avg_pnl_non_negative() -> None:
    """warm + flagged but avg_pnl_r >= 0 (no losing evidence) ⇒ no KILL."""
    inp = G3ShadowInputs(
        n_eff=8.0,
        quartile="cold",
        avg_pnl_r=0.1,
        warm_pool_local_bottom=True,
    )
    out = technical_validate_decision(inp)
    assert out.decision != GateDecision.KILL


def test_real_quartile_label_ignores_local_bottom_flag() -> None:
    """When the global gate is ACTIVE (real top/mid/bottom) Rules 2-4 own it.

    quartile='top' + flagged → still PASS (the new path only runs on 'cold').
    """
    inp = G3ShadowInputs(
        n_eff=12.0,
        quartile="top",
        avg_pnl_r=-0.5,  # even losing
        warm_pool_local_bottom=True,
    )
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.PASS


def test_real_bottom_label_still_uses_rule2_reason_not_new_reason() -> None:
    """quartile='bottom' losing → existing Rule 2 KILL, NOT the new reason."""
    inp = G3ShadowInputs(
        n_eff=8.0,
        quartile="bottom",
        avg_pnl_r=-0.4,
        warm_pool_local_bottom=True,
    )
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.KILL
    assert out.reason != WARM_POOL_LOCAL_BOTTOM_REASON
    assert out.reason.startswith("warm_bottom_losing")


def test_does_not_read_net_edge_field_still_absent() -> None:
    assert "net_edge_r" not in G3ShadowInputs.__dataclass_fields__


# ===========================================================================
# Helper — _warm_pool_local_bottom
# ===========================================================================


def test_helper_pool_below_min_members_returns_false(
    memdb: sqlite3.Connection,
) -> None:
    """Warm pool < 4 members ⇒ no quartile defined ⇒ False (no thin-sample)."""
    # Only 3 warm cells in regime 'chop'.
    _seed_cell(memdb, ticker="A-USDT", regime="chop", n_eff=10.0, score=0.5)
    _seed_cell(memdb, ticker="B-USDT", regime="chop", n_eff=10.0, score=0.3)
    _seed_cell(memdb, ticker="C-USDT", regime="chop", n_eff=10.0, score=0.1)
    out = _warm_pool_local_bottom(
        memdb, regime="chop", cell_score=0.0, cell_n_eff=10.0
    )
    assert out is False


def test_helper_cell_not_bottom_returns_false(memdb: sqlite3.Connection) -> None:
    """Cell score above the 25th-pct threshold ⇒ not bottom ⇒ False."""
    for i, sc in enumerate([0.1, 0.2, 0.3, 0.4, 0.5]):
        _seed_cell(
            memdb, ticker=f"T{i}-USDT", regime="bull", n_eff=10.0, score=sc
        )
    # cell_score 0.45 is in the top half of the pool → not bottom.
    out = _warm_pool_local_bottom(
        memdb, regime="bull", cell_score=0.45, cell_n_eff=10.0
    )
    assert out is False


def test_helper_cell_bottom_and_warm_returns_true(
    memdb: sqlite3.Connection,
) -> None:
    """Warm cell at/below the 25th-pct of a >=4-member warm pool ⇒ True."""
    for i, sc in enumerate([0.10, 0.20, 0.30, 0.40, 0.50]):
        _seed_cell(
            memdb, ticker=f"T{i}-USDT", regime="bear", n_eff=10.0, score=sc
        )
    # q25 of [0.10..0.50] = 0.20; a cell at 0.10 is <= threshold → bottom.
    out = _warm_pool_local_bottom(
        memdb, regime="bear", cell_score=0.10, cell_n_eff=10.0
    )
    assert out is True


def test_helper_cold_cell_short_circuits_false(memdb: sqlite3.Connection) -> None:
    """A cold cell (n_eff < floor) is never local-bottom regardless of pool."""
    for i, sc in enumerate([0.10, 0.20, 0.30, 0.40, 0.50]):
        _seed_cell(
            memdb, ticker=f"T{i}-USDT", regime="bear", n_eff=10.0, score=sc
        )
    out = _warm_pool_local_bottom(
        memdb, regime="bear", cell_score=0.10, cell_n_eff=WARM_POOL_FLOOR_N_EFF - 1.0
    )
    assert out is False


def test_helper_regime_scoped(memdb: sqlite3.Connection) -> None:
    """Pool is scoped to the SAME regime — other-regime cells do not count."""
    # 5 warm cells but in a DIFFERENT regime → 'chop' pool is empty for the call.
    for i, sc in enumerate([0.10, 0.20, 0.30, 0.40, 0.50]):
        _seed_cell(
            memdb, ticker=f"T{i}-USDT", regime="bull", n_eff=10.0, score=sc
        )
    out = _warm_pool_local_bottom(
        memdb, regime="chop", cell_score=0.10, cell_n_eff=10.0
    )
    assert out is False  # no warm pool in 'chop' → below min members


# ===========================================================================
# Integration — shadow path computes the flag from the live pool
# ===========================================================================


async def test_g3_shadow_discriminator_fires_via_signal_validator(
    memdb: sqlite3.Connection,
) -> None:
    """End-to-end: cold global label + warm losing cell at local bottom →
    shadow KILL logged, while GPT decision (PASS) is what the gate returns."""
    from polaris.core.pipeline.agents.shadow_log import fetch_shadow_events

    # Warm pool of 5 cells in regime 'chop' (global gate < 20 → label 'cold').
    for i, sc in enumerate([0.10, 0.20, 0.30, 0.40, 0.50]):
        _seed_cell(
            memdb, ticker=f"P{i}-USDT", regime="chop", n_eff=10.0, score=sc
        )
    gpt = _MockGPTClient('{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx(
        {
            "raw_signal": {"strategy": "vb", "score": 1.0},
            "cell_routing": {
                "quartile": "cold",  # global cardinality gate inactive
                "n_eff": 9.0,  # warm
                "avg_pnl_r": -0.5,  # losing
                "score": 0.05,  # below the 25th pct (0.20) → local bottom
            },
            "regime": "chop",
            "net_edge_r": -3.0,  # must NOT influence anything
        },
        gate_id=3,
    )
    result = await signal_validator_gate(ctx, client=gpt, shadow_conn=memdb)
    assert result.decision == GateDecision.PASS  # GPT wins (behavior 0)
    rows = fetch_shadow_events(memdb)
    assert len(rows) == 1
    assert rows[0]["technical_decision"] == "KILL"
    assert rows[0]["technical_reason"] == WARM_POOL_LOCAL_BOTTOM_REASON
    assert rows[0]["gpt_decision"] == "PASS"
    assert rows[0]["mismatch"] == 1
    assert rows[0]["cell_warm"] == 1


async def test_g3_shadow_discriminator_does_not_fire_when_not_local_bottom(
    memdb: sqlite3.Connection,
) -> None:
    """Same warm pool, but this cell's score is high → not local bottom →
    falls back to the legacy cold-quartile MODIFY (no new KILL)."""
    from polaris.core.pipeline.agents.shadow_log import fetch_shadow_events

    for i, sc in enumerate([0.10, 0.20, 0.30, 0.40, 0.50]):
        _seed_cell(
            memdb, ticker=f"Q{i}-USDT", regime="chop", n_eff=10.0, score=sc
        )
    gpt = _MockGPTClient('{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx(
        {
            "raw_signal": {"strategy": "vb", "score": 1.0},
            "cell_routing": {
                "quartile": "cold",
                "n_eff": 9.0,
                "avg_pnl_r": -0.5,
                "score": 0.49,  # near the top of the pool → NOT local bottom
            },
            "regime": "chop",
        },
        gate_id=3,
    )
    await signal_validator_gate(ctx, client=gpt, shadow_conn=memdb)
    rows = fetch_shadow_events(memdb)
    assert len(rows) == 1
    assert rows[0]["technical_decision"] != "KILL"
    assert rows[0]["technical_reason"] != WARM_POOL_LOCAL_BOTTOM_REASON


async def test_g3_shadow_conn_none_byte_identical_no_pool_read() -> None:
    """Behavior 0: shadow_conn=None ⇒ whole shadow block skipped, gate identical.

    The new discriminator defaults off and no pool query runs (None conn).
    """
    gpt = _MockGPTClient('{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx(
        {
            "raw_signal": {"strategy": "vb", "score": 1.0},
            "cell_routing": {
                "quartile": "cold",
                "n_eff": 9.0,
                "avg_pnl_r": -0.5,
                "score": 0.05,
            },
            "regime": "chop",
        },
        gate_id=3,
    )
    result = await signal_validator_gate(ctx, client=gpt)  # no shadow_conn
    assert result.decision == GateDecision.PASS
    assert result.payload["validated_signal"]["strength_scalar"] == 1.0


def test_g3_inputs_projection_default_flag_false() -> None:
    """g3_shadow_inputs_from_payload defaults warm_pool_local_bottom to False."""
    inp = g3_shadow_inputs_from_payload({"cell_routing": {"quartile": "cold"}})
    assert inp.warm_pool_local_bottom is False
