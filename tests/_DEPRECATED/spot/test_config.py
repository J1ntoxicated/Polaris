import pytest
from invasion.spot import config


def test_get_preg_returns_default_when_key_absent():
    # Synthetic key never present in live_config.json — caller default wins.
    assert config.get_preg("nonexistent_test_key_xyz", default=42) == 42


def test_get_preg_uses_internal_default():
    # When neither live_config nor caller default supplies a value, the
    # _DEFAULTS table inside config.py provides one.
    val = config.get_preg("spot_taker_score_threshold")
    assert val is not None


def test_okx_demo_creds_loaded(monkeypatch):
    monkeypatch.setenv("OKX_DEMO_API_KEY", "k1")
    monkeypatch.setenv("OKX_DEMO_SECRET", "s1")
    monkeypatch.setenv("OKX_DEMO_PASSPHRASE", "p1")
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_API_SECRET", raising=False)
    monkeypatch.delenv("OKX_PASSPHRASE", raising=False)
    creds = config.okx_demo_creds()
    assert creds == {"api_key": "k1", "secret": "s1", "passphrase": "p1"}


def test_okx_demo_creds_missing_raises(monkeypatch):
    for k in ("OKX_DEMO_API_KEY", "OKX_DEMO_SECRET", "OKX_DEMO_PASSPHRASE",
               "OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="OKX_DEMO"):
        config.okx_demo_creds()


def test_universe_default():
    u = config.universe()
    assert "BTC" in u and "ETH" in u
    assert len(u) == 10
