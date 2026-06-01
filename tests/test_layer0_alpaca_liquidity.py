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
    # Placeholder retained (no datum) — every row still flows.
    assert all(i.vol_24h_usd > 0 for i in out)
