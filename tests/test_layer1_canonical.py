"""Layer 1 unit + property tests.

Spec source: vault/30_components/layer-1-canonical-baseline.md.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.data.baseline import (
    append_sample,
    compute_baseline,
    read_baseline_state,
    update_baseline_from_window,
    upsert_baseline_state,
)
from polaris.core.data.canonical import (
    alpaca_quote_to_quote_tick,
    compute_underlying_group_id,
    okx_candle_to_bar,
    okx_ticker_to_quote_tick,
)
from polaris.core.data.schema import (
    ALLOWED_METRICS,
    LOOKBACK_FAST_SEC,
    LOOKBACK_SLOW_SEC,
    BaselineValue,
)

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# canonical.py
# ---------------------------------------------------------------------------


def test_compute_underlying_group_id_okx_dash() -> None:
    assert compute_underlying_group_id("okx", "BTC-USDT", "crypto") == "crypto:BTC"


def test_compute_underlying_group_id_capital_concat() -> None:
    assert compute_underlying_group_id("capital", "BTCUSD", "crypto") == "crypto:BTC"


def test_compute_underlying_group_id_forex() -> None:
    assert compute_underlying_group_id("capital", "EURUSD", "forex") == "forex:EURUSD"


def test_compute_underlying_group_id_equity() -> None:
    # Explicit equity branch (stream C prep). Symbol upper-cased like other branches.
    assert compute_underlying_group_id("alpaca", "SPY", "equity") == "equity:SPY"
    assert compute_underlying_group_id("alpaca", "aapl", "equity") == "equity:AAPL"


def test_compute_underlying_group_id_equity_behavior_unchanged() -> None:
    # The explicit equity branch must equal the prior generic-fallback output.
    assert compute_underlying_group_id("alpaca", "SPY", "equity") == "equity:SPY"


def test_compute_underlying_group_id_crypto_forex_regression() -> None:
    # Crypto/forex/index/commodity outputs are unchanged by the equity branch.
    assert compute_underlying_group_id("okx", "ETH-USDT", "crypto") == "crypto:ETH"
    assert compute_underlying_group_id("capital", "ETHUSD", "crypto") == "crypto:ETH"
    assert compute_underlying_group_id("capital", "GBPUSD", "fx") == "forex:GBPUSD"
    assert compute_underlying_group_id("capital", "US500", "index") == "index:US500"
    assert compute_underlying_group_id("capital", "XAUUSD", "commodity") == "commodity:XAUUSD"


def test_atr_floor_by_class_equity_key() -> None:
    from polaris.core.universe.schema import ATR_FLOOR_BY_CLASS

    assert ATR_FLOOR_BY_CLASS["equity"] == 1.0
    # Crypto floor unchanged (regression).
    assert ATR_FLOOR_BY_CLASS["crypto"] == 2.0


def test_okx_ticker_to_quote_tick_basic() -> None:
    payload = {
        "instId": "BTC-USDT",
        "bidPx": "60000",
        "askPx": "60002",
        "last": "60001",
        "bidSz": "0.5",
        "askSz": "0.4",
        "lastSz": "0.1",
    }
    qt = okx_ticker_to_quote_tick(payload, ts=NOW)
    assert qt.instrument_id == "okx:BTC-USDT"
    assert qt.mid == pytest.approx(60001.0)
    assert qt.spread_bps == pytest.approx((2.0 / 60001.0) * 10_000.0)


def test_okx_ticker_to_quote_tick_rejects_zero_bid() -> None:
    with pytest.raises(ValueError):
        okx_ticker_to_quote_tick({"instId": "X-USDT", "bidPx": "0", "askPx": "0"}, ts=NOW)


def test_alpaca_quote_to_quote_tick_basic() -> None:
    payload = {"S": "AAPL", "bp": 189.50, "ap": 189.60, "bs": 3, "as": 5}
    qt = alpaca_quote_to_quote_tick(payload, ts=NOW)
    assert qt.instrument_id == "alpaca:AAPL"
    assert qt.venue == "alpaca"
    assert qt.symbol == "AAPL"
    assert qt.ts == NOW
    assert qt.bid == 189.50
    assert qt.ask == 189.60
    assert math.isclose(qt.mid, 189.55)
    assert qt.bid_size == 3.0
    assert qt.ask_size == 5.0
    assert qt.source == "alpaca_ws"


def test_alpaca_quote_to_quote_tick_rejects_zero_ask() -> None:
    with pytest.raises(ValueError):
        alpaca_quote_to_quote_tick({"S": "AAPL", "bp": 189.5, "ap": 0}, ts=NOW)


def test_okx_candle_to_bar_canonicalizes_ts_and_fields() -> None:
    candle = ["1700000000000", "60000", "60500", "59800", "60100", "1.5", "90000", "90150", "1"]
    bar = okx_candle_to_bar(
        candle,
        inst_id="BTC-USDT",
        bar_interval="15m",
        underlying_group_id="crypto:BTC",
    )
    assert bar.ts == 1_700_000_000  # ms→s
    assert bar.open == 60000.0
    assert bar.close == 60100.0
    assert bar.notional_usd == 90150.0
    assert bar.bar_interval == "15m"


def test_okx_candle_to_bar_rejects_unknown_interval() -> None:
    with pytest.raises(ValueError):
        okx_candle_to_bar(
            ["1", "1", "1", "1", "1", "1", "1"],
            inst_id="X",
            bar_interval="42m",
            underlying_group_id="crypto:X",
        )


# ---------------------------------------------------------------------------
# baseline.py — pure
# ---------------------------------------------------------------------------


def test_compute_baseline_p50_lt_p75() -> None:
    bv = compute_baseline(metric="atr", samples=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], updated_ts=NOW)
    assert bv.p50 < bv.p75
    assert bv.sample_count == 10
    assert bv.lookback_sec == LOOKBACK_FAST_SEC


def test_compute_baseline_pnl_uses_slow_lookback() -> None:
    bv = compute_baseline(metric="pnl_std", samples=[0.1, 0.2, 0.3], updated_ts=NOW)
    assert bv.lookback_sec == LOOKBACK_SLOW_SEC


def test_compute_baseline_rejects_unknown_metric() -> None:
    with pytest.raises(ValueError):
        compute_baseline(metric="foo", samples=[1.0], updated_ts=NOW)


def test_compute_baseline_rejects_empty_samples() -> None:
    with pytest.raises(ValueError):
        compute_baseline(metric="atr", samples=[], updated_ts=NOW)


def test_baseline_value_p25_invariant() -> None:
    bv = compute_baseline(metric="atr", samples=[1, 2, 3, 4, 5, 6, 7, 8], updated_ts=NOW)
    assert bv.p25 <= bv.p50 <= bv.p75


# ---------------------------------------------------------------------------
# baseline.py — SQLite glue
# ---------------------------------------------------------------------------


def test_upsert_and_read_baseline_state(memdb) -> None:  # type: ignore[no-untyped-def]
    bv = BaselineValue(
        metric="atr",
        p50=2.5,
        p75=4.0,
        sample_count=120,
        lookback_sec=LOOKBACK_FAST_SEC,
        updated_ts=NOW,
    )
    upsert_baseline_state(
        memdb,
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        baseline=bv,
    )
    out = read_baseline_state(memdb, instrument_id="okx:BTC-USDT", metric="atr")
    assert out is not None
    assert out.p50 == 2.5
    assert out.p75 == 4.0
    # idempotent
    bv2 = BaselineValue(
        metric="atr",
        p50=3.0,
        p75=4.5,
        sample_count=130,
        lookback_sec=LOOKBACK_FAST_SEC,
        updated_ts=NOW + 60,
    )
    upsert_baseline_state(
        memdb,
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        baseline=bv2,
    )
    out2 = read_baseline_state(memdb, instrument_id="okx:BTC-USDT", metric="atr")
    assert out2 is not None and out2.p50 == 3.0


def test_update_baseline_from_window(memdb) -> None:  # type: ignore[no-untyped-def]
    for i, v in enumerate([1.0, 2.0, 3.0, 4.0, 5.0]):
        append_sample(
            memdb,
            instrument_id="okx:BTC-USDT",
            underlying_group_id="crypto:BTC",
            metric="atr",
            ts=NOW - 60 * i,
            value=v,
        )
    bv = update_baseline_from_window(
        memdb,
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        metric="atr",
        now_ts=NOW,
    )
    assert bv is not None
    assert bv.p50 == pytest.approx(3.0)
    persisted = read_baseline_state(memdb, instrument_id="okx:BTC-USDT", metric="atr")
    assert persisted is not None and persisted.p50 == bv.p50


def test_update_baseline_from_window_returns_none_when_empty(memdb) -> None:  # type: ignore[no-untyped-def]
    out = update_baseline_from_window(
        memdb,
        instrument_id="okx:UNK-USDT",
        underlying_group_id="crypto:UNK",
        metric="atr",
        now_ts=NOW,
    )
    assert out is None


@settings(max_examples=80, deadline=None)
@given(
    samples=st.lists(
        st.floats(min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=200,
    ),
    metric=st.sampled_from(sorted(ALLOWED_METRICS)),
)
def test_property_compute_baseline_p25_lt_p50_lt_p75(samples: list[float], metric: str) -> None:
    bv = compute_baseline(metric=metric, samples=samples, updated_ts=NOW)
    assert bv.p25 <= bv.p50 <= bv.p75
    assert bv.sample_count == len(samples)
