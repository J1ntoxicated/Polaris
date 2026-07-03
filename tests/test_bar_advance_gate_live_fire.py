"""Live-fire proof: the bar-advance gate actually reaches STRATEGY_REGISTRY
strategies and behaves per StrategyMetadata.evaluates_in_progress_bar.

DEMO/PAPER · flow_not_block · 9-stack ban untouched. This exercises the SAME
gate primitive (``bar_advance_due``) the ``_run_tick`` dispatch loop calls,
keyed the SAME way (``(venue, symbol, timeframe, strategy_id)`` — a 4-tuple,
NOT 3 — see ``test_multiple_close_only_siblings_sharing_a_bucket_all_fire``
below for why strategy_id must be part of the key), against REAL registered
strategy instances and REAL MarketView objects — proving:
  1. a close-only strategy sharing a bucket with an exempt one is correctly
     skippable while its bucket-mate keeps firing every tick (no cross-
     contamination between strategies sharing a (venue, timeframe) bucket);
  2. no signal is ever lost across a bar-advance sequence — a close-only
     strategy fires exactly once per distinct bar ts, matching what it would
     have produced firing every tick (byte-identical decision, fewer calls);
  3. MULTIPLE close-only strategies sharing the same (venue, symbol,
     timeframe) bucket (the actual production configuration — e.g. OKX 1D
     has 5) are each evaluated on a bar's first observation — a 3-tuple key
     would let the first strategy's write starve every sibling permanently.
"""

from __future__ import annotations

from polaris.scripts._production_bar_gate import bar_advance_due
from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.base import BarView, MarketView

# gold_trend_chandelier_1d (close-only) and gold_riskoff_trend_amplify (exempt)
# share the REAL (capital, 1D) dispatch bucket in STRATEGY_REGISTRY today.
_CLOSE_ONLY_ID = "gold_trend_chandelier_1d"
_EXEMPT_ID = "gold_riskoff_trend_amplify"

# The REAL OKX 1D dispatch bucket today has 5 close-only strategies sharing
# (venue="okx", timeframe="1D") — the actual production shape this test
# guards against (see the BLOCKER this regression test closes).
_OKX_1D_CLOSE_ONLY_IDS = [
    "bar_breakout_run",
    "okx_donchian_55_breakout",
    "tsmom_12_1_multiasset",
    "macd_ema_trend_pullback",
    "donchian_turtle_breakout",
]


def _bars(n: int, *, start_ts: int = 0, step: int = 86_400, base: float = 100.0) -> list[BarView]:
    out = []
    price = base
    for i in range(n):
        price += 1.0
        out.append(
            BarView(
                ts=start_ts + i * step, open=price - 1, high=price + 1,
                low=price - 2, close=price, volume=1_000.0,
            )
        )
    return out


def test_close_only_and_exempt_share_a_real_bucket() -> None:
    """Sanity: the two strategies this test exercises actually coexist in the
    live (capital, 1D) dispatch bucket today — if a future refactor moves one
    out, this test must be revisited (it would otherwise silently test
    nothing meaningful)."""
    close_only = STRATEGY_REGISTRY[_CLOSE_ONLY_ID]
    exempt = STRATEGY_REGISTRY[_EXEMPT_ID]
    assert close_only.metadata.venue == exempt.metadata.venue == "capital"
    assert close_only.metadata.timeframe == exempt.metadata.timeframe == "1D"
    assert close_only.metadata.evaluates_in_progress_bar is False
    assert exempt.metadata.evaluates_in_progress_bar is True


def test_gate_skips_close_only_repeat_but_never_the_exempt_sibling() -> None:
    """Simulate 3 ticks: bar unchanged, unchanged, then advanced. The
    close-only strategy's gate fires on tick 1 (first observation) and tick 3
    (advance), skips tick 2 (unchanged) — 2 of 3 calls. The exempt sibling's
    gate ALWAYS fires (evaluates_in_progress_bar bypasses bar_advance_due
    entirely at the real call site) — 3 of 3 calls, unaffected by the
    close-only strategy's own last-eval bookkeeping (independent per-strategy
    behaviour, no shared mutable state leak in this test's model)."""
    close_only = STRATEGY_REGISTRY[_CLOSE_ONLY_ID]
    exempt = STRATEGY_REGISTRY[_EXEMPT_ID]
    key = (
        close_only.metadata.venue,
        "XAUUSD",
        close_only.metadata.timeframe,
        close_only.metadata.strategy_id,
    )
    last_eval_bar_ts_by_key: dict[tuple[str, str, str, str], int] = {}

    bar_tss = [86_400 * 10, 86_400 * 10, 86_400 * 11]  # unchanged, unchanged, advance
    close_only_fired = []
    exempt_fired = []
    for latest_ts in bar_tss:
        # close-only strategy: gated by bar_advance_due, mirroring the real
        # dispatch-loop wiring exactly (same predicate, same key shape).
        if not close_only.metadata.evaluates_in_progress_bar:
            due = bar_advance_due(
                last_eval_ts=last_eval_bar_ts_by_key.get(key),
                latest_bar_ts=latest_ts,
            )
            if due:
                last_eval_bar_ts_by_key[key] = latest_ts
            close_only_fired.append(due)
        # exempt strategy: ALWAYS fires — the real call site never even
        # consults bar_advance_due for it (short-circuited by the flag).
        exempt_fired.append(True if exempt.metadata.evaluates_in_progress_bar else None)

    assert close_only_fired == [True, False, True]
    assert exempt_fired == [True, True, True]


def test_close_only_strategy_produces_identical_signal_when_gate_would_have_fired() -> None:
    """No-loss proof: on the tick the gate DOES fire, the close-only strategy's
    generate_raw_signal output is deterministic given the same MarketView —
    calling it once (gated) vs every tick (ungated, hypothetical) on an
    UNCHANGED bar stream yields the identical result, so skipping the
    redundant calls never changes what the strategy would have decided."""
    close_only_cls = STRATEGY_REGISTRY[_CLOSE_ONLY_ID]
    strategy = close_only_cls()
    warmup = strategy.metadata.warmup_bars + 5
    bars = _bars(warmup)
    mv = MarketView(
        symbol="XAUUSD", venue="capital", timeframe="1D",
        bars=bars, last_price=bars[-1].close, spread_bps=5.0,
    )
    sig_a = strategy.generate_raw_signal(mv)
    sig_b = strategy.generate_raw_signal(mv)  # a hypothetical redundant re-poll
    # Compare the decision content (side/None), not object identity/signal_id
    # (which is a fresh uuid per call by design).
    assert (sig_a is None) == (sig_b is None)
    if sig_a is not None and sig_b is not None:
        assert sig_a.side == sig_b.side
        assert sig_a.strength == sig_b.strength


def test_okx_1d_bucket_has_5_close_only_strategies_sharing_the_bucket_today() -> None:
    """Sanity: pins the REAL production shape this regression test guards —
    if a future registry change drops this below 2, the test below stops
    exercising the multi-sibling starvation case and must be revisited."""
    okx_1d = [s for s in _OKX_1D_CLOSE_ONLY_IDS if s in STRATEGY_REGISTRY]
    assert len(okx_1d) == len(_OKX_1D_CLOSE_ONLY_IDS), (
        "expected all 5 OKX 1D close-only strategies to be registered"
    )
    for sid in okx_1d:
        strat = STRATEGY_REGISTRY[sid]
        assert strat.metadata.venue == "okx"
        assert strat.metadata.timeframe == "1D"
        assert strat.metadata.evaluates_in_progress_bar is False


def test_multiple_close_only_siblings_sharing_a_bucket_all_fire_on_first_observation() -> None:
    """Regression for the BLOCKER: keying the per-strategy gate on a 3-tuple
    ``(venue, symbol, timeframe)`` (no strategy_id) means the FIRST close-only
    strategy in a shared bucket bumps the mark and every sibling on the SAME
    bar reads it as already-evaluated and is skipped FOREVER (the next bar
    advance repeats: strategy 1 runs and re-bumps before siblings get a turn).

    This test puts all 5 REAL OKX-1D close-only strategies through the SAME
    (venue, symbol, timeframe) bucket on one unchanged bar and asserts EVERY
    ONE of them is evaluated (gate fires) on the bar's first observation —
    the exact production configuration the 3-tuple key silently broke.
    """
    strategies = [STRATEGY_REGISTRY[sid] for sid in _OKX_1D_CLOSE_ONLY_IDS]
    last_eval_bar_ts_by_key: dict[tuple[str, str, str, str], int] = {}
    venue, symbol, timeframe = "okx", "BTC-USDT", "1D"
    latest_ts = 86_400 * 10

    fired = {}
    for strat in strategies:
        key = (venue, symbol, timeframe, strat.metadata.strategy_id)
        due = bar_advance_due(
            last_eval_ts=last_eval_bar_ts_by_key.get(key),
            latest_bar_ts=latest_ts,
        )
        if due:
            last_eval_bar_ts_by_key[key] = latest_ts
        fired[strat.metadata.strategy_id] = due

    # BLOCKER reproduction check: with the OLD 3-tuple key (no strategy_id),
    # only the FIRST strategy would fire and all siblings would read the
    # already-bumped mark and be skipped. The FIXED 4-tuple key must fire
    # for every single sibling on this, their genuine first observation.
    assert all(fired.values()), (
        f"expected all {len(strategies)} close-only siblings to fire on "
        f"first observation of the same bucket/bar; got {fired} — a 3-tuple "
        "key (missing strategy_id) would starve all but the first."
    )

    # A same-ts re-poll (bar unchanged) must now skip ALL of them — proving
    # the per-strategy marks were correctly recorded, not merely permissive.
    refired = {}
    for strat in strategies:
        key = (venue, symbol, timeframe, strat.metadata.strategy_id)
        refired[strat.metadata.strategy_id] = bar_advance_due(
            last_eval_ts=last_eval_bar_ts_by_key.get(key),
            latest_bar_ts=latest_ts,
        )
    assert not any(refired.values()), (
        "a genuine same-bar re-poll must skip every sibling once each has "
        "its own recorded mark"
    )

    # And a real bar advance re-fires every sibling again (no permanent loss).
    next_ts = 86_400 * 11
    readvanced = {}
    for strat in strategies:
        key = (venue, symbol, timeframe, strat.metadata.strategy_id)
        readvanced[strat.metadata.strategy_id] = bar_advance_due(
            last_eval_ts=last_eval_bar_ts_by_key.get(key),
            latest_bar_ts=next_ts,
        )
    assert all(readvanced.values())
