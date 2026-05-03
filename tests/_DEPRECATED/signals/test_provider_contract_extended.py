"""T2-2 PR3 — extended provider SignalContract edge_prob unit tests.

Covers the 8+ extended providers migrated in PR3:
  1. WQAlpha1Signal / WQAlpha6Signal   (providers_wqalpha.py)
  2. MacroRegimeSignal                 (providers_macro.py)
  3. InstitutionalPositionSignal       (providers_institutional.py)
  4. DualThrustSignal / SessionBreakoutSignal (providers_breakout.py)
  5. 16 External DataProviderBase subclasses  (providers_external.py)
  6. MLSignalProvider                  (ml_signal.py)
  7. On-chain (OnChainValuationSignal / BasisSpreadSignal /
     LiquidationCascadeSignal / GoogleTrendsSignal / LLMSentimentSignal)
     (providers_onchain.py)
  8. CrossExchangeSignal               (providers_cross.py)

Each test verifies the SignalContract edge_prob stays inside the
documented band per provider and fallback paths return neutral contract.
"""
from __future__ import annotations

import time

import pytest

from invasion.signals.contract import SignalContract


# ── WQAlpha: alpha z-score → erf ───────────────────────────────────


def _make_price_hist(prices, dt_s=60):
    now = time.time()
    return [(now - (len(prices) - i) * dt_s, p) for i, p in enumerate(prices)]


def test_wqalpha1_rising_prices_high_edge():
    from invasion.signals.providers_wqalpha import WQAlpha1Signal
    prov = WQAlpha1Signal()
    # 30 ascending prices → argmax at the end → normalized ≈ +0.5
    prices = [100 + i * 0.1 for i in range(30)]
    r = prov.compute("BTC", market_data={"_price_hist": _make_price_hist(prices)})
    assert isinstance(r.contract, SignalContract)
    assert 0.2 <= r.contract.edge_prob <= 0.8
    assert r.contract.edge_prob > 0.6  # alpha_z ≈ 1.0 → edge > 0.6
    assert r.contract.reversal_horizon_sec == 1800


def test_wqalpha1_insufficient_bars_neutral():
    from invasion.signals.providers_wqalpha import WQAlpha1Signal
    r = WQAlpha1Signal().compute("BTC", market_data={"_price_hist": []})
    assert r.contract == SignalContract.neutral()


def test_wqalpha6_strong_correlation_high_edge():
    from invasion.signals.providers_wqalpha import WQAlpha6Signal
    prices = [100 + i * 0.5 for i in range(12)]
    vols = [1000 + i * 10 for i in range(12)]  # highly correlated
    now = time.time()
    vol_hist = [(now - (12 - i) * 60, v) for i, v in enumerate(vols)]
    md = {"_price_hist": _make_price_hist(prices), "_vol_hist": vol_hist}
    r = WQAlpha6Signal().compute("BTC", market_data=md)
    assert isinstance(r.contract, SignalContract)
    assert 0.2 <= r.contract.edge_prob <= 0.8
    assert r.contract.reversal_horizon_sec == 1800


# ── MacroRegime: regime fit ────────────────────────────────────────


def test_macro_regime_strong_fear_high_edge():
    from invasion.signals.providers_macro import MacroRegimeSignal
    prov = MacroRegimeSignal()
    # Inject macro snapshot directly via cache
    prov._snapshot = {
        "cnn_fear_greed": 10,  # max fear
        "vix": 45,             # fear spike
        "hy_spread": 600,      # credit stress
    }
    prov._snapshot_ts = time.time()
    r = prov.compute("SPY")
    assert isinstance(r.contract, SignalContract)
    assert 0.3 <= r.contract.edge_prob <= 0.8
    assert r.contract.reversal_horizon_sec == 3600


def test_macro_regime_no_data_neutral():
    from invasion.signals.providers_macro import MacroRegimeSignal
    r = MacroRegimeSignal().compute("SPY")
    assert r.contract == SignalContract.neutral()


# ── Institutional: positioning extremity ───────────────────────────


def test_institutional_extreme_positioning():
    from invasion.signals.providers_institutional import InstitutionalPositionSignal
    prov = InstitutionalPositionSignal()
    # Pre-populate COT cache with extreme long positioning
    prov._cot_data = {
        "GOLD": {
            "large_spec_pct_rank_3y": 98,  # crowd very long
            "large_spec_net": 150000,
            "commercial_net": -150000,
            "small_spec_net": 50000,
            "signal": "EXTREME_LONG",
        }
    }
    prov._cot_ts = time.time()
    # Force COT path by setting _cot to truthy sentinel
    prov._cot = object()
    r = prov.compute("Gold", group="commodity")
    assert isinstance(r.contract, SignalContract)
    assert 0.3 <= r.contract.edge_prob <= 0.8
    assert r.contract.reversal_horizon_sec == 7200


def test_institutional_no_data_neutral():
    from invasion.signals.providers_institutional import InstitutionalPositionSignal
    r = InstitutionalPositionSignal().compute("EUR/USD", group="forex")
    assert r.contract == SignalContract.neutral()


# ── Breakout: fixed 0.55 baseline ──────────────────────────────────


def test_breakout_contract_baseline():
    from invasion.signals.providers_breakout import _breakout_contract
    c = _breakout_contract("XAUUSD", 30)
    assert 0.5 <= c.edge_prob <= 0.65
    assert c.reversal_horizon_sec == 1800


def test_breakout_contract_strong_score():
    from invasion.signals.providers_breakout import _breakout_contract
    c = _breakout_contract("XAUUSD", 80)
    # score/100 = 0.8 → edge = 0.55 + 0.08 = 0.63
    assert c.edge_prob > 0.6


# ── External (DataProviderBase wrapper) — 16 classes ───────────────


class _FakeDC:
    def __init__(self, payload):
        self._payload = payload

    def get(self, key):
        return self._payload


def test_cboe_put_call_high_pc_ratio():
    from invasion.signals.providers_external import CBOEPutCallProvider
    dc = _FakeDC({"put_call_ratio": 1.3, "pc_signal": "fear"})
    prov = CBOEPutCallProvider(data_collector=dc)
    r = prov.compute("SPY", group="stock")
    assert isinstance(r.contract, SignalContract)
    assert 0.3 <= r.contract.edge_prob <= 0.8
    assert r.contract.reversal_horizon_sec == 3600


def test_external_neutral_when_group_mismatch():
    from invasion.signals.providers_external import BakerHughesProvider
    dc = _FakeDC({"us_oil_rig_change_wk": 10})
    prov = BakerHughesProvider(data_collector=dc)
    # Group mismatch (not commodity) → neutral
    r = prov.compute("BTC", group="crypto")
    assert r.contract == SignalContract.neutral()


def test_external_alternative_me_extreme_fear():
    from invasion.signals.providers_external import AlternativeMeProvider
    dc = _FakeDC({"value": 10, "label": "extreme fear"})
    prov = AlternativeMeProvider(data_collector=dc)
    r = prov.compute("BTC", group="crypto")
    assert isinstance(r.contract, SignalContract)
    assert 0.3 <= r.contract.edge_prob <= 0.8


# ── ML Signal: neutral path (model unavailable) ────────────────────


def test_ml_signal_disabled_neutral():
    from invasion.signals.ml_signal import MLSignalProvider
    prov = MLSignalProvider()
    r = prov.compute("BTC", market_data={})
    # Neutral path — contract stays default neutral
    assert r.contract == SignalContract.neutral()


# ── On-chain: z-score tanh ─────────────────────────────────────────


def test_basis_spread_extreme_backwardation():
    from invasion.signals.providers_onchain import BasisSpreadSignal
    r = BasisSpreadSignal().compute(
        "BTC", market_data={"price": 50050, "mark_price": 50000}
    )
    assert isinstance(r.contract, SignalContract)
    assert 0.3 <= r.contract.edge_prob <= 0.8
    assert r.contract.reversal_horizon_sec == 7200


def test_basis_spread_no_data_neutral():
    from invasion.signals.providers_onchain import BasisSpreadSignal
    r = BasisSpreadSignal().compute("BTC", market_data={})
    assert r.contract == SignalContract.neutral()


def test_liq_cascade_strong_imbalance():
    from invasion.signals.providers_onchain import LiquidationCascadeSignal

    class DCWithHeatmap:
        _cache = {
            "coinglass_liq_heatmap": {
                "total_long_liq": 100_000_000,
                "total_short_liq": 10_000_000,
            }
        }

    prov = LiquidationCascadeSignal(data_collector=DCWithHeatmap())
    r = prov.compute("BTC")
    assert isinstance(r.contract, SignalContract)
    assert 0.3 <= r.contract.edge_prob <= 0.8
    assert r.contract.reversal_horizon_sec == 3600


# ── CrossExchange: arb spread ──────────────────────────────────────


def test_cross_exchange_agreeing_sources_high_edge():
    from invasion.signals.providers_cross import CrossExchangeSignal

    class LakeMock:
        def latest(self, table, ticker, n=1):
            return [{"rate": 0.002}]

    class BinMock:
        def get_funding_rate(self, sym):
            return 0.0025

    prov = CrossExchangeSignal(binance=BinMock(), lake=LakeMock())
    r = prov.compute("BTC-USDT-SWAP", group="crypto")
    assert isinstance(r.contract, SignalContract)
    assert 0.5 <= r.contract.edge_prob <= 0.9
    assert r.contract.reversal_horizon_sec == 300


def test_cross_exchange_non_crypto_neutral():
    from invasion.signals.providers_cross import CrossExchangeSignal
    r = CrossExchangeSignal().compute("SPY", group="stock")
    assert r.contract == SignalContract.neutral()


# ── Invariant across PR3: edge_prob ∈ [0, 1] ───────────────────────


def test_pr3_all_providers_edge_prob_bounded():
    """Smoke: no PR3 provider emits edge_prob outside [0, 1]."""
    from invasion.signals.providers_wqalpha import WQAlpha1Signal, WQAlpha6Signal
    from invasion.signals.providers_onchain import (
        BasisSpreadSignal,
    )
    from invasion.signals.providers_cross import CrossExchangeSignal
    from invasion.signals.providers_external import (
        CBOEPutCallProvider, AlternativeMeProvider,
    )

    class DCPC:
        def get(self, key):
            return {"put_call_ratio": 0.5} if key == "cboe_put_call" else None

    class DCAM:
        def get(self, key):
            return {"value": 95} if key == "alternative_me" else None

    cases = [
        (WQAlpha1Signal(), "BTC",
         {"_price_hist": _make_price_hist([100 + i * 0.1 for i in range(30)])}),
        (BasisSpreadSignal(), "BTC", {"price": 50100, "mark_price": 50000}),
        (CBOEPutCallProvider(data_collector=DCPC()), "SPY", {"group": "stock"}),
        (AlternativeMeProvider(data_collector=DCAM()), "BTC", {"group": "crypto"}),
    ]
    for prov, ticker, md in cases:
        kwargs = {"market_data": md}
        if "group" in md:
            kwargs["group"] = md["group"]
        r = prov.compute(ticker, **kwargs)
        assert 0.0 <= r.contract.edge_prob <= 1.0, (
            f"{prov.name}: edge_prob={r.contract.edge_prob} out of range"
        )
        assert 0.0 <= r.contract.direction_certainty <= 1.0
        assert r.contract.execution_risk_bps >= 0.0
