"""Unit test — DataStore.get_live_empirical_health (Fwd PR-B).

MSG-LIVE-GATE-SQL. Covers:
  1. Basic aggregate: 10 mock trades (6 win, 4 loss) → wr=0.6,
     asym = sum(wins_pct) / sum(|losses_pct|), net_usd sum, median.
  2. 3-axis filter: exchange + asset_group + strategy_family narrows
     the sample deterministically.
  3. Empty sample (n=0) returns all-zero shape (no exceptions).
  4. clean_epoch floor drops pre-epoch rows even when window_sec is
     large enough to otherwise include them.
  5. Window cutoff excludes rows outside the last ``window_sec`` slice.
  6. Asymmetric-only (all winners) → asym = +inf, not crash.

Run: ``pytest tests/data/test_live_empirical_health.py``
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from invasion.data._repo_trades import TradesMixin
from invasion.data.unified_schema import UNIFIED_TABLES


CLEAN_EPOCH = 1775839507  # matches repo constant


class _Shim(TradesMixin):
    """Minimal TradesMixin host for unit tests (no singleton)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(UNIFIED_TABLES["trades"])
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def _insert(
    shim: _Shim,
    *,
    trade_id: str,
    pnl_pct: float,
    pnl_usd: float,
    exit_ts: float,
    exchange: str = "okx",
    asset_group: str = "crypto",
    strategy_id: str = "meanrev_v1",
    status: str = "closed",
) -> None:
    shim._conn.execute(
        "INSERT INTO trades "
        "(id, strategy_id, ticker, direction, asset_group, exchange, "
        " entry_price, exit_price, pnl_pct, pnl_usd, entry_ts, exit_ts, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            trade_id, strategy_id, "BTC", "long", asset_group, exchange,
            100.0, 100.0 * (1 + pnl_pct / 100.0),
            pnl_pct, pnl_usd, exit_ts - 60.0, exit_ts, status,
        ],
    )
    shim._conn.commit()


@pytest.fixture
def shim(tmp_path: Path):
    s = _Shim(tmp_path / "invasion_test.sqlite")
    yield s
    s.close()


def test_basic_aggregate_6w_4l(shim: _Shim) -> None:
    now = time.time()
    # 6 winners: +1,+2,+3,+4,+5,+6 (%) → sum_pos = 21, mean of wins = 3.5
    # 4 losers: -1,-2,-3,-4 (%)       → sum_neg = 10
    for i, p in enumerate([1, 2, 3, 4, 5, 6], start=1):
        _insert(shim, trade_id=f"W{i}", pnl_pct=float(p),
                pnl_usd=10.0 * p, exit_ts=now - 100.0 + i)
    for i, p in enumerate([-1, -2, -3, -4], start=1):
        _insert(shim, trade_id=f"L{i}", pnl_pct=float(p),
                pnl_usd=10.0 * p, exit_ts=now - 50.0 + i)

    out = shim.get_live_empirical_health(window_sec=3600)

    assert out["n"] == 10
    assert out["wr"] == pytest.approx(0.6)
    # mean pnl_pct = (21 - 10) / 10 = 1.1
    assert out["pnl_mean"] == pytest.approx(1.1)
    # asym = 21 / 10 = 2.1
    assert out["asym"] == pytest.approx(2.1)
    # net_usd = 10*(1+2+3+4+5+6) + 10*(-1-2-3-4) = 210 - 100 = 110
    assert out["net_usd"] == pytest.approx(110.0)
    # median — sorted pnl_pct: [-4,-3,-2,-1,1,2,3,4,5,6], mid index 5 → 2
    assert out["pnl_median"] == pytest.approx(2.0)


def test_filter_exchange_asset_group_family(shim: _Shim) -> None:
    now = time.time()
    _insert(shim, trade_id="A1", pnl_pct=2.0, pnl_usd=20.0,
            exit_ts=now - 10, exchange="okx", asset_group="crypto",
            strategy_id="meanrev_v1")
    _insert(shim, trade_id="A2", pnl_pct=-1.0, pnl_usd=-10.0,
            exit_ts=now - 9, exchange="okx", asset_group="crypto",
            strategy_id="meanrev_v2")
    _insert(shim, trade_id="B1", pnl_pct=5.0, pnl_usd=50.0,
            exit_ts=now - 8, exchange="capital", asset_group="forex",
            strategy_id="breakout_v1")
    _insert(shim, trade_id="C1", pnl_pct=-3.0, pnl_usd=-30.0,
            exit_ts=now - 7, exchange="okx", asset_group="crypto",
            strategy_id="donchian_v1")

    out = shim.get_live_empirical_health(
        exchange="okx", asset_group="crypto",
        strategy_family="meanrev", window_sec=3600,
    )
    assert out["n"] == 2
    assert out["wr"] == pytest.approx(0.5)
    assert out["net_usd"] == pytest.approx(10.0)


def test_empty_sample_returns_zeros(shim: _Shim) -> None:
    out = shim.get_live_empirical_health(window_sec=3600)
    assert out == {
        "n": 0, "wr": 0.0, "asym": 0.0,
        "pnl_mean": 0.0, "pnl_median": 0.0, "net_usd": 0.0,
    }


def test_clean_epoch_floor_drops_preepoch_rows(shim: _Shim) -> None:
    # Row with exit_ts BEFORE clean_epoch — must be excluded even though
    # a very large window_sec would otherwise sweep it in.
    _insert(shim, trade_id="PRE", pnl_pct=99.0, pnl_usd=9900.0,
            exit_ts=CLEAN_EPOCH - 10.0)
    # Row with exit_ts after epoch, recent.
    now = time.time()
    _insert(shim, trade_id="POST", pnl_pct=1.0, pnl_usd=10.0,
            exit_ts=now - 5.0)

    # window large enough to include PRE if floor were ignored.
    out = shim.get_live_empirical_health(window_sec=10**10)
    assert out["n"] == 1
    assert out["pnl_mean"] == pytest.approx(1.0)


def test_window_excludes_old_rows(shim: _Shim) -> None:
    now = time.time()
    _insert(shim, trade_id="OLD", pnl_pct=5.0, pnl_usd=50.0,
            exit_ts=now - 7200.0)  # 2h ago
    _insert(shim, trade_id="NEW", pnl_pct=-1.0, pnl_usd=-10.0,
            exit_ts=now - 60.0)

    out = shim.get_live_empirical_health(window_sec=3600)
    assert out["n"] == 1
    assert out["wr"] == pytest.approx(0.0)
    assert out["pnl_mean"] == pytest.approx(-1.0)


def test_all_winners_yields_inf_asym(shim: _Shim) -> None:
    now = time.time()
    for i in range(3):
        _insert(shim, trade_id=f"W{i}", pnl_pct=1.0 + i,
                pnl_usd=10.0, exit_ts=now - 10 + i)

    out = shim.get_live_empirical_health(window_sec=3600)
    assert out["n"] == 3
    assert out["wr"] == pytest.approx(1.0)
    assert out["asym"] == float("inf")


def test_open_rows_ignored(shim: _Shim) -> None:
    now = time.time()
    _insert(shim, trade_id="OPEN", pnl_pct=99.0, pnl_usd=99.0,
            exit_ts=now - 5.0, status="open")
    _insert(shim, trade_id="CLOSED", pnl_pct=1.0, pnl_usd=10.0,
            exit_ts=now - 4.0, status="closed")

    out = shim.get_live_empirical_health(window_sec=3600)
    assert out["n"] == 1
    assert out["pnl_mean"] == pytest.approx(1.0)
