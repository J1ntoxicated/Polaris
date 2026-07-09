"""P0a DEFAULT_INSTRUMENTS — OFFLINE instrument pools per strategy (pure data).

Extracted from :mod:`polaris.scripts.run_p0a_spike` (pure move — behavior-0,
mirrors the ``_p0a_verdict`` split) so ``run_p0a_spike`` stays under the
500-LOC file cap. Re-imported / re-exported from ``run_p0a_spike`` so the CLI
and existing callers keep working unchanged.

2026-07-10 EARN-family reactivation (BG1-p0a-search-activation): added the 8
currently-registered EARN-family strategies (breakout Donchian gold/silver/
us100/uk100 + connors_rsi2/cci_reversion/index_52w_high_momentum/
macd_ema_trend_pullback) so the honest-N DSR re-run covers the LIVE search
space, not just the 2026-06-01 original 5. Pools are each strategy's own
design ``SUPPORTED_SYMBOLS`` set (or a representative liquid subset of it)
intersected with what the live DB actually has bars for — no cherry-picking
for a target N (that would be dishonest search-breadth theater).
"""

from __future__ import annotations

# strategy_id -> (instrument_ids, bar_interval)
DEFAULT_INSTRUMENTS: dict[str, tuple[tuple[str, ...], str]] = {
    # --- Track A: OKX SPOT (live in replay) ---
    "volume_burst": (
        ("okx:BTC-USDT", "okx:ALGO-USDT", "okx:INJ-USDT", "okx:NEAR-USDT", "okx:ETH-USDT"),
        "1m",
    ),
    "rsi_bb_pullback": (
        ("okx:BTC-USDT", "okx:ETH-USDT", "okx:ALGO-USDT", "okx:INJ-USDT", "okx:ADA-USDT"),
        "1H",
    ),
    "spot_donchian": (
        ("okx:BTC-USDT", "okx:FLOKI-USDT", "okx:HYPE-USDT", "okx:ALGO-USDT",
         "okx:ETH-USDT", "okx:INJ-USDT", "okx:ADA-USDT"),
        "1H",
    ),
    "fx_breakout_basket": (("capital:EURUSD_W",), "1H"),
    "xau_indices_trend": (("capital:US100",), "1H"),
    # --- EARN family (2026-07-10 reactivation) ---
    # Pure Donchian-55 breakout, window LOCKED (no entry-set knob — see
    # param_bounds.py) -> the search lever is instrument breadth, one epic
    # each (per_ticker_tailored: each clone owns its own correlation group).
    "gold_breakout_1h": (("capital:GOLD",), "1H"),
    "silver_breakout_1h": (("capital:SILVER",), "1H"),
    "us100_breakout_1h": (("capital:US100",), "1H"),
    "uk100_breakout_1h": (("capital:UK100",), "1H"),
    # RSI(2)+SMA200 oversold dip — HAS an entry-set knob (rsi_entry, see
    # param_bounds.py) + a liquid equity pool for symbol breadth.
    "connors_rsi2": (
        ("alpaca:AAPL", "alpaca:MSFT", "alpaca:GOOGL", "alpaca:AMZN",
         "alpaca:META", "alpaca:JPM", "alpaca:ABBV", "alpaca:ABT"),
        "1D",
    ),
    # CCI(20) oversold reversion — the design's REAL commodity/index majors
    # pool (XAUUSD has no bars under that alias; GOLD is the live epic).
    "cci_reversion": (
        ("capital:GOLD", "capital:US500", "capital:US100", "capital:DE40",
         "capital:UK100", "capital:EU50", "capital:US30"),
        "1H",
    ),
    # 252-bar (52-week) high + ROC_60 momentum — the design's index majors.
    "index_52w_high_momentum": (
        ("capital:US500", "capital:US100", "capital:J225", "capital:HK50",
         "capital:US30", "capital:UK100", "capital:SP35", "capital:NL25",
         "capital:EU50", "capital:DE40"),
        "1D",
    ),
    # MACD(12/26/9) re-acceleration inside a 200-EMA uptrend — the design's
    # OKX-liquid crypto leg (the Alpaca ETF leg is venue-inert until routed,
    # per the module docstring).
    "macd_ema_trend_pullback": (
        ("okx:BTC-USDT", "okx:ETH-USDT", "okx:SOL-USDT", "okx:XRP-USDT",
         "okx:BNB-USDT", "okx:DOGE-USDT", "okx:ADA-USDT", "okx:TRX-USDT",
         "okx:AVAX-USDT", "okx:LINK-USDT"),
        "1D",
    ),
}

__all__ = ["DEFAULT_INSTRUMENTS"]
