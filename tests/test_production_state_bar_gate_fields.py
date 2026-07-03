"""ProdLoopState new fields for the bar-advance gate + idle fanout backoff.

DEMO/PAPER. Pure dataclass-default checks — the fields are per-run in-memory
scheduling state only (never persisted, never a trading decision input).
"""

from __future__ import annotations

from polaris.scripts._production_state import ProdLoopState


def test_last_eval_bar_ts_by_key_defaults_empty_dict() -> None:
    state = ProdLoopState()
    assert state.last_eval_bar_ts_by_key == {}


def test_last_eval_bar_ts_by_key_is_independent_per_instance() -> None:
    """A mutable-default field must not leak across instances (the classic
    dataclass shared-mutable-default bug)."""
    a = ProdLoopState()
    b = ProdLoopState()
    a.last_eval_bar_ts_by_key[("okx", "BTC-USDT", "1D", "bar_breakout_run")] = 1_000
    assert b.last_eval_bar_ts_by_key == {}


def test_fanout_next_due_by_venue_defaults_empty_dict() -> None:
    state = ProdLoopState()
    assert state.fanout_next_due_by_venue == {}


def test_fanout_idle_streak_by_venue_defaults_empty_dict() -> None:
    state = ProdLoopState()
    assert state.fanout_idle_streak_by_venue == {}
