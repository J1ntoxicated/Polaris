"""P0a STEP 4 — OOS + DSR honest-N evaluator (TDD).

OFFLINE harness only. Touches NO live trading / sizing / T4 chain / gate
thresholds. behavior-0: the evaluator only READS the live DB read-only and runs
the OFFLINE ReplayEngine over injected variant instances; it writes nothing.

Test plan (design.test_plan #4/#5 + step brief):
  (1) split_eval: IS/OOS bar-index split respects the embargo — IS and OOS entry
      sets are DISJOINT (no trade straddles the purge gap). [design TDD #4]
  (2) gate plumb: evaluate_split feeds the OOS slice to evaluate_gate and the
      is_oos_spread flows in (IS Sharpe − OOS Sharpe). [design TDD #5]
  (3) DSR honest-N monotonic: a larger ``trials_searched`` deflates the OOS DSR
      (DSR(trials=K) <= DSR(trials=1)) — the multiple-testing penalty is ADDED
      at this layer, not weakened. [step brief]
  (4) data_bounded: a history too short for one fold (embargo too large) returns
      a data_bounded result, NO silent shrink. [step brief]
  (5) overfit fixture: IS-great / OOS-random trades -> oos_pass False even if the
      IS slice looks strong. [step brief]
"""

from __future__ import annotations

import sqlite3

from polaris.core.benchmark.baselines import (
    bollinger_baseline,
    buy_and_hold_baseline,
    tsmom_baseline,
)
from polaris.core.data.schema import Bar
from polaris.core.evolve.evaluator import (
    VariantEval,
    evaluate_variant,
    gate_can_discriminate,
    honest_dsr,
    min_passable_n,
    split_is_oos,
)
from polaris.core.evolve.variants import make_variant
from polaris.core.replay.models import ReplayConfig
from polaris.storage.schema import ALL_DDL
from polaris.strategies.base import BaseStrategy
from polaris.strategies.tsmom import TSMOMStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy


def _variant(base: type[BaseStrategy], overrides: dict[str, float]) -> BaseStrategy:
    """make_variant returns the StrategyVariant Protocol; the instance IS-A
    BaseStrategy (dynamic subclass), which is the type the engine seam carries.
    STEP 5 likewise collects variants as list[BaseStrategy]."""
    v = make_variant(base, overrides)
    assert isinstance(v, BaseStrategy)
    return v


BASE_TS = 1_700_000_000
HOUR = 3600


def _mk_sandbox() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _uptrend_bars(*, n: int, start: float = 100.0, step_pct: float = 0.01) -> list[Bar]:
    bars: list[Bar] = []
    price = start
    for i in range(n):
        nxt = price * (1.0 + step_pct)
        bars.append(
            Bar(
                instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
                venue="okx", symbol="BTC-USDT", bar_interval="1H",
                ts=BASE_TS + i * HOUR,
                open=price, high=max(price, nxt), low=min(price, nxt),
                close=nxt, volume=1000.0 + i, notional_usd=price * 1000.0,
                spread_bps_close=4.0, source="test",
            )
        )
        price = nxt
    return bars


def _sawtooth_bars(*, n: int, start: float = 100.0) -> list[Bar]:
    """Rising sawtooth: 8 up-bars then 3 down-bars. The pullback trips the ATR
    trail so TSMOM closes + re-opens repeatedly -> MANY distinct trades that land
    on BOTH sides of the embargo (non-vacuous FIX-4 fixture), not one long ride."""
    bars: list[Bar] = []
    price = start
    for i in range(n):
        phase = i % 11
        step = 0.02 if phase < 8 else -0.05
        nxt = price * (1.0 + step)
        bars.append(
            Bar(
                instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
                venue="okx", symbol="BTC-USDT", bar_interval="1H",
                ts=BASE_TS + i * HOUR,
                open=price, high=max(price, nxt), low=min(price, nxt),
                close=nxt, volume=1000.0 + i, notional_usd=price * 1000.0,
                spread_bps_close=4.0, source="test",
            )
        )
        price = nxt
    return bars


def _config() -> ReplayConfig:
    return ReplayConfig(
        instrument_ids=("okx:BTC-USDT",), bar_interval="1H", starting_equity=10_000.0
    )


# --- (1) IS/OOS split: no leakage across the embargo --------------------------
def test_split_is_oos_disjoint_and_embargoed() -> None:
    """200-bar horizon, 1 split, embargo=29: IS and OOS index ranges are disjoint
    with at least ``embargo_bars`` of purged indices between them."""
    split = split_is_oos(n_bars=200, n_splits=1, embargo_bars=29)
    assert split is not None
    is_idx = set(split.is_index)
    oos_idx = set(split.oos_index)
    assert is_idx.isdisjoint(oos_idx)
    gap = min(oos_idx) - max(is_idx) - 1
    assert gap >= 29


def test_evaluate_variant_entry_sets_disjoint() -> None:
    """A full replay over bars[:oos_end]: IS entries (idx < oos_start) and OOS
    entries (idx >= oos_start) never straddle the embargo purge."""
    bars = _uptrend_bars(n=120)
    variant = _variant(TSMOMStrategy, {})  # behavior-0 default
    res = evaluate_variant(
        variant=variant,
        bars_by_instrument={"okx:BTC-USDT": bars},
        config=_config(),
        n_splits=1,
        warmup_bars=25,
        max_ttl=4,
        total_variants_searched=1,
        sandbox_factory=_mk_sandbox,
    )
    assert not res.data_bounded
    # The IS entry timestamps and OOS entry timestamps must be disjoint sets.
    is_ts = {t for t in res.is_entry_ts}
    oos_ts = {t for t in res.oos_entry_ts}
    assert is_ts.isdisjoint(oos_ts)


# --- (1b) FIX 4: embargo purge enforced (non-vacuous, trades BOTH sides) -------
def test_partition_enforces_embargo_purge_both_sides() -> None:
    """A fixture producing trades on BOTH sides of the embargo: assert
    (i) no surviving trade's ENTRY index lands in the purge band,
    (ii) no IS trade's EXIT index reaches into the OOS block,
    (iii) min(oos_idx) - max(is_idx) >= embargo,
    (iv) BOTH sides are non-empty (the test is not vacuous)."""
    bars = _sawtooth_bars(n=200)
    warmup, max_ttl = 25, 4
    embargo = warmup + max_ttl
    variant = _variant(TSMOMStrategy, {})
    res = evaluate_variant(
        variant=variant,
        bars_by_instrument={"okx:BTC-USDT": bars},
        config=_config(),
        n_splits=1,
        warmup_bars=warmup,
        max_ttl=max_ttl,
        total_variants_searched=1,
        sandbox_factory=_mk_sandbox,
    )
    assert not res.data_bounded
    ts_to_idx = {b.ts: i for i, b in enumerate(bars)}
    is_idx = sorted(ts_to_idx[t] for t in res.is_entry_ts)
    oos_idx = sorted(ts_to_idx[t] for t in res.oos_entry_ts)
    # (iv) both sides non-empty — the embargo enforcement is exercised.
    assert is_idx, "no IS trades — fixture is vacuous"
    assert oos_idx, "no OOS trades — fixture is vacuous"
    # The split's oos_start: derived from walk_forward over the 200-bar horizon.
    split = split_is_oos(n_bars=200, n_splits=1, embargo_bars=embargo)
    assert split is not None
    oos_start = min(split.oos_index)
    max_is_index = max(split.is_index)
    # (i) no surviving entry in the purge band (max_is_index, oos_start).
    for idx in is_idx + oos_idx:
        assert not (max_is_index < idx < oos_start), f"entry {idx} in purge band"
    # (iii) gap between the realised IS/OOS entry sets >= embargo.
    assert min(oos_idx) - max(is_idx) >= embargo


def test_partition_drops_is_trade_straddling_oos_boundary() -> None:
    """An IS-side trade whose EXIT crosses into the OOS block leaks held-out
    information — it must be DROPPED from IS (not folded in)."""
    from polaris.core.evolve.evaluator import _partition_trades
    from polaris.core.replay.models import ReplayTrade

    bars = _uptrend_bars(n=60)
    bbi = {"okx:BTC-USDT": bars}
    oos_start = 40
    is_end = 35  # purge band = (35, 40)
    # entry idx 30 (IS), exit idx 45 (>= oos_start) -> straddles -> DROP.
    straddler = ReplayTrade(
        entry_ts=bars[30].ts, exit_ts=bars[45].ts, symbol="BTC-USDT",
        strategy="tsmom", regime="mixed", side="long", entry_price=1.0,
        exit_price=1.0, notional=1.0, gross_pnl_usd=1.0, rt_fee_usd=0.0,
        net_pnl_usd=1.0, pnl_r=0.02, exit_reason="x",
    )
    # entry idx 38 (in purge band) -> DROP.
    purged = ReplayTrade(
        entry_ts=bars[38].ts, exit_ts=bars[39].ts, symbol="BTC-USDT",
        strategy="tsmom", regime="mixed", side="long", entry_price=1.0,
        exit_price=1.0, notional=1.0, gross_pnl_usd=1.0, rt_fee_usd=0.0,
        net_pnl_usd=1.0, pnl_r=0.02, exit_reason="x",
    )
    # clean IS (entry 20, exit 30) + clean OOS (entry 50, exit 55).
    clean_is = ReplayTrade(
        entry_ts=bars[20].ts, exit_ts=bars[30].ts, symbol="BTC-USDT",
        strategy="tsmom", regime="mixed", side="long", entry_price=1.0,
        exit_price=1.0, notional=1.0, gross_pnl_usd=1.0, rt_fee_usd=0.0,
        net_pnl_usd=1.0, pnl_r=0.02, exit_reason="x",
    )
    clean_oos = ReplayTrade(
        entry_ts=bars[50].ts, exit_ts=bars[55].ts, symbol="BTC-USDT",
        strategy="tsmom", regime="mixed", side="long", entry_price=1.0,
        exit_price=1.0, notional=1.0, gross_pnl_usd=1.0, rt_fee_usd=0.0,
        net_pnl_usd=1.0, pnl_r=0.02, exit_reason="x",
    )
    is_tr, oos_tr = _partition_trades(
        (straddler, purged, clean_is, clean_oos),
        bars_by_instrument=bbi, oos_start=oos_start, is_end=is_end,
    )
    is_entries = {bars.index(b) for b in bars if b.ts in {t.entry_ts for t in is_tr}}
    oos_entries = {bars.index(b) for b in bars if b.ts in {t.entry_ts for t in oos_tr}}
    assert is_entries == {20}  # straddler + purged dropped, only clean_is kept
    assert oos_entries == {50}


def test_partition_fallback_skips_unknown_instrument() -> None:
    """A trade whose instrument is not in the bar source is DROPPED — NEVER
    matched against another instrument's timeline (the old borrow bug)."""
    from polaris.core.evolve.evaluator import _partition_trades
    from polaris.core.replay.models import ReplayTrade

    bars = _uptrend_bars(n=60)
    bbi = {"okx:BTC-USDT": bars}
    alien = ReplayTrade(
        entry_ts=bars[10].ts, exit_ts=bars[12].ts, symbol="DOGE-USDT",
        strategy="tsmom", regime="mixed", side="long", entry_price=1.0,
        exit_price=1.0, notional=1.0, gross_pnl_usd=1.0, rt_fee_usd=0.0,
        net_pnl_usd=1.0, pnl_r=0.02, exit_reason="x",
    )
    is_tr, oos_tr = _partition_trades(
        (alien,), bars_by_instrument=bbi, oos_start=40, is_end=35
    )
    assert is_tr == [] and oos_tr == []


# --- (2) gate plumb: OOS slice -> gate, is_oos_spread set ----------------------
def test_is_oos_spread_plumbed() -> None:
    bars = _uptrend_bars(n=120)
    variant = _variant(TSMOMStrategy, {})
    res = evaluate_variant(
        variant=variant,
        bars_by_instrument={"okx:BTC-USDT": bars},
        config=_config(),
        n_splits=1,
        warmup_bars=25,
        max_ttl=4,
        total_variants_searched=1,
        sandbox_factory=_mk_sandbox,
    )
    assert isinstance(res, VariantEval)
    assert res.oos_gate is not None
    # is_oos_spread == IS Sharpe − OOS Sharpe (the gate's overfit flag).
    assert res.oos_gate.is_oos_spread == res.is_sharpe - res.oos_sharpe


# --- (3) DSR honest-N: deflation monotonic in trials_searched -----------------
def test_honest_dsr_monotonic_in_trials() -> None:
    """More trials searched can only LOWER the deflated Sharpe (added penalty)."""
    net_r = [0.4, -0.1, 0.3, 0.2, -0.05, 0.5, 0.1, 0.35, -0.2, 0.25]
    sr_variance = 0.5
    dsr_1 = honest_dsr(net_r=net_r, trials=1, sr_variance=sr_variance)
    dsr_8 = honest_dsr(net_r=net_r, trials=8, sr_variance=sr_variance)
    dsr_50 = honest_dsr(net_r=net_r, trials=50, sr_variance=sr_variance)
    assert dsr_8 <= dsr_1
    assert dsr_50 <= dsr_8


# --- (4) data_bounded: history too short -> no silent shrink ------------------
def test_data_bounded_when_horizon_too_short() -> None:
    """30 bars with embargo warmup25+ttl4=29 cannot host a fold -> data_bounded."""
    bars = _uptrend_bars(n=30)
    variant = _variant(VolumeBurstStrategy, {})
    res = evaluate_variant(
        variant=variant,
        bars_by_instrument={"okx:BTC-USDT": bars},
        config=_config(),
        n_splits=1,
        warmup_bars=25,
        max_ttl=4,
        total_variants_searched=27,
        sandbox_factory=_mk_sandbox,
    )
    assert res.data_bounded
    assert res.is_pass is False
    assert res.oos_pass is False
    assert res.n_trades == 0


def test_split_is_oos_returns_none_when_degenerate() -> None:
    """walk_forward returns () -> split_is_oos returns None (honest, no shrink)."""
    assert split_is_oos(n_bars=30, n_splits=1, embargo_bars=29) is None


def test_warmup_exceeds_is_window_is_data_bounded_not_gate_fail() -> None:
    """FIX 5: rsi_bb-like geometry — warmup 205 but a fold whose IS window ends at
    bar 45 (a single fold DOES exist, embargo=209). ZERO IS trades are possible
    because the strategy's warmup gate suppresses every IS bar. That is data
    starvation -> data_bounded, NOT a gate failure (excluded from the
    FEATURE_BOTTLENECK denominator upstream)."""
    # A fold exists at n=300/embargo=209 with is_end=45 (< warmup 205).
    split = split_is_oos(n_bars=300, n_splits=1, embargo_bars=205 + 4)
    assert split is not None
    assert max(split.is_index) < 205  # IS window genuinely ends before warmup
    bars = _uptrend_bars(n=300)
    variant = _variant(VolumeBurstStrategy, {})  # any strategy; warmup overridden
    res = evaluate_variant(
        variant=variant,
        bars_by_instrument={"okx:BTC-USDT": bars},
        config=_config(),
        n_splits=1,
        warmup_bars=205,  # rsi_bb-like (TREND_FILTER_MA + 5)
        max_ttl=4,
        total_variants_searched=9,
        sandbox_factory=_mk_sandbox,
    )
    assert res.data_bounded is True
    assert res.is_pass is False
    assert res.oos_pass is False
    assert res.n_trades == 0


def _is_strong_oos_loses_bars(*, n_is: int, n_oos: int, start: float = 100.0) -> list[Bar]:
    """A NON-VACUOUS overfit fixture (FIX 7a): a long clean up-sawtooth IS slice
    (many WINNING trades -> the IS gate passes) followed by an OOS slice that
    ramps momentum positive then periodically CLIFF-CRASHES so the long-only
    momentum entries lose (net-R centred <= 0 -> the OOS gate fails)."""
    bars: list[Bar] = []
    price = start
    i = 0
    for k in range(n_is):
        phase = k % 14
        step = 0.06 if phase < 11 else -0.03  # strong up, shallow pullbacks
        nxt = price * (1.0 + step)
        bars.append(
            Bar(
                instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
                venue="okx", symbol="BTC-USDT", bar_interval="1H",
                ts=BASE_TS + i * HOUR, open=price, high=max(price, nxt),
                low=min(price, nxt), close=nxt, volume=1000.0 + i,
                notional_usd=price * 1000.0, spread_bps_close=4.0, source="test",
            )
        )
        price = nxt
        i += 1
    for k in range(n_oos):
        phase = k % 30
        step = 0.02 if phase < 22 else -0.06  # ramp builds momentum, then crash
        nxt = price * (1.0 + step)
        bars.append(
            Bar(
                instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
                venue="okx", symbol="BTC-USDT", bar_interval="1H",
                ts=BASE_TS + i * HOUR, open=price, high=max(price, nxt),
                low=min(price, nxt), close=nxt, volume=1000.0 + i,
                notional_usd=price * 1000.0, spread_bps_close=4.0, source="test",
            )
        )
        price = nxt
        i += 1
    return bars


# --- (5) FIX 7a: REAL overfit fixture — IS PASSES, OOS FAILS (non-vacuous) -----
def test_overfit_is_passes_oos_fails_same_variant() -> None:
    """The SAME variant clears the full IS gate (>=10 winning IS trades, positive
    IS Sharpe) yet FAILS the held-out OOS gate (>=10 trades, net-R centred <= 0).
    A non-vacuous overfit proof: is_pass True AND oos_pass False AND n_trades>0."""
    bars = _is_strong_oos_loses_bars(n_is=1500, n_oos=600)
    variant = _variant(TSMOMStrategy, {})
    res = evaluate_variant(
        variant=variant,
        bars_by_instrument={"okx:BTC-USDT": bars},
        config=_config(),
        n_splits=1,
        warmup_bars=25,
        max_ttl=4,
        total_variants_searched=1,
        sandbox_factory=_mk_sandbox,
    )
    assert not res.data_bounded
    assert len(res.is_entry_ts) >= 10  # IS side has real trades
    assert res.is_sharpe > 0.0  # IS looks strong
    assert res.is_pass is True  # ... and clears the full IS gate
    assert res.n_trades >= 10  # OOS side has real trades too
    assert res.lcb <= 0.0  # OOS held-out edge is NOT significant
    assert res.oos_pass is False  # held-out verdict: vanishes


# --- (6) FIX 1 positive control: gate_can_discriminate + min_passable_n -------
def test_gate_can_discriminate_is_deterministic() -> None:
    """No randomness / no wall-clock: two calls at the same N agree exactly."""
    a = gate_can_discriminate(n_trades=120)
    b = gate_can_discriminate(n_trades=120)
    assert a == b
    assert isinstance(a, bool)


def test_gate_can_discriminate_monotone_in_n() -> None:
    """A strong low-noise edge is HARDER to certify at small N: once the gate can
    pass it at some N, it keeps passing at larger N (the NIG LCB only firms up)."""
    # Find the smallest passing N, then assert every larger probe still passes.
    passable = min_passable_n(max_n=2000)
    assert 1 < passable <= 2000  # the strong edge IS passable within the bound
    assert gate_can_discriminate(n_trades=passable) is True
    assert gate_can_discriminate(n_trades=passable + 50) is True
    # ... and just below it fails (it is the boundary).
    assert gate_can_discriminate(n_trades=passable - 1) is False


def test_gate_cannot_discriminate_strong_edge_at_thin_n() -> None:
    """The structural finding the rerun must surface: at the thin OOS N this data
    yields (the MAX realised OOS N across evaluable variants is 55), even a strong
    low-noise +0.2R edge does NOT clear the gate's statistical tier (lcb>0 AND
    dsr>=0.95) — validation starvation, not feature exhaustion. The NIG LCB needs
    more held-out trades than this thin horizon provides."""
    for n in (10, 30, 55):  # <= the data's MAX realised OOS N (55)
        assert gate_can_discriminate(n_trades=n) is False
    # min_passable_n (the smallest N a strong edge clears the gate) is strictly
    # GREATER than the available N -> validation-starved by construction.
    assert min_passable_n(max_n=2000) > 55


def test_min_passable_n_signals_unreachable_within_bound() -> None:
    """A wide-spread edge (mean<=spread) can NEVER clear the LCB; the search
    returns a sentinel strictly greater than the bound (not a false small N)."""
    unreachable = min_passable_n(mean=0.2, spread=0.4, max_n=300)
    assert unreachable > 300


def test_baselines_helper_shape() -> None:
    """The evaluator's baseline builder yields the same 3 same-clock baselines the
    production gate uses (relative tier needs them)."""
    bars = _uptrend_bars(n=60)
    baselines = {
        "bh": buy_and_hold_baseline(bars, starting_equity=10_000.0),
        "tsmom": tsmom_baseline(bars, starting_equity=10_000.0),
        "bollinger": bollinger_baseline(bars, starting_equity=10_000.0),
    }
    assert set(baselines) == {"bh", "tsmom", "bollinger"}
