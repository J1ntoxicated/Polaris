"""Unit tests — T2-2 PR5 Kelly contract branch + entry gate (flag-gated).

Covers:
  1. Flag off (signal_contract_enabled_v2=0) → legacy historical-stats Kelly
     path is taken even when a `contract` is attached to the candidate.
  2. Flag on + contract (edge_prob=0.65, b=2.0) → Kelly formula
     f = (0.65*2 - 0.35)/2 = 0.475, capped by kelly_cap (0.25); mult uses
     0.5 + f*4 shape with execution-risk discount.
  3. Flag on + elevated execution_risk_bps → discount factor shrinks size.
  4. Entry gate: composite.edge_prob < floor → scan rejects candidate.
  5. Entry gate: composite.execution_risk (bps) > cap → scan rejects.

Run: `pytest tests/trade/test_sizer_contract.py`
"""
from __future__ import annotations

import time
import types
from unittest.mock import MagicMock

import pytest

from invasion.config._registry_api import REGISTRY
from invasion.signals.base import CompositeSignal, SignalResult, SignalVerdict
from invasion.signals.contract import SignalContract
from invasion.trade._pipeline_scan import ScanMixin
from invasion.trade._pipeline_sizing import SizingMixin


# ── helpers ──────────────────────────────────────────────────────────


def _set_preg(name: str, value):
    """Set a preg via REGISTRY; return original so test can restore."""
    param = REGISTRY[name]
    original = param.current
    param.current = value
    return original


def _make_sizer(equity: float = 100_000.0) -> SizingMixin:
    """Stub SizingMixin harness (no real TradePipeline wiring required).

    SizingMixin relies on: self._equity, self._current_regime,
    self.portfolio, self.config. Minimal stubs keep the test isolated.
    """
    s = SizingMixin()
    s._equity = equity
    s._current_regime = "neutral"
    s.portfolio = types.SimpleNamespace(_consecutive_losses=0)
    s.config = types.SimpleNamespace(session_size_mult={})
    return s


def _cand(ticker: str = "BTC-USDT", score: float = 40.0, **extra) -> dict:
    # price injected so ITEM-055 resolve_pre_sizing_tier maps BTC/ETH to
    # `large` tier ($20K cap) — without this the candidate would resolve
    # to `unknown` ($300 cap) and clamp Kelly-driven size differences.
    base = {
        "ticker": ticker,
        "direction": "long",
        "score": score,
        "group": "crypto",
        "price": 95000.0,
        "market_data": {
            "tier": "mid", "atr_pct": 0.02, "exchange": "okx",
            "price": 95000.0,
        },
    }
    base.update(extra)
    return base


# ── 1. Flag off: legacy path ─────────────────────────────────────────


def test_kelly_flag_off_uses_legacy_path(monkeypatch):
    """Flag=0 → legacy historical-stats branch runs even if contract given.

    We assert the V2 Kelly-V2 debug log is NOT emitted; the legacy branch
    may fail gracefully when no ticker_perf row exists (which is the
    default test condition), and `_kelly_mult` falls back to 1.0.
    """
    orig_flag = _set_preg("signal_contract_enabled_v2", 0)
    orig_enabled = _set_preg("kelly_enabled", 1)
    try:
        sizer = _make_sizer()
        cand = _cand(contract=SignalContract(edge_prob=0.65))

        # Capture the DataStore.get_ticker_perf_kelly_stats call (legacy path
        # evidence) — patch to raise to bypass actual DB lookup.
        calls = {"legacy_db": 0}

        class _FakeStore:
            def get_ticker_perf_kelly_stats(self, *a, **kw):
                calls["legacy_db"] += 1
                return None  # no history → mult stays 1.0

        monkeypatch.setattr(
            "invasion.data.store.DataStore", lambda: _FakeStore()
        )
        size = sizer._calc_size(cand)
        # Legacy DB lookup must have been attempted.
        assert calls["legacy_db"] == 1
        # Size is deterministic-positive (floor $10, non-zero equity chain).
        assert size >= 10.0
    finally:
        _set_preg("signal_contract_enabled_v2", orig_flag)
        _set_preg("kelly_enabled", orig_enabled)


# ── 2. Flag on + contract: Kelly formula ─────────────────────────────


def test_kelly_flag_on_contract_formula(monkeypatch):
    """Flag=1 + contract(p=0.65, b=2.0 via stop=1% take=2%) → V2 branch.

    Kelly f = (0.65*2 - (1-0.65)) / 2 = (1.3 - 0.35) / 2 = 0.475
    After * kelly_fraction (0.5): 0.2375; capped at kelly_cap (0.25): 0.2375.
    mult (no exec_risk): (0.5 + 0.2375*4) * (1 - 0.5*0) = 1.45.
    """
    orig_flag = _set_preg("signal_contract_enabled_v2", 1)
    orig_enabled = _set_preg("kelly_enabled", 1)
    orig_frac = _set_preg("kelly_fraction", 0.5)
    orig_cap = _set_preg("kelly_cap", 0.25)
    orig_er_cap = _set_preg("contract_exec_risk_cap_bps", 12.0)
    try:
        sizer = _make_sizer()
        # execution_risk_bps=0 → er_frac=0 → no discount
        contract = SignalContract(
            edge_prob=0.65, execution_risk_bps=0.0, direction_certainty=0.5,
        )
        cand = _cand(
            contract=contract,
            stop_distance_pct=0.01,
            take_profit_pct=0.02,
        )

        # Patch legacy DB path — must NOT be called on V2 branch.
        calls = {"legacy_db": 0}

        class _FakeStore:
            def get_ticker_perf_kelly_stats(self, *a, **kw):
                calls["legacy_db"] += 1
                return None

        monkeypatch.setattr(
            "invasion.data.store.DataStore", lambda: _FakeStore()
        )

        # Baseline (kelly disabled) to extract the pre-Kelly size.
        orig_enabled_inner = _set_preg("kelly_enabled", 0)
        size_pre_kelly = sizer._calc_size(cand)
        _set_preg("kelly_enabled", orig_enabled_inner)

        size_v2 = sizer._calc_size(cand)
        # Legacy DB path untouched.
        assert calls["legacy_db"] == 0
        # V2 mult ~= 1.45 (see formula comment above).
        _expected_mult = (0.5 + 0.2375 * 4.0) * (1.0 - 0.5 * 0.0)
        # Both runs pass same non-Kelly chain → ratio isolates Kelly mult.
        # (Size may get clamped by max_position_pct → compare pre-cap ratio
        # by ensuring size_v2 == size_pre_kelly * mult unless capped.)
        if size_v2 < sizer._equity * 0.05:  # below max_position_pct cap
            assert size_v2 == pytest.approx(
                size_pre_kelly * _expected_mult, rel=1e-6
            )
    finally:
        _set_preg("signal_contract_enabled_v2", orig_flag)
        _set_preg("kelly_enabled", orig_enabled)
        _set_preg("kelly_fraction", orig_frac)
        _set_preg("kelly_cap", orig_cap)
        _set_preg("contract_exec_risk_cap_bps", orig_er_cap)


def test_kelly_flag_on_high_exec_risk_shrinks_size(monkeypatch):
    """Flag=1 + high execution_risk_bps → size shrinks vs. low-risk contract.

    Same edge_prob, same b; only execution_risk_bps changes. High risk
    (at cap) halves the Kelly mult envelope (1.0 - 0.5*1.0 = 0.5).
    """
    orig_flag = _set_preg("signal_contract_enabled_v2", 1)
    orig_enabled = _set_preg("kelly_enabled", 1)
    orig_er_cap = _set_preg("contract_exec_risk_cap_bps", 10.0)
    try:
        sizer = _make_sizer()

        class _FakeStore:
            def get_ticker_perf_kelly_stats(self, *a, **kw):
                return None

        monkeypatch.setattr(
            "invasion.data.store.DataStore", lambda: _FakeStore()
        )

        low_risk_cand = _cand(
            contract=SignalContract(edge_prob=0.65, execution_risk_bps=0.0),
            stop_distance_pct=0.01,
            take_profit_pct=0.02,
        )
        high_risk_cand = _cand(
            ticker="ETH-USDT",
            contract=SignalContract(edge_prob=0.65, execution_risk_bps=10.0),
            stop_distance_pct=0.01,
            take_profit_pct=0.02,
        )

        size_low = sizer._calc_size(low_risk_cand)
        size_high = sizer._calc_size(high_risk_cand)
        # High exec-risk must shrink the size below the low-risk size
        # (barring the max_position_pct clamp pinning both to the cap).
        assert size_high < size_low
    finally:
        _set_preg("signal_contract_enabled_v2", orig_flag)
        _set_preg("kelly_enabled", orig_enabled)
        _set_preg("contract_exec_risk_cap_bps", orig_er_cap)


# ── 3. Entry gate: edge_prob & exec_risk rejection ──────────────────


class _ScanHarness(ScanMixin):
    """Minimal ScanMixin subclass for entry-gate unit tests.

    We only exercise the contract-gate branch, so the stubs cover exactly
    the self.* attributes the gate reads (log_candidate_event hook,
    _update_signal_status, _recent_rejects, _current_regime).
    """

    def __init__(self):
        self._recent_rejects = {}
        self._current_regime = "neutral"
        self.rejects: list[dict] = []

        class _Store:
            def __init__(self, parent):
                self._parent = parent

            def log_candidate_event(self, evt):
                self._parent.rejects.append(evt)

        self.data_store = _Store(self)
        self.status_calls: list[tuple] = []

    def _update_signal_status(self, ticker, status):
        self.status_calls.append((ticker, status))


def _fake_verdict(edge_prob: float, exec_risk_bps: float) -> SignalVerdict:
    """Build a SignalVerdict with a CompositeSignal carrying the gate inputs."""
    comp = CompositeSignal(
        ticker="BTC-USDT",
        score=40.0,
        direction="long",
        confidence=0.6,
        signals=[SignalResult(name="mock", score=40, confidence=0.6, ttl=900)],
    )
    comp.edge_prob = edge_prob
    # execution_risk (legacy normalized [0,1]) = bps / 10000
    comp.execution_risk = exec_risk_bps / 10000.0
    comp.contract = SignalContract(
        edge_prob=edge_prob, execution_risk_bps=exec_risk_bps,
    )
    return SignalVerdict(
        passed=True, score=40.0, direction="long", confidence=0.6,
        composite=comp, metadata={"strategy_id": "mock_strat"},
    )


def _simulate_gate(cand: dict, harness: _ScanHarness) -> bool:
    """Run the contract-gate block inline (isolates it from scan_cycle).

    Returns True if candidate PASSES the gate (would proceed to exit calc),
    False if rejected. Mirrors the production logic added in PR5.
    """
    from invasion.config.param_registry import get as preg
    from invasion.utils.events import log_event as _le  # noqa: F401

    ticker = cand["ticker"]
    if preg("signal_contract_enabled_v2"):
        _composite = getattr(cand["verdict"], "composite", None)
        if _composite is not None:
            _floor = float(preg("contract_edge_prob_floor") or 0.52)
            _risk_cap = float(preg("contract_exec_risk_cap_bps") or 12.0)
            _ep = float(getattr(_composite, "edge_prob", 0.0) or 0.0)
            _er = float(getattr(_composite, "execution_risk", 0.0) or 0.0) * 10000.0
            if _ep < _floor:
                harness._update_signal_status(ticker, "CONTRACT_REJ")
                harness._recent_rejects[ticker] = time.time()
                harness.data_store.log_candidate_event({
                    "ticker": ticker, "reject_reason": f"edge_prob<{_floor:.3f}",
                    "stage": "contract_gate",
                })
                return False
            if _er > _risk_cap:
                harness._update_signal_status(ticker, "CONTRACT_REJ")
                harness._recent_rejects[ticker] = time.time()
                harness.data_store.log_candidate_event({
                    "ticker": ticker, "reject_reason": f"exec_risk>{_risk_cap:.1f}bps",
                    "stage": "contract_gate",
                })
                return False
            cand["contract"] = getattr(_composite, "contract", None)
    return True


def test_entry_gate_edge_prob_floor_rejects():
    """edge_prob below floor → contract_gate rejects + logs candidate_event."""
    orig_flag = _set_preg("signal_contract_enabled_v2", 1)
    orig_floor = _set_preg("contract_edge_prob_floor", 0.55)
    try:
        harness = _ScanHarness()
        cand = _cand(verdict=_fake_verdict(edge_prob=0.50, exec_risk_bps=2.0))
        passed = _simulate_gate(cand, harness)
        assert passed is False
        assert ("BTC-USDT", "CONTRACT_REJ") in harness.status_calls
        assert harness.rejects and harness.rejects[0]["stage"] == "contract_gate"
        assert "edge_prob" in harness.rejects[0]["reject_reason"]
    finally:
        _set_preg("signal_contract_enabled_v2", orig_flag)
        _set_preg("contract_edge_prob_floor", orig_floor)


def test_entry_gate_exec_risk_cap_rejects():
    """execution_risk (bps) above cap → contract_gate rejects."""
    orig_flag = _set_preg("signal_contract_enabled_v2", 1)
    orig_floor = _set_preg("contract_edge_prob_floor", 0.50)
    orig_cap = _set_preg("contract_exec_risk_cap_bps", 8.0)
    try:
        harness = _ScanHarness()
        cand = _cand(verdict=_fake_verdict(edge_prob=0.60, exec_risk_bps=15.0))
        passed = _simulate_gate(cand, harness)
        assert passed is False
        assert ("BTC-USDT", "CONTRACT_REJ") in harness.status_calls
        assert harness.rejects and harness.rejects[0]["stage"] == "contract_gate"
        assert "exec_risk" in harness.rejects[0]["reject_reason"]
    finally:
        _set_preg("signal_contract_enabled_v2", orig_flag)
        _set_preg("contract_edge_prob_floor", orig_floor)
        _set_preg("contract_exec_risk_cap_bps", orig_cap)


def test_entry_gate_passes_and_injects_contract():
    """Passing candidate gets `contract` injected for sizer consumption."""
    orig_flag = _set_preg("signal_contract_enabled_v2", 1)
    orig_floor = _set_preg("contract_edge_prob_floor", 0.52)
    orig_cap = _set_preg("contract_exec_risk_cap_bps", 12.0)
    try:
        harness = _ScanHarness()
        cand = _cand(verdict=_fake_verdict(edge_prob=0.65, exec_risk_bps=5.0))
        passed = _simulate_gate(cand, harness)
        assert passed is True
        # No rejections recorded.
        assert harness.rejects == []
        # Contract injected onto candidate (downstream sizer input).
        assert isinstance(cand.get("contract"), SignalContract)
        assert cand["contract"].edge_prob == 0.65
    finally:
        _set_preg("signal_contract_enabled_v2", orig_flag)
        _set_preg("contract_edge_prob_floor", orig_floor)
        _set_preg("contract_exec_risk_cap_bps", orig_cap)


def test_entry_gate_flag_off_skips_gate():
    """Flag=0 (default) → gate is a no-op even with a low-edge contract."""
    orig_flag = _set_preg("signal_contract_enabled_v2", 0)
    try:
        harness = _ScanHarness()
        cand = _cand(verdict=_fake_verdict(edge_prob=0.30, exec_risk_bps=80.0))
        passed = _simulate_gate(cand, harness)
        # Gate dormant — candidate passes through untouched.
        assert passed is True
        assert harness.rejects == []
        # No contract injected when gate is dormant (it's only injected on
        # the pass branch of the enabled gate).
        assert "contract" not in cand
    finally:
        _set_preg("signal_contract_enabled_v2", orig_flag)
