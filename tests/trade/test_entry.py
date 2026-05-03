"""Unit tests — F-N7 PR2 entry-gate matrix (EntryGate.check).

Target: `invasion/trade/entry.py::EntryGate.check(ticker, verdict, market_data)`
returning `GateResult(passed, reason, metadata)`.

Covers the Plan F-N7 PR2 scope — 10 tests:

Golden-path (6):
  1. test_gate_pass_on_valid_signal
  2. test_blacklist_rejects
  3. test_auto_blacklist_from_preg
  4. test_cooldown_active_rejects
  5. test_cooldown_floor_crisis_vs_normal
  6. test_zero_strength_rejects

Invariant (4):
  7. test_repeat_entry_rate_limit
  8. test_repeat_entry_dca_exception
  9. test_daily_entry_cap_enforced
 10. test_direction_bias_rejects_opposite

Tests monkeypatch `invasion.trade.entry.preg` because `entry.py` does
`from ..config.param_registry import get as preg` (binding captured at
import time).
"""
from __future__ import annotations

import time
import types

import pytest

from invasion.trade.entry import EntryGate, GateResult
from tests.fixtures import mock_verdict, mock_market_data


# ── helpers ───────────────────────────────────────────────────────────


def _config(blacklist=None, **extra) -> types.SimpleNamespace:
    """Build a minimal EntryGate config stub."""
    cfg = types.SimpleNamespace(blacklist=list(blacklist or []))
    for k, v in extra.items():
        setattr(cfg, k, v)
    return cfg


def _portfolio(halt_until_ts: float = 0.0) -> types.SimpleNamespace:
    """Portfolio stub — entry.py only reads `_halt_until_ts`."""
    return types.SimpleNamespace(_halt_until_ts=halt_until_ts)


def _install_preg(monkeypatch, overrides: dict):
    """Replace `invasion.trade.entry.preg` with a dict-backed stub.

    Falls back to the real registry for keys not overridden so we don't
    have to enumerate every preg entry.py reads.
    """
    from invasion.config.param_registry import get as real_get

    def _fake_preg(key, default=None):
        if key in overrides:
            return overrides[key]
        try:
            return real_get(key)
        except Exception:
            return default

    monkeypatch.setattr("invasion.trade.entry.preg", _fake_preg)
    return overrides


def _md(**extra):
    """mock_market_data wrapper — add common fields entry.py reads."""
    base = dict(
        price=100.0,
        mid=100.0,
        price_timestamp=0,  # 0 → skip stale check
        ts=0,
        group="crypto",
        exchange="okx",
        direction="long",
        tier="mid",
        high24h=120.0,
        low24h=80.0,
        tech_available=True,
        candle_available=1,
    )
    base.update(extra)
    return base


# ── 1. Golden-path (6) ────────────────────────────────────────────────


def test_gate_pass_on_valid_signal(monkeypatch):
    """score=40, non-blacklist, fresh market_data → passed=True."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
    })
    gate = EntryGate(_config(), portfolio=_portfolio())
    verdict = mock_verdict(score=40, direction="long")
    result = gate.check("BTC-USDT", verdict, _md())
    assert isinstance(result, GateResult)
    assert result.passed is True, f"expected PASS, got reason={result.reason}"


def test_blacklist_rejects(monkeypatch):
    """Static config.blacklist → reason='blacklisted'."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
    })
    gate = EntryGate(_config(blacklist=["COAI"]), portfolio=_portfolio())
    result = gate.check("COAI", mock_verdict(score=40), _md())
    assert result.passed is False
    assert result.reason == "blacklisted"


def test_auto_blacklist_from_preg(monkeypatch):
    """preg('ticker_blacklist') containing ticker → reason='auto_blacklisted'."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": ["PLTR"],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
    })
    gate = EntryGate(_config(), portfolio=_portfolio())
    result = gate.check("PLTR", mock_verdict(score=40), _md())
    assert result.passed is False
    assert result.reason == "auto_blacklisted"


def test_cooldown_active_rejects(monkeypatch):
    """set_cooldown(t, 60) then check → reason starts with 'cooldown_'."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
        "entry_cooldown_normal_floor_sec": 0,  # let caller-seconds win
        "entry_cooldown_crisis_floor_sec": 0,
    })
    gate = EntryGate(_config(), portfolio=_portfolio())
    gate.set_cooldown("BTC", 60, group="", regime="neutral")
    result = gate.check("BTC", mock_verdict(score=40), _md())
    assert result.passed is False
    assert result.reason.startswith("cooldown_"), (
        f"expected cooldown_* reason, got {result.reason}"
    )


def test_cooldown_floor_crisis_vs_normal(monkeypatch):
    """Crisis floor (60s) < normal floor (180s) → set_cooldown honors regime."""
    _install_preg(monkeypatch, {
        "entry_cooldown_crisis_floor_sec": 60,
        "entry_cooldown_normal_floor_sec": 180,
    })
    gate = EntryGate(_config(), portfolio=_portfolio())

    # Crisis regime: floor=60 — caller asks for 10s, gets 60s
    t0 = time.time()
    gate.set_cooldown("BTC", 10, group="", regime="crisis")
    unlock_crisis = gate._cooldowns["BTC"]
    assert 55 < (unlock_crisis - t0) < 70, (
        f"crisis floor mismatch: {unlock_crisis - t0:.1f}s"
    )

    # Normal regime: floor=180
    t1 = time.time()
    gate.set_cooldown("ETH", 10, group="", regime="neutral")
    unlock_normal = gate._cooldowns["ETH"]
    assert 170 < (unlock_normal - t1) < 200, (
        f"normal floor mismatch: {unlock_normal - t1:.1f}s"
    )
    # Invariant: crisis floor is strictly shorter than normal floor.
    assert (unlock_crisis - t0) < (unlock_normal - t1)


def test_zero_strength_rejects(monkeypatch):
    """verdict.score=0 → reason='zero_strength'."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
    })
    gate = EntryGate(_config(), portfolio=_portfolio())
    verdict = mock_verdict(score=0, direction="long")
    result = gate.check("BTC", verdict, _md())
    assert result.passed is False
    assert result.reason == "zero_strength"


# ── 2. Invariants (4) ────────────────────────────────────────────────


def test_repeat_entry_rate_limit(monkeypatch):
    """Repeat-entry rolling-window cap — 3 entries on near-stationary price → 4th rejected."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
        "repeat_entry_window_sec": 3600,
        "repeat_entry_max_count": 3,
        "repeat_entry_move_pct": 0.02,
        "ticker_daily_entry_cap": 10,
    })
    gate = EntryGate(_config(), portfolio=_portfolio())

    # 3 passes at price=100 (stationary)
    for i in range(3):
        r = gate.check("BTC", mock_verdict(score=40), _md(price=100.0, mid=100.0))
        assert r.passed is True, f"entry {i} failed: {r.reason}"

    # 4th entry at price=100.5 (+0.5% < repeat_entry_move_pct=2%) → rejected
    r4 = gate.check("BTC", mock_verdict(score=40), _md(price=100.5, mid=100.5))
    assert r4.passed is False
    assert r4.reason.startswith("repeat_entry_"), (
        f"expected repeat_entry_* reason, got {r4.reason}"
    )


def test_repeat_entry_dca_exception(monkeypatch):
    """Price move ≥ repeat_entry_move_pct → DCA exception, 4th entry passes."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
        "repeat_entry_window_sec": 3600,
        "repeat_entry_max_count": 3,
        "repeat_entry_move_pct": 0.02,   # 2%
        "ticker_daily_entry_cap": 10,
    })
    gate = EntryGate(_config(), portfolio=_portfolio())

    for _ in range(3):
        r = gate.check("BTC", mock_verdict(score=40), _md(price=100.0, mid=100.0))
        assert r.passed is True

    # 4th entry at price=105 (+5% >= 2% threshold) → DCA allowed, passes
    r4 = gate.check("BTC", mock_verdict(score=40), _md(price=105.0, mid=105.0))
    assert r4.passed is True, f"expected DCA pass, got {r4.reason}"


def test_daily_entry_cap_enforced(monkeypatch):
    """ticker_daily_entry_cap=2 → 3rd entry in 24h rejected."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
        "repeat_entry_window_sec": 3600,
        "repeat_entry_max_count": 100,   # high — isolate daily cap
        "repeat_entry_move_pct": 0.02,
        "ticker_daily_entry_cap": 2,
    })
    gate = EntryGate(_config(), portfolio=_portfolio())

    # 2 passes, diverging prices so repeat_entry rate limit stays dormant
    for i, p in enumerate([100.0, 110.0]):
        r = gate.check("BTC", mock_verdict(score=40), _md(price=p, mid=p))
        assert r.passed is True, f"entry {i} failed: {r.reason}"

    # 3rd entry → daily cap exceeded
    r3 = gate.check("BTC", mock_verdict(score=40), _md(price=120.0, mid=120.0))
    assert r3.passed is False
    assert r3.reason == "ticker_daily_cap", (
        f"expected ticker_daily_cap, got {r3.reason}"
    )


# test_direction_bias_rejects_opposite REMOVED — direction_bias feature
# 자체가 북극성 sweep (2026-04-21) 으로 제거됨. entry.py:81-87 참조.
# Jin 2026-04-25 (dev-entry-gate-specialist): stale test debt cleanup.
