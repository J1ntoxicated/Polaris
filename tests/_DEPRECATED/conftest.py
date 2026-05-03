"""Session-wide fixtures for invasion/ test suite."""
from __future__ import annotations
import pytest
import time


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset module-level singletons between tests to avoid bleed."""
    # Reset RegimeService
    try:
        from invasion.market.regime_service import RegimeService
        RegimeService._instance = None
    except Exception:
        pass
    # MSG-NS-FLAT-AUTO-BLOCK-KILL (2026-04-18): _FLAT_AUTO_BLOCK 전역 삭제됨.
    yield


@pytest.fixture
def frozen_time(monkeypatch):
    """Freeze time.time() to a known value; return advancer callable."""
    t = [1_700_000_000.0]
    def _time():
        return t[0]
    def advance(sec):
        t[0] += sec
    monkeypatch.setattr("time.time", _time)
    return advance


@pytest.fixture
def preg_override(monkeypatch):
    """Override param_registry.get for test isolation."""
    overrides = {}
    def _get(key, default=None):
        if key in overrides:
            return overrides[key]
        from invasion.config.param_registry import get as real_get
        try:
            return real_get(key)
        except KeyError:
            return default
    monkeypatch.setattr("invasion.config.param_registry.get", _get)
    return overrides  # test mutates this dict


@pytest.fixture
def tmp_strategies_dir(tmp_path, monkeypatch):
    """Point STRATEGIES_DIR at a temp path."""
    d = tmp_path / "strategies"
    d.mkdir()
    monkeypatch.setenv("INVASION_STRATEGIES_DIR", str(d))
    return d
