"""§0c — loss_cooldown_bars (symbol-local loss-reentry pacing rider).

DEMO/PAPER virtual. AGGRESSIVE / flow_not_block: ``loss_cooldown_bars`` is a
PER-(venue, symbol, strategy_id) reentry-pacing skip on a strategy's OWN prior
LOSS, not a defensive throttle applied uniformly. Default 0 on
``StrategyMetadata`` keeps every EXISTING strategy byte-identical (the
consumer's ``cooldown_bars <= 0`` early-return never queries the DB). Only
``connors_rsi2`` (loss_cooldown_bars=2, the DINO -374 repeat-reentry lesson)
opts in this wave. The skip is symbol-local: a DIFFERENT symbol, a DIFFERENT
strategy on the SAME symbol, or a DIFFERENT venue is completely unaffected —
proven directly via the exact-match WHERE-clause key isolation below.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.data.schema import Bar
from polaris.storage.schema import ALL_DDL
from polaris.strategies.base import StrategyMetadata
from polaris.strategies.connors_rsi2 import ConnorsRSI2Strategy

_DAY = 86_400


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        c.execute(stmt)
    return c


def _insert_closed_position(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    closed_ts: int,
    pnl_r: float,
) -> None:
    conn.execute(
        "INSERT INTO positions ("
        "position_id, venue, symbol, underlying_group_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts, pnl_r"
        ") VALUES (?, ?, ?, '', ?, ?, ?, 'long', 1.0, 'closed', ?, ?, ?)",
        (
            uuid.uuid4().hex, venue, symbol, strategy_id, strategy_id,
            strategy_id, closed_ts - 10, closed_ts, pnl_r,
        ),
    )


def _bar(ts: int) -> Bar:
    return Bar(
        instrument_id="alpaca:AAPL", underlying_group_id="", venue="alpaca",
        symbol="AAPL", bar_interval="1D", ts=ts, open=1.0, high=1.0, low=1.0,
        close=1.0, volume=100.0,
    )


# ---------------------------------------------------------------------------
# A — StrategyMetadata field default + connors_rsi2 wiring
# ---------------------------------------------------------------------------


def test_loss_cooldown_bars_defaults_to_zero() -> None:
    m = StrategyMetadata(
        strategy_id="x", timeframe="1D", warmup_bars=1, max_positions=1,
        gross_cap=0.1, per_symbol_cap=0.1, expected_holding_bars=1,
        asset_class="equity", venue="alpaca", correlation_group_id="g",
    )
    assert m.loss_cooldown_bars == 0


def test_connors_rsi2_loss_cooldown_bars_is_2() -> None:
    assert ConnorsRSI2Strategy.metadata.loss_cooldown_bars == 2


def test_equity_bb_meanrev_15m_loss_cooldown_bars_is_8() -> None:
    from polaris.strategies import EquityBbMeanrev15mStrategy

    assert EquityBbMeanrev15mStrategy.metadata.loss_cooldown_bars == 8


def test_equity_opening_range_breakout_loss_cooldown_bars_is_4() -> None:
    from polaris.strategies import EquityOpeningRangeBreakoutStrategy

    assert EquityOpeningRangeBreakoutStrategy.metadata.loss_cooldown_bars == 4


def test_every_other_registered_strategy_defaults_to_zero() -> None:
    # Byte-identical guard: only connors_rsi2 (§0c) and the Alpaca sleeve Wave
    # 1b/1.5 pair (§1 #4-#5) opt in — every OTHER registered strategy's
    # metadata keeps the default 0 (no DB query, no skip).
    from polaris.strategies import STRATEGY_REGISTRY

    _opted_in = {
        "connors_rsi2",
        "equity_bb_meanrev_15m",
        "equity_opening_range_breakout",
    }
    for sid, cls in STRATEGY_REGISTRY.items():
        if sid in _opted_in:
            continue
        assert cls.metadata.loss_cooldown_bars == 0, (
            f"{sid} must keep loss_cooldown_bars=0 (byte-identical this wave)"
        )


# ---------------------------------------------------------------------------
# B — loss_cooldown_active pure query logic
# ---------------------------------------------------------------------------


def test_disabled_when_cooldown_zero_even_with_fresh_loss(
    conn: sqlite3.Connection,
) -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=-1.0,
    )
    bars = [_bar(1000 + _DAY)]  # 1 bar elapsed since close
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=0, bars=bars,
    ) is False


def test_no_prior_closed_position_allows(conn: sqlite3.Connection) -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=2, bars=[_bar(1000)],
    ) is False


def test_winning_close_never_cools_down(conn: sqlite3.Connection) -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=1.5,
    )
    bars = [_bar(1000 + 1)]  # 0 bars elapsed — would cool down if it were a loss
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=2, bars=bars,
    ) is False


def test_loss_within_cooldown_window_skips(conn: sqlite3.Connection) -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=-0.8,
    )
    # Only 1 bar has closed since the loss (cooldown=2) -> still cooling down.
    bars = [_bar(1000 + _DAY)]
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=2, bars=bars,
    ) is True


def test_loss_past_cooldown_window_allows(conn: sqlite3.Connection) -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=-0.8,
    )
    # 2 bars have closed since the loss (cooldown=2) -> pacing window elapsed.
    bars = [_bar(1000 + _DAY), _bar(1000 + 2 * _DAY)]
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=2, bars=bars,
    ) is False


def test_bar_exactly_at_closed_ts_does_not_count_as_elapsed(
    conn: sqlite3.Connection,
) -> None:
    # No look-ahead: a bar timestamped AT (not after) closed_ts is the closing
    # bar itself, not a new bar elapsed since the close.
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=-0.8,
    )
    bars = [_bar(1000)]
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=1, bars=bars,
    ) is True  # 0 elapsed bars < cooldown(1) -> still cooling down


def test_only_most_recent_closed_position_is_consulted(
    conn: sqlite3.Connection,
) -> None:
    # An OLDER loss must not re-trigger the cooldown once a MORE RECENT close
    # (even a win) exists on the same key.
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=500, pnl_r=-1.0,
    )
    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=0.5,
    )
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=5, bars=[_bar(1000 + 1)],
    ) is False


# ---------------------------------------------------------------------------
# C — symbol-local key isolation (the load-bearing flow_not_block proof)
# ---------------------------------------------------------------------------


def test_key_isolation_other_symbol_unaffected(conn: sqlite3.Connection) -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=-1.0,
    )
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="MSFT", strategy_id="connors_rsi2",
        cooldown_bars=5, bars=[_bar(1000 + 1)],
    ) is False


def test_key_isolation_other_strategy_same_symbol_unaffected(
    conn: sqlite3.Connection,
) -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=-1.0,
    )
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL",
        strategy_id="equity_donchian55_breakout",
        cooldown_bars=5, bars=[_bar(1000 + 1)],
    ) is False


def test_key_isolation_other_venue_unaffected(conn: sqlite3.Connection) -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    _insert_closed_position(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        closed_ts=1000, pnl_r=-1.0,
    )
    assert loss_cooldown_active(
        conn, venue="okx", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=5, bars=[_bar(1000 + 1)],
    ) is False


def test_db_error_degrades_to_allow_never_a_crash() -> None:
    from polaris.scripts._production_tick import loss_cooldown_active

    conn = sqlite3.connect(":memory:")
    conn.close()  # any query now raises sqlite3.ProgrammingError (a sqlite3.Error)
    assert loss_cooldown_active(
        conn, venue="alpaca", symbol="AAPL", strategy_id="connors_rsi2",
        cooldown_bars=5, bars=[_bar(1)],
    ) is False


# ---------------------------------------------------------------------------
# D — production wiring (source-level lint, mirrors the T13 wiring guards)
# ---------------------------------------------------------------------------


def test_run_tick_wires_loss_cooldown_before_signal_gen() -> None:
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    assert "loss_cooldown_active(" in src, (
        "§0c: loss_cooldown_active must be wired into the entry seam."
    )
    assert "strategy.metadata.loss_cooldown_bars" in src, (
        "§0c: the consumer must read the PER-STRATEGY cooldown_bars, never a "
        "hardcoded value."
    )
    idx_gate = src.index("loss_cooldown_active(")
    idx_signal = src.index("strategy.generate_raw_signal(mv)")
    assert idx_gate < idx_signal, (
        "§0c: the cooldown gate must run BEFORE generate_raw_signal (it holds "
        "only the NEW entry, same class as equity_session_entry_hold)."
    )
