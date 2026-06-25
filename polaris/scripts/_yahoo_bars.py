"""Yahoo Finance (yfinance) PRIMARY bar-history source — root 429 fix.

Root cause (Alpaca 429 storm, 2026-06-24): the exchange bar-history fetch
re-pulled the full window for every focus symbol every cadence-due tick (~99
Alpaca equity ``/bars`` per 5s → free-tier 429 storm → tick-engine STALL). Yahoo
Finance is free/unlimited-grade, so it becomes the PRIMARY source of bar HISTORY
(indicators / regime / baseline). The exchange bar-fetch is demoted to a
throttled FALLBACK only (see ``should_fetch_exchange_fallback``).

ABSOLUTE: this module changes ONLY the bar-HISTORY source. The live entry/exit
price still flows via the exchange WebSocket quote path (the quote-writer + venue
socket clients + the live-quote table) — UNTOUCHED and NOT imported here. Yahoo
never supplies a trading price; it supplies closed candles for the canvas.

Identity stays VENUE-NATIVE: the stored ``Bar`` keeps ``instrument_id ==
venue:symbol`` with the exchange-native symbol and the exchange
``underlying_group_id``. The Yahoo ticker (``BTC-USD`` / ``EURUSD=X`` / ``^N225``
/ ``GC=F`` / ``0700.HK`` / ``AAPL``) is ONLY an internal fetch detail and never
leaks into the stored Bar — the sole field that flips is ``source="yahoo"``.

DEMO/PAPER only. AGGRESSIVE bias preserved — flow_not_block: the only throttle in
this module is the fallback-fetch cooldown, which spaces out redundant
bar-HISTORY re-fetches for the genuinely-unmapped tail so they can't recreate a
REST storm; no entry / size / exit decision is ever gated.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.data.schema import Bar
from polaris.core.pipeline.agents._gpt_client import (
    GPT_P0_MODEL,
    call_gpt,
    make_system_prefix,
)
from polaris.scripts._yahoo_frame import _yahoo_df_to_bars  # re-export (≤500 LOC split)

logger = logging.getLogger(__name__)

# Degrade-never-crash import guard (incident 2026-06-24): an absent yfinance must
# NOT raise ModuleNotFoundError at import (that crashed the whole ignite_p1 chain).
# Absent → ``fetch_yahoo_bars`` short-circuits to [] → exchange fallback (flow_not_block).
try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via the import-block test
    yf = None
    _YF_AVAILABLE = False
    logger.warning("[L1/yahoo] yfinance unavailable — Yahoo bars off, exchange fallback")

# Canonical bar_interval → yfinance interval token. ``1h`` (not ``60m``) is the
# yfinance hourly token. Unmapped canonical interval → fetch returns [].
_YF_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1H": "1h",
    "1D": "1d",
}

# Canonical bar_interval → yfinance period (history window). yfinance hard
# limits: 1m ≤ 7d, intraday ≤ 60d. 2y daily covers the equity MA200 warmup.
_YF_PERIOD: dict[str, str] = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "1H": "60d",
    "1D": "2y",
}

# Capital index epic → Yahoo index ticker. Unmapped → None (exchange fallback).
_CAPITAL_INDEX_YF: dict[str, str] = {
    "US100": "^NDX",
    "US500": "^GSPC",
    "US30": "^DJI",
    "RTY": "^RUT",
    "J225": "^N225",
    "UK100": "^FTSE",
    "DE40": "^GDAXI",
    "FR40": "^FCHI",
    "EU50": "^STOXX50E",
    "HK50": "^HSI",
    "AU200AU": "^AXJO",
    "VIX": "^VIX",
    "IT40": "FTSEMIB.MI",
    "NL25": "^AEX",
    "SP35": "^IBEX",
    "SW20": "^SSMI",
}

# Capital commodity epic → Yahoo futures ticker. Unmapped → None (fallback).
_CAPITAL_COMMODITY_YF: dict[str, str] = {
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "OIL_CRUDE": "CL=F",
    "OIL_BRENT": "BZ=F",
    "NATURALGAS": "NG=F",
    "COPPER": "HG=F",
    "PLATINUM": "PL=F",
    "PALLADIUM": "PA=F",
    "CORN": "ZC=F",
    "WHEAT": "ZW=F",
    "SOYBEAN": "ZS=F",
    "SOYBEANOIL": "ZL=F",
    "SOYBEANMEAL": "ZM=F",
    "OATS": "ZO=F",
    "COFFEEARABICA": "KC=F",
    "GASOLINE": "RB=F",
    "HEATINGOIL": "HO=F",
    "LEANHOGS": "HE=F",
    "LIVECATTLE": "LE=F",
    "FEEDERCATTLE": "GF=F",
    # LUMBER intentionally omitted — LBS=F / LB=F return EMPTY on Yahoo in this
    # yfinance build, so it would waste one round-trip before falling back.
    # Unmapped → None → exchange fallback (throttled). Correct.
}

# OKX crypto bases where Yahoo ``{base}-USD`` is the IDENTITY-VERIFIED canonical
# coin (live ``.info`` name match). The bare ``{base}-USD`` pattern is UNSAFE for
# the long tail — many OKX bases collide with an unrelated Yahoo coin of the same
# ticker (ARB→"ARbit", UNI→"UNICORN Token", GAS→"Gas", APT→"Apricot Finance",
# SUI→"Salmonation", ...) which would SILENTLY persist the WRONG coin's OHLC and
# corrupt indicators/regime/baseline with no error. A base outside this set
# returns None → GPT disambiguation (which can resolve e.g. real Sui =
# SUI20947-USD) → exchange fallback. flow_not_block; no trade gated (live price
# is on the WS quote path, untouched).
_OKX_YAHOO_VERIFIED_BASES: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "BCH", "LINK", "AVAX",
    "DOT", "TRX", "ATOM", "XLM", "ETC", "FIL", "NEAR", "OP", "INJ", "AAVE",
    "ALGO", "ICP", "HBAR", "SHIB", "SAND", "MANA", "AXS", "CRV", "LDO",
    "RENDER", "TIA", "SEI", "BNB",
})

_FOREX_CLEAN6 = re.compile(r"^[A-Z]{6}$")
_HK_NUMERIC = re.compile(r"^\d{1,5}$")
# A plausible Yahoo ticker from the GPT fallback: alnum + the symbol chars Yahoo
# uses (``^`` index prefix, ``=`` for FX/futures, ``-``/``.`` suffixes). No
# whitespace. Bounds the trust we place in a free-form GPT string.
_YF_TICKER_OK = re.compile(r"^[A-Za-z0-9^=.\-]{1,15}$")

# Resolution cache. Key ``(venue, symbol)`` → Yahoo ticker or None. A cached
# value (INCLUDING None) is returned immediately → a known-unmapped symbol never
# re-asks GPT and never re-runs the deterministic map (negative caching). Single
# per-process loop, so a plain module dict is safe.
_YAHOO_TICKER_CACHE: dict[tuple[str, str], str | None] = {}


def map_to_yahoo_ticker(venue: str, symbol: str, asset_class: str) -> str | None:
    """Deterministic (venue, symbol, asset_class) → Yahoo ticker, or None.

    PURE. ~95% coverage of the live universe. None means "no confident
    deterministic Yahoo series" → the resolver falls through to a single GPT
    lookup, and if that also fails the exchange path is the fallback. Returning
    None is never a trading block (flow_not_block) — only a source choice.
    """
    cls = asset_class.lower()
    sym = symbol.upper()

    if venue == "alpaca":
        # US equity symbol is Yahoo-native (AAPL → AAPL). 100% coverage.
        return symbol

    if venue == "okx":
        # Crypto. ONLY a USD-equiv quote on an IDENTITY-VERIFIED base maps to the
        # Yahoo ``BASE-USD`` series. A non-USD quote → a different instrument; an
        # unverified base → a Yahoo ticker collision (wrong coin). Both → None →
        # GPT disambiguation → exchange fallback (never the WRONG coin's OHLC).
        if "-" in sym:
            base, _, quote = sym.partition("-")
        elif sym.endswith(("USDT", "USDC")):
            base, quote = sym[:-4], sym[-4:]
        elif sym.endswith("USD"):
            base, quote = sym[:-3], "USD"
        else:
            return None
        if not base:
            return None
        if quote in ("USDT", "USDC", "USD") and base in _OKX_YAHOO_VERIFIED_BASES:
            return f"{base}-USD"
        return None

    if venue == "capital":
        if cls in ("forex", "fx"):
            # A CLEAN 6-letter alpha pair → ``PAIR=X``. Suffixed / exotic
            # (EURUSD_W, AUDUSD_ZERO, anything not exactly 6 alpha) → None.
            if _FOREX_CLEAN6.match(sym):
                return f"{sym}=X"
            return None
        if cls in ("index", "indices"):
            return _CAPITAL_INDEX_YF.get(sym)
        if cls in ("commodity", "commodities"):
            return _CAPITAL_COMMODITY_YF.get(sym)
        if cls == "equity":
            # Numeric HK epic → zero-pad to 4 digits + ``.HK`` (0700 → 0700.HK,
            # 5 → 0005.HK). Non-numeric → None (exchange fallback).
            if _HK_NUMERIC.match(sym):
                return f"{int(sym):04d}.HK"
            return None
        return None

    return None


def _validate_yf_ticker(candidate: Any) -> str | None:
    """Validate a GPT-proposed Yahoo ticker (non-empty, no whitespace, charset)."""
    if not isinstance(candidate, str):
        return None
    t = candidate.strip()
    if not t or any(ch.isspace() for ch in t):
        return None
    if not _YF_TICKER_OK.match(t):
        return None
    return t


async def _gpt_resolve_yahoo_ticker(
    venue: str, symbol: str, asset_class: str, *, gpt_client_factory: Any
) -> str | None:
    """One GPT lookup for an unmapped instrument → Yahoo ticker, or None.

    Keyless-safe: ``gpt_client_factory`` is the production ``default_gpt_factory``
    which RAISES RuntimeError when ``OPENAI_API_KEY`` is missing — we wrap the
    call so a keyless host just yields None (exchange fallback), never a crash.
    The factory result may be a client OR an awaitable producing one (the test
    spy is async); both are handled.
    """
    try:
        produced = gpt_client_factory(GPT_P0_MODEL)
    except RuntimeError:
        return None
    if asyncio.iscoroutine(produced) or asyncio.isfuture(produced):
        try:
            client = await produced
        except RuntimeError:
            return None
    else:
        client = produced

    system_prefix = make_system_prefix(
        role=(
            "You map a trading instrument to its Yahoo Finance ticker symbol. "
            "Return ONLY the exact Yahoo Finance ticker string."
        ),
        decision_enum=["yahoo_ticker"],
        cell_summary="",
        baseline_summary="",
        recent_trades_summary="",
    )
    user_prompt = (
        f"Instrument: venue={venue}, asset_class={asset_class}, symbol={symbol}. "
        'Return JSON {"yahoo_ticker": "<exact Yahoo Finance ticker>"} only. '
        "If you are not confident, return {\"yahoo_ticker\": \"\"}."
    )
    result = await call_gpt(
        client=client,
        system_prefix=system_prefix,
        user_prompt=user_prompt,
        max_tokens=40,
        model=GPT_P0_MODEL,
    )
    if result.error or not result.parsed:
        return None
    return _validate_yf_ticker(result.parsed.get("yahoo_ticker"))


async def resolve_yahoo_ticker(
    venue: str, symbol: str, asset_class: str, *, gpt_client_factory: Any = None
) -> str | None:
    """Resolve (venue, symbol) → Yahoo ticker, cached (incl. negative).

    1. Cache hit (incl. a cached ``None``) → return immediately. A known-unmapped
       symbol never re-asks GPT and never re-runs the map (re-call cost = 0).
    2. Deterministic ``map_to_yahoo_ticker`` → cache + return on success.
    3. Still None AND a ``gpt_client_factory`` given → ONE GPT lookup; cache the
       resolved ticker, or cache ``None`` (negative) on keyless / error / invalid.
    """
    key = (venue, symbol)
    if key in _YAHOO_TICKER_CACHE:
        return _YAHOO_TICKER_CACHE[key]

    ticker = map_to_yahoo_ticker(venue, symbol, asset_class)
    if ticker is not None:
        _YAHOO_TICKER_CACHE[key] = ticker
        return ticker

    if gpt_client_factory is not None:
        ticker = await _gpt_resolve_yahoo_ticker(
            venue, symbol, asset_class, gpt_client_factory=gpt_client_factory,
        )

    # Cache the outcome — including None (negative cache, never re-asks).
    _YAHOO_TICKER_CACHE[key] = ticker
    return ticker


def _yf_history_blocking(ticker: str, interval: str, period: str) -> Any:
    """SYNCHRONOUS yfinance history fetch (requests-based; releases the GIL on I/O).

    Called only via ``asyncio.to_thread`` so it never blocks the event loop (that
    block IS the STALL this work removes). Tests monkeypatch this symbol so they
    never hit the network.
    """
    return yf.Ticker(ticker).history(
        period=period, interval=interval, auto_adjust=False,
    )


# Within-period frame cache (FETCH-EFFICIENCY, flow_not_block). The OKX 1m
# strategy (volume_burst) keeps OKX OUT of the upstream ``skip_if_current`` set,
# so on every 5s tick each OKX (and any non-skip-bucket) focus symbol would
# otherwise re-pull the FULL Yahoo window (1m→7d ≈ thousands of rows) — tens of
# symbols × 12 ticks/min = hundreds–1000 Yahoo requests/min → risk of a Yahoo
# IP-block (a WORSE all-venue outage than the Alpaca 429 storm). This cache caps
# Yahoo to ~once per bar-period per symbol regardless of the 5s cadence: a call
# within the same bar period returns the cached bars (no network); a period roll
# triggers a real refetch. Empty/[] results are cached too so an unmapped/empty
# symbol does not re-hit Yahoo every 5s — and an empty cached result still lets
# the EXCHANGE FALLBACK run in ``fetch_bars_one`` (its own 300s cooldown applies).
# NEVER gates a trading decision; live price flows on WS untouched.
_YF_PERIOD_SECONDS: dict[str, int] = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600}
# 1D has no clean UTC-midnight boundary for the in-progress daily bar (Yahoo
# updates the daily candle intraday), so it caches on an hourly TTL bucket.
_YF_1D_TTL_SEC = 3600
# Key (venue, symbol, bar_interval) → (period_bucket, bars). Single per-process
# loop, so a plain module dict is safe. Cleared per-test via conftest.
_YF_FRAME_CACHE: dict[tuple[str, str, str], tuple[int, list[Bar]]] = {}


def _period_open(bar_interval: str, now: int) -> int:
    """Bucket id for the current bar period of ``bar_interval`` at wall-clock ``now``.

    PURE. Floors ``now`` to the interval period (1m/5m/15m/1H); ``1D`` floors to an
    hourly TTL bucket (no clean UTC-midnight boundary for the in-progress daily
    bar). An unmapped interval falls back to the hourly bucket (conservative —
    caches for at most an hour). Self-contained to avoid a circular import with
    ``_production_bars`` (which imports this module).
    """
    period = _YF_PERIOD_SECONDS.get(bar_interval, _YF_1D_TTL_SEC)
    if period <= 0:
        period = _YF_1D_TTL_SEC
    return (now // period) * period


async def fetch_yahoo_bars(
    venue: str,
    symbol: str,
    asset_class: str,
    *,
    bar_interval: str = "1m",
    limit: int = 240,
    gpt_client_factory: Any = None,
) -> list[Bar]:
    """Fetch canonical bar HISTORY from Yahoo Finance (newest last), or [].

    Returns ``[]`` when the interval is unsupported, the symbol does not resolve
    to a Yahoo ticker, yfinance errors, or the frame is empty — in every such
    case the caller falls back to the exchange path (flow_not_block). yfinance is
    synchronous, so the blocking history fetch runs in a worker thread.

    Within-period frame cache (FETCH-EFFICIENCY): a call within the same bar
    period returns the cached bars without a network fetch (caps Yahoo to ~once
    per bar-period per symbol regardless of the 5s tick). The cache stores the
    full bar list; the per-call ``limit`` slice is applied on return so a varying
    ``limit`` within the period composes. Empty results are cached too.
    """
    if not _YF_AVAILABLE:
        return []  # yfinance absent → exchange fallback (incident 2026-06-24 guard)
    interval = _YF_INTERVAL.get(bar_interval)
    if interval is None:
        return []
    period = _YF_PERIOD.get(bar_interval, "60d")

    # Within-period frame cache — serve a recent fetch without re-hitting Yahoo.
    now = int(time.time())
    bucket = _period_open(bar_interval, now)
    cache_key = (venue, symbol, bar_interval)
    cached = _YF_FRAME_CACHE.get(cache_key)
    if cached is not None and cached[0] == bucket:
        return cached[1][-limit:]

    ticker = await resolve_yahoo_ticker(
        venue, symbol, asset_class, gpt_client_factory=gpt_client_factory,
    )
    if ticker is None:
        # Cache the empty result so an unmapped symbol does not re-resolve every
        # 5s within the period; the exchange fallback (own 300s cooldown) covers it.
        _YF_FRAME_CACHE[cache_key] = (bucket, [])
        return []

    try:
        df = await asyncio.to_thread(_yf_history_blocking, ticker, interval, period)
    except Exception as exc:  # noqa: BLE001 — yfinance raises many types incl. network
        logger.debug(
            "[L1/yahoo] %s (%s) fetch failed: %r — exchange fallback", symbol, ticker, exc,
        )
        # Cache the empty result for the period so a hard-erroring ticker does not
        # re-hammer Yahoo every 5s; refetch occurs when the period rolls.
        _YF_FRAME_CACHE[cache_key] = (bucket, [])
        return []

    if df is None or getattr(df, "empty", True):
        _YF_FRAME_CACHE[cache_key] = (bucket, [])
        return []

    underlying = compute_underlying_group_id(venue, symbol, asset_class)
    bars = _yahoo_df_to_bars(
        df, venue=venue, symbol=symbol, bar_interval=bar_interval,
        underlying_group_id=underlying,
    )
    _YF_FRAME_CACHE[cache_key] = (bucket, bars)
    return bars[-limit:]


# Exchange-fallback re-fetch cooldown (FETCH-EFFICIENCY throttle, NOT a trading
# block). For the genuinely-unmapped tail (Yahoo returned [] AND no GPT ticker),
# the exchange REST path is the only bar source. Without spacing, those symbols
# would re-hit the exchange every cadence-due tick and could recreate the very
# REST storm this work kills. ``should_fetch_exchange_fallback`` spaces those
# redundant bar-HISTORY re-fetches per (venue, symbol). The live entry/exit price
# still flows via the WS quote path — UNTOUCHED — so no entry / size / exit is
# ever gated (flow_not_block). A never-fetched symbol always fetches immediately.
FALLBACK_COOLDOWN_SEC = 300.0
_FALLBACK_LAST_MONO: dict[tuple[str, str], float] = {}


def should_fetch_exchange_fallback(venue: str, symbol: str, now_mono: float) -> bool:
    """True if the exchange bar fallback may re-fetch this symbol now.

    True (and records the time) when never-fetched or the cooldown has elapsed;
    False inside the cooldown window. FETCH-EFFICIENCY only — spaces out redundant
    bar-HISTORY re-fetches for the unmapped tail; never gates a trading decision.
    """
    key = (venue, symbol)
    last = _FALLBACK_LAST_MONO.get(key)
    if last is None or (now_mono - last) >= FALLBACK_COOLDOWN_SEC:
        _FALLBACK_LAST_MONO[key] = now_mono
        return True
    return False
