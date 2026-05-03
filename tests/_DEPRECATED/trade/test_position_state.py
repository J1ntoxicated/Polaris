"""Unit test — Position.state ExitState typing + preg-driven transitions
(T2-1 PR2).

Covers:
  1. Default state is ExitState.OPEN.
  2. _advance_state transitions at touch / protect / harvest thresholds.
  3. Monotonic contract — once harvest, never regresses.
  4. Legacy string `state` in dict → ExitState via from_dict.
  5. Unknown / missing string → inferred from max_profit_pct.
  6. to_dict serialises as a plain string (backward-compat with
     paper_state.json consumers that do not import ExitState).
  7. Round-trip (to_dict → from_dict) preserves state.
  8. CLOSING is terminal — _advance_state does not revert it.

Run: `pytest tests/trade/test_position_state.py`
"""
from __future__ import annotations

import sys

from invasion.trade.exit_types import ExitState
from invasion.trade.position import Position


def _pos(**overrides) -> Position:
    base = dict(
        ticker="BTC",
        direction="long",
        exchange="okx",
        asset_group="crypto",
        entry_price=100.0,
        size_usd=1000.0,
    )
    base.update(overrides)
    return Position(**base)


def test_state_open_default() -> None:
    pos = _pos()
    assert pos.state == ExitState.OPEN
    # Legacy str equality still works (ExitState is str-Enum).
    assert pos.state == "open"


def test_advance_to_touched() -> None:
    pos = _pos()
    pos.max_profit_pct = 0.15  # >= 0.10 touch
    pos._advance_state()
    assert pos.state == ExitState.TOUCHED_PROFIT


def test_advance_to_protected() -> None:
    pos = _pos()
    pos.max_profit_pct = 0.50  # >= 0.30
    pos._advance_state()
    assert pos.state == ExitState.PROTECTED


def test_advance_to_harvest() -> None:
    pos = _pos()
    pos.max_profit_pct = 1.5
    pos._advance_state()
    assert pos.state == ExitState.HARVEST


def test_monotonic_no_regress_from_harvest() -> None:
    pos = _pos()
    pos.state = ExitState.HARVEST
    pos.max_profit_pct = 0.0  # mp dropped (shouldn't happen but guard)
    pos._advance_state()
    assert pos.state == ExitState.HARVEST


def test_monotonic_no_regress_from_protected() -> None:
    pos = _pos()
    pos.state = ExitState.PROTECTED
    pos.max_profit_pct = 0.05  # below touch now
    pos._advance_state()
    assert pos.state == ExitState.PROTECTED


def test_closing_is_terminal() -> None:
    pos = _pos()
    pos.state = ExitState.CLOSING
    pos.max_profit_pct = 2.0  # would otherwise trigger HARVEST
    pos._advance_state()
    assert pos.state == ExitState.CLOSING


def test_request_close_sets_closing() -> None:
    pos = _pos()
    pos.request_close("test reason")
    assert pos.state == ExitState.CLOSING
    assert pos.pending_close_reason == "test reason"


def test_request_close_idempotent() -> None:
    pos = _pos()
    pos.request_close("first")
    pos.request_close("second")  # should be ignored
    assert pos.pending_close_reason == "first"


def test_from_dict_legacy_str() -> None:
    data = {
        "ticker": "BTC", "direction": "long", "exchange": "okx",
        "asset_group": "crypto", "entry_price": 100.0, "size_usd": 1000.0,
        "state": "touched_profit", "max_profit_pct": 0.2,
    }
    pos = Position.from_dict(data)
    assert pos.state == ExitState.TOUCHED_PROFIT
    assert isinstance(pos.state, ExitState)


def test_from_dict_all_legacy_strings() -> None:
    for s, expected in (
        ("open", ExitState.OPEN),
        ("touched_profit", ExitState.TOUCHED_PROFIT),
        ("protected", ExitState.PROTECTED),
        ("harvest", ExitState.HARVEST),
        ("closing", ExitState.CLOSING),
    ):
        data = {
            "ticker": "BTC", "direction": "long", "exchange": "okx",
            "asset_group": "crypto", "entry_price": 100.0, "size_usd": 1000.0,
            "state": s, "max_profit_pct": 0.0,
        }
        pos = Position.from_dict(data)
        assert pos.state == expected, f"{s} → {pos.state} (want {expected})"


def test_from_dict_unknown_state_infers_protected() -> None:
    data = {
        "ticker": "BTC", "direction": "long", "exchange": "okx",
        "asset_group": "crypto", "entry_price": 100.0, "size_usd": 1000.0,
        "state": "legacy_weird", "max_profit_pct": 0.5,
    }
    pos = Position.from_dict(data)
    assert pos.state == ExitState.PROTECTED  # inferred from mp=0.5


def test_from_dict_missing_state_infers_harvest() -> None:
    # Legacy row written before `state` existed, with high historical max.
    data = {
        "ticker": "BTC", "direction": "long", "exchange": "okx",
        "asset_group": "crypto", "entry_price": 100.0, "size_usd": 1000.0,
        "max_profit_pct": 1.5,
    }
    pos = Position.from_dict(data)
    assert pos.state == ExitState.HARVEST


def test_from_dict_missing_state_zero_mp_is_open() -> None:
    data = {
        "ticker": "BTC", "direction": "long", "exchange": "okx",
        "asset_group": "crypto", "entry_price": 100.0, "size_usd": 1000.0,
    }
    pos = Position.from_dict(data)
    assert pos.state == ExitState.OPEN


def test_to_dict_serialises_enum_as_string() -> None:
    pos = _pos()
    pos.state = ExitState.HARVEST
    d = pos.to_dict()
    assert d["state"] == "harvest"
    assert isinstance(d["state"], str)
    assert not isinstance(d["state"], ExitState)  # pure str


def test_round_trip_preserves_state() -> None:
    pos = _pos()
    pos.state = ExitState.PROTECTED
    pos.max_profit_pct = 0.5
    d = pos.to_dict()
    # Simulate JSON round-trip
    import json
    raw = json.loads(json.dumps(d))
    restored = Position.from_dict(raw)
    assert restored.state == ExitState.PROTECTED
    assert isinstance(restored.state, ExitState)


def test_update_pnl_advances_state() -> None:
    pos = _pos()
    pos.update_pnl(100.15)  # +0.15% → >= touch(0.10)
    assert pos.state == ExitState.TOUCHED_PROFIT


def _run_all() -> int:
    tests = [
        test_state_open_default,
        test_advance_to_touched,
        test_advance_to_protected,
        test_advance_to_harvest,
        test_monotonic_no_regress_from_harvest,
        test_monotonic_no_regress_from_protected,
        test_closing_is_terminal,
        test_request_close_sets_closing,
        test_request_close_idempotent,
        test_from_dict_legacy_str,
        test_from_dict_all_legacy_strings,
        test_from_dict_unknown_state_infers_protected,
        test_from_dict_missing_state_infers_harvest,
        test_from_dict_missing_state_zero_mp_is_open,
        test_to_dict_serialises_enum_as_string,
        test_round_trip_preserves_state,
        test_update_pnl_advances_state,
    ]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            fails += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            fails += 1
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
