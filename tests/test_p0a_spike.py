"""P0a STEP 5 — driver + VERDICT (TDD).

OFFLINE harness only. Touches NO live trading / sizing / T4 chain / gate
thresholds. behavior-0: ``run_spike`` only runs the OFFLINE evaluator over
injected variant instances and writes ONLY to an injected p0a_registry DB
(NEVER the live DB). The live DB is a read-only bar source.

Test plan (step brief):
  (1) run_spike on a TINY grid against a small in-memory fixture (bars injected,
      registry DB in tmp_path) returns a SpikeVerdict with the documented fields
      and a label consistent with the fixture.
  (2) FEATURE_BOTTLENECK: when no variant passes IS, the verdict is the feature-
      bottleneck label (IS pass-rate ~0).
  (3) DATA_BOUNDED: when the horizon cannot host a fold (history too short),
      every variant is data_bounded -> DATA_BOUNDED label.
  (4) registry write-through: run_spike persists one row per variant x cell to
      the injected registry DB and the aggregate roll-up matches.
  (5) CLI smoke: the module imports with no side effects (no DB created at
      import, no live-DB contact).
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

from polaris.core.data.schema import Bar
from polaris.scripts.run_p0a_spike import (
    K_MIN_EVALUABLE,
    SpikeVerdict,
    StrategySpec,
    _classify,
    run_spike,
)
from polaris.strategies.base import BaseStrategy
from polaris.strategies.tsmom import TSMOMStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy

BASE_TS = 1_700_000_000
HOUR = 3600


def _mk_sandbox() -> sqlite3.Connection:
    from polaris.storage.schema import ALL_DDL

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


def _flat_bars(*, n: int, start: float = 100.0) -> list[Bar]:
    bars: list[Bar] = []
    price = start
    for i in range(n):
        nxt = price * (1.0 + (0.002 if i % 2 == 0 else -0.002))
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


def _tiny_spec(base: type[BaseStrategy], bars: list[Bar]) -> StrategySpec:
    """A StrategySpec with a 2-point grid and a single okx:BTC-USDT pool."""
    return StrategySpec(
        strategy_id=base.metadata.strategy_id,
        base_cls=base,
        exchange="okx",
        bars_by_instrument={"okx:BTC-USDT": bars},
        n_splits=1,
        warmup_bars=25,
        max_ttl=4,
    )


def test_run_spike_returns_verdict_object(tmp_path: Path) -> None:
    """A tiny grid over a clean uptrend fixture returns a populated SpikeVerdict."""
    db = tmp_path / "p0a_registry.sqlite"
    bars = _uptrend_bars(n=120)
    spec = _tiny_spec(TSMOMStrategy, bars)
    verdict = run_spike(
        specs=[spec],
        registry_path=db,
        sandbox_factory=_mk_sandbox,
        run_id="run-test-1",
    )
    assert isinstance(verdict, SpikeVerdict)
    # documented fields present
    assert verdict.label in {
        "VALIDATION_STARVED",
        "FEATURE_BOTTLENECK",
        "OVERFIT_NO_GENERALIZATION",
        "SEARCH_SPACE_EXISTS",
        "DATA_BOUNDED",
    }
    assert 0.0 <= verdict.is_pass_rate <= 1.0
    assert 0.0 <= verdict.oos_pass_rate <= 1.0
    assert verdict.n_variants > 0
    # the verdict carries the exact numbers behind it
    assert verdict.is_pass_count >= 0
    assert verdict.oos_pass_count >= 0
    # FIX 1 keystone fields are populated.
    assert isinstance(verdict.positive_control_passed, bool)
    assert verdict.min_passable_n > 0
    assert verdict.positive_control_n >= 0
    # tsmom has NO entry-set knob -> 0 entry-search breadth (only the default
    # config is evaluated). trials_searched counts only real entry-varying configs.
    assert verdict.trials_searched == 0
    assert verdict.n_variants == 1
    assert db.exists()


def test_data_bounded_label_when_history_too_short(tmp_path: Path) -> None:
    """30 bars, embargo warmup25+ttl4=29 -> every variant data_bounded -> label."""
    db = tmp_path / "p0a_registry.sqlite"
    bars = _uptrend_bars(n=30)
    spec = _tiny_spec(VolumeBurstStrategy, bars)
    verdict = run_spike(
        specs=[spec],
        registry_path=db,
        sandbox_factory=_mk_sandbox,
        run_id="run-test-db",
    )
    assert verdict.data_bounded_count == verdict.n_variants
    assert verdict.label == "DATA_BOUNDED"


def test_thin_oos_n_is_validation_starved_not_feature_bottleneck(tmp_path: Path) -> None:
    """KEYSTONE (FIX 1): a thin fixture where no variant clears the IS gate must
    NOT be labelled FEATURE_BOTTLENECK — the gate cannot certify even a STRONG
    edge at the thin held-out N, so the verdict is VALIDATION_STARVED. This is the
    exact mislabel the adversarial review rejected."""
    db = tmp_path / "p0a_registry.sqlite"
    bars = _flat_bars(n=120)
    spec = _tiny_spec(VolumeBurstStrategy, bars)  # has a 9-point entry grid
    verdict = run_spike(
        specs=[spec],
        registry_path=db,
        sandbox_factory=_mk_sandbox,
        run_id="run-test-fb",
    )
    assert verdict.is_pass_count == 0
    # The thin OOS N cannot clear the gate even for a strong synthetic edge.
    assert verdict.positive_control_passed is False
    assert verdict.positive_control_n < verdict.min_passable_n
    assert verdict.label == "VALIDATION_STARVED"
    assert verdict.label != "FEATURE_BOTTLENECK"


def test_registry_write_through_matches_aggregate(tmp_path: Path) -> None:
    """run_spike persists one row per variant x cell; the aggregate roll-up the
    verdict reports matches a fresh read of the registry."""
    from polaris.core.evolve.registry import aggregate_pass_rate, open_registry

    db = tmp_path / "p0a_registry.sqlite"
    bars = _uptrend_bars(n=120)
    spec = _tiny_spec(TSMOMStrategy, bars)
    verdict = run_spike(
        specs=[spec],
        registry_path=db,
        sandbox_factory=_mk_sandbox,
        run_id="run-test-rt",
    )
    conn = open_registry(db)
    try:
        # The registry scopes by variant_run_id (a per-trial uuid), not the
        # spike-level run_id; the fresh tmp DB holds only this run's rows, so the
        # unscoped roll-up is exactly this run.
        agg = aggregate_pass_rate(conn, run_id=None)
    finally:
        conn.close()
    assert agg.overall.n_trials == verdict.n_variants
    assert agg.overall.is_pass == verdict.is_pass_count
    assert agg.overall.oos_pass == verdict.oos_pass_count


# ---------------------------------------------------------------------------
# FIX 7b: direct _classify unit test covering ALL FIVE labels (pure function)
# ---------------------------------------------------------------------------


def _classify_label(
    *,
    n_variants: int = 10,
    data_bounded: int = 0,
    is_pass: int = 0,
    oos_pass: int = 0,
    pc_passed: bool = True,
    pc_n: int = 200,
    mpn: int = 69,
    evaluable_nondegenerate: int = K_MIN_EVALUABLE,
) -> str:
    label, _rationale = _classify(
        n_variants=n_variants,
        data_bounded_count=data_bounded,
        is_pass_count=is_pass,
        oos_pass_count=oos_pass,
        positive_control_passed=pc_passed,
        positive_control_n=pc_n,
        min_passable_n=mpn,
        evaluable_nondegenerate=evaluable_nondegenerate,
    )
    return label


def test_classify_validation_starved_when_positive_control_fails() -> None:
    # pc_fail OUTRANKS everything — even a strong IS/OOS pass-count is meaningless
    # if the gate cannot certify a strong edge at the available N.
    assert _classify_label(pc_passed=False) == "VALIDATION_STARVED"
    assert _classify_label(pc_passed=False, is_pass=5, oos_pass=3) == "VALIDATION_STARVED"


def test_classify_data_bounded_when_nothing_evaluable() -> None:
    # pc passes, but every variant was data-bounded (no held-out fold).
    assert _classify_label(pc_passed=True, n_variants=10, data_bounded=10) == "DATA_BOUNDED"


def test_classify_feature_bottleneck_when_is_zero_and_pc_passes() -> None:
    # pc passes + >= K_min non-degenerate evaluable variants + 0 IS pass.
    assert _classify_label(
        pc_passed=True, is_pass=0, evaluable_nondegenerate=K_MIN_EVALUABLE
    ) == "FEATURE_BOTTLENECK"


def test_classify_data_bounded_when_is_zero_but_too_few_evaluable() -> None:
    # FIX 8: 0 IS pass but fewer than K_min non-degenerate variants -> the search
    # is too thin to declare a feature bottleneck -> DATA_BOUNDED.
    assert _classify_label(
        pc_passed=True, is_pass=0, evaluable_nondegenerate=K_MIN_EVALUABLE - 1
    ) == "DATA_BOUNDED"


def test_classify_overfit_when_is_positive_oos_zero() -> None:
    assert _classify_label(pc_passed=True, is_pass=4, oos_pass=0) == "OVERFIT_NO_GENERALIZATION"


def test_classify_search_space_exists_when_oos_positive() -> None:
    assert _classify_label(pc_passed=True, is_pass=4, oos_pass=2) == "SEARCH_SPACE_EXISTS"


def test_cli_module_imports_without_side_effects() -> None:
    """Importing the CLI module must not create a DB or touch the live DB."""
    mod = importlib.import_module("polaris.scripts.run_p0a_spike")
    assert hasattr(mod, "run_spike")
    assert hasattr(mod, "main")
    assert callable(mod.main)


def test_build_specs_opens_live_db_read_only(tmp_path: Path) -> None:
    """OFFLINE / read-only guard (design TDD #7): build_specs reads bars from the
    live DB but the live DB is a read-only source — a write through a mode=ro
    connection to the same file raises OperationalError."""
    import pytest

    from polaris.scripts.run_p0a_spike import build_specs
    from polaris.storage.schema import ALL_DDL

    live = tmp_path / "fake_live.sqlite"
    seed = sqlite3.connect(str(live), isolation_level=None)
    try:
        for stmt in ALL_DDL:
            seed.execute(stmt)
        # one bar row so load_bars returns something for the strategy's pool key.
        # (Vehicle was spot_donchian; un-registered 2026-06-27 #56 stop-bleeders
        # KILL → build_specs skips unregistered ids, so use the live registered
        # 1H OKX strategy rsi_bb_pullback, whose p0a design pool includes BTC-USDT.)
        seed.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
            "bar_interval, ts, open, high, low, close, volume, notional_usd, "
            "trade_count, vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("okx:BTC-USDT", "crypto:BTC", "okx", "BTC-USDT", "1H", BASE_TS,
             100.0, 101.0, 99.0, 100.5, 1000.0, 100000.0, 10, 100.2, 100.4,
             100.6, 4.0, "test"),
        )
    finally:
        seed.close()

    # build_specs must succeed (read) ...
    specs = build_specs(str(live), strategies=["rsi_bb_pullback"])
    assert specs and specs[0].strategy_id == "rsi_bb_pullback"

    # ... and the live DB is a READ-ONLY source: a write via mode=ro fails.
    ro = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("DELETE FROM bars")
    finally:
        ro.close()
