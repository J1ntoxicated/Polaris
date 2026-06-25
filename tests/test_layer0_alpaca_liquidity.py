"""Layer 0 — Alpaca real per-symbol liquidity injection (P1) tests.

Forensic bug (w9gq4ueep): ``_alpaca`` stamped EVERY us_equity with an identical
placeholder ``vol_24h_usd=50e6`` / ``atr=1.5`` / ``spread=2.0``. In
``rank_active_universe`` those collapse to z=0, all ~12.9k rows tie, and the
stable sort seats the first 40 ALPHABETICALLY — every megacap (AAPL/MSFT/NVDA/
SPY/TSLA/...) ends up ``is_active=0``.

These tests pin the fix: real per-symbol dollar-volume (screener most-actives +
batched snapshots, plus a curated megacap/top-ETF liquidity seed) is mapped onto
the universe rows so ranking DISCRIMINATES and the liquid names win the active
slots. Symbols with no liquidity datum keep the coarse placeholder and still
flow (flow_not_block — nothing removed). DEMO/PAPER only; Track C additive;
OKX/Capital untouched.
"""

from __future__ import annotations

import asyncio

import httpx

from polaris.core.universe._alpaca import (
    ALPACA_MOST_ACTIVES_PATH,
    ALPACA_PAPER_BASE,
    ALPACA_SNAPSHOTS_PATH,
    LIQUID_SEED_SYMBOLS,
    _apply_liquidity,
    _snapshot_to_liquidity,
    fetch_alpaca_instruments,
)
from polaris.core.universe._ranking import rank_active_universe
from polaris.core.universe.schema import UniverseInstrument

NOW = 1_780_000_000

# An alphabetical slab of low-liquidity tickers (the names that wrongly won the
# 40 active slots before the fix — early alphabet, no real liquidity).
_ALPHABET_SLAB = [
    "AAAA", "AAAB", "AAAC", "AAAD", "AAAE", "AAAF", "AAAG", "AAAH",
    "GT", "HCI", "AADR", "AAIC", "AAME", "AAOI", "AAON", "AAP",
]
# Real megacaps / top ETFs that MUST win once liquidity discriminates.
_MEGACAPS = ["AAPL", "MSFT", "NVDA", "SPY", "TSLA", "AMZN", "QQQ", "META", "GOOGL"]


def _assets_payload(symbols: list[str]) -> list[dict[str, object]]:
    """Canned ``GET /v2/assets`` rows (all tradable/active us_equity)."""
    return [
        {
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": s,
            "status": "active",
            "tradable": True,
        }
        for s in symbols
    ]


def _most_actives_payload(symbols: list[str]) -> dict[str, object]:
    """Screener shape: ``{"most_actives": [{symbol, volume, trade_count}, ...]}``."""
    return {
        "most_actives": [
            {"symbol": s, "volume": 1_000_000 * (i + 1), "trade_count": 1000 * (i + 1)}
            for i, s in enumerate(symbols)
        ]
    }


def _snapshots_payload(dollar_vol_by_sym: dict[str, float]) -> dict[str, object]:
    """Snapshots shape: per-symbol ``dailyBar`` {o,h,l,c,v} + ``minuteBar``.

    We back-solve a daily bar so that ``close * volume == dollar_vol`` and the
    high/low straddle the close (gives a non-degenerate intraday range proxy).
    """
    out: dict[str, object] = {}
    for sym, dvol in dollar_vol_by_sym.items():
        close = 100.0
        vol = dvol / close
        out[sym] = {
            "dailyBar": {
                "o": close,
                "h": close * 1.02,
                "l": close * 0.98,
                "c": close,
                "v": vol,
            },
            "minuteBar": {"c": close},
        }
    return {"snapshots": out}


def _liquidity_mock_client(
    *,
    assets: list[dict[str, object]],
    most_actives: dict[str, object],
    snapshots: dict[str, object],
) -> httpx.AsyncClient:
    """One MockTransport routing assets (trade host) + screener/snapshots (data host)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/assets":
            return httpx.Response(200, json=assets)
        if path == ALPACA_MOST_ACTIVES_PATH:
            return httpx.Response(200, json=most_actives)
        if path == ALPACA_SNAPSHOTS_PATH:
            return httpx.Response(200, json=snapshots)
        return httpx.Response(404, json={"path": path})

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(base_url=ALPACA_PAPER_BASE, transport=transport, timeout=5.0)


def test_seed_symbols_cover_megacaps() -> None:
    """The curated liquidity seed must include the megacaps + top ETFs."""
    for sym in _MEGACAPS:
        assert sym in LIQUID_SEED_SYMBOLS, f"{sym} missing from LIQUID_SEED_SYMBOLS"


def test_fetch_injects_real_liquidity() -> None:
    """Megacaps get real (large) dollar-volume; slab keeps the coarse placeholder."""
    symbols = _ALPHABET_SLAB + _MEGACAPS
    # Real dollar-volume: megacaps huge, slab absent from any liquidity source.
    dvol = {s: 5.0e8 + 1e7 * i for i, s in enumerate(_MEGACAPS)}

    async def run() -> list[UniverseInstrument]:
        client = _liquidity_mock_client(
            assets=_assets_payload(symbols),
            most_actives=_most_actives_payload(_MEGACAPS),
            snapshots=_snapshots_payload(dvol),
        )
        async with client as cli:
            return await fetch_alpaca_instruments(
                api_key="k", secret_key="s", now_ts=NOW, client=cli
            )

    out = asyncio.run(run())
    by_sym = {i.symbol: i for i in out}
    # Megacaps now carry real, large dollar-volume (>> any slab name).
    for sym in _MEGACAPS:
        assert by_sym[sym].vol_24h_usd >= 5.0e8
    # Slab names without a liquidity datum still flow (placeholder retained), and
    # rank strictly below the enriched megacaps.
    for sym in _ALPHABET_SLAB:
        assert by_sym[sym].vol_24h_usd < by_sym["AAPL"].vol_24h_usd
    # Nothing removed — every asset row survives (flow_not_block).
    assert set(by_sym) == set(symbols)


def test_ranking_seats_megacaps_not_alphabetical_slab() -> None:
    """With discriminating liquidity, the active set is liquid names, not A-slab."""
    symbols = _ALPHABET_SLAB + _MEGACAPS
    dvol = {s: 5.0e8 + 1e7 * i for i, s in enumerate(_MEGACAPS)}

    async def run() -> list[UniverseInstrument]:
        client = _liquidity_mock_client(
            assets=_assets_payload(symbols),
            most_actives=_most_actives_payload(_MEGACAPS),
            snapshots=_snapshots_payload(dvol),
        )
        async with client as cli:
            return await fetch_alpaca_instruments(
                api_key="k", secret_key="s", now_ts=NOW, client=cli
            )

    instruments = asyncio.run(run())
    active = rank_active_universe(instruments, top_n=9)
    active_syms = {i.symbol for i in active}
    # Every megacap is seated; no alphabetical-slab name displaces them.
    assert set(_MEGACAPS) <= active_syms, f"megacaps missing: {set(_MEGACAPS) - active_syms}"
    assert active_syms.isdisjoint(set(_ALPHABET_SLAB))


def test_placeholder_only_universe_still_ties_alphabetically() -> None:
    """Regression guard: WITHOUT enrichment the bug reproduces (all-tie → slab).

    Confirms the test harness actually exercises discrimination — an unenriched
    universe (identical placeholders) still collapses to an alphabetical seat, so
    the passing case above is meaningful.
    """
    symbols = _ALPHABET_SLAB + _MEGACAPS
    instruments = [
        UniverseInstrument(
            venue="alpaca",
            symbol=s,
            instrument_id=f"alpaca:{s}",
            underlying_group_id=f"equity:{s}",
            asset_class="equity",
            quote_ccy="USD",
            state="live",
            vol_24h_usd=50_000_000.0,
            spread_bps=2.0,
            atr_24h_pct=1.5,
            depth_10bps_usd=0.0,
            last_seen_ts=NOW,
        )
        for s in sorted(symbols)
    ]
    active = rank_active_universe(instruments, top_n=9)
    # All tie → stable sort seats the first 9 alphabetically (the slab), NOT megacaps.
    assert {i.symbol for i in active} == set(sorted(symbols)[:9])


def test_enrichment_failure_is_smoke_safe() -> None:
    """A failed enrichment call must not break fetch — rows keep placeholders."""
    symbols = _ALPHABET_SLAB + _MEGACAPS

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/assets":
            return httpx.Response(200, json=_assets_payload(symbols))
        # Screener / snapshots both 500 → enrichment must swallow + fall back.
        return httpx.Response(500, json={"error": "boom"})

    async def run() -> list[UniverseInstrument]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url=ALPACA_PAPER_BASE, transport=transport, timeout=5.0
        ) as cli:
            return await fetch_alpaca_instruments(
                api_key="k", secret_key="s", now_ts=NOW, client=cli
            )

    out = asyncio.run(run())
    assert {i.symbol for i in out} == set(symbols)
    # Sentinel retained (no datum) — every row still flows (flow_not_block) and a
    # sentinel vol=0 is non-floored (unknown != known-bad), it just ranks last.
    assert all(i.vol_24h_usd == 0.0 for i in out)
    from polaris.core.universe.schema import passes_liquidity_floor

    assert all(passes_liquidity_floor(i) for i in out)


# ---------------------------------------------------------------------------
# real spread plumbing — _snapshot_to_liquidity + _apply_liquidity (Part A)
# ---------------------------------------------------------------------------


def _snap(close: float, *, bid: float | None, ask: float | None) -> dict[str, object]:
    """One snapshot dict with a dailyBar/minuteBar + optional latestQuote (bp/ap)."""
    snap: dict[str, object] = {
        "dailyBar": {
            "o": close, "h": close * 1.02, "l": close * 0.98, "c": close,
            "v": 1_000_000.0,
        },
        "minuteBar": {"c": close},
    }
    if bid is not None and ask is not None:
        snap["latestQuote"] = {"bp": bid, "ap": ask}
    return snap


def test_snapshot_extracts_wide_real_spread() -> None:
    """A latestQuote with a wide bid/ask (MGN-class 8800bps=88%) → real spread.

    mid = 0.5*(bid+ask); spread_bps = (ask-bid)/mid*1e4. bid=1, ask=2.69 →
    mid=1.845, spread=(1.69/1.845)*1e4 ≈ 9160bps (un-tradeable). Just assert it
    lands as a large positive spread_bps key (the floor will exclude it).
    """
    liq = _snapshot_to_liquidity(_snap(2.0, bid=1.0, ask=2.69))
    assert liq is not None
    assert "spread_bps" in liq
    expected = (2.69 - 1.0) / (0.5 * (1.0 + 2.69)) * 1e4
    assert abs(liq["spread_bps"] - expected) < 1e-6
    assert liq["spread_bps"] > 100.0  # clearly past the Alpaca cut


def test_snapshot_extracts_tight_real_spread() -> None:
    """A tight liquid quote (bid 99.99 / ask 100.01) → small positive spread_bps."""
    liq = _snapshot_to_liquidity(_snap(100.0, bid=99.99, ask=100.01))
    assert liq is not None
    expected = (100.01 - 99.99) / (0.5 * (99.99 + 100.01)) * 1e4
    assert abs(liq["spread_bps"] - expected) < 1e-6
    assert 0.0 < liq["spread_bps"] < 10.0


def test_snapshot_missing_quote_omits_spread() -> None:
    """No latestQuote → NO spread_bps key (degrade-safe: keep the placeholder)."""
    liq = _snapshot_to_liquidity(_snap(50.0, bid=None, ask=None))
    assert liq is not None
    assert "spread_bps" not in liq
    # vol/atr/price still plumbed (the quote omission doesn't break enrichment).
    assert liq["vol_24h_usd"] > 0.0 and liq["price"] == 50.0


def test_snapshot_crossed_or_zero_quote_omits_spread() -> None:
    """Crossed/locked (ask<=bid) or non-positive bid/ask → NO spread_bps key."""
    crossed = _snapshot_to_liquidity(_snap(50.0, bid=51.0, ask=50.0))  # ask<bid
    assert crossed is not None and "spread_bps" not in crossed
    locked = _snapshot_to_liquidity(_snap(50.0, bid=50.0, ask=50.0))   # ask==bid
    assert locked is not None and "spread_bps" not in locked
    zero_bid = _snapshot_to_liquidity(_snap(50.0, bid=0.0, ask=50.0))  # bid<=0
    assert zero_bid is not None and "spread_bps" not in zero_bid


def _alpaca_row(symbol: str, *, spread_bps: float = 2.0) -> UniverseInstrument:
    return UniverseInstrument(
        venue="alpaca",
        symbol=symbol,
        instrument_id=f"alpaca:{symbol}",
        underlying_group_id=f"equity:{symbol}",
        asset_class="equity",
        quote_ccy="USD",
        state="live",
        vol_24h_usd=0.0,
        spread_bps=spread_bps,
        atr_24h_pct=1.5,
        depth_10bps_usd=0.0,
        last_seen_ts=NOW,
    )


def test_apply_liquidity_overwrites_real_spread() -> None:
    """A row enriched with a real spread datum gets its spread_bps overwritten."""
    rows = [_alpaca_row("MGN", spread_bps=2.0)]
    liq = {"MGN": {"vol_24h_usd": 5e7, "atr_24h_pct": 4.0, "price": 4.0,
                   "spread_bps": 8800.0}}
    out = _apply_liquidity(rows, liq)
    assert out[0].spread_bps == 8800.0  # placeholder replaced by the real spread


def test_apply_liquidity_keeps_placeholder_when_no_spread_datum() -> None:
    """A row enriched WITHOUT a spread datum keeps its placeholder spread (2.0)."""
    rows = [_alpaca_row("AAPL", spread_bps=2.0)]
    liq = {"AAPL": {"vol_24h_usd": 5e9, "atr_24h_pct": 3.0, "price": 190.0}}
    out = _apply_liquidity(rows, liq)
    assert out[0].spread_bps == 2.0  # no real spread → placeholder retained
    assert out[0].vol_24h_usd == 5e9  # vol still enriched (mirror unchanged)
