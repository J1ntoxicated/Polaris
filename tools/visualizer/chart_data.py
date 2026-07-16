"""On-demand OHLCV + technical-series builder for the dashboard Chart tab.

DEMO/PAPER, **display-only, READ-ONLY**. This module powers the ``/api/ticker_chart``
endpoint: given a venue/symbol/resolution it reads the stored ``bars`` (via
``read_recent_bars``) and computes per-bar indicator SERIES for the chart canvas
(candles + volume + MA/BB/VWAP overlays + RSI/MACD/ADX/Stochastic sub-panels).

Why series, not the live MarketView: ``build_real_market_view`` returns ONE
*scalar* value per indicator (the latest reading the gates consume); a chart
needs the full time-aligned line, so the series math lives here. The conventions
mirror ``polaris/scripts/_production_indicators.py`` (Wilder RSI, SMA-based MA,
20/2 Bollinger, 14 ATR/ADX) so the canvas agrees with what the bot reads.

Nothing here touches sizing / risk / orders. It is a pure read of the ``bars``
table plus arithmetic. When a dedicated technical store lands (roadmap #12) the
``build_ticker_chart`` reader can be pointed at it; the on-demand path stays the
graceful fallback (see the store-hook note in ``build_ticker_chart``).
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from polaris.scripts._production_bars import read_recent_bars

# Frontend resolution token → venue-native ``bars.bar_interval`` string. Only
# the intervals the ingest pipeline actually persists are mapped; anything else
# resolves to ``None`` and yields a graceful empty payload (never an error).
_RESOLUTION_TO_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1H",
    "1d": "1D",
}

# Default candles to draw. 240 ≈ a full session of 1m bars — enough for the
# slow 26/200-style windows to warm up without an unbounded read.
_DEFAULT_LIMIT = 240

Series = list[float | None]


def resolution_to_interval(resolution: str) -> str | None:
    """Map a frontend resolution token (``1m``/``1h``/``1d``) to a bar_interval."""
    return _RESOLUTION_TO_INTERVAL.get(resolution.strip().lower())


# ── indicator series (None-padded through each indicator's warmup) ──────────


def sma_series(values: list[float], n: int) -> Series:
    """Simple moving average; first ``n-1`` slots are ``None`` (warmup)."""
    out: Series = [None] * len(values)
    if n <= 0:
        return out
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def ema_series(values: list[float], n: int) -> Series:
    """EMA seeded with the SMA of the first ``n`` values (None before that)."""
    out: Series = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    k = 2.0 / (n + 1.0)
    seed = sum(values[:n]) / n
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rsi_series(closes: list[float], n: int = 14) -> Series:
    """Wilder's RSI; ``None`` for the first ``n`` slots (need n deltas)."""
    out: Series = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0.0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / n
    avg_loss = losses / n
    out[n] = _rsi_from_avgs(avg_gain, avg_loss)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0.0 else 0.0
        loss = -d if d < 0.0 else 0.0
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
        out[i] = _rsi_from_avgs(avg_gain, avg_loss)
    return out


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss <= 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd_series(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Series, Series, Series]:
    """MACD line (EMA_fast − EMA_slow), signal EMA, and histogram."""
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    macd: Series = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow, strict=True)
    ]
    # Signal EMA is computed over the contiguous defined tail of the MACD line.
    defined = [(i, v) for i, v in enumerate(macd) if v is not None]
    sig: Series = [None] * len(closes)
    hist: Series = [None] * len(closes)
    if len(defined) >= signal:
        vals = [v for _, v in defined]
        sig_tail = ema_series(vals, signal)
        for (orig_i, _), s in zip(defined, sig_tail, strict=True):
            sig[orig_i] = s
            m = macd[orig_i]
            if s is not None and m is not None:
                hist[orig_i] = m - s
    return macd, sig, hist


def bollinger_series(
    closes: list[float], n: int = 20, k: float = 2.0
) -> tuple[Series, Series, Series]:
    """(upper, middle, lower) Bollinger bands; ``None`` through warmup."""
    mid = sma_series(closes, n)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        m = mid[i]
        if m is None:
            continue
        window = closes[i - n + 1 : i + 1]
        var = sum((v - m) ** 2 for v in window) / n
        sd = math.sqrt(var)
        upper[i] = m + k * sd
        lower[i] = m - k * sd
    return upper, mid, lower


def atr_series(
    highs: list[float], lows: list[float], closes: list[float], n: int = 14
) -> Series:
    """Wilder ATR; ``None`` until ``n`` true ranges are available."""
    length = len(closes)
    out: Series = [None] * length
    if length <= n:
        return out
    trs: list[float] = [0.0] * length
    for i in range(1, length):
        trs[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = sum(trs[1 : n + 1]) / n
    out[n] = atr
    for i in range(n + 1, length):
        atr = (atr * (n - 1) + trs[i]) / n
        out[i] = atr
    return out


def stochastic_series(
    highs: list[float], lows: list[float], closes: list[float],
    n: int = 14, d: int = 3,
) -> tuple[Series, Series]:
    """Stochastic %K (n-window) and %D (SMA of %K over ``d``)."""
    length = len(closes)
    k_line: Series = [None] * length
    for i in range(n - 1, length):
        hh = max(highs[i - n + 1 : i + 1])
        ll = min(lows[i - n + 1 : i + 1])
        rng = hh - ll
        k_line[i] = 100.0 * (closes[i] - ll) / rng if rng > 0.0 else 50.0
    # %D = simple MA of the defined %K tail.
    defined = [(i, v) for i, v in enumerate(k_line) if v is not None]
    d_line: Series = [None] * length
    if len(defined) >= d:
        vals = [v for _, v in defined]
        d_tail = sma_series(vals, d)
        for (orig_i, _), dv in zip(defined, d_tail, strict=True):
            d_line[orig_i] = dv
    return k_line, d_line


def vwap_series(
    highs: list[float], lows: list[float], closes: list[float],
    volumes: list[float], n: int = 20,
) -> Series:
    """Rolling VWAP over the last ``n`` bars (typical price × volume).

    A rolling (not session-anchored) VWAP keeps the overlay meaningful on the
    bounded ``limit`` window the chart draws — no session boundary is stored
    per bar to anchor against.
    """
    length = len(closes)
    out: Series = [None] * length
    for i in range(length):
        lo = max(0, i - n + 1)
        pv = 0.0
        vol = 0.0
        for j in range(lo, i + 1):
            tp = (highs[j] + lows[j] + closes[j]) / 3.0
            pv += tp * volumes[j]
            vol += volumes[j]
        out[i] = pv / vol if vol > 0.0 else closes[i]
    return out


def adx_series(
    highs: list[float], lows: list[float], closes: list[float], n: int = 14
) -> Series:
    """Wilder ADX(n) with full DX smoothing; ``None`` through warmup."""
    length = len(closes)
    out: Series = [None] * length
    if length < 2 * n + 1:
        return out
    tr: list[float] = [0.0] * length
    plus_dm: list[float] = [0.0] * length
    minus_dm: list[float] = [0.0] * length
    for i in range(1, length):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0.0) else 0.0
        minus_dm[i] = down if (down > up and down > 0.0) else 0.0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    # Wilder-smoothed TR / +DM / −DM, then DX, then ADX = smoothed DX.
    atr = sum(tr[1 : n + 1])
    sp = sum(plus_dm[1 : n + 1])
    sm = sum(minus_dm[1 : n + 1])
    dx_vals: list[float] = []
    dx_index: list[int] = []
    for i in range(n + 1, length):
        atr = atr - atr / n + tr[i]
        sp = sp - sp / n + plus_dm[i]
        sm = sm - sm / n + minus_dm[i]
        if atr <= 0.0:
            dx_vals.append(0.0)
            dx_index.append(i)
            continue
        plus_di = 100.0 * sp / atr
        minus_di = 100.0 * sm / atr
        denom = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / denom if denom > 0.0 else 0.0
        dx_vals.append(dx)
        dx_index.append(i)
    if len(dx_vals) < n:
        return out
    adx = sum(dx_vals[:n]) / n
    out[dx_index[n - 1]] = adx
    for j in range(n, len(dx_vals)):
        adx = (adx * (n - 1) + dx_vals[j]) / n
        out[dx_index[j]] = adx
    return out


def momentum_series(closes: list[float], n: int = 20) -> Series:
    """Fractional return ``(close - close[-n]) / close[-n]``; ``None`` for warmup."""
    out: Series = [None] * len(closes)
    for i in range(n, len(closes)):
        base = closes[i - n]
        out[i] = (closes[i] - base) / base if base > 0.0 else 0.0
    return out


# ── payload builders ────────────────────────────────────────────────────────


def _indicators(
    highs: list[float], lows: list[float], closes: list[float],
    volumes: list[float],
) -> dict[str, Series]:
    macd, macd_sig, macd_hist = macd_series(closes)
    bb_up, bb_mid, bb_lo = bollinger_series(closes)
    stoch_k, stoch_d = stochastic_series(highs, lows, closes)
    return {
        "ema20": ema_series(closes, 20),
        "ema50": ema_series(closes, 50),
        "bb_upper": bb_up,
        "bb_mid": bb_mid,
        "bb_lower": bb_lo,
        "vwap": vwap_series(highs, lows, closes, volumes),
        "rsi": rsi_series(closes),
        "macd": macd,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "atr": atr_series(highs, lows, closes),
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "adx": adx_series(highs, lows, closes),
        "momentum": momentum_series(closes),
    }


def build_ticker_chart(
    conn: sqlite3.Connection, *, venue: str, symbol: str, resolution: str,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """OHLCV bars + technical series for one ticker/resolution (read-only).

    Returns ``{available, symbol, venue, resolution, native_interval, bars,
    indicators}``. ``available`` is ``False`` (with empty ``bars``/``indicators``)
    for an unknown resolution or a symbol with no stored bars — never raises.

    Roadmap #12 hook: when a precomputed technical store exists, swap the
    ``_indicators(...)`` call below for a store read keyed by
    ``(venue, symbol, native_interval)``; the on-demand compute remains the
    fallback for symbols the store has not materialised yet.

    ``conn`` — the marketdata-domain conn (storage-split, 2026-07-14): ``bars``
    lives there, not the trading DB.
    """
    native = resolution_to_interval(resolution)
    empty = {
        "available": False, "symbol": symbol, "venue": venue,
        "resolution": resolution, "native_interval": native,
        "bars": [], "indicators": {},
    }
    if native is None:
        return empty
    bars = read_recent_bars(
        conn, venue=venue, symbol=symbol, bar_interval=native, limit=limit
    )
    if not bars:
        return empty
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    closes = [float(b.close) for b in bars]
    volumes = [float(b.volume) for b in bars]
    bar_rows = [
        {
            "t": int(b.ts), "o": float(b.open), "h": float(b.high),
            "l": float(b.low), "c": float(b.close), "v": float(b.volume),
        }
        for b in bars
    ]
    return {
        "available": True, "symbol": symbol, "venue": venue,
        "resolution": resolution, "native_interval": native,
        "bars": bar_rows,
        "indicators": _indicators(highs, lows, closes, volumes),
    }


# Native bar_interval → frontend resolution token, for the selector list.
_INTERVAL_TO_RESOLUTION = {v: k for k, v in _RESOLUTION_TO_INTERVAL.items()}
# Stable resolution ordering for the dropdown.
_RESOLUTION_ORDER = list(_RESOLUTION_TO_INTERVAL.keys())


def _universe_names(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """(venue, symbol) → universe.name (human-readable). Read-only, graceful.

    Empty dict when the column/table is absent (legacy DB) — the selector then
    just shows the bare symbol. Display-only; never feeds gating/sizing/exit.
    """
    try:
        rows = conn.execute(
            "SELECT venue, symbol, name FROM universe WHERE name != ''"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {(str(r[0]), str(r[1])): str(r[2]) for r in rows}


def list_chart_symbols(
    conn: sqlite3.Connection, *, names_conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Distinct (venue, symbol) with the resolutions each actually has stored.

    Drives the Chart-tab selector: venue-grouped symbols, each tagged with the
    resolution tokens (``1m``/``5m``/…) for which bars exist + the human-readable
    ``name`` from ``universe`` (when known; '' otherwise). Read-only.

    ``conn`` — the marketdata-domain conn (storage-split, 2026-07-14): ``bars``
    lives there. ``names_conn`` — the trading conn ``universe`` still lives on;
    falls back to ``conn`` (byte-identical for legacy single-conn callers).
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT venue, symbol, bar_interval FROM bars"
        ).fetchall()
    except sqlite3.Error:
        return []
    names = _universe_names(names_conn if names_conn is not None else conn)
    by_venue: dict[str, dict[str, set[str]]] = {}
    for venue, symbol, interval in rows:
        token = _INTERVAL_TO_RESOLUTION.get(str(interval))
        if token is None:
            continue
        by_venue.setdefault(str(venue), {}).setdefault(str(symbol), set()).add(token)
    groups: list[dict[str, Any]] = []
    for venue in sorted(by_venue):
        symbols = [
            {
                "symbol": sym,
                "name": names.get((venue, sym), ""),
                "resolutions": [r for r in _RESOLUTION_ORDER if r in res],
            }
            for sym, res in sorted(by_venue[venue].items())
        ]
        groups.append({"venue": venue, "symbols": symbols})
    return groups
