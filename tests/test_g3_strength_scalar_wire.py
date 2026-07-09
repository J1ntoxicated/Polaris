"""TDD — G3 MODIFY strength_scalar actually reaches the T4 continuous fold.

DEMO/PAPER paper bot only. Aggressive bias preserved (flow_not_block, no
defensive throttle): a G3 MODIFY < 1.0 is an edge-proportional TRIM, never a
block, and a MODIFY > 1.0 pushes the SAME continuous scalar UP. 9-stack ban
intact — this wires the ALREADY-EXISTING ``SignalIntent.strength_scalar``
field (Wave B agenda ②) through the payload→intent seam; ``compute_size``'s
``fold_strength_scalar`` (single clamp, unchanged) is what actually folds it.

Root cause (bug): ``build_sizer_payload`` builds ``ctx.payload["signal_intent"]``
ONCE, up front, before the gate orchestrator walks G1→G8 over the SAME
``GateContext`` — so it is constructed BEFORE G3 (``signal_validator_gate``)
ever runs and cannot know G3's verdict. G3's actual decision lands in a
DIFFERENT payload key (``ctx.payload["validated_signal"]["strength_scalar"]``)
that nothing downstream ever read for sizing. Fix: ``entry_sizer_gate`` (G5)
folds it onto the intent via ``dataclasses.replace()`` right before
``compute_size`` — the SAME seam ``judge_conviction``/SIZE_UP already uses.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from polaris.core.pipeline._sizer_payload import build_sizer_payload
from polaris.core.pipeline.agents.entry_sizer import entry_sizer_gate
from polaris.core.pipeline.gate_state import (
    GATE_ENTRY_SIZER,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.core.sizing import (
    PortfolioState,
    SignalIntent,
    StrategyRiskState,
)
from polaris.core.sizing.schema import CONT_SCALAR_MAX, CONT_SCALAR_MIN
from polaris.strategies.base import RawSignal

NOW = 1_900_000_000


# ---------------------------------------------------------------------------
# build_sizer_payload — construction-site kwarg (interface parity, byte-
# identical default; real live wiring happens downstream in entry_sizer_gate,
# see below — G3 has not run yet when this function is called in the live
# bar pipeline).
# ---------------------------------------------------------------------------


def _raw_signal() -> RawSignal:
    return RawSignal(
        signal_id="sig-1",
        strategy_id="volume_burst",
        symbol="BTC-USDT",
        side="long",
        strength=1.0,
        sizing_hint=1.0,
        ttl_bars=5,
        thesis_tag="t",
        correlation_group="momentum_crypto",
    )


def test_build_sizer_payload_threads_strength_scalar() -> None:
    payload = build_sizer_payload(
        raw_signal=_raw_signal(), venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
        asset_class="crypto", regime="bull_trend", track="A",
        listing_age_hours=72.0, strength_scalar=0.85,
    )
    intent = payload["signal_intent"]
    assert isinstance(intent, SignalIntent)
    assert intent.strength_scalar == pytest.approx(0.85)


def test_build_sizer_payload_default_strength_scalar_is_byte_identical() -> None:
    payload = build_sizer_payload(
        raw_signal=_raw_signal(), venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
        asset_class="crypto", regime="bull_trend", track="A",
        listing_age_hours=72.0,
    )
    assert payload["signal_intent"].strength_scalar == 1.0


# ---------------------------------------------------------------------------
# _sized_notional (P5 tick engine) — same interface parity; tick path has no
# G3 step, stays default 1.0 forever at its current call sites.
# ---------------------------------------------------------------------------


def test_sized_notional_threads_strength_scalar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.ticks.signals import TickIntent
    from polaris.scripts import _production_tick_engine as eng_mod

    captured: dict[str, Any] = {}

    def _fake_compute_size(conn: Any, *, intent: Any, risk_state: Any,
                            portfolio: Any, now_ts: int) -> Any:
        captured["strength_scalar"] = intent.strength_scalar

        class _Sized:
            final_notional_usd = 100.0

        return _Sized()

    monkeypatch.setattr(eng_mod, "compute_size", _fake_compute_size)
    monkeypatch.setattr(eng_mod, "_read_strategy_risk_state", lambda *a, **k: object())
    monkeypatch.setattr(eng_mod, "_read_portfolio_state", lambda *a, **k: object())

    intent = TickIntent(
        venue="okx", symbol="BTC-USDT", side="long", conviction=0.9,
        signal_id="burst_rider", signal_family="momentum", ref_price=60_000.0,
    )
    eng_mod._sized_notional(
        None, intent=intent, asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="trend", now_ts=int(time.time()),
        strength_scalar=0.85,
    )
    assert captured["strength_scalar"] == pytest.approx(0.85)


def test_sized_notional_default_strength_scalar_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.ticks.signals import TickIntent
    from polaris.scripts import _production_tick_engine as eng_mod

    captured: dict[str, Any] = {}

    def _fake_compute_size(conn: Any, *, intent: Any, risk_state: Any,
                            portfolio: Any, now_ts: int) -> Any:
        captured["strength_scalar"] = intent.strength_scalar

        class _Sized:
            final_notional_usd = 100.0

        return _Sized()

    monkeypatch.setattr(eng_mod, "compute_size", _fake_compute_size)
    monkeypatch.setattr(eng_mod, "_read_strategy_risk_state", lambda *a, **k: object())
    monkeypatch.setattr(eng_mod, "_read_portfolio_state", lambda *a, **k: object())

    intent = TickIntent(
        venue="okx", symbol="BTC-USDT", side="long", conviction=0.9,
        signal_id="burst_rider", signal_family="momentum", ref_price=60_000.0,
    )
    eng_mod._sized_notional(
        None, intent=intent, asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="trend", now_ts=int(time.time()),
    )
    assert captured["strength_scalar"] == 1.0


# ---------------------------------------------------------------------------
# entry_sizer_gate — the ACTUAL payload→intent seam. This is what makes a
# live G3 MODIFY verdict reach compute_size (the bug this fix closes).
# ---------------------------------------------------------------------------


def _intent(strength_scalar: float = 1.0) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-g3",
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=1.0,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
        signal_family="momentum",
        strength_scalar=strength_scalar,
    )


def _unsaturated_intent(strength_scalar: float = 1.0) -> SignalIntent:
    """Same as ``_intent`` but signal_strength/family kept OFF the CONT_SCALAR
    ceiling (reversion family = neutral regime-fit) so an upward MODIFY push
    is observable — ``_intent``'s momentum×bull_trend combo already saturates
    continuous_scalar at CONT_SCALAR_MAX for strength_scalar==1.0."""
    return SignalIntent(
        signal_id="sig-g3",
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=0.9,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
        signal_family="reversion",
        strength_scalar=strength_scalar,
    )


def _risk_state() -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst", closed_trades=25,
        kelly_p=0.55, kelly_q=0.45, kelly_fraction=0.05,
        win_streak=0, hit_rate_10=0.5, updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[],
        fill_rate_active_cut=False,
    )


def _ctx(
    *, validated_scalar: float | None, intent: SignalIntent | None = None
) -> GateContext:
    payload: dict[str, Any] = {
        "signal_intent": intent if intent is not None else _intent(1.0),
        "risk_state": _risk_state(),
        "portfolio": _portfolio(),
    }
    if validated_scalar is not None:
        payload["validated_signal"] = {
            "symbol": "BTC-USDT",
            "strength_scalar": validated_scalar,
        }
    return GateContext(
        run_id="r", signal_id="s", position_id=None, gate_id=GATE_ENTRY_SIZER,
        venue="okx", symbol="BTC-USDT", strategy_id="volume_burst",
        payload=payload, started_ts=NOW, state=SignalLifecycle.WATCHED,
    )


@pytest.mark.asyncio
async def test_g3_modify_scalar_trims_the_continuous_scalar(
    memdb: sqlite3.Connection,
) -> None:
    """G3 MODIFY(0.85) reaching entry_sizer_gate must lower continuous_scalar
    vs a PASS(1.0) baseline — the edge-proportional trim actually fires."""
    base = await entry_sizer_gate(_ctx(validated_scalar=None), conn=memdb)
    modified = await entry_sizer_gate(_ctx(validated_scalar=0.85), conn=memdb)
    assert base.decision == GateDecision.SIZED
    assert modified.decision == GateDecision.SIZED
    base_cont = base.payload["sized"]["proposal"]["continuous_scalar"]
    mod_cont = modified.payload["sized"]["proposal"]["continuous_scalar"]
    assert mod_cont < base_cont
    assert modified.payload["sized"]["final_risk_pct"] < base.payload["sized"]["final_risk_pct"]


@pytest.mark.asyncio
async def test_g3_modify_scalar_above_one_pushes_up(memdb: sqlite3.Connection) -> None:
    """MODIFY > 1.0 (a confident G3 read) pushes the SAME scalar UP — the
    aggressive-bias-preserving direction (flow_not_block: never a block)."""
    base = await entry_sizer_gate(
        _ctx(validated_scalar=None, intent=_unsaturated_intent()), conn=memdb
    )
    modified = await entry_sizer_gate(
        _ctx(validated_scalar=1.3, intent=_unsaturated_intent()), conn=memdb
    )
    base_cont = base.payload["sized"]["proposal"]["continuous_scalar"]
    mod_cont = modified.payload["sized"]["proposal"]["continuous_scalar"]
    assert mod_cont > base_cont


@pytest.mark.asyncio
async def test_g3_pass_scalar_one_is_byte_identical(memdb: sqlite3.Connection) -> None:
    """PASS (strength_scalar==1.0, explicitly stamped) must be byte-identical
    to no validated_signal at all — the regression guard."""
    absent = await entry_sizer_gate(_ctx(validated_scalar=None), conn=memdb)
    explicit_pass = await entry_sizer_gate(_ctx(validated_scalar=1.0), conn=memdb)
    assert absent.payload["sized"] == explicit_pass.payload["sized"]


@pytest.mark.asyncio
async def test_g3_modify_scalar_does_not_add_a_new_chain_factor(
    memdb: sqlite3.Connection,
) -> None:
    """9-stack guard: MODIFY only moves continuous_scalar — tier/cell/listing
    factors are untouched (no new multiplier slot introduced)."""
    base = await entry_sizer_gate(_ctx(validated_scalar=None), conn=memdb)
    modified = await entry_sizer_gate(_ctx(validated_scalar=0.85), conn=memdb)
    bp = base.payload["sized"]["proposal"]
    mp = modified.payload["sized"]["proposal"]
    assert mp["tier_amplifier"] == bp["tier_amplifier"]
    assert mp["cell_routing_mult"] == bp["cell_routing_mult"]
    assert mp["listing_watchdog_mult"] == bp["listing_watchdog_mult"]


# ---------------------------------------------------------------------------
# Property — end-to-end through entry_sizer_gate. Clamp-once ownership stays
# with compute_size's fold_strength_scalar (engine.py:710, unchanged) — the
# payload→intent seam in entry_sizer_gate must not re-clamp. G3 itself only
# ever emits strength_scalar in [MODIFY_MIN, MODIFY_MAX] = [0.5, 1.5]
# (signal_validator.py _validate_decision / _g3_technical).
# ---------------------------------------------------------------------------


@given(scalar=st.floats(min_value=0.5, max_value=1.5, allow_nan=False))
@settings(
    max_examples=25, deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_continuous_scalar_stays_in_band_for_any_g3_scalar(
    scalar: float, memdb: sqlite3.Connection
) -> None:
    result = asyncio.run(entry_sizer_gate(_ctx(validated_scalar=scalar), conn=memdb))
    assert result.decision == GateDecision.SIZED
    cont = result.payload["sized"]["proposal"]["continuous_scalar"]
    # engine.py's ONE clamp (fold_strength_scalar) — never breached, never
    # double-clamped by this seam.
    assert CONT_SCALAR_MIN <= cont <= CONT_SCALAR_MAX


@given(
    lo=st.floats(min_value=0.5, max_value=1.5, allow_nan=False),
    hi=st.floats(min_value=0.5, max_value=1.5, allow_nan=False),
)
@settings(
    max_examples=25, deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_higher_g3_scalar_never_yields_lower_continuous_scalar(
    lo: float, hi: float, memdb: sqlite3.Connection
) -> None:
    if lo > hi:
        lo, hi = hi, lo
    r_lo = asyncio.run(entry_sizer_gate(_ctx(validated_scalar=lo), conn=memdb))
    r_hi = asyncio.run(entry_sizer_gate(_ctx(validated_scalar=hi), conn=memdb))
    cont_lo = r_lo.payload["sized"]["proposal"]["continuous_scalar"]
    cont_hi = r_hi.payload["sized"]["proposal"]["continuous_scalar"]
    assert cont_hi >= cont_lo
