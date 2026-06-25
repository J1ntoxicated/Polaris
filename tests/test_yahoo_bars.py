"""Yahoo Finance (yfinance) PRIMARY bar-history source — root 429 fix.

Root cause (Alpaca 429 storm, 2026-06-24): the exchange bar-history fetch
re-pulled the full window for every focus symbol every cadence tick (~99 Alpaca
equity /bars per 5s → free-tier 429 storm → tick-engine STALL). Yahoo Finance is
free/unlimited-grade, so it becomes the PRIMARY source of bar HISTORY (indicators
/regime/baseline). The exchange bar-fetch is demoted to a throttled FALLBACK only.

ABSOLUTE: this changes only the bar-HISTORY source. The live entry/exit price
still flows via the exchange WebSocket quote path (``quote_writer`` /
``venues/*/ws``) — UNTOUCHED. Yahoo never supplies a trading price.

The stored ``Bar`` stays VENUE-NATIVE: ``instrument_id = venue:symbol`` with the
exchange-native symbol; the Yahoo ticker is an internal fetch detail and never
leaks into the stored Bar (only ``Bar.source == "yahoo"`` flips).

DEMO/PAPER only. flow_not_block: the fallback cooldown spaces out redundant
re-fetches of the genuinely-unmapped tail (fetch-efficiency), never gating any
entry/size/exit decision.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pandas as pd
import pytest

import polaris.scripts._yahoo_bars as ybars
from polaris.scripts._yahoo_bars import (
    _YF_INTERVAL,
    _YF_PERIOD,
    _yahoo_df_to_bars,
    fetch_yahoo_bars,
    map_to_yahoo_ticker,
    resolve_yahoo_ticker,
    should_fetch_exchange_fallback,
)

# ---------------------------------------------------------------------------
# Fixtures — isolate the module-level caches between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_yahoo_caches() -> Iterator[None]:
    ybars._YAHOO_TICKER_CACHE.clear()
    ybars._FALLBACK_LAST_MONO.clear()
    ybars._YF_FRAME_CACHE.clear()
    yield
    ybars._YAHOO_TICKER_CACHE.clear()
    ybars._FALLBACK_LAST_MONO.clear()
    ybars._YF_FRAME_CACHE.clear()


# ---------------------------------------------------------------------------
# GPT spy double — factory() returns an awaitable producing a client whose
# ``messages.create`` returns a JSON-only response. ``calls`` counts factory
# invocations so a cached lookup can assert GPT was NOT re-asked.
# ---------------------------------------------------------------------------


class GPTCallSpy:
    """Async-factory test double for the Yahoo GPT-resolution fallback."""

    def __init__(self, response_text: str = '{"yahoo_ticker": "^STOXX50E"}') -> None:
        self.response_text = response_text
        self.factory_calls = 0
        self.create_calls: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                outer.create_calls.append(kwargs)

                class _Block:
                    text = outer.response_text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self._client = type("_Client", (), {"messages": _Messages()})()

    def factory(self, model: str | None = None) -> Any:
        self.factory_calls += 1

        async def _await_client() -> Any:
            return self._client

        return _await_client()


def _raising_factory(model: str | None = None) -> Any:
    """Keyless production factory — raises like ``default_gpt_factory`` (no key)."""
    raise RuntimeError("OPENAI_API_KEY missing from environment")


# ---------------------------------------------------------------------------
# 1. map_to_yahoo_ticker — deterministic, pure
# ---------------------------------------------------------------------------


def test_map_alpaca_equity_verbatim() -> None:
    assert map_to_yahoo_ticker("alpaca", "AAPL", "equity") == "AAPL"
    assert map_to_yahoo_ticker("alpaca", "MSFT", "equity") == "MSFT"


def test_map_okx_verified_base_usd_quote_to_base_usd() -> None:
    # Identity-VERIFIED bases only (Yahoo "{base}-USD" == the OKX coin).
    assert map_to_yahoo_ticker("okx", "BTC-USDT", "crypto") == "BTC-USD"
    assert map_to_yahoo_ticker("okx", "ETH-USDC", "crypto") == "ETH-USD"
    assert map_to_yahoo_ticker("okx", "SOL-USD", "crypto") == "SOL-USD"
    assert map_to_yahoo_ticker("okx", "AAVE-USDT", "crypto") == "AAVE-USD"


def test_map_okx_collision_base_to_none() -> None:
    """REGRESSION GUARD — the bare ``{base}-USD`` pattern is UNSAFE for the long
    tail: many OKX bases collide with an unrelated Yahoo coin of the same ticker
    (ARB→"ARbit", UNI→"UNICORN Token", GAS→"Gas", APT→"Apricot Finance",
    SUI→"Salmonation", ...) which would SILENTLY persist the WRONG coin's OHLC.
    A non-allow-listed base must return None → GPT disambiguation → exchange
    fallback. Pins the proven collisions so a future edit can't reintroduce them.
    """
    for base in ("ARB", "GAS", "UNI", "APT", "SUI", "GRT", "PEPE", "COMP",
                 "STX", "IMX", "HYPE", "TRUMP", "PI"):
        assert map_to_yahoo_ticker("okx", f"{base}-USDT", "crypto") is None, base


def test_map_okx_non_usd_quote_to_none() -> None:
    # base-USD of these is a DIFFERENT instrument — let exchange fallback handle.
    assert map_to_yahoo_ticker("okx", "AAVE-BTC", "crypto") is None
    assert map_to_yahoo_ticker("okx", "BAL-OKB", "crypto") is None
    assert map_to_yahoo_ticker("okx", "BETH-ETH", "crypto") is None


def test_map_capital_forex_clean6_to_x() -> None:
    assert map_to_yahoo_ticker("capital", "EURUSD", "forex") == "EURUSD=X"
    assert map_to_yahoo_ticker("capital", "USDJPY", "fx") == "USDJPY=X"


def test_map_capital_forex_suffixed_to_none() -> None:
    assert map_to_yahoo_ticker("capital", "EURUSD_W", "forex") is None
    assert map_to_yahoo_ticker("capital", "AUDUSD_ZERO", "forex") is None
    assert map_to_yahoo_ticker("capital", "EUR", "forex") is None  # not 6 alpha


def test_map_capital_index_table_and_unmapped() -> None:
    assert map_to_yahoo_ticker("capital", "US100", "index") == "^NDX"
    assert map_to_yahoo_ticker("capital", "US500", "indices") == "^GSPC"
    assert map_to_yahoo_ticker("capital", "J225", "index") == "^N225"
    assert map_to_yahoo_ticker("capital", "ZZZ999", "index") is None


def test_map_capital_commodity_table_and_unmapped() -> None:
    assert map_to_yahoo_ticker("capital", "GOLD", "commodity") == "GC=F"
    assert map_to_yahoo_ticker("capital", "OIL_CRUDE", "commodities") == "CL=F"
    assert map_to_yahoo_ticker("capital", "SILVER", "commodity") == "SI=F"
    assert map_to_yahoo_ticker("capital", "UNOBTANIUM", "commodity") is None
    # LBS=F / LB=F return EMPTY on Yahoo in this yfinance build — a dead map entry
    # wastes one round-trip before falling back. LUMBER → None → exchange fallback.
    assert map_to_yahoo_ticker("capital", "LUMBER", "commodity") is None


def test_map_capital_equity_hk_numeric_zero_pad() -> None:
    assert map_to_yahoo_ticker("capital", "0700", "equity") == "0700.HK"
    assert map_to_yahoo_ticker("capital", "5", "equity") == "0005.HK"
    assert map_to_yahoo_ticker("capital", "9988", "equity") == "9988.HK"


def test_map_capital_equity_non_numeric_to_none() -> None:
    assert map_to_yahoo_ticker("capital", "TSLA", "equity") is None


def test_map_unknown_venue_to_none() -> None:
    assert map_to_yahoo_ticker("kraken", "BTCUSD", "crypto") is None


# ---------------------------------------------------------------------------
# 2. resolve_yahoo_ticker — cache + GPT fallback + keyless safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_deterministic_hit_caches_no_gpt() -> None:
    spy = GPTCallSpy()
    t1 = await resolve_yahoo_ticker("okx", "BTC-USDT", "crypto", gpt_client_factory=spy.factory)
    assert t1 == "BTC-USD"
    # Second call: served from cache, GPT spy NEVER invoked (deterministic hit).
    t2 = await resolve_yahoo_ticker("okx", "BTC-USDT", "crypto", gpt_client_factory=spy.factory)
    assert t2 == "BTC-USD"
    assert spy.factory_calls == 0
    assert spy.create_calls == []


@pytest.mark.asyncio
async def test_resolve_gpt_fallback_resolves_unmapped_and_caches() -> None:
    spy = GPTCallSpy('{"yahoo_ticker": "^STOXX50E"}')
    # EU50 is in the index table → map to ^STOXX50E deterministically; use a
    # genuinely unmapped index symbol so the GPT branch is exercised.
    t1 = await resolve_yahoo_ticker(
        "capital", "MYSTERYIDX", "index", gpt_client_factory=spy.factory,
    )
    assert t1 == "^STOXX50E"
    assert spy.factory_calls == 1
    assert len(spy.create_calls) == 1
    # Cached → second call does NOT re-ask GPT.
    t2 = await resolve_yahoo_ticker(
        "capital", "MYSTERYIDX", "index", gpt_client_factory=spy.factory,
    )
    assert t2 == "^STOXX50E"
    assert spy.factory_calls == 1
    assert len(spy.create_calls) == 1


@pytest.mark.asyncio
async def test_resolve_okx_collision_base_routes_to_gpt() -> None:
    """An OKX collision base (deterministic map → None) routes to the GPT
    disambiguator, which can resolve the real coin (e.g. Sui = SUI20947-USD)."""
    spy = GPTCallSpy('{"yahoo_ticker": "SUI20947-USD"}')
    t1 = await resolve_yahoo_ticker(
        "okx", "SUI-USDT", "crypto", gpt_client_factory=spy.factory,
    )
    assert t1 == "SUI20947-USD"
    assert spy.factory_calls == 1
    # Cached → second call does NOT re-ask GPT.
    t2 = await resolve_yahoo_ticker(
        "okx", "SUI-USDT", "crypto", gpt_client_factory=spy.factory,
    )
    assert t2 == "SUI20947-USD"
    assert spy.factory_calls == 1


@pytest.mark.asyncio
async def test_resolve_gpt_invalid_response_negative_caches() -> None:
    spy = GPTCallSpy('{"yahoo_ticker": "has whitespace bad"}')
    t1 = await resolve_yahoo_ticker(
        "capital", "WEIRDSYM", "index", gpt_client_factory=spy.factory,
    )
    assert t1 is None
    assert spy.factory_calls == 1
    # Negative-cached → no retry.
    t2 = await resolve_yahoo_ticker(
        "capital", "WEIRDSYM", "index", gpt_client_factory=spy.factory,
    )
    assert t2 is None
    assert spy.factory_calls == 1


@pytest.mark.asyncio
async def test_resolve_keyless_factory_returns_none_no_retry() -> None:
    t1 = await resolve_yahoo_ticker(
        "capital", "NOKEYSYM", "index", gpt_client_factory=_raising_factory,
    )
    assert t1 is None
    # Negative-cached: a second call does NOT re-invoke the (raising) factory.
    calls = {"n": 0}

    def _counting_raising(model: str | None = None) -> Any:
        calls["n"] += 1
        raise RuntimeError("OPENAI_API_KEY missing from environment")

    t2 = await resolve_yahoo_ticker(
        "capital", "NOKEYSYM", "index", gpt_client_factory=_counting_raising,
    )
    assert t2 is None
    assert calls["n"] == 0, "negative cache must not re-invoke the factory"


@pytest.mark.asyncio
async def test_resolve_no_factory_unmapped_returns_none() -> None:
    assert await resolve_yahoo_ticker("capital", "MYSTERY", "index") is None


# ---------------------------------------------------------------------------
# 3. _yahoo_df_to_bars — tz-aware index → correct UTC epoch; bad rows dropped
# ---------------------------------------------------------------------------


def _ny_df() -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-02 09:30:00", tz="America/New_York"),
            pd.Timestamp("2024-01-02 09:31:00", tz="America/New_York"),
        ]
    )
    return pd.DataFrame(
        {
            "Open": [185.0, 186.0],
            "High": [186.5, 187.0],
            "Low": [184.5, 185.5],
            "Close": [186.0, 186.8],
            "Volume": [1_000_000, 0],
        },
        index=idx,
    )


def test_df_to_bars_tz_aware_utc_epoch_and_fields() -> None:
    df = _ny_df()
    bars = _yahoo_df_to_bars(
        df, venue="alpaca", symbol="AAPL", bar_interval="1m",
        underlying_group_id="equity:AAPL",
    )
    assert len(bars) == 2
    # 2024-01-02 09:30 ET == 14:30 UTC == 1704205800 epoch.
    assert bars[0].ts == 1_704_205_800
    assert bars[1].ts == 1_704_205_860
    b0 = bars[0]
    assert b0.venue == "alpaca"
    assert b0.symbol == "AAPL"  # venue-native, NOT the yahoo ticker
    assert b0.underlying_group_id == "equity:AAPL"
    assert b0.source == "yahoo"
    assert b0.open == 185.0
    assert b0.close == 186.0
    assert b0.volume == 1_000_000
    assert b0.notional_usd == pytest.approx(186.0 * 1_000_000)
    assert b0.trade_count == 0
    # Volume 0 row → notional 0 (FX has 0 volume; not an error).
    assert bars[1].notional_usd == 0.0


def test_df_to_bars_drops_bad_rows() -> None:
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-02 09:30:00", tz="America/New_York"),
            pd.Timestamp("2024-01-02 09:31:00", tz="America/New_York"),
        ]
    )
    df = pd.DataFrame(
        {
            "Open": [0.0, 186.0],   # row 0 non-positive open → dropped
            "High": [186.5, 187.0],
            "Low": [184.5, 185.5],
            "Close": [186.0, 186.8],
            "Volume": [1_000, 2_000],
        },
        index=idx,
    )
    bars = _yahoo_df_to_bars(
        df, venue="alpaca", symbol="AAPL", bar_interval="1m",
        underlying_group_id="equity:AAPL",
    )
    assert len(bars) == 1
    assert bars[0].open == 186.0


def test_df_to_bars_newest_last() -> None:
    # Build a descending-order df → output must be sorted ascending (newest last).
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-02 09:32:00", tz="America/New_York"),
            pd.Timestamp("2024-01-02 09:30:00", tz="America/New_York"),
        ]
    )
    df = pd.DataFrame(
        {
            "Open": [187.0, 185.0],
            "High": [188.0, 186.0],
            "Low": [186.0, 184.0],
            "Close": [187.5, 185.5],
            "Volume": [10, 20],
        },
        index=idx,
    )
    bars = _yahoo_df_to_bars(
        df, venue="alpaca", symbol="AAPL", bar_interval="1m",
        underlying_group_id="equity:AAPL",
    )
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)


# ---------------------------------------------------------------------------
# 4. fetch_yahoo_bars — injectable history fn (NO network)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_yahoo_bars_returns_canonical_newest_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_history(ticker: str, interval: str, period: str) -> pd.DataFrame:
        captured["ticker"] = ticker
        captured["interval"] = interval
        captured["period"] = period
        return _ny_df()

    monkeypatch.setattr(ybars, "_yf_history_blocking", _fake_history)
    bars = await fetch_yahoo_bars(
        "alpaca", "AAPL", "equity", bar_interval="1m", limit=240,
    )
    assert len(bars) == 2
    assert captured["ticker"] == "AAPL"
    assert captured["interval"] == "1m"
    assert captured["period"] == "7d"
    assert bars[0].source == "yahoo"
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)


@pytest.mark.asyncio
async def test_fetch_yahoo_bars_honours_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2024-01-02 09:30:00", tz="UTC") + pd.Timedelta(minutes=i)
         for i in range(5)]
    )
    df = pd.DataFrame(
        {
            "Open": [1.0] * 5, "High": [2.0] * 5, "Low": [0.5] * 5,
            "Close": [1.5] * 5, "Volume": [10] * 5,
        },
        index=idx,
    )
    monkeypatch.setattr(ybars, "_yf_history_blocking", lambda *a, **k: df)
    bars = await fetch_yahoo_bars(
        "okx", "BTC-USDT", "crypto", bar_interval="1m", limit=3,
    )
    assert len(bars) == 3  # last 3
    # newest-last → kept the 3 most recent
    assert bars[-1].ts == int(idx[-1].timestamp())


@pytest.mark.asyncio
async def test_fetch_yahoo_bars_unmapped_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _should_not_run(*a: Any, **k: Any) -> pd.DataFrame:
        raise AssertionError("history fn must not be called for unmapped symbol")

    monkeypatch.setattr(ybars, "_yf_history_blocking", _should_not_run)
    # capital equity non-numeric → unmapped → no history fetch.
    bars = await fetch_yahoo_bars("capital", "TSLA", "equity", bar_interval="1D")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_yahoo_bars_history_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*a: Any, **k: Any) -> pd.DataFrame:
        raise ValueError("yfinance network blew up")

    monkeypatch.setattr(ybars, "_yf_history_blocking", _boom)
    bars = await fetch_yahoo_bars("alpaca", "AAPL", "equity", bar_interval="1D")
    assert bars == []  # flow_not_block: exchange fallback covers it


@pytest.mark.asyncio
async def test_fetch_yahoo_bars_empty_df_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ybars, "_yf_history_blocking", lambda *a, **k: pd.DataFrame())
    bars = await fetch_yahoo_bars("alpaca", "AAPL", "equity", bar_interval="1D")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_yahoo_bars_unsupported_interval_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _should_not_run(*a: Any, **k: Any) -> pd.DataFrame:
        raise AssertionError("history fn must not run for unsupported interval")

    monkeypatch.setattr(ybars, "_yf_history_blocking", _should_not_run)
    bars = await fetch_yahoo_bars("alpaca", "AAPL", "equity", bar_interval="4h")
    assert bars == []


# ---------------------------------------------------------------------------
# 5. interval/period map coverage
# ---------------------------------------------------------------------------


def test_interval_map_covers_all_canonical() -> None:
    assert _YF_INTERVAL == {"1m": "1m", "5m": "5m", "15m": "15m", "1H": "1h", "1D": "1d"}


def test_period_map_covers_all_canonical() -> None:
    assert set(_YF_PERIOD) == {"1m", "5m", "15m", "1H", "1D"}
    assert _YF_PERIOD["1m"] == "7d"   # yfinance 1m hard limit
    assert _YF_PERIOD["1D"] == "2y"   # MA200 warmup


# ---------------------------------------------------------------------------
# 6. exchange-fallback cooldown — fetch-efficiency throttle (NOT a trading block)
# ---------------------------------------------------------------------------


def test_fallback_cooldown_first_call_allowed_then_blocked() -> None:
    now = 1_000_000.0
    assert should_fetch_exchange_fallback("capital", "EXOTIC", now) is True
    # Immediately after → within cooldown → spaced out (no re-fetch).
    assert should_fetch_exchange_fallback("capital", "EXOTIC", now + 1.0) is False
    # After the cooldown window → allowed again.
    assert should_fetch_exchange_fallback(
        "capital", "EXOTIC", now + ybars.FALLBACK_COOLDOWN_SEC + 1.0,
    ) is True


def test_fallback_cooldown_per_symbol() -> None:
    now = 2_000_000.0
    assert should_fetch_exchange_fallback("capital", "A", now) is True
    # A different symbol is independent (never-fetched → allowed).
    assert should_fetch_exchange_fallback("capital", "B", now) is True


# ---------------------------------------------------------------------------
# 7. live-price WS path is UNTOUCHED — guard against source-file imports
# ---------------------------------------------------------------------------


def test_yahoo_module_does_not_import_live_price_ws() -> None:
    import inspect

    src = inspect.getsource(ybars)
    assert "quote_writer" not in src, "Yahoo bar source must not touch the live-price path"
    assert "venues.okx.ws" not in src
    assert "venues.capital.ws" not in src
    assert "venues.alpaca.ws" not in src
    assert ".ws import" not in src
    # The only source-field change is to "yahoo".
    assert 'source="yahoo"' in src or "source='yahoo'" in src


# ---------------------------------------------------------------------------
# 8. fetch_bars_one — Yahoo PRIMARY, exchange FALLBACK + cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_bars_one_yahoo_primary_skips_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.scripts import _production_bars as pbars

    monkeypatch.setattr(ybars, "_yf_history_blocking", lambda *a, **k: _ny_df())

    async def _okx_must_not_run(*a: Any, **k: Any) -> list[Any]:
        raise AssertionError("exchange OKX fetch must NOT run when Yahoo has bars")

    monkeypatch.setattr(pbars, "fetch_okx_bars", _okx_must_not_run)
    bars = await pbars.fetch_bars_one(
        "okx", "BTC-USDT", "crypto", bar_interval="1m", limit=240,
    )
    assert len(bars) == 2
    assert bars[0].source == "yahoo"
    # newest last preserved
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)


@pytest.mark.asyncio
async def test_fetch_bars_one_falls_back_when_yahoo_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.data.schema import Bar
    from polaris.scripts import _production_bars as pbars

    # Yahoo unmapped (capital equity non-numeric) → [] → exchange fallback path.
    calls = {"n": 0}

    async def _okx(*a: Any, **k: Any) -> list[Bar]:
        calls["n"] += 1
        return []

    monkeypatch.setattr(pbars, "fetch_okx_bars", _okx)
    # Use an OKX non-USD-quote pair (unmapped → Yahoo []), so the OKX branch runs.
    await pbars.fetch_bars_one("okx", "AAVE-BTC", "crypto", bar_interval="1m")
    assert calls["n"] == 1, "exchange fallback must run once when Yahoo is empty"


@pytest.mark.asyncio
async def test_fetch_bars_one_fallback_cooldown_blocks_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.data.schema import Bar
    from polaris.scripts import _production_bars as pbars

    calls = {"n": 0}

    async def _okx(*a: Any, **k: Any) -> list[Bar]:
        calls["n"] += 1
        return []

    monkeypatch.setattr(pbars, "fetch_okx_bars", _okx)
    mono = {"t": 5_000.0}
    monkeypatch.setattr(pbars.time, "monotonic", lambda: mono["t"])

    await pbars.fetch_bars_one("okx", "AAVE-BTC", "crypto", bar_interval="1m")
    assert calls["n"] == 1
    # Immediate retry → cooldown spaces the redundant fallback re-fetch.
    mono["t"] = 5_001.0
    await pbars.fetch_bars_one("okx", "AAVE-BTC", "crypto", bar_interval="1m")
    assert calls["n"] == 1, "fallback cooldown must space the second immediate re-fetch"
    # After cooldown → allowed again.
    mono["t"] = 5_000.0 + ybars.FALLBACK_COOLDOWN_SEC + 1.0
    await pbars.fetch_bars_one("okx", "AAVE-BTC", "crypto", bar_interval="1m")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_fetch_bars_one_gpt_factory_threaded_to_yahoo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``gpt_client_factory`` reaches the Yahoo resolver (GPT fallback wired)."""
    from polaris.scripts import _production_bars as pbars

    spy = GPTCallSpy('{"yahoo_ticker": "^N225"}')
    monkeypatch.setattr(ybars, "_yf_history_blocking", lambda *a, **k: _ny_df())

    # capital index unmapped symbol → deterministic map miss → GPT resolves it.
    bars = await pbars.fetch_bars_one(
        "capital", "WEIRDINDEX", "index", bar_interval="1D",
        gpt_client_factory=spy.factory,
    )
    assert spy.factory_calls == 1
    assert len(bars) == 2
    assert bars[0].source == "yahoo"
    # The stored symbol stays venue-native (NOT the yahoo ticker ^N225).
    assert bars[0].symbol == "WEIRDINDEX"
    assert bars[0].venue == "capital"


# ---------------------------------------------------------------------------
# 9. within-period frame cache — cap Yahoo fetches to ~1/bar-period/symbol
# ---------------------------------------------------------------------------
#
# OKX 1m strategy (volume_burst) is EXCLUDED from skip_if_current, so OKX (and any
# non-skip-bucket) focus symbol would otherwise re-pull the FULL Yahoo window
# (1m→7d, thousands of rows) every 5s tick → risk of a Yahoo IP-block (worse than
# the Alpaca 429 storm). The frame cache returns the cached bars within the same
# bar period (no network), refetching only when the period rolls. Pure
# fetch-efficiency (flow_not_block); live price is on WS untouched.


class _CountingHistory:
    """Records how many times the blocking yfinance history fn is invoked."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.calls = 0

    def __call__(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        self.calls += 1
        return self._df


@pytest.mark.asyncio
async def test_frame_cache_one_fetch_within_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two calls in the SAME 1m period → history fn invoked ONCE; identical bars."""
    hist = _CountingHistory(_ny_df())
    monkeypatch.setattr(ybars, "_yf_history_blocking", hist)
    clock = {"t": 1_700_000_000.0}  # mid-minute
    monkeypatch.setattr(ybars.time, "time", lambda: clock["t"])

    b1 = await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")
    clock["t"] += 5.0  # next 5s tick, same minute
    b2 = await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")

    assert hist.calls == 1, "second call within the period must be served from cache"
    assert [b.ts for b in b1] == [b.ts for b in b2]
    assert [b.close for b in b1] == [b.close for b in b2]


@pytest.mark.asyncio
async def test_frame_cache_refetches_when_period_rolls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call after the 1m boundary rolls → history fn invoked again (refresh)."""
    hist = _CountingHistory(_ny_df())
    monkeypatch.setattr(ybars, "_yf_history_blocking", hist)
    clock = {"t": 1_700_000_000.0}
    monkeypatch.setattr(ybars.time, "time", lambda: clock["t"])

    await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")
    assert hist.calls == 1
    clock["t"] += 5.0
    await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")
    assert hist.calls == 1  # still same minute
    # Advance past the minute boundary → cache bucket rolls → refetch.
    clock["t"] += 60.0
    await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")
    assert hist.calls == 2


@pytest.mark.asyncio
async def test_frame_cache_per_symbol_and_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache key is (venue, symbol, bar_interval) — distinct keys fetch
    independently within the same period."""
    hist = _CountingHistory(_ny_df())
    monkeypatch.setattr(ybars, "_yf_history_blocking", hist)
    clock = {"t": 1_700_000_000.0}
    monkeypatch.setattr(ybars.time, "time", lambda: clock["t"])

    await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")
    await fetch_yahoo_bars("okx", "ETH-USDT", "crypto", bar_interval="1m")  # diff symbol
    await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="5m")  # diff interval
    assert hist.calls == 3
    # Repeat all three within the same periods → all served from cache.
    await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")
    await fetch_yahoo_bars("okx", "ETH-USDT", "crypto", bar_interval="1m")
    await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="5m")
    assert hist.calls == 3


@pytest.mark.asyncio
async def test_frame_cache_caches_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty/unmapped frame is cached too → no re-hit within the period; still
    returns [] each time (so the exchange fallback path is preserved)."""
    hist = _CountingHistory(pd.DataFrame())  # empty frame
    monkeypatch.setattr(ybars, "_yf_history_blocking", hist)
    clock = {"t": 1_700_000_000.0}
    monkeypatch.setattr(ybars.time, "time", lambda: clock["t"])

    r1 = await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")
    clock["t"] += 5.0
    r2 = await fetch_yahoo_bars("okx", "BTC-USDT", "crypto", bar_interval="1m")
    assert r1 == [] and r2 == []
    assert hist.calls == 1, "empty result is cached — no re-hit within the period"


@pytest.mark.asyncio
async def test_frame_cache_unmapped_symbol_never_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmapped symbol resolves to no ticker → history fn never runs, [] cached."""
    hist = _CountingHistory(_ny_df())
    monkeypatch.setattr(ybars, "_yf_history_blocking", hist)
    clock = {"t": 1_700_000_000.0}
    monkeypatch.setattr(ybars.time, "time", lambda: clock["t"])

    # capital equity non-numeric → unmapped (no GPT factory) → []
    r1 = await fetch_yahoo_bars("capital", "TSLA", "equity", bar_interval="1m")
    r2 = await fetch_yahoo_bars("capital", "TSLA", "equity", bar_interval="1m")
    assert r1 == [] and r2 == []
    assert hist.calls == 0


@pytest.mark.asyncio
async def test_frame_cache_1d_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """1D has no clean UTC-midnight boundary for the in-progress daily bar, so it
    uses an hourly TTL bucket: cached within the hour, refetched after it."""
    hist = _CountingHistory(_ny_df())
    monkeypatch.setattr(ybars, "_yf_history_blocking", hist)
    # Anchor mid-hour so a +1800s step stays in-bucket and +3600s rolls it.
    clock = {"t": 1_700_000_000.0}
    monkeypatch.setattr(ybars.time, "time", lambda: clock["t"])

    await fetch_yahoo_bars("alpaca", "AAPL", "equity", bar_interval="1D")
    assert hist.calls == 1
    clock["t"] += 1800.0  # +30min, same hour bucket
    await fetch_yahoo_bars("alpaca", "AAPL", "equity", bar_interval="1D")
    assert hist.calls == 1, "1D cached within the hourly TTL"
    clock["t"] += 3600.0  # past the hour boundary
    await fetch_yahoo_bars("alpaca", "AAPL", "equity", bar_interval="1D")
    assert hist.calls == 2


def test_period_open_buckets() -> None:
    """``_period_open`` floors to the interval period; 1D → hourly TTL bucket."""
    now = 1_700_000_037  # 57s past a minute boundary; mid-hour
    assert ybars._period_open("1m", now) == now - 57
    assert ybars._period_open("5m", now) % 300 == 0
    assert ybars._period_open("15m", now) % 900 == 0
    assert ybars._period_open("1H", now) % 3600 == 0
    # 1D → hourly bucket (no clean UTC-midnight boundary for the in-progress bar).
    assert ybars._period_open("1D", now) % 3600 == 0
    assert ybars._period_open("1D", now) <= now
