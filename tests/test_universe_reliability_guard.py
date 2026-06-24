"""Layer 0 — enrichment reliability guard + 429-aware snapshot sweep tests.

Incident root cause (Jin 2026-06-24): an UNCAPPED watch set + a FAILED full-sweep
liquidity enrichment (data.alpaca.markets 429 storm) left ~3000 un-enriched Alpaca
rows carrying the sentinel ``vol_24h_usd=0.0``. The sentinel is non-floored
(flow_not_block) AND ties at vol_z=0 in the grouped rank, so the active book swelled
to 3000, fanning out 3000 bar-ingests that fed the 429 spiral (WAL 509M).

These tests pin the fix:

  (A) ENRICH-FIRST + (C) RELIABILITY GUARD — when a vol-expecting venue's enriched
      fraction is below ``POLARIS_RELIABILITY_MIN_ENRICHED_RATIO`` (default 0.8), the
      un-enriched (sentinel-0) slab is barred from the ACTIVE set, so the active book
      stays bounded to the REAL-LIQUID names instead of swelling to ~3000.
  (B) RATE-LIMIT-AWARE — the snapshot batch GET retries a 429 with exponential
      backoff (honouring Retry-After) instead of starving enrichment.
  (D) INC2 WATCH-ALL — with WATCH_MAX raised high, a HEALTHY (fully enriched) sweep
      still watches every real-liquid name (the guard is a NO-OP when enrichment is
      reliable; flow_not_block holds).

The headline invariant (junk_expansion_blocked): enrich-failure must NOT produce a
3000-row active set — it stays bounded to the enriched real-liquid names.

DEMO/PAPER only; Track C additive; OKX/Capital untouched; flow_not_block; in-loop
GPT = 0.
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
from polaris.core.universe._ranking import (
    apply_enrichment_reliability_guard,
    rank_active_universe,
)
from polaris.core.universe.schema import UniverseInstrument

NOW = 1_780_000_000


def _equity(symbol: str, vol: float) -> UniverseInstrument:
    """An Alpaca equity row — ``vol==0.0`` = un-enriched (sentinel)."""
    return UniverseInstrument(
        venue="alpaca",
        symbol=symbol,
        instrument_id=f"alpaca:{symbol}",
        underlying_group_id=f"equity:{symbol}",
        asset_class="equity",
        quote_ccy="USD",
        state="live",
        vol_24h_usd=vol,
        spread_bps=2.0,
        atr_24h_pct=1.5,
        depth_10bps_usd=0.0,
        last_price=100.0 if vol > 0.0 else 0.0,
        last_seen_ts=NOW,
    )


# --------------------------------------------------------------------------- #
# (C) Reliability guard — pure-function level.
# --------------------------------------------------------------------------- #
def test_guard_bars_unenriched_slab_below_threshold() -> None:
    """Enriched 2/3000 (< 0.8) → the 2998 un-enriched rows are barred from active."""
    rows = [_equity(f"SYM{i:04d}", vol=1.0e8) for i in range(2)]
    rows += [_equity(f"JUNK{i:04d}", vol=0.0) for i in range(2998)]
    kept = apply_enrichment_reliability_guard(rows, min_enriched_ratio=0.8)
    # Only the real-liquid (enriched) names survive as active candidates.
    assert len(kept) == 2
    assert all(i.vol_24h_usd > 0.0 for i in kept)


def test_guard_is_noop_when_enrichment_reliable() -> None:
    """Enriched 0.9 (>= 0.8) → the slab still flows (guard is a NO-OP)."""
    rows = [_equity(f"SYM{i:03d}", vol=1.0e8) for i in range(90)]
    rows += [_equity(f"JUNK{i:03d}", vol=0.0) for i in range(10)]
    kept = apply_enrichment_reliability_guard(rows, min_enriched_ratio=0.8)
    assert len(kept) == 100  # nothing dropped — flow_not_block on a reliable sweep


def test_guard_threshold_zero_disables() -> None:
    """A 0.0 threshold disables the guard entirely (every row eligible)."""
    rows = [_equity(f"JUNK{i:03d}", vol=0.0) for i in range(50)]
    kept = apply_enrichment_reliability_guard(rows, min_enriched_ratio=0.0)
    assert len(kept) == 50


def test_guard_skips_capital_no_native_vol() -> None:
    """Capital exposes no native vol (floor vol==0) — the guard must NOT wipe it."""
    cap = [
        UniverseInstrument(
            venue="capital",
            symbol=f"FX{i}",
            instrument_id=f"capital:FX{i}",
            underlying_group_id=f"forex:FX{i}",
            asset_class="forex",
            quote_ccy="USD",
            state="live",
            vol_24h_usd=0.0,  # structural, not an enrichment miss
            spread_bps=5.0,
            atr_24h_pct=0.5,
            depth_10bps_usd=0.0,
            last_seen_ts=NOW,
        )
        for i in range(20)
    ]
    kept = apply_enrichment_reliability_guard(cap, min_enriched_ratio=0.8)
    assert len(kept) == 20  # Capital untouched (vol not expected there)


# --------------------------------------------------------------------------- #
# Headline invariant — enrich-failure → active stays BOUNDED (never 3000).
# --------------------------------------------------------------------------- #
def test_enrich_failure_active_stays_bounded_not_3000(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A failed full-sweep (only 2 enriched of 3000) must NOT yield a 3000 active set.

    This is the junk_expansion_blocked invariant: with an UNCAPPED watch set, the
    reliability guard bars the un-enriched slab so the active book stays bounded to
    the real-liquid names.
    """
    monkeypatch.setenv("POLARIS_WATCH_MAX", "5000")  # uncap the watch set (INC2/D)
    monkeypatch.setenv("POLARIS_UNIVERSE_RANK_TOP_N", "5000")  # uncap top-N too
    monkeypatch.setenv("POLARIS_RELIABILITY_MIN_ENRICHED_RATIO", "0.8")
    # 2 enriched real-liquid + 2998 un-enriched sentinel junk. With BOTH the watch
    # cap and the rank top-N uncapped, ONLY the reliability guard keeps the active
    # set bounded — so this proves the guard, not a count-cap, blocks the swell.
    instruments = [_equity(f"REAL{i:02d}", vol=5.0e8 + i) for i in range(2)]
    instruments += [_equity(f"JUNK{i:04d}", vol=0.0) for i in range(2998)]
    active = rank_active_universe(instruments)
    # Bounded to the 2 real-liquid names — NOT the 3000 swell.
    assert len(active) == 2, f"junk expansion NOT blocked: active={len(active)}"
    assert {i.symbol for i in active} == {"REAL00", "REAL01"}


def test_guard_disabled_reproduces_the_swell(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Negative control: with the guard OFF the un-enriched slab DOES swell active.

    Confirms the headline test is meaningful — disabling the guard (ratio=0) with an
    uncapped watch set reproduces the 3000-row swell the guard is built to block.
    """
    monkeypatch.setenv("POLARIS_WATCH_MAX", "5000")
    monkeypatch.setenv("POLARIS_UNIVERSE_RANK_TOP_N", "5000")
    monkeypatch.setenv("POLARIS_RELIABILITY_MIN_ENRICHED_RATIO", "0")  # guard OFF
    instruments = [_equity(f"REAL{i:02d}", vol=5.0e8 + i) for i in range(2)]
    instruments += [_equity(f"JUNK{i:04d}", vol=0.0) for i in range(2998)]
    active = rank_active_universe(instruments)
    assert len(active) == 3000  # the swell reproduces without the guard


def test_reliable_sweep_watches_all_real_liquid_uncapped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """INC2/D: a HEALTHY sweep + uncapped WATCH_MAX watches every real-liquid name."""
    monkeypatch.setenv("POLARIS_WATCH_MAX", "5000")
    monkeypatch.setenv("POLARIS_UNIVERSE_RANK_TOP_N", "5000")
    monkeypatch.setenv("POLARIS_RELIABILITY_MIN_ENRICHED_RATIO", "0.8")
    # 1500 fully-enriched real-liquid names (the real-liquid bound), 0 junk.
    instruments = [_equity(f"L{i:04d}", vol=1.0e8 + i) for i in range(1500)]
    active = rank_active_universe(instruments)
    # Watch-all the real-liquid set (no count-cap artifact); none dropped.
    assert len(active) == 1500


# --------------------------------------------------------------------------- #
# (B) 429-aware snapshot sweep — backoff + recovery, not starvation.
# --------------------------------------------------------------------------- #
def _mock_429_then_ok(
    *,
    assets: list[dict[str, object]],
    snapshots: dict[str, object],
    fail_times: int,
) -> tuple[httpx.AsyncClient, list[int]]:
    """Snapshot endpoint returns 429 ``fail_times`` then 200; records call count."""
    calls: list[int] = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/assets":
            return httpx.Response(200, json=assets)
        if path == ALPACA_MOST_ACTIVES_PATH:
            return httpx.Response(200, json={"most_actives": []})
        if path == ALPACA_SNAPSHOTS_PATH:
            calls[0] += 1
            if calls[0] <= fail_times:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json=snapshots)
        return httpx.Response(404, json={"path": path})

    transport = httpx.MockTransport(handler)
    cli = httpx.AsyncClient(base_url=ALPACA_PAPER_BASE, transport=transport, timeout=5.0)
    return cli, calls


def _snapshots_payload(dollar_vol_by_sym: dict[str, float]) -> dict[str, object]:
    out: dict[str, object] = {}
    for sym, dvol in dollar_vol_by_sym.items():
        close = 100.0
        out[sym] = {
            "dailyBar": {"o": close, "h": close * 1.02, "l": close * 0.98,
                         "c": close, "v": dvol / close},
            "minuteBar": {"c": close},
        }
    return {"snapshots": out}


def test_snapshot_sweep_retries_429_then_succeeds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A transient 429 is retried with backoff, then the batch enriches (no starve)."""
    monkeypatch.setenv("POLARIS_ALPACA_SNAPSHOT_FULL", "1")
    # No-op the backoff sleep so the test is instant.
    monkeypatch.setattr(
        "polaris.core.universe._alpaca._async_sleep",
        lambda _delay: asyncio.sleep(0),
    )
    rows = [{"class": "us_equity", "exchange": "NASDAQ", "symbol": s,
             "status": "active", "tradable": True} for s in ("AAA", "BBB", "CCC")]
    cli, calls = _mock_429_then_ok(
        assets=rows,
        snapshots=_snapshots_payload({"AAA": 1.0e8, "BBB": 1.0e8, "CCC": 1.0e8}),
        fail_times=2,  # 429 twice, then 200
    )

    async def run() -> list[UniverseInstrument]:
        async with cli as c:
            return await fetch_alpaca_instruments(
                api_key="k", secret_key="s", now_ts=NOW, client=c
            )

    out = asyncio.run(run())
    # Retried past the two 429s (3 total calls) and enriched every row — not starved.
    assert calls[0] == 3
    assert all(i.vol_24h_usd >= 1.0e8 for i in out)


def test_snapshot_sweep_429_exhausted_keeps_sentinel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When every retry is 429, the batch is skipped — rows keep the sentinel (0.0).

    The reliability guard (not a crash, not a fake datum) then bounds the active set.
    """
    monkeypatch.setenv("POLARIS_ALPACA_SNAPSHOT_FULL", "1")
    monkeypatch.setenv("POLARIS_ALPACA_SNAPSHOT_MAX_RETRIES", "1")
    monkeypatch.setattr(
        "polaris.core.universe._alpaca._async_sleep",
        lambda _delay: asyncio.sleep(0),
    )
    rows = [{"class": "us_equity", "exchange": "NASDAQ", "symbol": s,
             "status": "active", "tradable": True} for s in ("AAA", "BBB")]
    cli, calls = _mock_429_then_ok(
        assets=rows, snapshots=_snapshots_payload({}), fail_times=99
    )

    async def run() -> list[UniverseInstrument]:
        async with cli as c:
            return await fetch_alpaca_instruments(
                api_key="k", secret_key="s", now_ts=NOW, client=c
            )

    out = asyncio.run(run())
    # Bounded retries (1 + 1 = 2 calls), no fake vol injected, rows keep sentinel.
    assert calls[0] == 2
    assert all(i.vol_24h_usd == 0.0 for i in out)
