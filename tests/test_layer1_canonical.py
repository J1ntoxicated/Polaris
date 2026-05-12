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
    compute_underlying_group_id,
    make_market_event,
    okx_candle_to_bar,
    okx_ticker_to_quote_tick,
)
from polaris.core.data.normalize import (
    get_baseline,
    normalize,
    ratio_to_baseline,
)
from polaris.core.data.schema import (
    ALLOWED_METRICS,
    COLD_START_NEUTRAL,
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


def test_make_market_event_round_trip() -> None:
    ev = make_market_event(
        ts=NOW,
        event_type="listing",
        venue="okx",
        symbol="ZEC-USDT",
        payload={"reason": "auto-onboard"},
    )
    assert ev.type == "listing"
    assert "auto-onboard" in ev.payload_json


def test_make_market_event_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        make_market_event(ts=NOW, event_type="rogue", venue="okx", symbol="X")


def test_signal_dataclass_smoke() -> None:
    """Spec L1 Q1: `signals` table → `Signal` dataclass exists."""
    from polaris.core.data.schema import Signal

    sig = Signal(
        strategy_id="volume_burst",
        signal_id="abc-123",
        instrument_id="okx:BTC-USDT",
        direction="long",
        score=0.82,
        thesis="vol expansion 2x baseline",
        ts=NOW,
    )
    assert sig.strategy_id == "volume_burst"
    assert sig.score == 0.82
    assert sig.correlation_group is None


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


# ---------------------------------------------------------------------------
# normalize.py — pure ratio
# ---------------------------------------------------------------------------


def test_ratio_to_baseline_basic() -> None:
    assert ratio_to_baseline(2.0, 1.0) == 2.0
    assert ratio_to_baseline(0.5, 1.0) == 0.5


def test_ratio_to_baseline_zero_p50_neutral() -> None:
    assert ratio_to_baseline(99.0, 0.0) == COLD_START_NEUTRAL


def test_ratio_to_baseline_nonfinite_neutral() -> None:
    assert ratio_to_baseline(math.inf, 1.0) == COLD_START_NEUTRAL
    assert ratio_to_baseline(math.nan, 1.0) == COLD_START_NEUTRAL


def test_ratio_to_baseline_negative_raw_neutral() -> None:
    """Domain [0, +inf) — negative raw = invalid magnitude → neutral 1.0."""
    assert ratio_to_baseline(-1.0, 2.0) == COLD_START_NEUTRAL
    assert ratio_to_baseline(-99.0, 1.0) == COLD_START_NEUTRAL


# ---------------------------------------------------------------------------
# normalize.py — DB-backed
# ---------------------------------------------------------------------------


def _seed_baseline(
    conn,
    *,
    inst: str,
    group: str,
    metric: str,
    p50: float,
    asset_class: str = "",
) -> None:
    bv = BaselineValue(
        metric=metric,
        p50=p50,
        p75=p50 * 1.5,
        sample_count=50,
        lookback_sec=LOOKBACK_FAST_SEC,
        updated_ts=NOW,
    )
    upsert_baseline_state(
        conn,
        instrument_id=inst,
        underlying_group_id=group,
        asset_class=asset_class,
        baseline=bv,
    )


def test_normalize_uses_instrument_baseline(memdb) -> None:  # type: ignore[no-untyped-def]
    _seed_baseline(memdb, inst="okx:BTC-USDT", group="crypto:BTC", metric="atr", p50=2.0)
    out = normalize(
        memdb,
        instrument_id="okx:BTC-USDT",
        metric="atr",
        raw_value=4.0,
        underlying_group_id="crypto:BTC",
    )
    assert out == pytest.approx(2.0)


def test_normalize_falls_back_to_underlying_group(memdb) -> None:  # type: ignore[no-untyped-def]
    _seed_baseline(memdb, inst="okx:BTC-USDT", group="crypto:BTC", metric="atr", p50=3.0)
    out = normalize(
        memdb,
        instrument_id="capital:BTCUSD",
        metric="atr",
        raw_value=6.0,
        underlying_group_id="crypto:BTC",
    )
    assert out == pytest.approx(2.0)


def test_normalize_falls_back_to_asset_class(memdb) -> None:  # type: ignore[no-untyped-def]
    """Spec L1 Q4 cold-start chain: instrument → group → asset_class → 1.0."""
    _seed_baseline(
        memdb,
        inst="okx:DOGE-USDT",
        group="crypto:DOGE",
        metric="atr",
        p50=4.0,
        asset_class="crypto",
    )
    out = normalize(
        memdb,
        instrument_id="okx:NEW-USDT",
        metric="atr",
        raw_value=8.0,
        underlying_group_id="crypto:NEW",
        asset_class="crypto",
    )
    assert out == pytest.approx(2.0)


def test_normalize_cold_start_neutral(memdb) -> None:  # type: ignore[no-untyped-def]
    out = normalize(
        memdb,
        instrument_id="okx:NEW-USDT",
        metric="atr",
        raw_value=99.0,
        underlying_group_id="crypto:NEW",
        asset_class="crypto",
    )
    assert out == COLD_START_NEUTRAL


def test_get_baseline_asof_ts_filters(memdb) -> None:  # type: ignore[no-untyped-def]
    _seed_baseline(
        memdb,
        inst="okx:BTC-USDT",
        group="crypto:BTC",
        metric="atr",
        p50=3.0,
        asset_class="crypto",
    )
    # asof before seed updated_ts → no row.
    out = get_baseline(memdb, instrument_id="okx:BTC-USDT", metric="atr", asof_ts=NOW - 86400)
    assert out is None
    # asof at NOW → seed visible.
    out2 = get_baseline(memdb, instrument_id="okx:BTC-USDT", metric="atr", asof_ts=NOW)
    assert out2 is not None and out2.p50 == 3.0


def test_get_baseline_unknown_metric_raises(memdb) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        get_baseline(memdb, instrument_id="okx:BTC-USDT", metric="bogus")


# ---------------------------------------------------------------------------
# Property: normalize always finite
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    raw=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    p50=st.floats(min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_property_ratio_finite_and_positive(raw: float, p50: float) -> None:
    out = ratio_to_baseline(raw, p50)
    assert math.isfinite(out)
    assert out >= 0.0
    # raw / p50 may underflow to 0 in subnormal territory; allow >= 0 only.
    if raw >= 1e-6 and p50 <= 1e6:
        assert out > 0.0


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
