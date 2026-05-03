"""Unit tests — `invasion.trade.exit_cycle.ExitCycleMixin.exit_cycle`
(F-N7 PR3).

Focused branch coverage for the 4 behaviours that are most at risk of
silent regression:

    1. Reopen-gap force-close fires on adverse price move for non-crypto
       positions when the prior tick saw the market closed.
    2. Crypto positions skip the reopen-gap branch entirely (always-open).
    3. `pending_close_reason` drain closes the position when FSM is on.
    4. When FSM is on, a fired exit returns via `_close_position` with the
       stashed FSM `exit_decision` propagated.

Tests construct a minimal pipeline-shaped stub with only the fields
`exit_cycle` touches so the mixin can be driven without spinning up the
full TradePipeline.
"""
from __future__ import annotations

import pytest

from invasion.config import param_registry as pr
from invasion.trade.exit_cycle import ExitCycleMixin
from invasion.trade.exit_fsm import ExitFSM
from invasion.trade.exit_types import ExitDecision, ExitState, ExitTrigger
from invasion.trade.position import Position


# ────────────────────────────────────────────────────────────────────
# Minimal pipeline stub
# ────────────────────────────────────────────────────────────────────


class _StubPortfolio:
    def __init__(self, positions):
        self._positions = list(positions)
        self._initial_balances = {"okx": 10_000.0}

    def positions(self):
        return list(self._positions)

    def total_equity(self):
        return 10_000.0

    def _save_state(self):
        pass

    def remove(self, ticker):
        self._positions = [p for p in self._positions if p.ticker != ticker]


class _StubExitEngine:
    """Exit engine with scripted `check()` return + FSM slice gate."""

    def __init__(self, reason=None, fsm_routed: bool = False,
                 last_decision: ExitDecision | None = None):
        self._reason = reason
        self._fsm_routed = fsm_routed
        self._last_fsm_decision = last_decision
        self.bus = None

    def check(self, pos, price, regime="unknown"):
        return self._reason

    def _is_fsm_enabled_for(self, pos) -> bool:
        return self._fsm_routed


class _StubPipeline(ExitCycleMixin):
    """Attach ExitCycleMixin to a stub with all fields `exit_cycle` reads."""

    def __init__(self, positions, exit_engine):
        self.portfolio = _StubPortfolio(positions)
        self.exit_engine = exit_engine
        self.safety = None
        self.dpm = None
        self.data_store = None
        self.on_close_fn = None
        self._close_dead_letter = {}
        self._equity = 10_000.0
        self._initial_equity = 10_000.0
        # Record every close for assertions.
        self.closes: list[tuple[Position, str, object]] = []

    def _close_position(self, pos, reason, exit_decision=None):
        self.closes.append((pos, reason, exit_decision))
        # Remove from portfolio to prevent double-processing.
        self.portfolio._positions = [
            p for p in self.portfolio._positions if p is not pos
        ]

    def _finalize_close(self, pos, reason):
        pass

    def _regime_for_group(self, group, ticker):
        return "neutral"


def _pos(
    *,
    ticker: str = "EURUSD",
    exchange: str = "cap",
    asset_group: str = "forex",
    direction: str = "long",
    entry_price: float = 100.0,
    size_usd: float = 1000.0,
    state: ExitState = ExitState.OPEN,
    pending_close_reason: str = "",
) -> Position:
    p = Position(
        ticker=ticker,
        direction=direction,
        exchange=exchange,
        asset_group=asset_group,
        entry_price=entry_price,
        size_usd=size_usd,
    )
    p.current_price = entry_price
    p.last_price_ts = p.entry_time
    p.state = state
    p.pending_close_reason = pending_close_reason
    return p


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def fsm_flag():
    """Yield a setter for `exit_fsm_enabled`; restore on teardown."""
    prev = pr.get("exit_fsm_enabled")

    def _set(v: int) -> None:
        pr.set("exit_fsm_enabled", v, source="test")

    yield _set
    pr.set("exit_fsm_enabled", prev or 0, source="test")


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────


def test_exit_cycle_reopen_gap_closes_adverse_long(fsm_flag) -> None:
    """Adverse gap > reopen_gap_pct on re-open → force close.

    `_market_is_open` currently returns True unconditionally
    (MSG-115), so the transition fires when the prior tick's cached
    `_last_market_closed` default is True.
    """
    fsm_flag(0)  # disable FSM to avoid pending-close drain confusion.
    pos = _pos(
        ticker="AAPL",
        exchange="cap",
        asset_group="stock",
        direction="long",
        entry_price=100.0,
    )
    # _last_market_closed defaults to True for the first tick.
    engine = _StubExitEngine(reason=None, fsm_routed=False)
    pipe = _StubPipeline([pos], engine)

    prev_thresh = pr.get("reopen_gap_pct")
    pr.set("reopen_gap_pct", 2.0, source="test")
    try:
        pipe.exit_cycle(get_price_fn=lambda _t: 95.0)  # -5% adverse long
    finally:
        pr.set("reopen_gap_pct", prev_thresh or 2.0, source="test")

    assert pipe.closes, "reopen-gap branch did not fire"
    reasons = [c[1] for c in pipe.closes]
    assert any("REOPEN_GAP" in r for r in reasons), f"reasons={reasons!r}"


def test_exit_cycle_crypto_skips_reopen_check(fsm_flag) -> None:
    """Crypto positions bypass the reopen-gap branch entirely.

    Even with a massive adverse gap, a crypto Position must NOT close on
    the reopen-gap path (markets are 24/7 — the concept doesn't apply).
    """
    fsm_flag(0)
    pos = _pos(
        ticker="BTCUSDT",
        exchange="okx",
        asset_group="crypto",
        direction="long",
        entry_price=100.0,
    )
    engine = _StubExitEngine(reason=None, fsm_routed=False)
    pipe = _StubPipeline([pos], engine)

    pipe.exit_cycle(get_price_fn=lambda _t: 50.0)  # -50% move

    # No close fired from the reopen-gap branch (engine.check returns None
    # so main path also doesn't close). exit_cycle must be a no-op.
    close_reasons = [r for _, r, _ in pipe.closes]
    assert not any("REOPEN_GAP" in r for r in close_reasons), (
        f"crypto should skip reopen-gap; got {close_reasons}"
    )


def test_exit_cycle_pending_close_drains_request(fsm_flag) -> None:
    """`pending_close_reason` drain closes the position when FSM flag=1."""
    fsm_flag(1)
    pos = _pos(
        ticker="BTCUSDT",
        exchange="okx",
        asset_group="crypto",
        pending_close_reason="DPM_KILL: test",
    )
    engine = _StubExitEngine(reason=None, fsm_routed=True)
    pipe = _StubPipeline([pos], engine)

    pipe.exit_cycle(get_price_fn=lambda _t: 100.0)

    assert len(pipe.closes) == 1
    _, reason, _ = pipe.closes[0]
    assert reason == "DPM_KILL: test"
    assert pos.pending_close_reason == ""  # drained


def test_exit_cycle_pending_close_routes_to_fsm_when_enabled(fsm_flag) -> None:
    """FSM flag=1 + engine returns reason → close via `_close_position`
    with the stashed `ExitDecision` propagated.
    """
    fsm_flag(1)
    pos = _pos(
        ticker="BTCUSDT",
        exchange="okx",
        asset_group="crypto",
    )
    decision = ExitDecision(
        trigger=ExitTrigger.STOP,
        reason="STOP -10.00% (limit -1.5%)",
        state_at_decision=ExitState.OPEN,
    )
    engine = _StubExitEngine(
        reason="STOP -10.00% (limit -1.5%)",
        fsm_routed=True,
        last_decision=decision,
    )
    pipe = _StubPipeline([pos], engine)

    pipe.exit_cycle(get_price_fn=lambda _t: 90.0)

    assert len(pipe.closes) == 1
    pos_cl, reason, ed = pipe.closes[0]
    assert pos_cl is pos
    assert reason.startswith("STOP")
    # FSM path threads the ExitDecision through so close_handler can
    # write exit_type_fine.
    assert ed is decision
