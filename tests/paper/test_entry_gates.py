"""Tests for src/paper/entry_gates.py — entry gate composition (P6 pure)."""
from __future__ import annotations

import pytest

from src.paper.entry_gates import GateVerdict, evaluate_entry_gates


def _all_clear() -> dict:
    """Default kwargs — all gates open."""
    return dict(
        has_open=False,
        daily_breached=False,
        closed_this_tick=False,
        in_cooldown=False,
        in_regime_pause=False,
        spread_too_wide=False,
        regime_blocked=False,
        liq_skip=False,
        portfolio_halt=False,
    )


class TestAllowPath:
    def test_all_clear_allows(self):
        v = evaluate_entry_gates(**_all_clear())
        assert v.allow is True
        assert v.reason == ""


class TestPriorityOrder:
    """Verify each gate trips in declared precedence order."""

    @pytest.mark.parametrize(
        "field,expected_reason",
        [
            ("has_open", "has_open"),
            ("portfolio_halt", "portfolio_halt"),
            ("daily_breached", "daily_breached"),
            ("closed_this_tick", "closed_this_tick"),
            ("in_cooldown", "in_cooldown"),
            ("in_regime_pause", "in_regime_pause"),
            ("spread_too_wide", "spread_too_wide"),
            ("regime_blocked", "regime_blocked"),
            ("liq_skip", "liq_skip"),
        ],
    )
    def test_single_blocker(self, field, expected_reason):
        kwargs = _all_clear()
        kwargs[field] = True
        v = evaluate_entry_gates(**kwargs)
        assert v.allow is False
        assert v.reason == expected_reason

    def test_first_blocker_wins(self):
        # All set → has_open reported (first in precedence)
        kwargs = {k: True for k in _all_clear()}
        v = evaluate_entry_gates(**kwargs)
        assert v.reason == "has_open"

    def test_daily_breached_beats_cooldown(self):
        kwargs = _all_clear()
        kwargs["daily_breached"] = True
        kwargs["in_cooldown"] = True
        v = evaluate_entry_gates(**kwargs)
        assert v.reason == "daily_breached"

    def test_spread_beats_regime_block(self):
        kwargs = _all_clear()
        kwargs["spread_too_wide"] = True
        kwargs["regime_blocked"] = True
        v = evaluate_entry_gates(**kwargs)
        assert v.reason == "spread_too_wide"


class TestNamedTuple:
    def test_unpack(self):
        allow, reason = evaluate_entry_gates(**_all_clear())
        assert allow is True
        assert reason == ""

    def test_field_access(self):
        v = evaluate_entry_gates(**_all_clear())
        assert v.allow is True
        assert v.reason == ""

    def test_immutable(self):
        v = evaluate_entry_gates(**_all_clear())
        with pytest.raises(AttributeError):
            v.allow = False  # type: ignore[misc]
