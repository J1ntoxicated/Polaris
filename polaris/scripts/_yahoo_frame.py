"""Yahoo OHLCV DataFrame → canonical ``Bar`` converter (pure, no yfinance import).

Split out of ``_yahoo_bars`` to keep that module ≤500 LOC after the yfinance
import guard was added (incident 2026-06-24). Pure: depends only on the canonical
``Bar`` schema and never imports yfinance, so it loads even when yfinance is
absent. ``_yahoo_bars`` re-exports these names so existing import paths keep
working. The stored Bar stays VENUE-NATIVE; only ``source`` flips to ``"yahoo"``.
"""

from __future__ import annotations

from typing import Any

from polaris.core.data.schema import Bar


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _yahoo_df_to_bars(
    df: Any,
    *,
    venue: str,
    symbol: str,
    bar_interval: str,
    underlying_group_id: str,
) -> list[Bar]:
    """Convert a yfinance OHLCV DataFrame to canonical Bars (newest last).

    The index is a tz-AWARE DatetimeIndex in the instrument's LOCAL tz;
    ``Timestamp.timestamp()`` yields the correct UTC epoch regardless of display
    tz. Rows with a bad ts or non-positive open/close are dropped (mirrors
    ``_alpaca_bar_to_canonical``) so one malformed candle never aborts the batch.
    The stored Bar is VENUE-NATIVE: ``venue`` / ``symbol`` / ``underlying_group_id``
    are the exchange identity; only ``source`` flips to ``"yahoo"``.
    """
    instrument_id = f"{venue}:{symbol}"
    out: list[Bar] = []
    for idx_ts, row in zip(df.index, df.itertuples(index=False), strict=False):
        try:
            ts = int(idx_ts.timestamp())
        except (AttributeError, ValueError, OverflowError):
            continue
        if ts <= 0:
            continue
        o = _to_float(getattr(row, "Open", None))
        h = _to_float(getattr(row, "High", None))
        low = _to_float(getattr(row, "Low", None))
        c = _to_float(getattr(row, "Close", None))
        if o <= 0.0 or c <= 0.0:
            continue
        vol = _to_float(getattr(row, "Volume", None))
        out.append(
            Bar(
                instrument_id=instrument_id,
                underlying_group_id=underlying_group_id,
                venue=venue,
                symbol=symbol,
                bar_interval=bar_interval,
                ts=ts,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=vol,
                notional_usd=c * vol if vol > 0 else 0.0,
                trade_count=0,
                vwap=0.0,
                bid_close=0.0,
                ask_close=0.0,
                spread_bps_close=0.0,
                source="yahoo",
            )
        )
    # yfinance is already ascending; sort to be safe (canonical newest-last).
    out.sort(key=lambda b: b.ts)
    return out
