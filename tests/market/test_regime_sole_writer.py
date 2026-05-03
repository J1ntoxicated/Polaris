"""Unit tests — RegimeService sole-writer invariants (F-N7 PR4).

Covers:
  * I-R1 : current(domain) must be a real Regime enum instance after
            observe() — never empty / None.
  * I-R3 : DataStore.insert_context is called exactly once per
            successful observe(persist=True).

Scope: unit. Uses the RegimeService singleton (reset by conftest's
reset_singletons autouse fixture) and a monkeypatched DataStore that
acts as a call-count spy.

Run: `pytest tests/market/test_regime_sole_writer.py -v`
"""
from __future__ import annotations

import pytest

from invasion.config.schema import Regime
from invasion.market.regime_service import RegimeService


# ── helpers ──────────────────────────────────────────────────────────


def _fresh_service() -> RegimeService:
    """Produce a clean RegimeService (singleton reset by autouse fixture).

    We inject a preg stub so the test is insulated from live preg values.
    """
    RegimeService._instance = None

    def _fake_preg(key: str):
        return {
            "regime_flip_confirmations": 5,
            "regime_min_hold_sec": 900,
            "regime_flip_rate_ceiling_1h": 5,
        }.get(key)

    return RegimeService(preg_getter=_fake_preg)


class _SpyStore:
    """Fake DataStore — records every insert_context call."""

    def __init__(self):
        self.calls: list[dict] = []

    def insert_context(self, **kwargs):
        self.calls.append(kwargs)


# ── Invariants ──────────────────────────────────────────────────────


@pytest.mark.invariant
def test_inv_R1_regime_non_empty_after_observe(monkeypatch):
    """I-R1: After observe() commits a state, current(domain) returns a
    real Regime enum — never None and never an empty string.

    We drive the service with a CRISIS sample (crisis_bypass path) so
    the state flips on the first observation; then we assert both the
    type and the .value of the current state.
    """
    svc = _fresh_service()
    spy = _SpyStore()
    # Route the DataStore() constructor inside _persist to the spy.
    import invasion.market.regime_service as _mod
    monkeypatch.setattr(
        _mod, "DataStore", lambda: spy, raising=False,
    )
    monkeypatch.setattr(
        "invasion.data.store.DataStore", lambda: spy,
    )

    # Cold start must be UNKNOWN (not None / not empty string).
    pre = svc.current("crypto")
    assert isinstance(pre, Regime)
    assert pre == Regime.UNKNOWN
    assert pre.value == "unknown"

    # Commit CRISIS via crisis_bypass.
    res = svc.observe("crypto", Regime.CRISIS, now=1000.0, persist=True)
    assert res.changed
    assert res.reason == "crisis_bypass"

    # I-R1: post-observe current must be a Regime enum with non-empty value.
    cur = svc.current("crypto")
    assert isinstance(cur, Regime), f"current must be Regime, got {type(cur)}"
    assert cur == Regime.CRISIS
    assert cur.value == "crisis"
    assert cur.value != ""


@pytest.mark.invariant
def test_inv_R3_sole_writer_datastore_called_once_per_observe(monkeypatch):
    """I-R3: RegimeService is the SOLE writer; each observe(persist=True)
    produces exactly ONE DataStore.insert_context call.

    We spy on the DataStore factory and count calls across 3 observations.
    """
    svc = _fresh_service()
    spy = _SpyStore()
    monkeypatch.setattr(
        "invasion.data.store.DataStore", lambda: spy,
    )

    # 3 observations, each with persist=True.
    svc.observe("crypto", Regime.CRISIS, now=1000.0, persist=True,
                fear_greed=15, macro={"vix": 42.0})
    svc.observe("crypto", Regime.CRISIS, now=1001.0, persist=True,
                fear_greed=18)
    svc.observe("crypto", Regime.CRISIS, now=1002.0, persist=True,
                fear_greed=20)

    assert len(spy.calls) == 3, (
        f"I-R3: expected 3 insert_context calls (one per observe), "
        f"got {len(spy.calls)}"
    )
    # Every call must carry a Regime instance (not a raw string).
    for call in spy.calls:
        assert isinstance(call["regime"], Regime)
        assert call["regime"] == Regime.CRISIS

    # persist=False must NOT produce a call (hatch for state-machine tests).
    svc.observe("crypto", Regime.CRISIS, now=1003.0, persist=False)
    assert len(spy.calls) == 3, (
        "persist=False must not trigger insert_context"
    )
