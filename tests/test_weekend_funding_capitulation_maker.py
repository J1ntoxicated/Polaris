"""weekend_funding_capitulation_maker — the 2nd weekend OKX edge (positioning-axis).

DEMO/PAPER only — virtual funds. AGGRESSIVE / flow_not_block. Research task
w0p4essz2 (portfolio[weekend_funding_capitulation_maker], build_priority P1): the
2nd genuine weekend OKX edge, ORTHOGONAL to the flush (positioning-axis vs
price-axis; +24-48h squeeze-unwind vs minutes). On a WEEKEND bar where a symbol's
perp funding is <= its own p10 (extreme-negative = crowded-short capitulation),
rest a post-only SPOT long bid and harvest the forward unwind drift.

  * funding = SIGNAL only; the trade is an OKX SPOT LONG (long-only compliant) —
    RawSignal.symbol is the tradeable spot symbol, never the perp.
  * fires ONLY on a Sat/Sun UTC bar (weekday bars never fire — weekend edge);
  * fires ONLY when funding_rate_symbol <= funding_rate_p10 AND funding < 0
    (extreme-neg crowded short). A missing funding / p10 -> no emit (neutral);
  * metadata declares maker_no_fill_cancel=True (no taker fallback — a missed
    passive bid = 0 realised cost), a REVERSION exit bucket (post-only passive
    entry mode), and bounded profit_target_r == 1.0 (the research-validated exit;
    let-run was OOS-negative = overfit and is BANNED). The -1.0R rail is the
    engine-owned TAKER floor.

Sample-limit caveat (documented in the strategy docstring): the edge rests on a
95-day single market period; the strategy ships behind the maker_fill_shadow
real-fee accumulation (cold-cell x1.0 neutral until trades accrue) before any
live size.
"""

from __future__ import annotations

from datetime import UTC, datetime

from polaris.scripts._production_tick import keep_on_bar_path
from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.base import AltDataView, BarView, MarketView
from polaris.strategies.weekend_funding_capitulation_maker import (
    PROFIT_TARGET_R,
    WeekendFundingCapitulationMakerStrategy,
)

STRATEGY_ID = "weekend_funding_capitulation_maker"


def _ts_on(weekday: int) -> int:
    """An epoch-seconds ts whose UTC weekday == ``weekday`` (Mon=0 .. Sun=6)."""
    # 2026-06-27 is a Saturday (weekday 5). Walk to the target weekday.
    sat = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    delta_days = (weekday - 5) % 7
    return int(sat.timestamp()) + delta_days * 86400


def _bars(n: int, *, weekday: int) -> list[BarView]:
    ts0 = _ts_on(weekday) - (n - 1) * 3600
    out: list[BarView] = []
    for i in range(n):
        c = 100.0
        out.append(
            BarView(ts=ts0 + i * 3600, open=c, high=c + 0.2, low=c - 0.2,
                    close=c, volume=1000.0)
        )
    return out


def _mv(
    bars: list[BarView],
    *,
    funding_symbol: float | None,
    funding_p10: float | None,
) -> MarketView:
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=4.0, atr_pct=0.02,
        altdata=AltDataView(
            funding_rate_symbol=funding_symbol,
            funding_rate_p10=funding_p10,
        ),
    )


# ── registration (3-touchpoint) ──────────────────────────────────────────────


def test_registered_three_touchpoints() -> None:
    assert STRATEGY_ID in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY[STRATEGY_ID] is WeekendFundingCapitulationMakerStrategy


# ── reachability (OKX spot is always on the bar fan-out) ──────────────────────


def test_reaches_bar_path_via_okx_spot() -> None:
    # OKX spot is unconditionally on the bar path -> generate_raw_signal is
    # reachable for any active OKX spot symbol (same dispatch as the flush
    # sibling). No new reachability wiring required.
    assert keep_on_bar_path(asset_class="spot", symbol="BTC-USDT") is True
    assert keep_on_bar_path(asset_class="crypto", symbol="ETH-USDT") is True


# ── metadata contract (maker + reversion + bounded +1R) ──────────────────────


def test_metadata_maker_reversion_bounded_contract() -> None:
    md = WeekendFundingCapitulationMakerStrategy.metadata
    assert md.venue == "okx"
    assert md.asset_class == "spot"
    # no-fill = CANCEL (the missed passive bid is a 0-cost skip, never a taker).
    assert md.maker_no_fill_cancel is True
    # REVERSION correlation group (post-only passive entry mode + bounded exit) —
    # carries the "mean_reversion" routing substring while naming the positioning
    # axis. The group conveys "positioning" but routes to the REVERSION bucket.
    assert "mean_reversion" in md.correlation_group_id
    assert "positioning" in md.correlation_group_id
    # bounded +1R harvest (the research-validated config; let-run is BANNED).
    assert md.profit_target_r == PROFIT_TARGET_R
    assert PROFIT_TARGET_R == 1.0


# ── trigger behaviour ─────────────────────────────────────────────────────────


def test_fires_on_weekend_extreme_negative_funding_long_only() -> None:
    bars = _bars(30, weekday=5)  # Saturday
    # funding -0.0025 <= p10 -0.0019 and < 0 -> crowded-short capitulation.
    mv = _mv(bars, funding_symbol=-0.0025, funding_p10=-0.0019)
    sig = WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"  # long-only; funding=signal, trade=spot long.
    assert sig.strategy_id == STRATEGY_ID
    assert sig.symbol == "BTC-USDT"  # tradeable SPOT symbol, never the perp.
    assert 0.0 < sig.strength <= 1.0
    assert sig.correlation_group == md_group()


def md_group() -> str:
    return WeekendFundingCapitulationMakerStrategy.metadata.correlation_group_id


def test_fires_on_sunday_too() -> None:
    bars = _bars(30, weekday=6)  # Sunday
    mv = _mv(bars, funding_symbol=-0.0030, funding_p10=-0.0019)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is not None


def test_no_fire_on_weekday_even_with_extreme_funding() -> None:
    for wd in range(0, 5):  # Mon..Fri
        bars = _bars(30, weekday=wd)
        mv = _mv(bars, funding_symbol=-0.0025, funding_p10=-0.0019)
        assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is None, (
            f"weekday {wd} must not fire — edge is weekend-only"
        )


def test_no_fire_when_funding_above_p10() -> None:
    # funding -0.0010 is ABOVE (less extreme than) p10 -0.0019 -> not crowded.
    bars = _bars(30, weekday=5)
    mv = _mv(bars, funding_symbol=-0.0010, funding_p10=-0.0019)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is None


def test_no_fire_when_funding_positive_even_if_below_p10() -> None:
    # A positive funding can never be a crowded-SHORT capitulation, even if a
    # degenerate p10 were >= it. Extreme-NEG gate (funding < 0) holds.
    bars = _bars(30, weekday=5)
    mv = _mv(bars, funding_symbol=0.0005, funding_p10=0.0010)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is None


def test_no_fire_when_funding_or_p10_missing() -> None:
    bars = _bars(30, weekday=5)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(
        _mv(bars, funding_symbol=None, funding_p10=-0.0019)
    ) is None
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(
        _mv(bars, funding_symbol=-0.0025, funding_p10=None)
    ) is None
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(
        _mv(bars, funding_symbol=None, funding_p10=None)
    ) is None


def test_no_fire_when_warmup_insufficient() -> None:
    bars = _bars(3, weekday=5)
    mv = _mv(bars, funding_symbol=-0.0025, funding_p10=-0.0019)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is None


# ---------------------------------------------------------------------------
# Session-gate boundary (P1 fix): Friday 21:00 UTC .. Sunday 24:00 UTC.
# Mirrors the flush sibling's fix — the 2026-07-08 VIRTUAL loosening un-gated
# all 7 weekdays outside the validated 95d/~13.5-weekend regime.
# ---------------------------------------------------------------------------


def _ts_at(weekday: int, hour: int) -> int:
    sat = datetime(2026, 6, 27, hour, 0, tzinfo=UTC)  # 2026-06-27 = Saturday
    delta_days = (weekday - 5) % 7
    return int(sat.timestamp()) + delta_days * 86400


def _bars_at(n: int, *, ts: int) -> list[BarView]:
    ts0 = ts - (n - 1) * 3600
    out: list[BarView] = []
    for i in range(n):
        c = 100.0
        out.append(
            BarView(ts=ts0 + i * 3600, open=c, high=c + 0.2, low=c - 0.2,
                    close=c, volume=1000.0)
        )
    return out


def test_no_fire_friday_before_2100_utc() -> None:
    bars = _bars_at(30, ts=_ts_at(4, 20))  # Fri 20:00 UTC
    mv = _mv(bars, funding_symbol=-0.0025, funding_p10=-0.0019)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is None


def test_fires_friday_at_2100_utc_session_start() -> None:
    bars = _bars_at(30, ts=_ts_at(4, 21))  # Fri 21:00 UTC
    mv = _mv(bars, funding_symbol=-0.0025, funding_p10=-0.0019)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is not None


def test_fires_sunday_late_2359_utc() -> None:
    bars = _bars_at(30, ts=_ts_at(6, 23))  # Sun 23:00 UTC
    mv = _mv(bars, funding_symbol=-0.0025, funding_p10=-0.0019)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is not None


def test_no_fire_monday_0000_utc_session_end() -> None:
    bars = _bars_at(30, ts=_ts_at(0, 0))  # Mon 00:00 UTC
    mv = _mv(bars, funding_symbol=-0.0025, funding_p10=-0.0019)
    assert WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is None


def test_session_boundary_unaffected_by_virtual_mode() -> None:
    """Not virtual_loosen'd — Monday noon never fires in EITHER mode."""
    import importlib
    import os

    prior = os.environ.get("POLARIS_VIRTUAL_ACCOUNT")
    try:
        os.environ["POLARIS_VIRTUAL_ACCOUNT"] = "1"
        import polaris.strategies.weekend_funding_capitulation_maker as mod
        mod = importlib.reload(mod)
        bars = _bars(30, weekday=0)  # Monday, noon fixture
        mv = _mv(bars, funding_symbol=-0.0025, funding_p10=-0.0019)
        assert mod.WeekendFundingCapitulationMakerStrategy().generate_raw_signal(mv) is None
    finally:
        if prior is None:
            os.environ.pop("POLARIS_VIRTUAL_ACCOUNT", None)
        else:
            os.environ["POLARIS_VIRTUAL_ACCOUNT"] = prior
        import polaris.strategies.weekend_funding_capitulation_maker as mod
        importlib.reload(mod)
