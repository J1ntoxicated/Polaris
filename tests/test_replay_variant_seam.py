"""P0a STEP 1 — ReplayEngine ``strategies=`` injection seam (behaviour 0).

OFFLINE / read-only. Asserts:
  (a) ``ReplayEngine(config)`` (default) still uses ``all_strategies()`` —
      byte-identical live path (same strategy ids, same trades).
  (b) ``ReplayEngine(config, strategies=[...])`` runs ONLY the injected set.

Written FAILING-FIRST: before the param exists ``strategies=`` raises
``TypeError``. The seam is the ONLY new behaviour; the default (None) path is
verbatim the current behaviour.
"""

from __future__ import annotations

import sqlite3

from polaris.core.data.schema import Bar
from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.models import ReplayConfig
from polaris.storage.schema import ALL_DDL
from polaris.strategies import all_strategies
from polaris.strategies.base import BaseStrategy
from polaris.strategies.tsmom import TSMOMStrategy

BASE_TS = 1_700_000_000
HOUR = 3600


def _mk_sandbox() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _uptrend_bars(*, n: int = 60, start: float = 100.0, step_pct: float = 0.01) -> list[Bar]:
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


def _config() -> ReplayConfig:
    return ReplayConfig(
        instrument_ids=("okx:BTC-USDT",), bar_interval="1H", starting_equity=10_000.0
    )


# --- (a) default path == all_strategies(), behaviour 0 ----------------------
def test_default_strategies_is_all_strategies() -> None:
    """No ``strategies=`` -> the full registry, identical ids + order."""
    eng = ReplayEngine(_config())
    expected_ids = [s.metadata.strategy_id for s in all_strategies()]
    got_ids = [s.metadata.strategy_id for s in eng.strategies]
    assert got_ids == expected_ids


def test_strategies_none_is_byte_identical_to_default() -> None:
    """Explicit ``strategies=None`` reproduces the default trade set exactly."""
    bars = {"okx:BTC-USDT": _uptrend_bars()}
    r_default = ReplayEngine(_config()).run_with_bars(
        _mk_sandbox(), {k: list(v) for k, v in bars.items()}
    )
    r_none = ReplayEngine(_config(), strategies=None).run_with_bars(
        _mk_sandbox(), {k: list(v) for k, v in bars.items()}
    )
    assert r_none.trades == r_default.trades
    assert r_none.equity_curve_real_fee_net == r_default.equity_curve_real_fee_net
    assert r_none.metrics == r_default.metrics


def test_injecting_all_strategies_is_byte_identical() -> None:
    """Passing ``all_strategies()`` explicitly == the default path (behaviour 0)."""
    bars = {"okx:BTC-USDT": _uptrend_bars()}
    r_default = ReplayEngine(_config()).run_with_bars(
        _mk_sandbox(), {k: list(v) for k, v in bars.items()}
    )
    r_inject = ReplayEngine(_config(), strategies=all_strategies()).run_with_bars(
        _mk_sandbox(), {k: list(v) for k, v in bars.items()}
    )
    assert r_inject.trades == r_default.trades


# --- (b) injection restricts to ONLY the provided set -----------------------
def test_injected_subset_runs_only_those_strategies() -> None:
    """A single-strategy injection -> only that strategy's id appears in trades."""
    bars = {"okx:BTC-USDT": _uptrend_bars()}
    res = ReplayEngine(_config(), strategies=[TSMOMStrategy()]).run_with_bars(
        _mk_sandbox(), {k: list(v) for k, v in bars.items()}
    )
    # The uptrend fixture fires tsmom (momentum_20bar > 0) -> trades exist.
    assert res.trades
    strat_ids = {tr.strategy for tr in res.trades}
    assert strat_ids == {"tsmom"}


def test_injected_list_is_copied_not_aliased() -> None:
    """The engine must own its own list — mutating the caller's list after
    construction must not change which strategies run."""
    injected: list[BaseStrategy] = [TSMOMStrategy()]
    eng = ReplayEngine(_config(), strategies=injected)
    injected.clear()  # caller mutation must not affect the engine
    assert [s.metadata.strategy_id for s in eng.strategies] == ["tsmom"]
