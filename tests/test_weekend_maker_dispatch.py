"""weekend-maker dispatch wiring — the silent-INERT fix (validated makers only).

DEMO/PAPER only — virtual funds. AGGRESSIVE / flow_not_block: this RESTORES a dead
flow, it does NOT block one. The two VALIDATED weekend OKX makers
(``weekend_thin_book_flush_maker`` #77, +73 bps real-fee;
``weekend_funding_capitulation_maker`` #80, +0.83R median, shadow-first) were present
in ``STRATEGY_REGISTRY`` but ABSENT from the
``_all_strategies()`` live bar-dispatch literal — so ``generate_raw_signal`` was NEVER
invoked on them (silent INERT: no-emit count 0 because the call itself never happened).
This is the ``feedback_verify_firing_after_build`` "등록 ≠ 발화" pattern.

The fix adds EXACTLY those two validated makers to the dispatch literal. The three
registered-but-UNvalidated strategies (``supertrend`` / ``connors_rsi2`` /
``cci_reversion`` — design rationale only, no OOS / fee-hurdle evidence) MUST stay out
of dispatch (unvalidated live = churn risk). Shadow / maker economics
(``maker_no_fill_cancel``, the REVERSION post-only entry mode) are keyed off
``STRATEGY_REGISTRY`` + metadata DOWNSTREAM of the emit, so they activate automatically
once the signal flows — no extra wiring (asserted via the maker no-fill helper).
"""

from __future__ import annotations

from datetime import UTC, datetime

from polaris.scripts._production_tick import _all_strategies, keep_on_bar_path
from polaris.scripts._run_signal_helpers import _bar_maker_no_fill
from polaris.strategies.base import AltDataView, BarView, BaseStrategy, MarketView
from polaris.strategies.weekend_funding_capitulation_maker import (
    WeekendFundingCapitulationMakerStrategy,
)
from polaris.strategies.weekend_thin_book_flush_maker import (
    WeekendThinBookFlushMakerStrategy,
)

THIN_BOOK_ID = "weekend_thin_book_flush_maker"
FUNDING_ID = "weekend_funding_capitulation_maker"
# Registered-but-unvalidated — must stay OUT of dispatch (churn risk).
UNVALIDATED_IDS = frozenset({"supertrend", "connors_rsi2", "cci_reversion"})


def _dispatch_ids() -> set[str]:
    return {s.metadata.strategy_id for s in _all_strategies()}


# ── the fix: validated weekend makers ARE dispatched ─────────────────────────


def test_thin_book_maker_in_dispatch() -> None:
    assert THIN_BOOK_ID in _dispatch_ids()


def test_funding_maker_in_dispatch() -> None:
    assert FUNDING_ID in _dispatch_ids()


def test_dispatch_instantiates_correct_classes() -> None:
    by_id = {s.metadata.strategy_id: s for s in _all_strategies()}
    assert isinstance(by_id[THIN_BOOK_ID], WeekendThinBookFlushMakerStrategy)
    assert isinstance(by_id[FUNDING_ID], WeekendFundingCapitulationMakerStrategy)


# ── the guard: unvalidated strategies stay OUT of dispatch ───────────────────


def test_unvalidated_strategies_absent_from_dispatch() -> None:
    ids = _dispatch_ids()
    for sid in UNVALIDATED_IDS:
        assert sid not in ids, (
            f"{sid} is unvalidated (design rationale only, no OOS/fee evidence) — "
            "must NOT be woken live (churn risk)"
        )


# ── shadow / maker economics preserved automatically (downstream of emit) ─────


def test_thin_book_maker_no_fill_resolves_to_cancel() -> None:
    # maker_no_fill_cancel=True ⇒ a post-only no-fill SKIPs (0-cost miss), NOT a
    # forced taker. Keyed off STRATEGY_REGISTRY + metadata, so dispatch wiring
    # alone restores the maker behaviour — no extra plumbing.
    assert _bar_maker_no_fill(THIN_BOOK_ID) == "cancel"


def test_funding_maker_no_fill_resolves_to_cancel() -> None:
    # The shadow-first funding maker resolves the SAME way — its shadow-first
    # measurement (maker_fill_shadow / entry_admission) is downstream of the
    # emit, so it begins only once dispatch reaches its post-only entry.
    assert _bar_maker_no_fill(FUNDING_ID) == "cancel"


# ── dispatch actually reaches generate_raw_signal (firing path) ──────────────


def _ts_on(weekday: int) -> int:
    """Epoch-seconds ts whose UTC weekday == ``weekday`` (Mon=0 .. Sun=6)."""
    sat = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)  # 2026-06-27 is a Saturday (5).
    return int(sat.timestamp()) + ((weekday - 5) % 7) * 86400


def _bars(n: int, *, weekday: int, flush: bool) -> list[BarView]:
    ts0 = _ts_on(weekday) - (n - 1) * 3600
    out: list[BarView] = []
    for i in range(n):
        c = 100.0
        out.append(
            BarView(ts=ts0 + i * 3600, open=c, high=c + 0.2, low=c - 0.2,
                    close=c, volume=1000.0)
        )
    if flush:
        last_ts = out[-1].ts
        out[-1] = BarView(ts=last_ts, open=99.0, high=99.2, low=95.0,
                          close=95.5, volume=4000.0)
    return out


def _thin_book_mv(bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=4.0, atr_pct=0.02,
        rsi_14=18.0, bb_lower=96.0, bb_middle=100.0, bb_upper=104.0,
    )


def _funding_mv(bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=4.0, atr_pct=0.02,
        altdata=AltDataView(
            funding_rate_symbol=-0.0025, funding_rate_p10=-0.0019,
        ),
    )


def _dispatch_strategy(strategy_id: str) -> BaseStrategy:
    """The instance the LIVE dispatch literal would run (not a hand-built one)."""
    for s in _all_strategies():
        if s.metadata.strategy_id == strategy_id:
            return s
    raise AssertionError(f"{strategy_id} not in dispatch")


def test_dispatched_thin_book_emits_on_weekend_flush() -> None:
    strat = _dispatch_strategy(THIN_BOOK_ID)
    sig = strat.generate_raw_signal(_thin_book_mv(_bars(30, weekday=5, flush=True)))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == THIN_BOOK_ID


def test_dispatched_funding_emits_on_weekend_capitulation() -> None:
    strat = _dispatch_strategy(FUNDING_ID)
    sig = strat.generate_raw_signal(_funding_mv(_bars(30, weekday=5, flush=False)))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == FUNDING_ID


def test_dispatched_makers_no_emit_on_weekday() -> None:
    # Same dispatch instances, a WEEKDAY bar → no emit (the edge is weekend-only).
    # This is the no-emit path the live log will now show (call HAPPENS, returns
    # None) — distinct from the prior silent INERT (call never happened).
    thin = _dispatch_strategy(THIN_BOOK_ID)
    fund = _dispatch_strategy(FUNDING_ID)
    for wd in range(0, 5):  # Mon..Fri
        assert thin.generate_raw_signal(
            _thin_book_mv(_bars(30, weekday=wd, flush=True))
        ) is None
        assert fund.generate_raw_signal(
            _funding_mv(_bars(30, weekday=wd, flush=False))
        ) is None


def test_makers_reachable_on_okx_spot_bar_path() -> None:
    # OKX crypto/spot is unconditionally kept on the bar fan-out, so a dispatched
    # OKX maker is reachable (not vacated to the tick engine).
    assert keep_on_bar_path(asset_class="spot", symbol="BTC-USDT") is True
    assert keep_on_bar_path(asset_class="crypto", symbol="ETH-USDT") is True
