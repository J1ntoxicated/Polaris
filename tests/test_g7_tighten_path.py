"""G7 deterministic TIGHTEN path — precise exit timing, ratchet-safe, never a block.

DEMO/PAPER · aggressive · flow_not_block. The probe TIGHTEN consumer feeds a
TIGHTER trail to G7 (exit TIMING). This locks the deterministic tighten rail added
alongside the existing widen-only ``_python_widen_only`` (which stays byte-identical):

- TIGHTEN pulls the stop CLOSER to price (long: stop UP toward peak; short: stop DOWN).
- ratchet-safe ONLY: a tighten proposal that would LOOSEN the stop (move it away from
  price, i.e. below current for long) is rejected → HOLD. The existing ratchet
  (never loosen an already-set stop) is preserved.
- the G6 -1.0R hard rail, BEP, size and entry side are all UNTOUCHED. A tighten can
  never push the stop past the entry into a guaranteed-loss zone is NOT a constraint —
  tightening toward profit is always allowed; we only forbid loosening.
- cooldown / override-cap reuse the widen rails (local debounce).
- flag gate (``tighten_proposal`` absent / malformed) → byte-identical widen behaviour.
"""

from __future__ import annotations

import asyncio

from polaris.core.pipeline.agents.adaptive_exit import (
    adaptive_exit_gate,
    can_tighten_exit,
)
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GateContext,
    GateDecision,
    SignalLifecycle,
)


def _ctx(payload: dict) -> GateContext:
    return GateContext(
        run_id="r",
        signal_id="s",
        position_id="pos_test",
        gate_id=GATE_ADAPTIVE_EXIT,
        venue="capital",
        symbol="US100",
        strategy_id="index_breakout",
        payload=payload,
        started_ts=0,
        state=SignalLifecycle.MONITORED,
    )


# --------------------------------------------------------------------------- #
# Pure rail: can_tighten_exit
# --------------------------------------------------------------------------- #


def test_can_tighten_long_moves_stop_up_toward_price() -> None:
    """Long tighten = proposed stop ABOVE current (closer to price/peak) → allowed."""
    ok, reason = can_tighten_exit(
        side="long",
        current_stop_price=19980.0,
        proposed_stop_price=19990.0,  # higher = tighter = closer to last price
        overrides_used=0,
        seconds_since_last_override=60,
    )
    assert ok is True
    assert reason == "ok"


def test_can_tighten_short_moves_stop_down_toward_price() -> None:
    """Short tighten = proposed stop BELOW current (closer to price) → allowed."""
    ok, reason = can_tighten_exit(
        side="short",
        current_stop_price=20020.0,
        proposed_stop_price=20010.0,  # lower = tighter for a short
        overrides_used=0,
        seconds_since_last_override=60,
    )
    assert ok is True
    assert reason == "ok"


def test_can_tighten_rejects_loosening_long() -> None:
    """A 'tighten' that actually LOOSENS (stop further from price) is rejected — the
    ratchet (never loosen) is preserved."""
    ok, reason = can_tighten_exit(
        side="long",
        current_stop_price=19990.0,
        proposed_stop_price=19980.0,  # lower = LOOSER for a long → reject
        overrides_used=0,
        seconds_since_last_override=60,
    )
    assert ok is False
    assert reason == "not_tightening_long"


def test_can_tighten_respects_cooldown() -> None:
    """Local debounce: a tighten within the cooldown window is rejected."""
    ok, reason = can_tighten_exit(
        side="long",
        current_stop_price=19980.0,
        proposed_stop_price=19990.0,
        overrides_used=0,
        seconds_since_last_override=5,  # < COOLDOWN_SEC
    )
    assert ok is False
    assert reason == "cooldown"


def test_can_tighten_respects_override_cap() -> None:
    ok, reason = can_tighten_exit(
        side="long",
        current_stop_price=19980.0,
        proposed_stop_price=19990.0,
        overrides_used=5,  # >= MAX_OVERRIDES_PER_TRADE
        seconds_since_last_override=60,
    )
    assert ok is False
    assert reason == "override_cap"


# --------------------------------------------------------------------------- #
# Gate dispatch: tighten_proposal branch (P0 deterministic)
# --------------------------------------------------------------------------- #


def test_gate_applies_tighten_proposal_long() -> None:
    """A ratchet-safe tighten_proposal → ADJUST_EXIT with the tighter stop applied."""
    res = asyncio.run(
        adaptive_exit_gate(
            _ctx({
                "tighten_proposal": {
                    "side": "long",
                    "current_stop_price": 19980.0,
                    "proposed_stop_price": 19990.0,
                    "overrides_used": 0,
                    "seconds_since_last_override": 60,
                },
            })
        )
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload["stop_price"] == 19990.0
    assert res.payload["tightening_applied"] is True
    assert res.model_used == "python"


def test_gate_rejects_loosening_tighten_proposal_to_hold() -> None:
    """A tighten_proposal that would LOOSEN → HOLD, current stop preserved."""
    res = asyncio.run(
        adaptive_exit_gate(
            _ctx({
                "tighten_proposal": {
                    "side": "long",
                    "current_stop_price": 19990.0,
                    "proposed_stop_price": 19980.0,  # looser → reject
                    "overrides_used": 0,
                    "seconds_since_last_override": 60,
                },
            })
        )
    )
    assert res.decision == GateDecision.HOLD
    assert res.payload["stop_price"] == 19990.0
    assert res.payload.get("tightening_applied") is False


def test_gate_never_emits_exit_now_for_tighten() -> None:
    """flow_not_block: tighten is precise exit TIMING (trail), never a HOLD->EXIT_NOW
    block. Even a deep-adverse tighten proposal only ADJUSTs the trail."""
    res = asyncio.run(
        adaptive_exit_gate(
            _ctx({
                "tighten_proposal": {
                    "side": "long",
                    "current_stop_price": 19980.0,
                    "proposed_stop_price": 19995.0,
                    "overrides_used": 0,
                    "seconds_since_last_override": 60,
                },
            })
        )
    )
    assert res.decision != GateDecision.EXIT_NOW


def test_widen_proposal_unaffected_by_tighten_path() -> None:
    """A payload with ONLY widen_proposal (no tighten_proposal) keeps the exact
    widen-only behaviour — byte-identical to before this change."""
    res = asyncio.run(
        adaptive_exit_gate(
            _ctx({
                "widen_proposal": {
                    "side": "long",
                    "current_stop_price": 19990.0,
                    "proposed_stop_price": 19980.0,  # widen = stop further below
                    "entry_price": 20000.0,
                    "unrealized_pnl_r": 1.0,  # > 0.7R widen window
                    "max_loss_r": 1.0,
                    "overrides_used": 0,
                    "seconds_since_last_override": 60,
                },
            })
        )
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload["stop_price"] == 19980.0
    assert res.payload["widening_applied"] is True


def test_no_proposal_holds() -> None:
    """No widen and no tighten proposal → HOLD (unchanged fail-open default)."""
    res = asyncio.run(
        adaptive_exit_gate(_ctx({"current_stop_price": 19990.0}))
    )
    assert res.decision == GateDecision.HOLD
