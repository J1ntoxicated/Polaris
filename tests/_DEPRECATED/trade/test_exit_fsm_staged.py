"""Unit test — MSG-FSM-STAGED (Codex Fwd PR1, scope-reduced 04-18 12:18).

Covers:
  1. `ExitEngine._is_fsm_enabled_for` — global kill beats everything,
     per-slice flag decides when the slice is registered, unregistered
     slices fall back to the global flag (alpaca/capital preserve
     legacy behaviour in this PR).
  2. `HarnessAlerter._check_fsm_auto_revert` — rolling asym < floor on
     an active slice emits a HIGH alert WITHOUT calling pset (human-
     in-loop revert per Jin 12:18 scope reduction).

Run: `pytest tests/trade/test_exit_fsm_staged.py`
"""
from __future__ import annotations

import os
import time

from invasion.config import param_registry as pr
from invasion.trade.exit import ExitEngine
from invasion.trade.position import Position


def _pos(exchange: str = "okx", asset_group: str = "crypto") -> Position:
    """Minimal Position — only fields read by `_is_fsm_enabled_for`."""
    return Position(
        ticker="BTC",
        direction="long",
        exchange=exchange,
        asset_group=asset_group,
        entry_price=100.0,
        size_usd=1000.0,
    )


# ── _is_fsm_enabled_for ─────────────────────────────────────────────
def test_fsm_global_kill_beats_slice() -> None:
    """global=0 → any Position → False, regardless of slice flag."""
    pr.set("exit_fsm_enabled", 0, source="test")
    pr.set("exit_fsm_enabled_okx_crypto", 1, source="test")
    try:
        assert ExitEngine._is_fsm_enabled_for(_pos()) is False
    finally:
        pr.set("exit_fsm_enabled", 1, source="test")
        pr.set("exit_fsm_enabled_okx_crypto", 0, source="test")


def test_fsm_slice_on_enables() -> None:
    """global=1 + slice=1 → True."""
    pr.set("exit_fsm_enabled", 1, source="test")
    pr.set("exit_fsm_enabled_okx_crypto", 1, source="test")
    try:
        assert ExitEngine._is_fsm_enabled_for(_pos()) is True
    finally:
        pr.set("exit_fsm_enabled_okx_crypto", 0, source="test")


def test_fsm_slice_off_disables() -> None:
    """global=1 + slice=0 → False (per-slice gate wins when registered)."""
    pr.set("exit_fsm_enabled", 1, source="test")
    pr.set("exit_fsm_enabled_okx_crypto", 0, source="test")
    assert ExitEngine._is_fsm_enabled_for(_pos()) is False


def test_fsm_unregistered_slice_fallback_to_global() -> None:
    """global=1 + unregistered slice combo → True (legacy behaviour).

    Scope-reduced PR: alpaca/capital slices are NOT registered. Their
    Positions must therefore honour only the global `exit_fsm_enabled`
    flag so existing behaviour is preserved until a follow-up PR
    enumerates them.
    """
    pr.set("exit_fsm_enabled", 1, source="test")
    # Alpaca × stock is intentionally unregistered in this PR.
    pos_alpaca = _pos(exchange="alpaca", asset_group="stock")
    assert ExitEngine._is_fsm_enabled_for(pos_alpaca) is True
    # Capital × forex is intentionally unregistered in this PR.
    pos_cap = _pos(exchange="cap", asset_group="forex")
    assert ExitEngine._is_fsm_enabled_for(pos_cap) is True


def test_fsm_okx_forex_slice_registered() -> None:
    """okx_forex is the second registered slice and obeys its flag."""
    pr.set("exit_fsm_enabled", 1, source="test")
    pr.set("exit_fsm_enabled_okx_forex", 0, source="test")
    pos = _pos(exchange="okx", asset_group="forex")
    assert ExitEngine._is_fsm_enabled_for(pos) is False
    pr.set("exit_fsm_enabled_okx_forex", 1, source="test")
    try:
        assert ExitEngine._is_fsm_enabled_for(pos) is True
    finally:
        pr.set("exit_fsm_enabled_okx_forex", 0, source="test")


# ── live-gate alert (alert-only, no pset) ───────────────────────────
class _FakeStore:
    """Minimal Store stub — exposes .query(sql, params) matching the
    signature used by HarnessAlerter._check_fsm_auto_revert."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        # Filter rows by the exchange/asset_group in params so the test
        # can stash heterogenous rows and verify slice-scoped SQL.
        if not params or len(params) < 3:
            return self._rows
        _since, exch, group = params[0], params[1], params[2]
        return [
            r for r in self._rows
            if r.get("exchange") == exch and r.get("asset_group") == group
        ]


def test_live_gate_alerts_without_pset(tmp_path) -> None:
    """Asym below floor → HIGH alert written, slice flag UNCHANGED.

    Scope-reduction assertion (Jin 12:18): detector is alert-only. The
    slice flag must stay 1 after the detector runs so Jin/Harness can
    review and pset manually.
    """
    from invasion.ops.harness_alerter import HarnessAlerter

    pr.set("exit_fsm_enabled", 1, source="test")
    pr.set("exit_fsm_enabled_okx_crypto", 1, source="test")
    pr.set("fsm_live_gate_window_sec", 900, source="test")
    pr.set("fsm_live_gate_asym_floor", 0.9, source="test")

    try:
        # Asym ~ 0.5: avg_win=+0.5%, avg_loss=-1.0% → 0.5 < 0.9 floor.
        rows = (
            [{"pnl_pct": 0.5, "exchange": "okx", "asset_group": "crypto"}] * 5
            + [{"pnl_pct": -1.0, "exchange": "okx", "asset_group": "crypto"}] * 5
        )
        store = _FakeStore(rows)

        alerter = HarnessAlerter(alert_dir=str(tmp_path))
        alerter._check_fsm_auto_revert(store, time.time())

        # CORE ASSERTION: slice flag NOT flipped by detector.
        assert pr.get("exit_fsm_enabled_okx_crypto") == 1, (
            "live-gate detector must not pset; human-in-loop only."
        )
        # Slice-scoped alert file written with the recommended pset cmd.
        files = os.listdir(tmp_path)
        matching = [
            f for f in files
            if "fsm_live_gate_exit_fsm_enabled_okx_crypto" in f
        ]
        assert matching, f"no slice-scoped alert file: {files}"
        body = (tmp_path / matching[0]).read_text()
        assert "pset('exit_fsm_enabled_okx_crypto', 0)" in body, (
            f"alert must include recommended pset cmd; body={body!r}"
        )
        assert "severity: HIGH" in body
    finally:
        pr.set("exit_fsm_enabled_okx_crypto", 0, source="test")


def test_live_gate_silent_above_floor(tmp_path) -> None:
    """Asym above floor → no alert."""
    from invasion.ops.harness_alerter import HarnessAlerter

    pr.set("exit_fsm_enabled", 1, source="test")
    pr.set("exit_fsm_enabled_okx_crypto", 1, source="test")
    pr.set("fsm_live_gate_asym_floor", 0.9, source="test")

    try:
        # Asym = 1.2: avg_win=+1.2%, avg_loss=-1.0% → 1.2 >= 0.9 floor.
        rows = (
            [{"pnl_pct": 1.2, "exchange": "okx", "asset_group": "crypto"}] * 5
            + [{"pnl_pct": -1.0, "exchange": "okx", "asset_group": "crypto"}] * 5
        )
        store = _FakeStore(rows)
        alerter = HarnessAlerter(alert_dir=str(tmp_path))
        alerter._check_fsm_auto_revert(store, time.time())

        assert pr.get("exit_fsm_enabled_okx_crypto") == 1
        assert not any("fsm_live_gate" in f for f in os.listdir(tmp_path))
    finally:
        pr.set("exit_fsm_enabled_okx_crypto", 0, source="test")


def test_live_gate_noop_when_no_active_slice(tmp_path) -> None:
    """All slices off → silent no-op, no SQL, no alert."""
    from invasion.ops.harness_alerter import HarnessAlerter

    pr.set("exit_fsm_enabled", 1, source="test")
    for slice_key in ExitEngine.FSM_SLICE_FLAGS:
        pr.set(slice_key, 0, source="test")

    rows = [{"pnl_pct": 0.5, "exchange": "okx", "asset_group": "crypto"}] * 10
    store = _FakeStore(rows)
    alerter = HarnessAlerter(alert_dir=str(tmp_path))
    alerter._check_fsm_auto_revert(store, time.time())
    assert not any("fsm_live_gate" in f for f in os.listdir(tmp_path))


def test_live_gate_min_sample_guard(tmp_path) -> None:
    """<3 wins OR <3 losses → skip slice (tiny sample guard)."""
    from invasion.ops.harness_alerter import HarnessAlerter

    pr.set("exit_fsm_enabled", 1, source="test")
    pr.set("exit_fsm_enabled_okx_crypto", 1, source="test")
    pr.set("fsm_live_gate_asym_floor", 0.9, source="test")

    try:
        # 2 wins + 2 losses = below the 3+3 sample floor.
        rows = (
            [{"pnl_pct": 0.5, "exchange": "okx", "asset_group": "crypto"}] * 2
            + [{"pnl_pct": -1.0, "exchange": "okx", "asset_group": "crypto"}] * 2
        )
        store = _FakeStore(rows)
        alerter = HarnessAlerter(alert_dir=str(tmp_path))
        alerter._check_fsm_auto_revert(store, time.time())
        assert pr.get("exit_fsm_enabled_okx_crypto") == 1
        assert not any("fsm_live_gate" in f for f in os.listdir(tmp_path))
    finally:
        pr.set("exit_fsm_enabled_okx_crypto", 0, source="test")
