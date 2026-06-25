"""Chart-tab backend tests (DEMO/PAPER, display-only, READ-ONLY).

Covers the on-demand OHLCV + technical-series builder behind the dashboard
``/api/ticker_chart`` endpoint. Nothing here touches sizing/risk/orders — the
module reads the ``bars`` table read-only and computes indicator SERIES for the
chart canvas. Tests assert indicator math on known inputs, the bars+indicators
payload shape, per-resolution venue-native interval mapping, graceful empty
handling, and the symbol-list grouping for the selector.
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from tools.visualizer import chart_data as cd

# ── indicator series math ───────────────────────────────────────────────────


def test_sma_series_aligns_and_warms_up() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = cd.sma_series(closes, 3)
    assert len(out) == len(closes)
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)


def test_ema_series_seeds_with_sma_then_recurses() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    out = cd.ema_series(closes, 3)
    assert out[0] is None and out[1] is None
    # seed = SMA of first 3 = 2.0
    assert out[2] == pytest.approx(2.0)
    k = 2.0 / (3 + 1)
    expect3 = 4.0 * k + 2.0 * (1 - k)
    assert out[3] == pytest.approx(expect3)


def test_rsi_series_all_gains_is_100() -> None:
    closes = [float(i) for i in range(1, 30)]  # strictly increasing
    out = cd.rsi_series(closes, 14)
    assert out[:14] == [None] * 14
    assert out[-1] == pytest.approx(100.0)


def test_rsi_series_flat_is_neutral_50() -> None:
    closes = [10.0] * 30
    out = cd.rsi_series(closes, 14)
    assert out[-1] == pytest.approx(50.0)


def test_macd_series_shapes_and_zero_on_flat() -> None:
    closes = [10.0] * 60
    macd, signal, hist = cd.macd_series(closes, 12, 26, 9)
    assert len(macd) == len(signal) == len(hist) == len(closes)
    # On a flat series every defined MACD/hist value is ~0.
    defined = [v for v in hist if v is not None]
    assert defined  # signal is defined somewhere
    assert all(abs(v) < 1e-9 for v in defined)


def test_bollinger_series_matches_manual() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    up, mid, lo = cd.bollinger_series(closes, 5, 2.0)
    assert mid[-1] == pytest.approx(3.0)
    var = sum((v - 3.0) ** 2 for v in closes) / 5
    sd = math.sqrt(var)
    assert up[-1] == pytest.approx(3.0 + 2.0 * sd)
    assert lo[-1] == pytest.approx(3.0 - 2.0 * sd)


def test_atr_series_constant_range() -> None:
    # high-low = 2 every bar, no gaps → ATR converges to 2.0
    highs = [11.0] * 20
    lows = [9.0] * 20
    closes = [10.0] * 20
    out = cd.atr_series(highs, lows, closes, 14)
    assert out[-1] == pytest.approx(2.0, abs=1e-6)


def test_stochastic_series_top_and_bottom() -> None:
    highs = [float(i) + 1 for i in range(20)]
    lows = [float(i) for i in range(20)]
    closes_top = [h for h in highs]  # close at high → %K = 100
    k, d = cd.stochastic_series(highs, lows, closes_top, 14, 3)
    assert k[-1] == pytest.approx(100.0)
    assert len(d) == len(k)


def test_vwap_series_rolling_constant_price() -> None:
    highs = [10.0] * 10
    lows = [10.0] * 10
    closes = [10.0] * 10
    vols = [5.0] * 10
    out = cd.vwap_series(highs, lows, closes, vols, 20)
    assert out[-1] == pytest.approx(10.0)


def test_adx_series_trending_is_high() -> None:
    # strong uptrend → ADX should be elevated, defined after warmup
    highs = [float(i) + 0.5 for i in range(40)]
    lows = [float(i) for i in range(40)]
    closes = [float(i) + 0.25 for i in range(40)]
    out = cd.adx_series(highs, lows, closes, 14)
    assert len(out) == 40
    last = out[-1]
    assert last is not None and last > 40.0


def test_momentum_series_zero_on_flat() -> None:
    closes = [10.0] * 30
    out = cd.momentum_series(closes, 10)
    assert out[-1] == pytest.approx(0.0)


def test_macd_alignment_first_defined_indices() -> None:
    # Non-flat series so MACD/signal are genuinely non-zero where defined.
    closes = [100.0 + math.sin(i / 3.0) * 5.0 for i in range(80)]
    macd, signal, hist = cd.macd_series(closes, 12, 26, 9)
    first_macd = next(i for i, v in enumerate(macd) if v is not None)
    first_sig = next(i for i, v in enumerate(signal) if v is not None)
    # MACD line is defined once the slow EMA exists (index slow-1).
    assert first_macd == 26 - 1
    # signal EMA needs `signal` defined MACD points → +(signal-1).
    assert first_sig == first_macd + (9 - 1)
    # histogram is defined exactly where BOTH macd and signal are defined.
    hist_mask = [v is not None for v in hist]
    both_mask = [
        (m is not None and s is not None)
        for m, s in zip(macd, signal, strict=True)
    ]
    assert hist_mask == both_mask


def test_adx_alignment_first_defined_index() -> None:
    highs = [float(i) + 0.5 for i in range(60)]
    lows = [float(i) for i in range(60)]
    closes = [float(i) + 0.25 for i in range(60)]
    out = cd.adx_series(highs, lows, closes, 14)
    first = next(i for i, v in enumerate(out) if v is not None)
    # Wilder ADX warms up after 2n bars (n for DX, then n DX values smoothed).
    assert first == 2 * 14
    # every slot from `first` onward is defined (contiguous tail).
    assert all(v is not None for v in out[first:])


# ── build_ticker_chart over a seeded bars table ─────────────────────────────


def _seed_bars(
    conn: sqlite3.Connection, *, venue: str, symbol: str, interval: str, n: int
) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bars (
            instrument_id TEXT, underlying_group_id TEXT, venue TEXT, symbol TEXT,
            bar_interval TEXT, ts INTEGER, open REAL, high REAL, low REAL,
            close REAL, volume REAL, notional_usd REAL, trade_count INTEGER,
            vwap REAL, bid_close REAL, ask_close REAL, spread_bps_close REAL,
            source TEXT)"""
    )
    base = 1_700_000_000
    rows = []
    for i in range(n):
        c = 100.0 + i
        rows.append(
            (
                f"{venue}:{symbol}", f"{venue}.{symbol}", venue, symbol, interval,
                base + i * 60, c - 0.5, c + 1.0, c - 1.0, c, 10.0 + i,
                0.0, 0, 0.0, 0.0, 0.0, 0.0, "rest",
            )
        )
    conn.executemany(
        "INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()


def test_build_ticker_chart_returns_bars_and_indicators() -> None:
    conn = sqlite3.connect(":memory:")
    _seed_bars(conn, venue="okx", symbol="BTC-USDT", interval="1m", n=60)
    out = cd.build_ticker_chart(
        conn, venue="okx", symbol="BTC-USDT", resolution="1m", limit=240
    )
    assert out["available"] is True
    assert out["symbol"] == "BTC-USDT"
    assert out["venue"] == "okx"
    assert out["native_interval"] == "1m"
    assert len(out["bars"]) == 60
    b0 = out["bars"][0]
    assert set(b0) == {"t", "o", "h", "l", "c", "v"}
    ind = out["indicators"]
    for key in (
        "ema20", "ema50", "bb_upper", "bb_mid", "bb_lower", "vwap", "rsi",
        "macd", "macd_signal", "macd_hist", "atr", "stoch_k", "stoch_d",
        "adx", "momentum",
    ):
        assert key in ind, key
        assert len(ind[key]) == 60, key


def test_resolution_maps_to_native_interval() -> None:
    conn = sqlite3.connect(":memory:")
    _seed_bars(conn, venue="okx", symbol="ETH-USDT", interval="15m", n=30)
    out = cd.build_ticker_chart(
        conn, venue="okx", symbol="ETH-USDT", resolution="15m", limit=240
    )
    assert out["native_interval"] == "15m"
    assert out["available"] is True
    # 1h → 1H, 1d → 1D mapping
    assert cd.resolution_to_interval("1h") == "1H"
    assert cd.resolution_to_interval("1d") == "1D"
    assert cd.resolution_to_interval("1m") == "1m"


def test_empty_symbol_is_graceful() -> None:
    conn = sqlite3.connect(":memory:")
    _seed_bars(conn, venue="okx", symbol="BTC-USDT", interval="1m", n=10)
    out = cd.build_ticker_chart(
        conn, venue="okx", symbol="NOPE-USDT", resolution="1m", limit=240
    )
    assert out["available"] is False
    assert out["bars"] == []
    assert out["indicators"] == {}


def test_unknown_resolution_is_graceful() -> None:
    conn = sqlite3.connect(":memory:")
    _seed_bars(conn, venue="okx", symbol="BTC-USDT", interval="1m", n=10)
    out = cd.build_ticker_chart(
        conn, venue="okx", symbol="BTC-USDT", resolution="7x", limit=240
    )
    assert out["available"] is False


def test_list_chart_symbols_groups_by_venue() -> None:
    conn = sqlite3.connect(":memory:")
    _seed_bars(conn, venue="okx", symbol="BTC-USDT", interval="1m", n=5)
    _seed_bars(conn, venue="okx", symbol="ETH-USDT", interval="15m", n=5)
    _seed_bars(conn, venue="capital", symbol="EURUSD", interval="5m", n=5)
    groups = cd.list_chart_symbols(conn)
    by_venue = {g["venue"]: g for g in groups}
    assert set(by_venue) == {"okx", "capital"}
    okx = by_venue["okx"]
    syms = {s["symbol"] for s in okx["symbols"]}
    assert syms == {"BTC-USDT", "ETH-USDT"}
    # each symbol carries the resolutions that actually exist for it
    btc = next(s for s in okx["symbols"] if s["symbol"] == "BTC-USDT")
    assert btc["resolutions"] == ["1m"]
    eth = next(s for s in okx["symbols"] if s["symbol"] == "ETH-USDT")
    assert eth["resolutions"] == ["15m"]
