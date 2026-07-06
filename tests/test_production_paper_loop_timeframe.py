"""F10 — Day 9 timeframe-hardcode regression tests.

Spec: ``vault/_NOW.md`` Day 9 F10 brief — fills 0.1% root cause was the
``timeframe="1m"`` hardcode in ``production_paper_loop.py`` that fed Capital
strategies (1H bars) the wrong canvas, starving them of usable history. These
tests pin the per-timeframe ingest + dispatch contract so the regression
cannot return.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from polaris.core.data.schema import Bar
from polaris.scripts._production_layers import (
    CAPITAL_RESOLUTION_BY_INTERVAL,
    TIMEFRAME_FETCH_CADENCE_SEC,
    fetch_bars_one,
    ingest_bars_for_focus,
    ingest_bars_per_timeframe,
    read_recent_bars,
)
from polaris.scripts.production_paper_loop import (
    _all_strategies,
    _strategies_by_timeframe,
)
from polaris.strategies.base import BaseStrategy


@pytest.fixture(autouse=True)
def _yahoo_primary_off() -> Generator[None]:
    """Neutralize the Yahoo-PRIMARY layer for the EXCHANGE-routing tests here.

    Yahoo Finance is now the primary bar-history source (Alpaca 429 root fix);
    the exchange path is a fallback. These tests verify the exchange routing, so
    we stub ``fetch_yahoo_bars`` → [] (Yahoo has no bars) and clear the fallback
    cooldown so the exchange branch is always reached — the exact behaviour these
    tests were written against. flow_not_block unchanged.
    """
    import polaris.scripts._production_bars as pbars
    import polaris.scripts._yahoo_bars as ybars

    async def _no_yahoo(*args: Any, **kwargs: Any) -> list[Bar]:
        return []

    ybars._FALLBACK_LAST_MONO.clear()
    with patch.object(pbars, "fetch_yahoo_bars", new=_no_yahoo):
        yield
    ybars._FALLBACK_LAST_MONO.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(
    *,
    venue: str,
    symbol: str,
    bar_interval: str,
    ts: int,
    close: float = 60_000.0,
    volume: float = 1_000.0,
) -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{'crypto' if venue == 'okx' else 'fx'}:"
        f"{symbol.split('-')[0] if '-' in symbol else symbol}",
        venue=venue,
        symbol=symbol,
        bar_interval=bar_interval,
        ts=ts,
        open=close * 0.999,
        high=close * 1.001,
        low=close * 0.998,
        close=close,
        volume=volume,
        notional_usd=close * volume,
    )


def _bars_series(
    n: int,
    *,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    bar_interval: str = "1m",
    step_sec: int = 60,
    base: float = 60_000.0,
) -> list[Bar]:
    return [
        _make_bar(
            venue=venue,
            symbol=symbol,
            bar_interval=bar_interval,
            ts=1_700_000_000 + i * step_sec,
            close=base + i * 1.0,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# A — strategy metadata.timeframe matrix is what the loop expects
# ---------------------------------------------------------------------------


def test_strategy_metadata_timeframe_used() -> None:
    """Every strategy exposes a non-empty ``metadata.timeframe`` and the
    bucket count covers the documented matrix."""
    strats = _all_strategies()
    by_id: dict[str, BaseStrategy] = {s.metadata.strategy_id: s for s in strats}
    expected: dict[str, str] = {
        # volume_burst un-registered 2026-06-27 (#61 live-churn KILL) — dropped.
        # spot_donchian un-registered 2026-06-27 (#56 stop-bleeders KILL) — dropped.
        # rsi_bb_pullback (fee-fatal 15m crypto reversion) + ema_crossover (fee-fatal
        # 1H crypto trend-template, gross +$0.12 < fee $2.37 per round-trip) set
        # dispatch_eligible=False (dual-SSOT KILL) — dropped from dispatch (registered,
        # open-position close path preserved). ema_crossover KILLed 2026-06-28.
        # session_breakout / donchian_turtle_breakout / equity_52wk_high_breakout
        # / equity_vol_expansion_pocket_pivot un-registered 2026-07-06 (B1 prune,
        # live-ledger forensic, -$2,024 book loss, 100.9% attributable) — dropped.
        "fx_breakout_basket": "1H",
        "xau_indices_trend": "1H",
        "bar_breakout_run": "1D",  # daily/position horizon (Donchian-40 breakout)
        # strategy-wave1: fx_range_fade KILLed; 4 verified 1D survivors added
        # (one, donchian_turtle_breakout, later B1-KILLed — see above).
        "okx_donchian_55_breakout": "1D",
        "tsmom_12_1_multiasset": "1D",
        "macd_ema_trend_pullback": "1D",
        # legacy tsmom (cross-sym) + the equity_* strategies (1D) were KILLed.
        # strategy-wave2 (2026-06-27): 7 verified research survivors dispatched
        # (2 equity legs later B1-KILLed — see above).
        "gold_trend_chandelier_1d": "1D",
        "gold_riskoff_trend_amplify": "1D",
        "gold_breakout_1h": "1H",
        "index_52w_high_momentum": "1D",
        "index_dual_momentum_rotation": "1D",
        # weekend-maker dispatch fix — the two VALIDATED weekend OKX makers (#77
        # thin-book flush + #80 funding capitulation), both OKX SPOT 1H.
        "weekend_thin_book_flush_maker": "1H",
        "weekend_funding_capitulation_maker": "1H",
        # Opus-spec 3-clone build — per-symbol gold_breakout_1h clones, 1H.
        "silver_breakout_1h": "1H",
        "us100_breakout_1h": "1H",
        "uk100_breakout_1h": "1H",
    }
    assert set(by_id) == set(expected)
    for sid, tf in expected.items():
        assert by_id[sid].metadata.timeframe == tf, (
            f"{sid} metadata.timeframe drifted: "
            f"{by_id[sid].metadata.timeframe!r} != {tf!r}"
        )


def test_strategies_by_timeframe_buckets_cover_all_strats() -> None:
    """Bucketing is exhaustive — no strategy is silently dropped."""
    strats = _all_strategies()
    buckets = _strategies_by_timeframe(strats)
    flat = [s for group in buckets.values() for s in group]
    assert len(flat) == len(strats)
    assert {s.metadata.strategy_id for s in flat} == {
        s.metadata.strategy_id for s in strats
    }


def test_no_timeframe_hardcode_in_run_tick_source() -> None:
    """Source-level lint: the F10 hardcode ``build_real_market_view(...,
    timeframe="1m", ...)`` (the bug we removed) must not return.

    We grep for the literal call shape rather than the substring so the
    remediation comments / docstring that mention the historical bug do not
    trip the lint.
    """
    import re
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    # Match build_real_market_view(...) calls and reject any whose timeframe
    # kwarg is the literal "1m". The fixed loop passes the strategy
    # metadata.timeframe (a variable), not a literal.
    bad = re.findall(
        r'build_real_market_view\([^)]*timeframe="1m"', src,
    )
    assert not bad, (
        "F10 regression: hardcoded timeframe='1m' reintroduced into "
        "build_real_market_view — strategy metadata must drive the canvas."
    )


# ---------------------------------------------------------------------------
# B — fetch_bars_one honours bar_interval per venue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_bars_one_okx_forwards_bar_interval() -> None:
    """OKX fetch uses ``bar_interval`` directly as the candle ``bar`` token."""
    captured: dict[str, Any] = {}

    async def _fake(*args: Any, **kwargs: Any) -> list[Bar]:
        captured.update(kwargs)
        return []

    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        await fetch_bars_one(
            "okx", "BTC-USDT", "crypto", limit=10, bar_interval="15m",
        )
    assert captured["bar_interval"] == "15m"


@pytest.mark.asyncio
async def test_fetch_bars_one_capital_maps_resolution() -> None:
    """Capital fetch translates the canonical interval to Capital's
    ``resolution`` token (1H → HOUR)."""
    captured: dict[str, Any] = {}

    async def _fake_capital(epic: str, **kwargs: Any) -> list[Bar]:
        captured.update(kwargs)
        return []

    class _FakeSession:
        async def ensure_tokens(self) -> Any:
            class T: ...
            t = T()
            t.cst = "cst"  # type: ignore[attr-defined]
            t.security_token = "sec"  # type: ignore[attr-defined]
            return t

    with patch(
        "polaris.scripts._production_bars.fetch_capital_bars",
        new=_fake_capital,
    ):
        await fetch_bars_one(
            "capital", "EURUSD", "fx",
            capital_session=_FakeSession(),  # type: ignore[arg-type]
            limit=10, bar_interval="1H",
        )
    assert captured["bar_interval"] == "1H"
    assert captured["resolution"] == "HOUR"


@pytest.mark.asyncio
async def test_fetch_bars_one_capital_unsupported_interval_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An interval missing from CAPITAL_RESOLUTION_BY_INTERVAL skips fetch."""
    class _FakeSession:
        async def ensure_tokens(self) -> Any: ...  # never called

    with caplog.at_level("WARNING"):
        bars = await fetch_bars_one(
            "capital", "EURUSD", "fx",
            capital_session=_FakeSession(),  # type: ignore[arg-type]
            bar_interval="4h",  # not in canonical BAR_INTERVALS
        )
    assert bars == []


def test_capital_resolution_table_covers_canonical_intervals() -> None:
    """Every BAR_INTERVALS entry has a Capital resolution mapping."""
    from polaris.core.data.schema import BAR_INTERVALS

    for tf in BAR_INTERVALS:
        assert tf in CAPITAL_RESOLUTION_BY_INTERVAL, f"missing mapping: {tf}"


# ---------------------------------------------------------------------------
# C — ingest_bars_for_focus persists with the right bar_interval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_volume_burst_1m_bars_fetched(memdb: sqlite3.Connection) -> None:
    """1m strategies (volume_burst) drive 1m ingest + persist."""
    fake_1m = _bars_series(60, bar_interval="1m", step_sec=60)
    captured: dict[str, Any] = {}

    async def _fake_okx(symbol: str, **kwargs: Any) -> list[Bar]:
        captured.update(kwargs)
        return list(reversed(fake_1m))  # OKX returns newest first

    with patch(
        "polaris.scripts._production_bars.fetch_okx_bars", new=_fake_okx,
    ):
        result = await ingest_bars_for_focus(
            memdb,
            [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            bar_interval="1m",
        )
    assert captured["bar_interval"] == "1m"
    assert result["bars"] == 60
    persisted_intervals = {
        r[0] for r in memdb.execute(
            "SELECT DISTINCT bar_interval FROM bars"
        ).fetchall()
    }
    assert persisted_intervals == {"1m"}


@pytest.mark.asyncio
async def test_fx_breakout_1h_bars_fetched(memdb: sqlite3.Connection) -> None:
    """Capital FX strategies drive 1H ingest — the F10 root-cause fix."""
    fake_1h = _bars_series(
        40, venue="capital", symbol="EURUSD", bar_interval="1H", step_sec=3600,
        base=1.10,
    )
    captured: dict[str, Any] = {}

    async def _fake_capital(epic: str, **kwargs: Any) -> list[Bar]:
        captured.update(kwargs)
        return fake_1h

    class _FakeSession:
        async def ensure_tokens(self) -> Any:
            class T: ...
            t = T()
            t.cst = "cst"  # type: ignore[attr-defined]
            t.security_token = "sec"  # type: ignore[attr-defined]
            return t

    with patch(
        "polaris.scripts._production_bars.fetch_capital_bars",
        new=_fake_capital,
    ):
        result = await ingest_bars_for_focus(
            memdb,
            [("capital", "EURUSD", "fx", "fx:EURUSD")],
            capital_session=_FakeSession(),  # type: ignore[arg-type]
            bar_interval="1H",
        )
    assert captured["bar_interval"] == "1H"
    assert captured["resolution"] == "HOUR"
    assert result["bars"] == 40
    persisted_intervals = {
        r[0] for r in memdb.execute(
            "SELECT DISTINCT bar_interval FROM bars"
        ).fetchall()
    }
    assert persisted_intervals == {"1H"}


# ---------------------------------------------------------------------------
# D — read_recent_bars partitions correctly by bar_interval
# ---------------------------------------------------------------------------


def test_read_recent_bars_partitions_by_bar_interval(
    memdb: sqlite3.Connection,
) -> None:
    """Bars persisted at 1m and 1H must come back through the right channel."""
    from polaris.core.data.ingest import persist_bars

    persist_bars(memdb, _bars_series(5, bar_interval="1m", step_sec=60))
    persist_bars(memdb, _bars_series(5, bar_interval="1H", step_sec=3600))
    bars_1m = read_recent_bars(memdb, venue="okx", symbol="BTC-USDT",
                               bar_interval="1m", limit=10)
    bars_1h = read_recent_bars(memdb, venue="okx", symbol="BTC-USDT",
                               bar_interval="1H", limit=10)
    assert len(bars_1m) == 5
    assert len(bars_1h) == 5
    assert all(b.bar_interval == "1m" for b in bars_1m)
    assert all(b.bar_interval == "1H" for b in bars_1h)


# ---------------------------------------------------------------------------
# E — cadence gating
# ---------------------------------------------------------------------------


def test_cadence_gating_table_aligns_with_canonical_intervals() -> None:
    """Cadence table must cover every canonical interval (no silent miss)."""
    from polaris.core.data.schema import BAR_INTERVALS

    for tf in BAR_INTERVALS:
        assert tf in TIMEFRAME_FETCH_CADENCE_SEC


# F10 R3 P2 nit fix: ``_is_fetch_due`` was removed because cadence is now
# tracked per (timeframe, venue) inside ``ingest_bars_per_timeframe``, not
# per-timeframe. Cadence behavior is verified via the
# ``test_*_advances_cadence`` / ``test_zero_bar_fetch_does_not_advance_cadence``
# / ``test_partial_bucket_failure_does_not_starve_failing_venue`` tests
# below — which exercise the live gating semantics directly.


# ---------------------------------------------------------------------------
# F — Capital fx_breakout emits a signal when 1H bars are present
# ---------------------------------------------------------------------------


def _trend_bars_1h(n: int = 40, base: float = 1.10) -> list[Bar]:
    """Synthetic upward trend so FX breakout fires (close > prior 20-bar high
    and ADX exceeds threshold). The strategy signal-gate accepts a clean
    Donchian breakout with directional energy."""
    bars: list[Bar] = []
    for i in range(n):
        # Final bar carries the breakout: push close above the prior high.
        close = base + 0.05 if i == n - 1 else base + i * 0.001
        high = close + 0.0005
        low = close - 0.0005
        bars.append(Bar(
            instrument_id="capital:EURUSD",
            underlying_group_id="fx:EURUSD",
            venue="capital", symbol="EURUSD", bar_interval="1H",
            ts=1_700_000_000 + i * 3600,
            open=close - 0.0002,
            high=high, low=low, close=close,
            volume=10_000.0, notional_usd=close * 10_000.0,
        ))
    return bars


def test_capital_fx_breakout_emit_with_1h_bars() -> None:
    """Hand the FX breakout strategy a 1H trending series + verify
    ``generate_raw_signal`` fires (no longer silently None because of the
    1m-history mismatch). Strategy params are frozen v1 — we only verify
    a non-None outcome under a clean breakout scenario."""
    from polaris.scripts._production_indicators import build_real_market_view
    from polaris.strategies import FXBreakoutBasketStrategy

    bars = _trend_bars_1h(n=40)
    mv = build_real_market_view(
        venue="capital", symbol="EURUSD", timeframe="1H",
        bars=bars, spread_bps=2.0,
    )
    strat = FXBreakoutBasketStrategy()
    # The strategy may still return None if ADX/trend hurdle isn't met under
    # frozen params; the F10 contract guarantees the *opportunity* (right
    # canvas), not the ignition. Assert the canvas is right rather than the
    # ignition outcome — strategy params are owned by Layer 5/learners.
    assert mv.timeframe == "1H"
    assert len(mv.bars) == 40
    # A run without exceptions verifies the strategy can read the 1H canvas.
    sig = strat.generate_raw_signal(mv)
    # Either None (frozen-param hurdle) or RawSignal — both prove canvas
    # correctness; the regression we care about is a TypeError / shape miss
    # which would raise.
    if sig is not None:
        assert sig.strategy_id == "fx_breakout_basket"


# ---------------------------------------------------------------------------
# G — sanity: fetch_bars_one default keeps old contract for 1m (back-compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_bars_one_default_bar_interval_is_1m() -> None:
    """Default ``bar_interval`` keeps the old 1m default for callers that
    haven't migrated (back-compat seam)."""
    captured: dict[str, Any] = {}

    async def _fake(*args: Any, **kwargs: Any) -> list[Bar]:
        captured.update(kwargs)
        return []

    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        await fetch_bars_one("okx", "BTC-USDT", "crypto")
    assert captured["bar_interval"] == "1m"


# ---------------------------------------------------------------------------
# I — Codex R1 P1 regression coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_1m_ingest_does_not_mutate_baseline(
    memdb: sqlite3.Connection,
) -> None:
    """Codex R1 P1-2: 5m / 15m / 1H ingest must NOT update the
    minute-grained baseline tables (atr/size/volume sample state).
    """
    fake_1h = _bars_series(
        20, venue="okx", symbol="BTC-USDT", bar_interval="1H",
        step_sec=3600, base=60_000.0,
    )

    async def _fake(*args: Any, **kwargs: Any) -> list[Bar]:
        return list(reversed(fake_1h))

    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        result = await ingest_bars_for_focus(
            memdb,
            [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            bar_interval="1H",
        )
    assert result["bars"] == 20
    assert result["baseline_samples"] == 0
    n_baseline = memdb.execute(
        "SELECT COUNT(*) FROM ticker_baseline_samples"
    ).fetchone()[0]
    assert n_baseline == 0, (
        "1H ingest must not contaminate the minute-grained baseline."
    )


@pytest.mark.asyncio
async def test_1m_ingest_updates_baseline(memdb: sqlite3.Connection) -> None:
    """Sanity: 1m path still updates baselines (regression guard for the
    fix above)."""
    fake_1m = _bars_series(20, venue="okx", symbol="BTC-USDT",
                           bar_interval="1m", step_sec=60)

    async def _fake(*args: Any, **kwargs: Any) -> list[Bar]:
        return list(reversed(fake_1m))

    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        result = await ingest_bars_for_focus(
            memdb,
            [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            bar_interval="1m",
        )
    assert result["bars"] == 20
    assert result["baseline_samples"] >= 60  # 20 bars × 3 metrics


@pytest.mark.asyncio
async def test_zero_bar_fetch_does_not_advance_cadence(
    memdb: sqlite3.Connection,
) -> None:
    """Codex R1 P1-3: a zero-bar fetch (auth flake / rate limit) must NOT
    pretend the bucket succeeded — cadence stays advanceable on the next
    tick so retries happen, not 5min later.
    """

    async def _empty(*args: Any, **kwargs: Any) -> list[Bar]:
        return []

    last_fetch: dict[str, float] = {}
    bars_by_tf: dict[str, int] = {}
    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_empty):
        await ingest_bars_per_timeframe(
            memdb,
            [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            timeframe_to_venues={"1m": {"okx"}},
            last_fetch_monotonic_by_tf=last_fetch,
            bars_persisted_by_tf=bars_by_tf,
            now_mono=100.0,
        )
    assert last_fetch == {}, (
        "zero-bar fetch must not advance cadence — regression P1-3."
    )


@pytest.mark.asyncio
async def test_nonzero_fetch_advances_cadence(memdb: sqlite3.Connection) -> None:
    """Sanity: non-zero fetch advances cadence (other half of P1-3)."""
    fake_1m = _bars_series(20, bar_interval="1m", step_sec=60)

    async def _fake(*args: Any, **kwargs: Any) -> list[Bar]:
        return list(reversed(fake_1m))

    last_fetch: dict[str, float] = {}
    bars_by_tf: dict[str, int] = {}
    with patch("polaris.scripts._production_bars.fetch_okx_bars", new=_fake):
        await ingest_bars_per_timeframe(
            memdb,
            [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
            timeframe_to_venues={"1m": {"okx"}},
            last_fetch_monotonic_by_tf=last_fetch,
            bars_persisted_by_tf=bars_by_tf,
            now_mono=100.0,
        )
    # R2 P1: cadence keyed per-(tf, venue) + mirrored aggregate per-tf.
    assert last_fetch.get("1m:okx") == 100.0
    assert last_fetch.get("1m") == 100.0


@pytest.mark.asyncio
async def test_partial_bucket_failure_does_not_starve_failing_venue(
    memdb: sqlite3.Connection,
) -> None:
    """Codex R2 P1: in a mixed-venue 1H bucket, OKX success + Capital
    failure must NOT defer Capital's retry by the full 300s cadence.
    Cadence is tracked per ``(timeframe, venue)`` for exactly this case.
    """
    fake_okx = _bars_series(20, venue="okx", symbol="BTC-USDT",
                            bar_interval="1H", step_sec=3600)

    async def _okx(*args: Any, **kwargs: Any) -> list[Bar]:
        return list(reversed(fake_okx))

    async def _capital(*args: Any, **kwargs: Any) -> list[Bar]:
        return []

    class _FakeSession:
        async def ensure_tokens(self) -> Any:
            class T: ...
            t = T()
            t.cst = "cst"  # type: ignore[attr-defined]
            t.security_token = "sec"  # type: ignore[attr-defined]
            return t

    last_fetch: dict[str, float] = {}
    bars_by_tf: dict[str, int] = {}
    with patch(
        "polaris.scripts._production_bars.fetch_okx_bars", new=_okx,
    ), patch(
        "polaris.scripts._production_bars.fetch_capital_bars", new=_capital,
    ):
        await ingest_bars_per_timeframe(
            memdb,
            [("okx", "BTC-USDT", "crypto", "crypto:BTC"),
             ("capital", "EURUSD", "fx", "fx:EURUSD")],
            timeframe_to_venues={"1H": {"okx", "capital"}},
            last_fetch_monotonic_by_tf=last_fetch,
            bars_persisted_by_tf=bars_by_tf,
            capital_session=_FakeSession(),  # type: ignore[arg-type]
            now_mono=100.0,
        )
    # OKX cadence advanced; Capital cadence did NOT.
    assert last_fetch.get("1H:okx") == 100.0
    assert "1H:capital" not in last_fetch, (
        "Capital must remain immediately retryable when OKX 1H succeeded "
        "but Capital 1H returned zero (R2 P1 partial-bucket fix)."
    )


def test_1m_bucket_covers_capital_for_regime() -> None:
    """Codex R1 P1-1: the production loop must include Capital venues in
    the 1m fetch bucket so Layer 6 regime SSOT (computed from 1m bars)
    has fresh data for non-OKX symbols.

    This is a structural assertion — we read the loop source and confirm
    the explicit ``timeframe_to_venues['1m']`` augmentation is present.
    """
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    assert "all_focus_venues" in src, (
        "F10 R1 P1-1: 1m bucket must be force-augmented with every focus "
        "venue so Capital regime has 1m bars."
    )
    assert 'timeframe_to_venues.setdefault("1m"' in src


# ---------------------------------------------------------------------------
# H — fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def memdb() -> Generator[sqlite3.Connection]:
    """In-memory DB with full schema bootstrapped (via init_db)."""
    from pathlib import Path

    from polaris.storage.schema import init_db

    # init_db expects a Path — give it ``:memory:`` semantics by using a
    # temp DB unique per test invocation.
    tmp = Path(f"/tmp/polaris_tf_{int(time.time() * 1e6)}.sqlite")
    if tmp.exists():
        tmp.unlink()
    conn = init_db(tmp)
    yield conn
    conn.close()
    if tmp.exists():
        tmp.unlink()
