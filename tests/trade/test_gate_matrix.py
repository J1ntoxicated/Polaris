"""Unit tests — F-N7 PR2 GateMatrix.evaluate_safety + evaluate_pre_signal.

Target: `invasion/trade/gate_matrix.py::GateMatrix`

Gates covered (3 tests):
 11. test_gm_kill_switch_h1_blocks_safety
 12. test_gm_consecutive_halt_h4 (drives H4 via `consecutive_losses`)
 14. test_gm_stale_price_h11

MSG-NS-FLAT-AUTO-BLOCK-KILL (2026-04-18): test 13 (flat_auto_block) 제거,
scaffold 삭제와 동기화.

Notes:
- `_halt_until_ts` is an EntryGate-side portfolio field (entry.py reads it).
  GateMatrix's H4 consecutive_halt reads `ctx['consecutive_losses']`
  directly, so test 12 exercises that path.
- preg overrides are applied to `invasion.trade.gate_matrix.preg`
  because gate_matrix.py does `from ..config.param_registry import get as preg`.
"""
from __future__ import annotations

import time

import pytest

from invasion.trade.gate_matrix import (
    GateMatrix,
    GateResult,
)


def _install_preg(monkeypatch, overrides: dict):
    """Replace `invasion.trade.gate_matrix.preg` with a dict-backed stub."""
    from invasion.config.param_registry import get as real_get

    def _fake_preg(key, default=None):
        if key in overrides:
            return overrides[key]
        try:
            return real_get(key)
        except Exception:
            return default

    monkeypatch.setattr("invasion.trade.gate_matrix.preg", _fake_preg)
    return overrides


# ── 11. H1 kill_switch in evaluate_safety ──────────────────────────────


def test_gm_kill_switch_h1_blocks_safety(monkeypatch):
    """Equity dropped 20% with kill_switch_pct=0.15 → H1 blocks safety."""
    _install_preg(monkeypatch, {
        "kill_switch_pct": 0.15,
        "max_daily_loss_pct": 100.0,   # keep H3 dormant
        "consecutive_loss_halt": 100,  # keep H4 dormant
    })
    gm = GateMatrix()
    ctx = {
        "ticker": "BTC",
        "equity": 80_000.0,
        "initial_equity": 100_000.0,    # -20% loss > 15%
        "daily_start_equity": 100_000.0,
        "consecutive_losses": 0,
    }
    result = gm.evaluate_safety(ctx)
    assert result.passed is False
    assert result.gate_id == "H1"
    assert "kill_switch" in result.reason


# test_gm_consecutive_halt_h4 REMOVED — H4 consecutive_halt 자체가 북극성
# sweep (2026-04-21) 으로 제거됨. gate_matrix.py:97-99 참조.
# Jin 2026-04-25 (dev-entry-gate-specialist): stale test debt cleanup.


# MSG-NS-FLAT-AUTO-BLOCK-KILL (2026-04-18): test 13 제거됨 (flat_auto_block
# scaffold 전체 삭제).


# ── 14. H11 stale_price (evaluate_pre_signal) ──────────────────────────


def test_gm_stale_price_h11(monkeypatch):
    """market_data.price_timestamp > 60s old → H11 blocks pre_signal."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "okx_blacklist": [],
        "gate_stale_price_sec": 60,
        "gate_stale_price_sec_neutral": 0,
    })
    gm = GateMatrix()
    stale_ts = time.time() - 120  # 2 minutes old, limit is 60s
    ctx = {
        "ticker": "BTC",
        "static_blacklist": set(),
        "regime": "",       # avoid regime-override path
        "exchange": "okx",
        "open_tickers": set(),
        "market_data": {"price_timestamp": stale_ts},
    }
    result = gm.evaluate_pre_signal(ctx)
    assert result.passed is False
    assert result.gate_id == "H11"
    assert "stale_price" in result.reason

    # Fresh price → PASS
    ctx["market_data"] = {"price_timestamp": time.time()}
    result2 = gm.evaluate_pre_signal(ctx)
    assert result2.passed is True, f"expected PASS, got {result2.reason}"
