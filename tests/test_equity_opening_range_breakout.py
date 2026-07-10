"""equity_opening_range_breakout — Alpaca 15m calendar-anchored ORB.

DEMO/PAPER virtual sleeve, Wave 1.5 (§1 #5). Covers the OR-bar lookup, the
entry-window bound, the same-NY-slot volume confirm, the §0d liquidity gate,
warmup, and degrade-never-crash (missing OR bar / outside window / not RTH).
"""

from __future__ import annotations

import datetime as dt

from polaris.core.sessions.equity_session_gate import NY_TZ
from polaris.strategies import STRATEGY_REGISTRY, EquityOpeningRangeBreakoutStrategy
from polaris.strategies._virtual_loosen import virtual_loosen, virtual_mode_enabled
from polaris.strategies.base import BarView, MarketView
from polaris.strategies.equity_opening_range_breakout import (
    ENTRY_WINDOW_END_LOCAL_MINUTES,
    ENTRY_WINDOW_START_LOCAL_MINUTES,
    VOLUME_MULT,
    WARMUP_BARS,
)

SLOTS_PER_SESSION = 26  # 09:30..15:45 ET, 15m each
ENTRY_SLOT_IDX = 2  # 10:00 ET — inside [09:45, 11:30)
PRIOR_VOLUME = 15_000.0
OR_HIGH = 100.5


def _ny_ts(y: int, m: int, d: int, hh: int, mm: int) -> int:
    return int(dt.datetime(y, m, d, hh, mm, tzinfo=NY_TZ).timestamp())


def _slot_ts(date: dt.date, idx: int) -> int:
    total_min = 9 * 60 + 30 + 15 * idx
    return _ny_ts(date.year, date.month, date.day, total_min // 60, total_min % 60)


def _session_dates(start: dt.date, n: int) -> list[dt.date]:
    dates: list[dt.date] = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:  # Mon-Fri
            dates.append(d)
        d += dt.timedelta(days=1)
    return dates


def _full_session_bars(date: dt.date, *, volume: float = PRIOR_VOLUME) -> list[BarView]:
    out: list[BarView] = []
    for idx in range(SLOTS_PER_SESSION):
        ts = _slot_ts(date, idx)
        high = OR_HIGH if idx == 0 else 100.3
        low = 99.5 if idx == 0 else 99.7
        out.append(
            BarView(ts=ts, open=100.0, high=high, low=low, close=100.0,
                    volume=volume)
        )
    return out


def _orb_fixture(
    *, n_prior_sessions: int = 22, entry_close: float = 101.0,
    entry_volume: float = 40_000.0, include_or_bar: bool = True,
    entry_slot_idx: int = ENTRY_SLOT_IDX,
) -> list[BarView]:
    dates = _session_dates(dt.date(2024, 1, 8), n_prior_sessions + 1)
    bars: list[BarView] = []
    for date in dates[:-1]:
        bars.extend(_full_session_bars(date))
    entry_date = dates[-1]
    for idx in range(entry_slot_idx + 1):
        if idx == 0 and not include_or_bar:
            continue
        ts = _slot_ts(entry_date, idx)
        if idx == entry_slot_idx:
            bars.append(
                BarView(ts=ts, open=100.4, high=entry_close + 0.2, low=100.3,
                        close=entry_close, volume=entry_volume)
            )
        else:
            high = OR_HIGH if idx == 0 else 100.2
            bars.append(
                BarView(ts=ts, open=100.0, high=high, low=99.5, close=100.0,
                        volume=PRIOR_VOLUME)
            )
    return bars


def _mv(bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol="AAPL", venue="alpaca", timeframe="15m",
        bars=bars, last_price=bars[-1].close if bars else 0.0, spread_bps=2.0,
    )


# ---------------------------------------------------------------------------
# A — entry / no-entry boundary
# ---------------------------------------------------------------------------


def test_fires_on_or_breakout_with_volume_confirm() -> None:
    bars = _orb_fixture()
    assert len(bars) >= WARMUP_BARS
    sig = EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "equity_opening_range_breakout"
    assert 0.0 < sig.strength <= 1.0
    assert float(sig.tags["or_high"]) == OR_HIGH


def test_no_signal_when_close_does_not_clear_or_high() -> None:
    bars = _orb_fixture(entry_close=100.4)  # <= OR_HIGH(100.5)
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is None


def test_no_signal_when_volume_insufficient() -> None:
    # 25,000 < VOLUME_MULT(2.0) * median(15,000) = 30,000.
    bars = _orb_fixture(entry_volume=25_000.0)
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is None


def test_volume_at_exact_threshold_fires() -> None:
    bars = _orb_fixture(entry_volume=VOLUME_MULT * PRIOR_VOLUME)
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is not None


# ---------------------------------------------------------------------------
# B — OR bar existence (halt / holiday / partial-open degrade)
# ---------------------------------------------------------------------------


def test_no_signal_when_or_bar_missing() -> None:
    bars = _orb_fixture(include_or_bar=False)
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# C — entry window bound
# ---------------------------------------------------------------------------


def test_no_signal_at_or_bar_itself_before_window() -> None:
    # last bar IS the OR bar (09:30 ET, minute 570) — before the 09:45 window open.
    bars = _orb_fixture(entry_slot_idx=0)
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is None


def test_no_signal_at_or_after_window_end() -> None:
    # idx=8 -> 11:30 ET (minute 690) == ENTRY_WINDOW_END, half-open excludes it.
    end_idx = (ENTRY_WINDOW_END_LOCAL_MINUTES - (9 * 60 + 30)) // 15
    bars = _orb_fixture(entry_slot_idx=end_idx, entry_close=101.0)
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is None


def test_fires_at_window_start_boundary() -> None:
    # idx=1 -> 09:45 ET (minute 585) == ENTRY_WINDOW_START, half-open includes it.
    start_idx = (ENTRY_WINDOW_START_LOCAL_MINUTES - (9 * 60 + 30)) // 15
    bars = _orb_fixture(entry_slot_idx=start_idx, entry_close=101.0)
    sig = EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars))
    assert sig is not None


# ---------------------------------------------------------------------------
# D — not-RTH gate
# ---------------------------------------------------------------------------


def test_no_signal_when_not_rth() -> None:
    bars = _orb_fixture()
    last = bars[-1]
    local = dt.datetime.fromtimestamp(last.ts, tz=NY_TZ)
    after_hours_ts = _ny_ts(local.year, local.month, local.day, 17, 0)
    bars[-1] = BarView(ts=after_hours_ts, open=last.open, high=last.high,
                       low=last.low, close=last.close, volume=last.volume)
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# E — §0d liquidity gate
# ---------------------------------------------------------------------------


def test_no_signal_when_illiquid() -> None:
    bars = _orb_fixture()
    illiquid = [
        BarView(ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close,
                volume=1.0)
        for b in bars
    ]
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(illiquid)) is None


# ---------------------------------------------------------------------------
# F — warmup
# ---------------------------------------------------------------------------


def test_no_signal_before_warmup() -> None:
    bars = _orb_fixture(n_prior_sessions=5)  # far fewer than 560 total
    assert len(bars) < WARMUP_BARS
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# G — degrade-never-crash
# ---------------------------------------------------------------------------


def test_degrade_never_crash_on_empty_bars() -> None:
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv([])) is None


def test_degrade_never_crash_on_short_bars() -> None:
    bars = _full_session_bars(dt.date(2024, 1, 8))[:5]
    assert EquityOpeningRangeBreakoutStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# H — metadata (spec §1 table row #5, verbatim)
# ---------------------------------------------------------------------------


def test_metadata_matches_spec_table() -> None:
    md = EquityOpeningRangeBreakoutStrategy.metadata
    assert md.strategy_id == "equity_opening_range_breakout"
    assert md.timeframe == "15m"
    assert md.warmup_bars == 560
    assert md.max_positions == 3
    assert md.gross_cap == 0.09
    assert md.per_symbol_cap == 0.03
    assert md.expected_holding_bars == 8
    assert md.asset_class == "equity"
    assert md.venue == "alpaca"
    assert md.product_class == "equity"
    assert md.correlation_group_id == "equity_orb_open"
    assert md.hold_overnight is False
    assert md.profit_target_r is None
    assert md.loss_cooldown_bars == 4


def test_dispatch_eligible_is_virtual_only() -> None:
    md = EquityOpeningRangeBreakoutStrategy.metadata
    assert md.dispatch_eligible == virtual_loosen(True, False)
    assert md.dispatch_eligible == virtual_mode_enabled()


def test_registered_in_registry() -> None:
    assert (
        STRATEGY_REGISTRY["equity_opening_range_breakout"]
        is EquityOpeningRangeBreakoutStrategy
    )
