"""Unit test — RegimeService state machine (T2-4 PR1/PR4).

Covers: cold start, same-observe (no transition), hysteresis hold,
confirmed transition, min_hold_active, crisis bypass, flip rate, type guard.

PR4 wiring added `persist` kwarg — tests pass ``persist=False`` so the
state machine is exercised without touching DataStore.

Run: `pytest tests/market/test_regime_service.py`
"""
from __future__ import annotations

import sys

from invasion.config.schema import Regime
from invasion.market.regime_service import RegimeService
from invasion.market.regime_types import TransitionResult, RegimeDomain


def _fresh_service() -> RegimeService:
    """Reset singleton for isolated tests."""
    RegimeService._instance = None
    # Stub preg getter: deterministic defaults (ignore live preg).
    def _fake_preg(key: str):
        return {
            "regime_flip_confirmations": 5,
            "regime_min_hold_sec": 900,
            "regime_flip_rate_ceiling_1h": 5,
        }.get(key)
    return RegimeService(preg_getter=_fake_preg)


def _obs(s, domain, sample, now):
    """Shortcut — observe with persist disabled for state-machine tests."""
    return s.observe(domain, sample, now=now, persist=False)


def test_cold_start_unknown() -> None:
    s = _fresh_service()
    assert s.current("crypto") == Regime.UNKNOWN, "cold start must be UNKNOWN"


def test_same_sample_no_transition() -> None:
    s = _fresh_service()
    # First prime to a confirmed state via crisis bypass (to avoid N-sample hurdle).
    _obs(s, "crypto", Regime.CRISIS, 1000.0)
    res = _obs(s, "crypto", Regime.CRISIS, 1001.0)
    assert not res.changed
    assert res.reason == "confirmed"
    assert res.resolved == Regime.CRISIS


def test_hysteresis_hold_samples_1_to_4() -> None:
    s = _fresh_service()
    # Prime with crisis to leave UNKNOWN, then observe different sample.
    _obs(s, "crypto", Regime.CRISIS, 1000.0)
    # Now advance past min_hold so only hysteresis gate is active.
    base = 1000.0 + 1000.0  # > 900s min_hold
    for i in range(1, 5):
        res = _obs(s, "crypto", Regime.RISK_ON, base + i)
        assert not res.changed, f"sample {i} should not transition yet"
        assert res.reason == "hysteresis_hold"
    assert s.current("crypto") == Regime.CRISIS


def test_confirmed_transition_after_n_samples() -> None:
    s = _fresh_service()
    _obs(s, "crypto", Regime.CRISIS, 1000.0)
    base = 1000.0 + 1000.0  # > 900s min_hold
    for i in range(1, 5):
        _obs(s, "crypto", Regime.RISK_ON, base + i)
    res = _obs(s, "crypto", Regime.RISK_ON, base + 5)
    assert res.changed, "5th same sample should commit transition"
    assert res.reason == "confirmed"
    assert res.resolved == Regime.RISK_ON
    assert s.current("crypto") == Regime.RISK_ON


def test_min_hold_active_blocks_transition() -> None:
    s = _fresh_service()
    _obs(s, "crypto", Regime.CRISIS, 1000.0)
    # Feed 5 RISK_ON within min_hold window (<900s from CRISIS commit).
    for i in range(1, 6):
        res = _obs(s, "crypto", Regime.RISK_ON, 1000.0 + i)
    # 5th sample reaches N=5 but min_hold (900s) since crisis commit at 1000 is not elapsed.
    assert not res.changed, "min_hold should block transition"
    assert res.reason == "min_hold_active"
    assert s.current("crypto") == Regime.CRISIS


def test_crisis_bypass_instant() -> None:
    s = _fresh_service()
    res = _obs(s, "crypto", Regime.CRISIS, 500.0)
    assert res.changed
    assert res.reason == "crisis_bypass"
    assert res.resolved == Regime.CRISIS
    assert res.prev == Regime.UNKNOWN
    assert s.current("crypto") == Regime.CRISIS


def test_flip_rate_1h_increments() -> None:
    import time as _time
    s = _fresh_service()
    base = _time.time()
    # Commit 3 transitions within a 1h window using injected timestamps.
    _obs(s, "crypto", Regime.CRISIS, base)
    s._commit_transition("crypto", Regime.RISK_ON, base + 10.0)
    s._commit_transition("crypto", Regime.RISK_OFF, base + 20.0)
    # flip_rate_1h uses time.time() internally; cutoff = now - 3600.
    # All three entries are within 1h of wall clock, so count == 3.
    rate = s.flip_rate_1h("crypto")
    assert rate == 3, f"expected 3 flips in last hour, got {rate}"
    # _compute_flip_rate with injected now should also count all 3.
    assert s._compute_flip_rate("crypto", base + 30.0) == 3


def test_non_regime_sample_raises() -> None:
    s = _fresh_service()
    try:
        s.observe("crypto", "risk_on", now=1.0, persist=False)  # type: ignore[arg-type]
    except TypeError as e:
        assert "Regime" in str(e)
        return
    raise AssertionError("TypeError expected for non-Regime sample")


def test_snapshot_shape() -> None:
    s = _fresh_service()
    _obs(s, "crypto", Regime.CRISIS, 1000.0)
    snap = s.snapshot()
    assert "current" in snap and "last_transition_ts" in snap and "flip_rate_1h" in snap
    assert snap["current"]["crypto"] == "crisis"


def test_domain_isolation() -> None:
    s = _fresh_service()
    _obs(s, "crypto", Regime.CRISIS, 1000.0)
    assert s.current("crypto") == Regime.CRISIS
    assert s.current("macro") == Regime.UNKNOWN


def test_persist_default_writes_datastore(monkeypatch=None) -> None:
    """PR4 wiring: default persist=True routes resolved state into DataStore."""
    s = _fresh_service()
    calls: list[dict] = []

    class _FakeStore:
        def insert_context(self, **kwargs):
            calls.append(kwargs)

    # Monkeypatch DataStore import inside _persist
    import invasion.market.regime_service as _mod
    orig = _mod.__dict__.get("_test_store_override")
    _mod._test_store_override = _FakeStore()

    # Patch _persist to use override
    def _fake_persist(self, *, resolved, fear_greed, btc_dominance, btc_price,
                     volatility_z, trend_z, macro, ts):
        _mod._test_store_override.insert_context(
            regime=resolved, fear_greed=int(fear_greed),
            btc_dominance=float(btc_dominance), btc_price=float(btc_price),
            volatility_z=float(volatility_z), trend_z=float(trend_z),
            macro=macro, ts=ts,
        )

    import types
    s._persist = types.MethodType(_fake_persist, s)

    s.observe("crypto", Regime.CRISIS, now=1000.0, fear_greed=15,
              macro={"vix": 42.5})
    assert calls, "expected at least one insert_context call"
    last = calls[-1]
    assert last["regime"] == Regime.CRISIS
    assert last["fear_greed"] == 15
    assert last["macro"] == {"vix": 42.5}


def test_insert_context_sole_writer() -> None:
    """T2-4 PR5 invariant (I-R3): only RegimeService writes market_context.

    Grep-based CI assertion — scans invasion/ for any `.insert_context(`
    call site. The regime service is the single sanctioned caller; every
    other hit means a regression that bypasses the hysteresis state
    machine (the very thrash root-cause T2-4 was built to fix).
    """
    import subprocess
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[2]
    invasion_dir = repo_root / "invasion"
    r = subprocess.run(
        ["grep", "-rn", "--include=*.py", r"\.insert_context(",
         str(invasion_dir)],
        capture_output=True, text=True, check=False,
    )
    offenders: list[str] = []
    for line in r.stdout.splitlines():
        if not line:
            continue
        # Strip out legitimate sites:
        #   - definitions (`def insert_context`)
        #   - tests (regime_service's own unit test exercises the fake store)
        #   - the regime_service call itself (sole writer)
        if "def insert_context" in line:
            continue
        # Path portion is everything up to the first ':'
        path = line.split(":", 1)[0]
        pstr = str(path)
        if "regime_service.py" in pstr:
            continue
        if pstr.endswith("test_regime_service.py"):
            continue
        offenders.append(line)
    assert not offenders, (
        "Non-service insert_context callers detected (violates I-R3): "
        + "\n  ".join(offenders)
    )


def _run_all() -> int:
    tests = [
        test_cold_start_unknown,
        test_same_sample_no_transition,
        test_hysteresis_hold_samples_1_to_4,
        test_confirmed_transition_after_n_samples,
        test_min_hold_active_blocks_transition,
        test_crisis_bypass_instant,
        test_flip_rate_1h_increments,
        test_non_regime_sample_raises,
        test_snapshot_shape,
        test_domain_isolation,
        test_persist_default_writes_datastore,
        test_insert_context_sole_writer,
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
    # Smoke assert module-level imports worked
    assert TransitionResult and RegimeDomain
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
