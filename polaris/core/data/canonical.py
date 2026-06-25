"""Canonical market model — venue payload → unified `Bar` / `QuoteTick`.

Pure functions only (P6). No I/O. Network adapters live in `polaris/venues/*`.

Spec source: vault/30_components/layer-1-canonical-baseline.md (Q1 + Q4).
"""

from __future__ import annotations

from typing import Any

from polaris.core.data.schema import (
    BAR_INTERVALS,
    Bar,
    QuoteTick,
)

# ---------------------------------------------------------------------------
# Underlying group id — cross-venue normalization
# ---------------------------------------------------------------------------


def compute_underlying_group_id(venue: str, symbol: str, asset_class: str) -> str:
    """Map (venue, symbol) → cross-venue `underlying_group_id`.

    Examples
    --------
    >>> compute_underlying_group_id("okx", "BTC-USDT", "crypto")
    'crypto:BTC'
    >>> compute_underlying_group_id("capital", "BTCUSD", "crypto")
    'crypto:BTC'
    >>> compute_underlying_group_id("capital", "EURUSD", "forex")
    'forex:EURUSD'
    """
    sym = symbol.upper()
    cls = asset_class.lower()

    if cls == "crypto":
        # OKX `BTC-USDT` → BTC; Capital `BTCUSD` → BTC.
        if "-" in sym:
            base = sym.split("-", 1)[0]
        elif sym.endswith(("USDT", "USDC")):
            base = sym[:-4]
        elif sym.endswith("USD"):
            base = sym[:-3]
        else:
            base = sym
        return f"crypto:{base}"

    if cls in ("forex", "fx"):
        return f"forex:{sym}"

    if cls in ("indices", "index"):
        return f"index:{sym}"

    if cls in ("commodity", "commodities"):
        return f"commodity:{sym}"

    if cls == "equity":
        # Stream C (Alpaca) equities. Explicit branch makes the prior
        # generic-fallback output (`equity:{sym}`) first-class; behavior
        # unchanged for crypto/forex/index/commodity.
        return f"equity:{sym}"

    return f"{cls}:{sym}"


# ---------------------------------------------------------------------------
# OKX → canonical
# ---------------------------------------------------------------------------


def okx_ticker_to_quote_tick(payload: dict[str, Any], *, ts: int) -> QuoteTick:
    """Convert OKX `/api/v5/market/tickers` row to canonical QuoteTick.

    OKX fields used: ``instId``, ``bidPx``, ``askPx``, ``last``, ``bidSz``,
    ``askSz``. All numeric strings (per OKX docs) → cast via ``float``.
    """
    inst_id = str(payload["instId"])  # e.g. "BTC-USDT"
    bid = _to_float(payload.get("bidPx"))
    ask = _to_float(payload.get("askPx"))
    if bid <= 0.0 or ask <= 0.0:
        raise ValueError(f"OKX ticker {inst_id}: non-positive bid/ask ({bid}/{ask})")
    mid = 0.5 * (bid + ask)
    spread_bps = ((ask - bid) / mid) * 10_000.0

    return QuoteTick(
        instrument_id=f"okx:{inst_id}",
        venue="okx",
        symbol=inst_id,
        ts=ts,
        bid=bid,
        ask=ask,
        mid=mid,
        spread_bps=spread_bps,
        bid_size=_to_float(payload.get("bidSz")),
        ask_size=_to_float(payload.get("askSz")),
        last_trade_price=_to_float(payload.get("last")),
        last_trade_size=_to_float(payload.get("lastSz")),
        source="okx_rest",
    )


def okx_candle_to_bar(
    candle: list[Any],
    *,
    inst_id: str,
    bar_interval: str,
    underlying_group_id: str,
) -> Bar:
    """Convert one OKX `/api/v5/market/candles` row to canonical Bar.

    OKX candle row layout: ``[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]``.
    """
    if bar_interval not in BAR_INTERVALS:
        raise ValueError(f"unsupported bar_interval={bar_interval!r}")
    if len(candle) < 7:
        raise ValueError(f"OKX candle row too short: {candle!r}")

    ts = int(candle[0]) // 1000  # OKX returns ms epoch; canonicalize to seconds
    o = _to_float(candle[1])
    h = _to_float(candle[2])
    low = _to_float(candle[3])
    c = _to_float(candle[4])
    volume = _to_float(candle[5])
    notional = _to_float(candle[7]) if len(candle) > 7 else 0.0
    return Bar(
        instrument_id=f"okx:{inst_id}",
        underlying_group_id=underlying_group_id,
        venue="okx",
        symbol=inst_id,
        bar_interval=bar_interval,
        ts=ts,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=volume,
        notional_usd=notional,
        trade_count=0,
        vwap=0.0,
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="okx_rest",
    )


# ---------------------------------------------------------------------------
# Capital → canonical
# ---------------------------------------------------------------------------


def capital_market_to_quote_tick(payload: dict[str, Any], *, ts: int) -> QuoteTick:
    """Convert Capital ``/markets`` row to canonical QuoteTick.

    Capital fields: ``epic`` (market id), ``bid``, ``offer``, ``high``, ``low``.
    """
    epic = str(payload["epic"])
    bid = _to_float(payload.get("bid"))
    ask = _to_float(payload.get("offer"))
    if bid <= 0.0 or ask <= 0.0:
        raise ValueError(f"Capital market {epic}: non-positive bid/ask ({bid}/{ask})")
    mid = 0.5 * (bid + ask)
    spread_bps = ((ask - bid) / mid) * 10_000.0

    return QuoteTick(
        instrument_id=f"capital:{epic}",
        venue="capital",
        symbol=epic,
        ts=ts,
        bid=bid,
        ask=ask,
        mid=mid,
        spread_bps=spread_bps,
        bid_size=0.0,
        ask_size=0.0,
        last_trade_price=mid,
        last_trade_size=0.0,
        source="capital_rest",
    )


# ---------------------------------------------------------------------------
# Alpaca → canonical
# ---------------------------------------------------------------------------


def alpaca_quote_to_quote_tick(payload: dict[str, Any], *, ts: int) -> QuoteTick:
    """Convert an Alpaca WS quote message (``T="q"``) to canonical QuoteTick.

    Alpaca v2 quote fields: ``S`` (symbol), ``bp`` (bid price), ``ap`` (ask
    price), ``bs`` (bid size), ``as`` (ask size). ``ts`` is the canonical epoch
    seconds supplied by the caller (we keep venue ``t`` parsing out of this pure
    converter; the WS client passes ``int(time.time())``).
    """
    sym = str(payload["S"])  # e.g. "AAPL"
    bid = _to_float(payload.get("bp"))
    ask = _to_float(payload.get("ap"))
    if bid <= 0.0 or ask <= 0.0:
        raise ValueError(f"Alpaca quote {sym}: non-positive bid/ask ({bid}/{ask})")
    mid = 0.5 * (bid + ask)
    spread_bps = ((ask - bid) / mid) * 10_000.0

    return QuoteTick(
        instrument_id=f"alpaca:{sym}",
        venue="alpaca",
        symbol=sym,
        ts=ts,
        bid=bid,
        ask=ask,
        mid=mid,
        spread_bps=spread_bps,
        bid_size=_to_float(payload.get("bs")),
        ask_size=_to_float(payload.get("as")),
        last_trade_price=mid,
        last_trade_size=0.0,
        source="alpaca_ws",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float:
    """Best-effort cast to float; empty / None → 0.0."""
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
