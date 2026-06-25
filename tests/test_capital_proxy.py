"""Capital CFD vol/depth proxy tests.

Spec source: vault/30_components/layer-0-universe-discovery.md (Q2 hard filter — CFD proxy).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from polaris.core.universe.discovery import apply_active_filters
from polaris.core.universe.schema import UniverseInstrument, default_thresholds
from polaris.venues.capital.market_proxy import (
    CAPITAL_DEPTH_PROXY_FLOOR_USD,
    CAPITAL_PROXY_MAX_CONCURRENCY,
    CAPITAL_PROXY_TOTAL_TIMEOUT_SEC,
    CAPITAL_VOL_PROXY_FLOOR_USD,
    CapitalProxyConfig,
    compute_depth_proxy,
    compute_vol_24h_proxy,
    passes_proxy_4_axis,
    populate_capital_proxies,
)

NOW = 1_780_000_000


def _capital_inst(
    *,
    symbol: str = "EURUSD",
    asset_class: str = "forex",
    spread_bps: float = 1.5,
    atr_pct: float = 2.5,
    vol: float = 0.0,
    depth: float = 0.0,
    state: str = "live",
) -> UniverseInstrument:
    return UniverseInstrument(
        venue="capital",
        symbol=symbol,
        instrument_id=f"capital:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy="USD",
        state=state,
        vol_24h_usd=vol,
        spread_bps=spread_bps,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=depth,
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=NOW,
    )


# ---------------------------------------------------------------------------
# Vol-proxy compute
# ---------------------------------------------------------------------------


def test_vol_24h_proxy_compute() -> None:
    candles = [
        {"closePrice": {"bid": 1.10, "ask": 1.10}, "lastTradedVolume": 1_000_000},
        {"closePrice": {"bid": 1.11, "ask": 1.11}, "lastTradedVolume": 2_000_000},
    ]
    out = compute_vol_24h_proxy(candles)
    assert out == pytest.approx(1.10 * 1_000_000 + 1.11 * 2_000_000)


def test_vol_24h_proxy_handles_flat_close_field() -> None:
    candles = [{"close": 1.10, "lastTradedVolume": 500_000}]
    assert compute_vol_24h_proxy(candles) == pytest.approx(550_000.0)


def test_vol_24h_proxy_skips_invalid_rows() -> None:
    candles = [
        {"closePrice": {"bid": 0, "ask": 0}, "lastTradedVolume": 100},
        {"closePrice": {"bid": 1.0, "ask": 1.0}, "lastTradedVolume": 0},
        {"close": 1.0, "lastTradedVolume": "garbage"},
        {"closePrice": {"bid": 1.0, "ask": 1.0}, "lastTradedVolume": 100},
    ]
    assert compute_vol_24h_proxy(candles) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Depth-proxy compute
# ---------------------------------------------------------------------------


def test_depth_proxy_spread_weighted_decreases_with_wider_spread() -> None:
    tight = compute_depth_proxy(spread_bps=0.5, atr_24h_pct=1.0, asset_class="forex")
    wide = compute_depth_proxy(spread_bps=5.0, atr_24h_pct=1.0, asset_class="forex")
    assert tight > wide
    assert tight == pytest.approx((1 / 0.5) * 1.0 * 10.0 * 10_000.0)


def test_depth_proxy_zero_inputs_returns_zero() -> None:
    assert compute_depth_proxy(spread_bps=0.0, atr_24h_pct=1.0, asset_class="forex") == 0.0
    assert compute_depth_proxy(spread_bps=1.0, atr_24h_pct=0.0, asset_class="forex") == 0.0


def test_depth_proxy_asset_class_pip_lookup() -> None:
    indices = compute_depth_proxy(spread_bps=1.0, atr_24h_pct=1.0, asset_class="indices")
    forex = compute_depth_proxy(spread_bps=1.0, atr_24h_pct=1.0, asset_class="forex")
    # indices pip notional 25 vs forex 10 ⇒ indices > forex.
    assert indices > forex


# ---------------------------------------------------------------------------
# Proxy 4-axis OR-gate
# ---------------------------------------------------------------------------


def test_passes_proxy_4_axis_either_clears() -> None:
    assert passes_proxy_4_axis(
        vol_proxy_usd=CAPITAL_VOL_PROXY_FLOOR_USD,
        depth_proxy_usd=0.0,
    )
    assert passes_proxy_4_axis(
        vol_proxy_usd=0.0,
        depth_proxy_usd=CAPITAL_DEPTH_PROXY_FLOOR_USD,
    )
    assert not passes_proxy_4_axis(
        vol_proxy_usd=CAPITAL_VOL_PROXY_FLOOR_USD - 1.0,
        depth_proxy_usd=CAPITAL_DEPTH_PROXY_FLOOR_USD - 1.0,
    )


def test_passes_proxy_4_axis_custom_config() -> None:
    cfg = CapitalProxyConfig(vol_floor_usd=1000.0, depth_floor_usd=100.0)
    assert passes_proxy_4_axis(vol_proxy_usd=1000.0, depth_proxy_usd=0.0, config=cfg)
    assert not passes_proxy_4_axis(vol_proxy_usd=999.0, depth_proxy_usd=99.0, config=cfg)


# ---------------------------------------------------------------------------
# 4-axis filter integration (Capital OR gate)
# ---------------------------------------------------------------------------


def test_4_axis_filter_pass_with_vol_proxy_only() -> None:
    """Capital row with strong vol proxy + zero depth still passes (OR-gate)."""
    inst = _capital_inst(vol=5e8, depth=0.0)
    out = apply_active_filters([inst])
    assert out == [inst]


def test_4_axis_filter_pass_with_depth_proxy_only() -> None:
    """Capital row with strong depth proxy + zero vol still passes (OR-gate)."""
    inst = _capital_inst(vol=0.0, depth=600_000.0)
    out = apply_active_filters([inst])
    assert out == [inst]


def test_4_axis_filter_capital_zero_zero_rejected() -> None:
    """Zero vol AND zero depth must still fail — OR-gate doesn't whitelist garbage."""
    inst = _capital_inst(vol=0.0, depth=0.0)
    th = default_thresholds()
    out = apply_active_filters([inst], thresholds=th)
    assert out == []


def test_4_axis_filter_strict_mode_for_capital() -> None:
    """capital_proxy_or_gate=False → OKX-style strict gate even for capital row."""
    inst = _capital_inst(vol=5e8, depth=0.0)  # depth fails
    out = apply_active_filters([inst], capital_proxy_or_gate=False)
    assert out == []


def test_4_axis_filter_okx_unaffected_by_proxy_flag() -> None:
    """OKX rows always need ALL axes (proxy flag is venue-scoped to capital)."""
    okx = UniverseInstrument(
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=5e8,
        spread_bps=2.0,
        atr_24h_pct=4.0,
        depth_10bps_usd=200_000.0,
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=NOW,
    )
    assert apply_active_filters([okx]) == [okx]
    # Strip depth → must fail (OR-gate is capital-only).
    weak = UniverseInstrument(
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=5e8,
        spread_bps=2.0,
        atr_24h_pct=4.0,
        depth_10bps_usd=10.0,
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=NOW,
    )
    assert apply_active_filters([weak]) == []


# ---------------------------------------------------------------------------
# populate_capital_proxies — bounded-concurrency parallel fetch
# (startup bottleneck fix: sequential 1658×timeout → ~(N/conc)×timeout)
# ---------------------------------------------------------------------------


def _okx_inst(symbol: str = "BTC-USDT") -> UniverseInstrument:
    return UniverseInstrument(
        venue="okx",
        symbol=symbol,
        instrument_id=f"okx:{symbol}",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=5e8,
        spread_bps=2.0,
        atr_24h_pct=4.0,
        depth_10bps_usd=200_000.0,
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=NOW,
    )


def _candles(close: float, vol: float) -> list[dict[str, Any]]:
    return [{"closePrice": {"bid": close, "ask": close}, "lastTradedVolume": vol}]


class _Recorder:
    """Stub fetch fn that records concurrency + per-epic behaviour."""

    def __init__(
        self,
        *,
        per_epic: dict[str, list[dict[str, Any]]] | None = None,
        raise_on: set[str] | None = None,
        delay_on: dict[str, float] | None = None,
    ) -> None:
        self.per_epic = per_epic or {}
        self.raise_on = raise_on or set()
        self.delay_on = delay_on or {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.seen: list[str] = []

    async def __call__(self, epic: str, **_: Any) -> list[dict[str, Any]]:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.seen.append(epic)
        try:
            delay = self.delay_on.get(epic, 0.0)
            if delay:
                await asyncio.sleep(delay)
            if epic in self.raise_on:
                import httpx

                raise httpx.HTTPError("boom")
            return self.per_epic.get(epic, _candles(1.0, 1_000_000))
        finally:
            self.in_flight -= 1


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_parallel_result_matches_sequential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parallel populate yields the SAME rows (same order, same proxy values)
    a sequential per-row fetch would produce."""
    import polaris.venues.capital.market_proxy as mp

    insts = [
        _capital_inst(symbol="EURUSD", asset_class="forex", spread_bps=1.0, atr_pct=2.0),
        _capital_inst(symbol="GBPUSD", asset_class="forex", spread_bps=2.0, atr_pct=3.0),
        _capital_inst(symbol="US500", asset_class="indices", spread_bps=1.0, atr_pct=1.0),
    ]
    per_epic = {
        "EURUSD": _candles(1.10, 1_000_000),
        "GBPUSD": _candles(1.25, 2_000_000),
        "US500": _candles(5000.0, 3_000),
    }
    rec = _Recorder(per_epic=per_epic)
    monkeypatch.setattr(mp, "fetch_capital_chart_24h", rec)

    out = _run(populate_capital_proxies(insts, cst="c", security_token="s"))

    assert [o.symbol for o in out] == ["EURUSD", "GBPUSD", "US500"]
    for o, src in zip(out, insts, strict=True):
        expected_vol = compute_vol_24h_proxy(per_epic[o.symbol])
        expected_depth = compute_depth_proxy(
            spread_bps=src.spread_bps,
            atr_24h_pct=src.atr_24h_pct,
            asset_class=src.asset_class,
        )
        assert o.vol_24h_usd == pytest.approx(expected_vol)
        assert o.depth_10bps_usd == pytest.approx(expected_depth)


def test_non_capital_rows_pass_through_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polaris.venues.capital.market_proxy as mp

    okx = _okx_inst()
    cap = _capital_inst(symbol="EURUSD")
    rec = _Recorder(per_epic={"EURUSD": _candles(1.10, 1_000_000)})
    monkeypatch.setattr(mp, "fetch_capital_chart_24h", rec)

    out = _run(populate_capital_proxies([okx, cap], cst="c", security_token="s"))

    assert out[0] is okx  # OKX row untouched (identity preserved)
    assert rec.seen == ["EURUSD"]  # fetch only called for capital row


def test_semaphore_bounds_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """With more capital rows than the concurrency cap, max simultaneous
    in-flight fetches must never exceed CAPITAL_PROXY_MAX_CONCURRENCY."""
    import polaris.venues.capital.market_proxy as mp

    n = CAPITAL_PROXY_MAX_CONCURRENCY * 3
    insts = [_capital_inst(symbol=f"EP{i}") for i in range(n)]
    # Every epic sleeps so tasks genuinely overlap and the cap is exercised.
    rec = _Recorder(delay_on={f"EP{i}": 0.02 for i in range(n)})
    monkeypatch.setattr(mp, "fetch_capital_chart_24h", rec)

    out = _run(populate_capital_proxies(insts, cst="c", security_token="s"))

    assert len(out) == n
    assert rec.max_in_flight <= CAPITAL_PROXY_MAX_CONCURRENCY
    # Sanity: concurrency actually happened (not silently serialized).
    assert rec.max_in_flight > 1


def test_per_instrument_failure_keeps_default_zeros(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One epic raising must not abort the batch; that row keeps its original
    (zero) metrics, the others get proxy values (degrade-never-halt per row)."""
    import polaris.venues.capital.market_proxy as mp

    good = _capital_inst(symbol="EURUSD", vol=0.0, depth=0.0)
    bad = _capital_inst(symbol="BADEP", vol=0.0, depth=0.0)
    rec = _Recorder(
        per_epic={"EURUSD": _candles(1.10, 1_000_000)},
        raise_on={"BADEP"},
    )
    monkeypatch.setattr(mp, "fetch_capital_chart_24h", rec)

    out = _run(populate_capital_proxies([good, bad], cst="c", security_token="s"))

    by_sym = {o.symbol: o for o in out}
    assert by_sym["EURUSD"].vol_24h_usd > 0.0  # good row populated
    assert by_sym["BADEP"].vol_24h_usd == 0.0  # failed row stays at default zeros
    assert by_sym["BADEP"].depth_10bps_usd == 0.0


def test_total_timeout_degrades_without_halt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the whole batch can't finish within the total-timeout cap, the call
    returns ALL input rows (unprocessed ones keep default zeros) — startup must
    never block indefinitely on a slow Capital API."""
    import polaris.venues.capital.market_proxy as mp

    fast = _capital_inst(symbol="FAST", vol=0.0, depth=0.0)
    slow = _capital_inst(symbol="SLOW", vol=0.0, depth=0.0)
    rec = _Recorder(
        per_epic={"FAST": _candles(1.10, 1_000_000)},
        delay_on={"SLOW": 100.0},  # far longer than the total timeout
    )
    monkeypatch.setattr(mp, "fetch_capital_chart_24h", rec)
    # Shrink the total timeout so the test runs fast.
    monkeypatch.setattr(mp, "CAPITAL_PROXY_TOTAL_TIMEOUT_SEC", 0.1)

    out = _run(populate_capital_proxies([fast, slow], cst="c", security_token="s"))

    # All input rows returned, original order preserved, no exception raised.
    assert [o.symbol for o in out] == ["FAST", "SLOW"]
    by_sym = {o.symbol: o for o in out}
    assert by_sym["SLOW"].vol_24h_usd == 0.0  # unprocessed → default zeros


def test_total_timeout_constant_is_bounded() -> None:
    """Sanity: the startup cap is a finite, sane upper bound (degrade-never-halt
    backstop) and concurrency respects Capital's rate limits."""
    assert 0.0 < CAPITAL_PROXY_TOTAL_TIMEOUT_SEC <= 600.0
    assert 1 <= CAPITAL_PROXY_MAX_CONCURRENCY <= 20
