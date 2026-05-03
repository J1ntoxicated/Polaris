"""Unit tests — `invasion.trade.exit.ExitEngine` + `calc_entry_exits`
(F-N7 PR3).

Covers:

Golden-path (6)
    1. STOP fires when pnl <= hard_stop_pct.
    2. Full TRAIL activation + retrace triggers TRAIL reason.
    3. Trail tier progression — distance scales with max_pnl across tiers.
    4. EARLY FLAT / flat_kill fires after age > flat_kill_sec with no peak.
    5. TIME MAX fires when age > max_hold_sec (time_max_enabled=1 path).
    6. min_hold gate suppresses non-STOP exits.

Invariant (4)
    7.  I-E1 — STATE_ALLOWED_TRIGGERS[HARVEST] has no TIME_LOSER (regression
        lock on top of `exit_types.py` import-time assertion).
    8.  I-E3 — PROTECTED state + pnl < 0 returns PROTECTED_BEP reason via
        FSM path (flag-on okx×crypto slice).
    9.  I-E4 — TOUCHED_PROFIT + age overflow produces NO TIME_LOSER decision
        under the FSM path.
    10. I-E5 — OPEN + max_pnl=0 + age > time_exit_max_age_sec + pnl < 0 may
        produce TIME_LOSER.

Legacy-branch tests suppress the FSM gate via `exit_fsm_enabled=0`.
Invariant tests exercise the FSM gate explicitly.
"""
from __future__ import annotations

import pytest

from invasion.config import param_registry as pr
from invasion.trade.exit import ExitEngine
from invasion.trade.exit_fsm import ExitFSM
from invasion.trade.exit_types import (
    STATE_ALLOWED_TRIGGERS,
    ExitState,
    ExitTrigger,
)
from invasion.trade.position import Position


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


def _make_pos(
    *,
    exchange: str = "okx",
    asset_group: str = "crypto",
    direction: str = "long",
    entry_price: float = 100.0,
    size_usd: float = 1000.0,
    pnl_pct: float = 0.0,
    max_profit_pct: float = 0.0,
    age_sec: float = 0.0,
    state: ExitState = ExitState.OPEN,
    exit_params: dict | None = None,
    adopted: bool = False,
) -> Position:
    """Construct a Position with precise internal state for exit tests.

    `age_sec` is applied by back-dating entry_time relative to time.time()
    at construction; the caller is expected to already have monkey-patched
    time.time if they want determinism.
    """
    import time as _t
    now = _t.time()
    pos = Position(
        ticker="BTCUSDT",
        direction=direction,
        exchange=exchange,
        asset_group=asset_group,
        entry_price=entry_price,
        size_usd=size_usd,
        entry_time=now - age_sec,
        exit_params=exit_params or {},
        entry_signal={"score": 0},
    )
    pos.pnl_pct = pnl_pct
    pos.max_profit_pct = max_profit_pct
    pos.current_price = entry_price * (1 + pnl_pct / 100.0)
    pos.state = state
    pos.adopted = adopted
    return pos


@pytest.fixture
def engine() -> ExitEngine:
    """ExitEngine with no bus + no config — enough for check() paths."""
    return ExitEngine(config=type("C", (), {"group_volatility_windows": {}})())


@pytest.fixture
def fsm_off():
    """Disable FSM globally so legacy branch is exercised.

    Saves+restores the three flags the tests touch.
    """
    prev = {
        "exit_fsm_enabled": pr.get("exit_fsm_enabled"),
        "exit_fsm_enabled_okx_crypto": None,
    }
    try:
        prev["exit_fsm_enabled_okx_crypto"] = pr.get("exit_fsm_enabled_okx_crypto")
    except Exception:
        prev["exit_fsm_enabled_okx_crypto"] = None
    pr.set("exit_fsm_enabled", 0, source="test")
    yield
    pr.set("exit_fsm_enabled", prev["exit_fsm_enabled"] or 0, source="test")
    if prev["exit_fsm_enabled_okx_crypto"] is not None:
        pr.set(
            "exit_fsm_enabled_okx_crypto",
            prev["exit_fsm_enabled_okx_crypto"],
            source="test",
        )


@pytest.fixture
def fsm_on():
    """Enable FSM globally + okx×crypto slice for invariant tests."""
    prev = pr.get("exit_fsm_enabled")
    try:
        prev_slice = pr.get("exit_fsm_enabled_okx_crypto")
    except Exception:
        prev_slice = 0
    pr.set("exit_fsm_enabled", 1, source="test")
    pr.set("exit_fsm_enabled_okx_crypto", 1, source="test")
    yield
    pr.set("exit_fsm_enabled", prev or 0, source="test")
    pr.set("exit_fsm_enabled_okx_crypto", prev_slice or 0, source="test")


@pytest.fixture
def no_warmup(monkeypatch):
    """Disable warm-up guard so min_hold-free tests can fire exits."""
    monkeypatch.setattr(
        "invasion.utils.runtime.is_bot_warming_up",
        lambda *a, **kw: False,
    )


# ────────────────────────────────────────────────────────────────────
# Golden-path (6)
# ────────────────────────────────────────────────────────────────────


def test_exit_hard_stop_triggers(engine, fsm_off, no_warmup) -> None:
    """STOP fires when pnl <= hard_stop_pct, ignoring min_hold.

    Use pnl=-10% which sits below any regime-scaled stop floor for all
    branches so the test is robust to tuning changes.
    """
    pos = _make_pos(
        pnl_pct=-10.0,     # past any regime stop (floor ≥ -6%)
        max_profit_pct=0.0,
        age_sec=5.0,
        # Pin a loose exit_params so no None preg fallback surprises.
        exit_params={"hard_stop_pct": -1.5, "min_hold_sec": 60,
                     "fee_floor": -5.0, "profit_cap": 10.0},
    )
    price = pos.entry_price * (1 + pos.pnl_pct / 100.0)
    reason = engine.check(pos, price, regime="risk_on")
    assert reason is not None
    assert reason.startswith("STOP") or reason.startswith("CATASTROPHIC_STOP")


def test_exit_trail_activates_at_threshold(engine, fsm_off, no_warmup) -> None:
    """max_pnl above trail_activate + retrace below trail_floor → TRAIL."""
    # Build exit_params with a simple trail ladder: activate at 1.0%, distance 0.2%
    ep = {
        "trail_tiers": [[1.0, 0.2]],
        "trail_activate": 1.0,
        "hard_stop_pct": -5.0,
        "fee_floor": -5.0,
        "min_hold_sec": 0,
        "max_hold_sec": 7_200,
        "flat_kill_sec": 7_200,
        "flat_peak_pct": 0.0,
        "profit_cap": 10.0,
        "bep_activate": 0.3,
        "bep_distance": 0.2,
        "partial_close": 0,
    }
    pos = _make_pos(
        pnl_pct=0.5,       # retraced
        max_profit_pct=1.5, # well above trail_activate
        age_sec=600.0,
        exit_params=ep,
    )
    price = pos.entry_price * (1 + pos.pnl_pct / 100.0)
    reason = engine.check(pos, price, regime="risk_on")
    assert reason is not None
    assert "TRAIL" in reason


def test_exit_tier_progression(engine) -> None:
    """`_trail_distance` linearly interpolates between sorted tiers."""
    # 3 tiers: (0.5, 0.2), (1.5, 0.5), (3.0, 0.8)
    ep = {"trail_tiers": [[0.5, 0.2], [1.5, 0.5], [3.0, 0.8]]}
    # Below first tier → first tier distance
    assert engine._trail_distance(0.3, ep) == pytest.approx(0.2)
    # At first tier threshold → first tier distance
    assert engine._trail_distance(0.5, ep) == pytest.approx(0.2)
    # Midway between tier 1 and tier 2 (mp=1.0 between 0.5 and 1.5)
    # ratio = 0.5 → dist = 0.2 + 0.5 * (0.5-0.2) = 0.35
    assert engine._trail_distance(1.0, ep) == pytest.approx(0.35)
    # Above highest tier → tier 3 distance
    assert engine._trail_distance(5.0, ep) == pytest.approx(0.8)


def test_exit_flat_kill_fires(engine, fsm_off, no_warmup) -> None:
    """age > flat_kill_sec, max < flat_peak_pct, |pnl| small → TIME STALE."""
    ep = {
        "hard_stop_pct": -5.0,
        "fee_floor": -5.0,
        "min_hold_sec": 0,
        "flat_kill_sec": 600,       # 10 min
        "flat_peak_pct": 0.2,
        "max_hold_sec": 7_200,
        "profit_cap": 10.0,
        "trail_activate": 10.0,     # high so TRAIL cannot fire
        "trail_tiers": [[10.0, 0.5]],
        "bep_activate": 10.0,
        "bep_distance": 0.2,
        "partial_close": 0,
    }
    pos = _make_pos(
        pnl_pct=-0.1,     # below flat_kill_loss_floor (-0.05)
        max_profit_pct=0.0,
        age_sec=1_800.0,  # 30 min, well past flat_kill
        exit_params=ep,
    )
    price = pos.entry_price * (1 + pos.pnl_pct / 100.0)
    reason = engine.check(pos, price, regime="neutral")
    assert reason is not None
    # TIME STALE / TIME NEUTRAL / EARLY FLAT — all satisfy the flat-kill
    # behavioural contract (time-based exit on stagnant loser).
    assert ("FLAT" in reason) or ("STALE" in reason) or ("TIME" in reason)


def test_exit_max_hold_fires(engine, fsm_off, no_warmup) -> None:
    """time_max_enabled=1 + age > max_hold_sec + max_pnl below winner_bypass → TIME MAX."""
    # Exit engine rebuilds `ep` from live preg() each call (only
    # trail_tiers survives from the frozen Position.exit_params), so
    # tuning preg directly is the only way to make this branch fire.
    prev_enabled = pr.get("time_max_enabled")
    prev_nt = pr.get("neutral_timeout_enabled")
    prev_max_hold = pr.get("max_hold_sec")
    prev_flat_kill = pr.get("flat_kill_sec")
    pr.set("time_max_enabled", 1, source="test")
    pr.set("neutral_timeout_enabled", 0, source="test")  # isolate TIME MAX
    pr.set("max_hold_sec", 600, source="test")           # tightest bound
    # flat_kill_sec bounds also constrain; set high so it doesn't pre-empt.
    pr.set("flat_kill_sec", 3_600, source="test")
    try:
        ep = {
            "trail_tiers": [[99.0, 0.5]],  # keeps TRAIL inactive
        }
        pos = _make_pos(
            pnl_pct=0.3,             # positive small — skips flat/decay
            max_profit_pct=0.3,      # below winner_bypass (0.5)
            age_sec=1_200.0,         # past max_hold=600
            exit_params=ep,
        )
        price = pos.entry_price * 1.003
        # neutral regime keeps default preg("max_hold_sec") unchanged.
        reason = engine.check(pos, price, regime="neutral")
        assert reason is not None
        assert "TIME" in reason  # TIME MAX / TIME STALE / TIME NEUTRAL
    finally:
        pr.set("time_max_enabled", prev_enabled or 0, source="test")
        pr.set("neutral_timeout_enabled", prev_nt or 0, source="test")
        pr.set("max_hold_sec", prev_max_hold or 1800, source="test")
        pr.set("flat_kill_sec", prev_flat_kill or 1800, source="test")


def test_exit_returns_none_before_min_hold(engine, fsm_off, no_warmup) -> None:
    """Non-STOP exits suppressed while age < min_hold."""
    ep = {
        "hard_stop_pct": -5.0,
        "fee_floor": -5.0,
        "min_hold_sec": 600,   # 10 min guard
        "max_hold_sec": 3_600,
        "flat_kill_sec": 3_600,
        "flat_peak_pct": 0.0,
        "profit_cap": 10.0,
        "trail_activate": 0.5,
        "trail_tiers": [[0.5, 0.1]],
        "bep_activate": 0.3,
        "bep_distance": 0.1,
        "partial_close": 0,
    }
    pos = _make_pos(
        pnl_pct=-0.1,     # above hard_stop (no STOP fires)
        max_profit_pct=0.0,
        age_sec=60.0,     # well under min_hold
        exit_params=ep,
    )
    price = pos.entry_price * (1 + pos.pnl_pct / 100.0)
    reason = engine.check(pos, price, regime="risk_on")
    assert reason is None


# ────────────────────────────────────────────────────────────────────
# Invariant (4)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.invariant
def test_inv_E1_winner_no_time_loser() -> None:
    """I-E1/I-E4: TIME_LOSER forbidden in every winner state.

    Regression lock — `exit_types.py` already asserts this at import time,
    but keep a runtime test so a future edit that weakens the assertion
    is caught by the suite regardless.
    """
    for winner in (
        ExitState.TOUCHED_PROFIT,
        ExitState.PROTECTED,
        ExitState.HARVEST,
    ):
        allowed = STATE_ALLOWED_TRIGGERS[winner]
        assert ExitTrigger.TIME_LOSER not in allowed, (
            f"I-E1/I-E4 violation: TIME_LOSER in {winner}"
        )


@pytest.mark.invariant
def test_inv_E3_protected_bep_lock(fsm_on) -> None:
    """PROTECTED state + pnl < -buffer → FSM returns PROTECTED_BEP reason."""
    engine = ExitEngine(config=type("C", (), {"group_volatility_windows": {}})())
    pos = _make_pos(
        pnl_pct=-0.5,           # below the protect buffer (≈0.05%)
        max_profit_pct=0.5,     # past protect threshold (0.30)
        age_sec=120.0,
        state=ExitState.PROTECTED,
    )
    fsm = ExitFSM(legacy_engine=engine)
    decision = fsm.evaluate(pos, pos.entry_price, regime="risk_on")
    assert decision.trigger == ExitTrigger.PROTECTED_BEP
    assert "PROTECTED_BEP" in (decision.reason or "")
    assert decision.state_at_decision == ExitState.PROTECTED


@pytest.mark.invariant
def test_inv_E4_touched_profit_no_time(fsm_on) -> None:
    """TOUCHED_PROFIT + massive age → never TIME_LOSER.

    The FSM must not synthesise TIME_LOSER for a winner state; price-less
    and price-present paths both tested.
    """
    engine = ExitEngine(config=type("C", (), {"group_volatility_windows": {}})())
    pos = _make_pos(
        pnl_pct=0.15,            # above BEP buffer, won't trip touched_stop
        max_profit_pct=0.2,      # past touch threshold (0.10)
        age_sec=10_000.0,        # absurdly old
        state=ExitState.TOUCHED_PROFIT,
    )
    fsm = ExitFSM(legacy_engine=engine)
    # Price-present evaluate
    decision = fsm.evaluate(pos, pos.entry_price, regime="risk_on")
    assert decision.trigger != ExitTrigger.TIME_LOSER
    # Price-less evaluate — winner state must short-circuit to NONE
    np_decision = fsm.evaluate_no_price(pos)
    assert np_decision.trigger == ExitTrigger.NONE


@pytest.mark.invariant
def test_inv_E5_open_time_loser_only(fsm_on) -> None:
    """OPEN + max_pnl=0 + age > time_exit_max_age_sec + pnl<0 → TIME_LOSER.

    Belt-and-suspenders for the loser-only contract. Adjusts preg to a
    tiny max_age so the test runs quickly.
    """
    prev_enabled = pr.get("time_exit_loser_only")
    prev_max_age = pr.get("time_exit_max_age_sec")
    pr.set("time_exit_loser_only", 1, source="test")
    # time_exit_max_age_sec bounds (300, 3600) — stay inside.
    pr.set("time_exit_max_age_sec", 300, source="test")
    try:
        engine = ExitEngine(
            config=type("C", (), {"group_volatility_windows": {}})()
        )
        pos = _make_pos(
            pnl_pct=-0.3,         # loser, above hard_stop
            max_profit_pct=0.0,    # never positive
            age_sec=3_600.0,       # well past 300s max_age
            state=ExitState.OPEN,
        )
        fsm = ExitFSM(legacy_engine=engine)
        decision = fsm.evaluate(pos, pos.entry_price, regime="risk_on")
        # risk_on widens hard_stop via regime_stop_risk_on, so pnl=-0.3
        # is safely above STOP. TIME_LOSER is the structurally correct exit.
        assert decision.trigger == ExitTrigger.TIME_LOSER, (
            f"expected TIME_LOSER, got {decision.trigger} "
            f"(reason={decision.reason!r})"
        )
        assert decision.state_at_decision == ExitState.OPEN
    finally:
        pr.set("time_exit_loser_only", prev_enabled or 1, source="test")
        pr.set("time_exit_max_age_sec", prev_max_age or 900, source="test")
