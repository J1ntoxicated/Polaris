"""Layer 0 — Alpaca sentinel-vol + exchange-plumb + widened-enrichment tests.

Forensic (alpaca_validity_2026-06-24): the old fetcher stamped EVERY tradable
``us_equity`` with a class-constant ``vol_24h_usd=50e6`` placeholder. That phantom
slab (>5M floor, last_price=0) was structurally un-discriminable — 13151 identical
"liquid" rows tied at the rank median. The fix:

  (1) SENTINEL — un-enriched rows carry ``vol_24h_usd=0.0`` (unknown), never 50e6.
      Floor still treats 0 as non-floored (flow_not_block) but the rank no longer
      sees a phantom liquid slab — unknowns rank LAST, not at a fake median.
  (2) EXCHANGE PLUMB — ``/v2/assets`` carries ``exchange``; it is read onto the
      instrument and OTC/pink venues are excluded (gradable = listed exchanges).
  (3) WIDENED ENRICHMENT — ``POLARIS_ALPACA_SNAPSHOT_FULL=1`` sweeps every
      universe symbol through batched snapshots so real vol reaches all rows.

DEMO/PAPER only; Track C additive; OKX/Capital untouched; flow_not_block; the
real Alpaca floor (min_vol 5M / min_price $1) applies to the now-real data.
"""

from __future__ import annotations

import asyncio

import httpx

from polaris.core.universe._alpaca import (
    ALPACA_MOST_ACTIVES_PATH,
    ALPACA_PAPER_BASE,
    ALPACA_SNAPSHOTS_PATH,
    fetch_alpaca_instruments,
)
from polaris.core.universe.schema import (
    UniverseInstrument,
    liquidity_floor_for_venue,
    passes_liquidity_floor,
)

NOW = 1_780_000_000


def _assets_payload(rows: list[tuple[str, str]]) -> list[dict[str, object]]:
    """``GET /v2/assets`` rows from ``(symbol, exchange)`` tuples."""
    return [
        {
            "class": "us_equity",
            "exchange": exch,
            "symbol": sym,
            "status": "active",
            "tradable": True,
        }
        for sym, exch in rows
    ]


def _most_actives_payload(symbols: list[str]) -> dict[str, object]:
    return {
        "most_actives": [
            {"symbol": s, "volume": 1_000_000 * (i + 1)} for i, s in enumerate(symbols)
        ]
    }


def _snapshots_payload(dollar_vol_by_sym: dict[str, float]) -> dict[str, object]:
    out: dict[str, object] = {}
    for sym, dvol in dollar_vol_by_sym.items():
        close = 100.0
        out[sym] = {
            "dailyBar": {
                "o": close,
                "h": close * 1.02,
                "l": close * 0.98,
                "c": close,
                "v": dvol / close,
            },
            "minuteBar": {"c": close},
        }
    return {"snapshots": out}


def _mock_client(
    *,
    assets: list[dict[str, object]],
    most_actives: dict[str, object],
    snapshots: dict[str, object],
    record_snapshot_symbols: list[str] | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/assets":
            return httpx.Response(200, json=assets)
        if path == ALPACA_MOST_ACTIVES_PATH:
            return httpx.Response(200, json=most_actives)
        if path == ALPACA_SNAPSHOTS_PATH:
            if record_snapshot_symbols is not None:
                syms = request.url.params.get("symbols", "")
                record_snapshot_symbols.extend(s for s in syms.split(",") if s)
            return httpx.Response(200, json=snapshots)
        return httpx.Response(404, json={"path": path})

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(base_url=ALPACA_PAPER_BASE, transport=transport, timeout=5.0)


def _fetch(client: httpx.AsyncClient) -> list[UniverseInstrument]:
    async def run() -> list[UniverseInstrument]:
        async with client as cli:
            return await fetch_alpaca_instruments(
                api_key="k", secret_key="s", now_ts=NOW, client=cli
            )

    return asyncio.run(run())


# --------------------------------------------------------------------------- #
# (1) SENTINEL — un-enriched rows carry vol_24h_usd == 0.0, never 50e6.
# --------------------------------------------------------------------------- #
def test_unenriched_rows_carry_sentinel_zero_not_50m() -> None:
    """A row with no liquidity datum must report vol_24h_usd == 0.0 (sentinel)."""
    rows = [("ZZZA", "NASDAQ"), ("ZZZB", "NYSE")]
    out = _fetch(
        _mock_client(
            assets=_assets_payload(rows),
            most_actives=_most_actives_payload([]),
            snapshots=_snapshots_payload({}),
        )
    )
    assert {i.symbol for i in out} == {"ZZZA", "ZZZB"}
    for ins in out:
        assert ins.vol_24h_usd == 0.0, f"{ins.symbol} still on phantom placeholder"


def test_sentinel_zero_is_non_floored_flow_not_block() -> None:
    """Sentinel vol=0 must pass the liquidity floor (unknown != known-bad)."""
    floor = liquidity_floor_for_venue("alpaca")
    assert floor.min_vol_24h_usd > 0.0  # the floor IS armed
    sentinel_row = UniverseInstrument(
        venue="alpaca",
        symbol="ZZZA",
        instrument_id="alpaca:ZZZA",
        underlying_group_id="equity:ZZZA",
        asset_class="equity",
        quote_ccy="USD",
        state="live",
        vol_24h_usd=0.0,
        spread_bps=2.0,
        atr_24h_pct=1.5,
        depth_10bps_usd=0.0,
        last_seen_ts=NOW,
    )
    assert passes_liquidity_floor(sentinel_row) is True  # flow_not_block


def test_enriched_real_vol_applies_existing_floor() -> None:
    """With REAL vol the floor now bites: a sub-5M real name is known-bad."""
    floor = liquidity_floor_for_venue("alpaca")
    below = UniverseInstrument(
        venue="alpaca",
        symbol="THIN",
        instrument_id="alpaca:THIN",
        underlying_group_id="equity:THIN",
        asset_class="equity",
        quote_ccy="USD",
        state="live",
        vol_24h_usd=1_000_000.0,  # real, below 5M floor
        spread_bps=2.0,
        atr_24h_pct=1.5,
        depth_10bps_usd=0.0,
        last_price=10.0,
        last_seen_ts=NOW,
    )
    assert below.vol_24h_usd < floor.min_vol_24h_usd
    assert passes_liquidity_floor(below) is False  # real data → floor bites


# --------------------------------------------------------------------------- #
# (3) EXCHANGE PLUMB — exchange read onto the row; OTC/pink excluded.
# --------------------------------------------------------------------------- #
def test_primary_exchange_plumbed_onto_instrument() -> None:
    out = _fetch(
        _mock_client(
            assets=_assets_payload([("AAPL", "NASDAQ"), ("GE", "NYSE")]),
            most_actives=_most_actives_payload([]),
            snapshots=_snapshots_payload({}),
        )
    )
    by_sym = {i.symbol: i for i in out}
    assert by_sym["AAPL"].primary_exchange == "NASDAQ"
    assert by_sym["GE"].primary_exchange == "NYSE"


def test_otc_pink_rows_excluded() -> None:
    """OTC / pink-sheet rows are NOT gradable us-equity universe members."""
    out = _fetch(
        _mock_client(
            assets=_assets_payload(
                [("AAPL", "NASDAQ"), ("PINKY", "OTC"), ("GREYZ", "")]
            ),
            most_actives=_most_actives_payload([]),
            snapshots=_snapshots_payload({}),
        )
    )
    syms = {i.symbol for i in out}
    assert "AAPL" in syms
    assert "PINKY" not in syms  # OTC excluded
    assert "GREYZ" not in syms  # unknown/blank exchange excluded


# --------------------------------------------------------------------------- #
# (3) WIDENED ENRICHMENT — full snapshot sweep reaches every universe symbol.
# --------------------------------------------------------------------------- #
def test_full_snapshot_sweep_enriches_all_rows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``POLARIS_ALPACA_SNAPSHOT_FULL=1`` snapshots EVERY tradable symbol."""
    monkeypatch.setenv("POLARIS_ALPACA_SNAPSHOT_FULL", "1")
    rows = [(f"SYM{i:03d}", "NASDAQ") for i in range(250)]
    symbols = [s for s, _ in rows]
    dvol = {s: 1.0e8 for s in symbols}  # every symbol has a real snapshot
    probed: list[str] = []
    out = _fetch(
        _mock_client(
            assets=_assets_payload(rows),
            most_actives=_most_actives_payload([]),
            snapshots=_snapshots_payload(dvol),
            record_snapshot_symbols=probed,
        )
    )
    # Every universe symbol was probed (batched), not just the screener ∩ seed.
    assert set(probed) == set(symbols)
    # And every row carries real vol (none left on the sentinel 0.0).
    assert all(i.vol_24h_usd >= 1.0e8 for i in out)


def test_full_sweep_opt_out_bounds_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit opt-out (=0) bounds probing to screener ∩ seed (default is ON)."""
    monkeypatch.setenv("POLARIS_ALPACA_SNAPSHOT_FULL", "0")
    rows = [(f"SYM{i:03d}", "NASDAQ") for i in range(250)]
    symbols = [s for s, _ in rows]
    probed: list[str] = []
    _fetch(
        _mock_client(
            assets=_assets_payload(rows),
            most_actives=_most_actives_payload(symbols[:5]),  # 5 screener names
            snapshots=_snapshots_payload({s: 1.0e8 for s in symbols[:5]}),
            record_snapshot_symbols=probed,
        )
    )
    # Bounded: nowhere near the full 250 (screener ∩ seed only).
    assert 0 < len(set(probed)) <= 60
