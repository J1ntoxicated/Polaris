"""ADR-012 Slice 1 — probe → engine core (observe-only, byte-identical).

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block: the probe layer ONLY
describes the world; the engine in observe mode returns ALL knobs ``None`` +
``applied=False`` so NOTHING threads into ``run_precise_exit``. These tests
prove: ProbeReading bounds, per-probe fault isolation, ABSTAIN-on-missing,
the shared action enum (engine action == log action), and the observe-mode
no-knobs contract.
"""

from __future__ import annotations

import math

from polaris.core.probes import (
    EngineDecision,
    ExitEngine,
    ProbeBus,
    ProbeContext,
    ProbeKind,
    ProbeReading,
)
from polaris.core.probes.catalog import (
    LossDefenseProbe,
    ProfitTakingProbe,
    SessionHoursProbe,
    TechnicalProbe,
)


def _ctx(**over: object) -> ProbeContext:
    base: dict[str, object] = dict(
        position_id="p1",
        venue="okx",
        symbol="BTC-USDT",
        underlying_group_id="BTC",
        side="long",
        entry_price=100.0,
        last_price=101.0,
        atr_pct=0.01,
        pnl_r=0.3,
        mfe_r=0.6,
        mae_r=-0.1,
        held_seconds=120,
        volume_now=10.0,
        volume_z=0.5,
        atr_slope=0.001,
        recent_ticks=[
            {"ts": 1, "close": 100.0, "volume": 9.0},
            {"ts": 2, "close": 100.5, "volume": 9.5},
            {"ts": 3, "close": 101.0, "volume": 10.0},
        ],
        exit_state="open",
        regime="trend",
        seconds_to_close=None,
        now_ts=1000,
    )
    base.update(over)
    return ProbeContext(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ProbeReading bounds + frozen
# ---------------------------------------------------------------------------


def test_probe_reading_is_frozen() -> None:
    r = ProbeReading(
        probe_id="x", kind="technical", lean=0.0, confidence=0.0, evidence={}
    )
    try:
        r.lean = 1.0  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("ProbeReading must be frozen")


def test_profit_probe_lean_in_bounds_and_signed() -> None:
    # Big giveback (mfe 1.2R, pnl 0.2R → 1.0R round-trip) → adverse (negative).
    r = ProfitTakingProbe().evaluate(_ctx(mfe_r=1.2, pnl_r=0.2))
    assert r is not None
    assert -1.0 <= r.lean <= 1.0
    assert 0.0 <= r.confidence <= 1.0
    assert r.lean < 0.0  # giveback pressure → favor harvest/tighten


def test_profit_probe_abstains_without_mfe() -> None:
    assert ProfitTakingProbe().evaluate(_ctx(mfe_r=0.0)) is None


def test_loss_probe_adverse_lean_on_deep_mae() -> None:
    r = LossDefenseProbe().evaluate(_ctx(mae_r=-0.9, pnl_r=-0.8, held_seconds=300))
    assert r is not None
    assert r.lean < 0.0
    assert -1.0 <= r.lean <= 1.0


def test_loss_probe_abstains_when_not_adverse() -> None:
    # In profit, no adverse excursion → ABSTAIN.
    assert LossDefenseProbe().evaluate(_ctx(mae_r=0.0, pnl_r=0.5)) is None


def test_technical_probe_bounds() -> None:
    r = TechnicalProbe().evaluate(_ctx(atr_slope=0.01))
    assert r is not None
    assert -1.0 <= r.lean <= 1.0
    assert 0.0 <= r.confidence <= 1.0


def test_technical_probe_abstains_without_ticks() -> None:
    assert TechnicalProbe().evaluate(_ctx(recent_ticks=[])) is None


def test_session_probe_time_only_lean_grows_near_close() -> None:
    # Both inside the lead window so both produce a reading; closer = stronger.
    far = SessionHoursProbe().evaluate(_ctx(seconds_to_close=1500))
    near = SessionHoursProbe().evaluate(_ctx(seconds_to_close=60))
    assert far is not None and near is not None
    # Closer to close → stronger tighten/exit lean (more negative).
    assert near.lean < far.lean
    assert -1.0 <= near.lean <= 1.0


def test_session_probe_abstains_far_beyond_lead() -> None:
    # Far beyond the lead window → no proximity → ABSTAIN (TIME-only, pnl-blind).
    assert SessionHoursProbe().evaluate(_ctx(seconds_to_close=36000)) is None


def test_session_probe_abstains_when_no_close() -> None:
    assert SessionHoursProbe().evaluate(_ctx(seconds_to_close=None)) is None


def test_session_probe_independent_of_pnl() -> None:
    a = SessionHoursProbe().evaluate(_ctx(seconds_to_close=120, pnl_r=2.0))
    b = SessionHoursProbe().evaluate(_ctx(seconds_to_close=120, pnl_r=-2.0))
    assert a is not None and b is not None
    assert a.lean == b.lean  # TIME only, never pnl


# ---------------------------------------------------------------------------
# ProbeBus fault isolation
# ---------------------------------------------------------------------------


class _RaisingProbe:
    probe_id: str = "boom"
    kind: ProbeKind = "technical"

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
        raise RuntimeError("probe blew up")


def test_bus_fault_isolation_one_raises_others_run() -> None:
    bus = ProbeBus([_RaisingProbe(), ProfitTakingProbe()])
    readings = bus.observe(_ctx(mfe_r=1.2, pnl_r=0.2))
    # The raising probe is skipped (logged), the good probe still produced.
    assert any(r.probe_id == "profit_taking" for r in readings)
    assert all(r.probe_id != "boom" for r in readings)


def test_bus_drops_none_abstentions() -> None:
    bus = ProbeBus([LossDefenseProbe()])
    # not adverse → ABSTAIN → empty list (no None in output).
    readings = bus.observe(_ctx(mae_r=0.0, pnl_r=0.5))
    assert readings == []


# ---------------------------------------------------------------------------
# ExitEngine.compose — observe-mode no-knobs contract + shared enum
# ---------------------------------------------------------------------------


def test_observe_mode_all_knobs_none_not_applied() -> None:
    bus = ProbeBus(
        [ProfitTakingProbe(), LossDefenseProbe(), TechnicalProbe(), SessionHoursProbe()]
    )
    readings = bus.observe(_ctx(mfe_r=1.2, pnl_r=0.2, mae_r=-0.6, seconds_to_close=60))
    dec = ExitEngine().compose(readings, mode="observe")
    assert isinstance(dec, EngineDecision)
    # observe mode: NOTHING can thread into run_precise_exit.
    assert dec.trail_mult is None
    assert dec.mfe_protect is None
    assert dec.profit_target_r is None
    assert dec.widen_atr_mult is None
    assert dec.applied is False
    # ...but it STILL computes the composite + the action it WOULD take (log).
    assert dec.action in {"HOLD", "WIDEN", "TIGHTEN", "HARVEST", "SWAP"}
    assert -1.0 <= dec.composite_lean <= 1.0


def test_observe_mode_empty_readings_is_hold_neutral() -> None:
    dec = ExitEngine().compose([], mode="observe")
    assert dec.action == "HOLD"
    assert dec.composite_lean == 0.0
    assert dec.applied is False


def test_engine_action_enum_shared_with_log() -> None:
    # The same EngineDecision.action value is what the log records — one enum.
    dec = ExitEngine().compose(
        [ProbeReading("profit_taking", "profit", -0.8, 0.9, {})], mode="observe"
    )
    assert dec.action in {"HOLD", "WIDEN", "TIGHTEN", "HARVEST", "SWAP"}
    # adverse composite → leans toward harvest/tighten, never widen.
    assert dec.action in {"TIGHTEN", "HARVEST", "HOLD"}


def test_composite_lean_is_confidence_weighted() -> None:
    eng = ExitEngine()
    # Two readings: strong-confidence favorable + weak-confidence adverse.
    fav = ProbeReading("a", "technical", 1.0, 1.0, {})
    adv = ProbeReading("b", "loss", -1.0, 0.1, {})
    dec = eng.compose([fav, adv], mode="observe")
    assert dec.composite_lean > 0.0  # favorable dominates by confidence weight
    assert math.isfinite(dec.composite_lean)
