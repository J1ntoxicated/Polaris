"""Multi-horizon ensemble replay TDD — shared capital + per-horizon attribution.

Parity target: the just-deployed live multi-horizon structure (commit e92bbbc)
runs the SAME strategies on independent sub-books per horizon (scalp = 1m tick
flow, swing = 1H bar pipeline) holding *complementary, independent positions* on
the same symbol, accounted against ONE capital base. The ensemble must
replicate that exactly:

  * each horizon = an independent ``ReplayEngine`` over its native bar interval
    (independent position book + independent sandbox sizing), and
  * the combined equity curve compounds EVERY horizon's net pnl on a single
    shared running equity (cross-horizon capital contention surfaces here).

Behaviour-0 guard: a single-horizon ensemble must equal the plain engine
byte-for-byte (no regression to the existing 6 replay tests / run_replay.py).
"""

from __future__ import annotations

import sqlite3

from polaris.core.data.schema import Bar
from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.ensemble import MultiHorizonReplay
from polaris.core.replay.equity_curve import build_equity_curve
from polaris.core.replay.models import ReplayConfig, ReplayResult, ReplayTrade
from polaris.storage.schema import ALL_DDL

BASE_TS = 1_700_000_000
HOUR = 3600
MINUTE = 60


def _mk_sandbox() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _uptrend_bars(
    *, n: int, interval: str, step_sec: int, step_pct: float = 0.01,
    symbol: str = "BTC-USDT", venue: str = "okx", start: float = 100.0,
) -> list[Bar]:
    bars: list[Bar] = []
    price = start
    for i in range(n):
        nxt = price * (1.0 + step_pct)
        bars.append(
            Bar(
                instrument_id=f"{venue}:{symbol}",
                underlying_group_id="crypto:BTC",
                venue=venue, symbol=symbol, bar_interval=interval,
                ts=BASE_TS + i * step_sec,
                open=price, high=max(price, nxt), low=min(price, nxt),
                close=nxt, volume=1000.0 + i, notional_usd=price * 1000.0,
                spread_bps_close=4.0, source="test",
            )
        )
        price = nxt
    return bars


# --- behaviour-0: single horizon == plain engine ---------------------------
def test_single_horizon_ensemble_equals_plain_engine() -> None:
    """A one-element horizon set reproduces the plain ReplayEngine exactly."""
    bars = _uptrend_bars(n=60, interval="1H", step_sec=HOUR)
    cfg = ReplayConfig(instrument_ids=("okx:BTC-USDT",), bar_interval="1H")
    plain = ReplayEngine(cfg).run_with_bars(
        _mk_sandbox(), {"okx:BTC-USDT": list(bars)}
    )

    ens_cfg = ReplayConfig(
        instrument_ids=("okx:BTC-USDT",), bar_interval="1H", horizons=("1H",)
    )
    ens = MultiHorizonReplay(ens_cfg).run_with_bars(
        sandbox_factory=_mk_sandbox,
        bars_by_horizon={"1H": {"okx:BTC-USDT": list(bars)}},
    )
    assert ens.per_horizon["1H"].trades == plain.trades
    assert ens.combined_trades == plain.trades
    assert ens.combined_curve == plain.equity_curve_real_fee_net
    assert ens.combined_metrics["net_pnl"] == plain.metrics["net_pnl"]


# --- two horizons, same symbol: independent books, shared capital ----------
def test_two_horizons_same_symbol_independent_books() -> None:
    """Scalp (1m) + swing (1H) on the SAME symbol both trade and are NOT
    deduped — they are complementary independent positions (live parity)."""
    scalp = _uptrend_bars(n=120, interval="1m", step_sec=MINUTE, step_pct=0.002)
    swing = _uptrend_bars(n=60, interval="1H", step_sec=HOUR, step_pct=0.01)
    cfg = ReplayConfig(
        instrument_ids=("okx:BTC-USDT",), bar_interval="1H",
        horizons=("1m", "1H"),
    )
    res = MultiHorizonReplay(cfg).run_with_bars(
        sandbox_factory=_mk_sandbox,
        bars_by_horizon={
            "1m": {"okx:BTC-USDT": scalp},
            "1H": {"okx:BTC-USDT": swing},
        },
    )
    # Both sub-books produced trades (the horizons are not collapsed/deduped).
    assert res.per_horizon["1m"].trades, "scalp horizon must trade"
    assert res.per_horizon["1H"].trades, "swing horizon must trade"
    # The combined book is the UNION of both horizons (no dedup of the same
    # symbol across horizons).
    n_combined = len(res.combined_trades)
    n_union = len(res.per_horizon["1m"].trades) + len(res.per_horizon["1H"].trades)
    assert n_combined == n_union


# --- shared capital: one running equity over the union of all trades -------
def test_shared_capital_combined_equity_is_union_pnl() -> None:
    scalp = _uptrend_bars(n=120, interval="1m", step_sec=MINUTE, step_pct=0.002)
    swing = _uptrend_bars(n=60, interval="1H", step_sec=HOUR, step_pct=0.01)
    start_equity = 10_000.0
    cfg = ReplayConfig(
        instrument_ids=("okx:BTC-USDT",), bar_interval="1H",
        horizons=("1m", "1H"), starting_equity=start_equity,
    )
    res = MultiHorizonReplay(cfg).run_with_bars(
        sandbox_factory=_mk_sandbox,
        bars_by_horizon={
            "1m": {"okx:BTC-USDT": scalp},
            "1H": {"okx:BTC-USDT": swing},
        },
    )
    # Shared capital: the combined final equity is start + the net pnl of EVERY
    # horizon's trades accounted on ONE running base (not two parallel $10k
    # books). Accumulate in the SAME event order the curve uses so the assertion
    # is exact (float summation order is part of the shared-capital contract).
    ordered = sorted(
        (t for r in res.per_horizon.values() for t in r.trades),
        key=lambda t: (t.exit_ts, t.entry_ts, t.symbol, t.strategy),
    )
    equity = start_equity
    for t in ordered:
        equity += t.net_pnl_usd
    assert res.combined_curve[-1][1] == equity
    # net_pnl metric is the plain sum of every horizon's net pnl.
    assert res.combined_metrics["net_pnl"] == sum(t.net_pnl_usd for t in ordered)
    # The shared-capital base really is shared: the final equity is NOT what two
    # independent $10k books would each report summed (that would double-count
    # the starting capital). One base + union pnl, exactly.
    assert res.combined_curve[0][1] == start_equity
    # The combined curve is globally time-ordered (shared clock across horizons).
    ts_seq = [ts for ts, _ in res.combined_curve]
    assert ts_seq == sorted(ts_seq)


# --- determinism -----------------------------------------------------------
def test_ensemble_determinism() -> None:
    scalp = _uptrend_bars(n=120, interval="1m", step_sec=MINUTE, step_pct=0.002)
    swing = _uptrend_bars(n=60, interval="1H", step_sec=HOUR, step_pct=0.01)
    cfg = ReplayConfig(
        instrument_ids=("okx:BTC-USDT",), bar_interval="1H", horizons=("1m", "1H")
    )

    def _run() -> object:
        return MultiHorizonReplay(cfg).run_with_bars(
            sandbox_factory=_mk_sandbox,
            bars_by_horizon={
                "1m": {"okx:BTC-USDT": list(scalp)},
                "1H": {"okx:BTC-USDT": list(swing)},
            },
        )

    a = _run()
    b = _run()
    assert a.combined_trades == b.combined_trades  # type: ignore[attr-defined]
    assert a.combined_curve == b.combined_curve  # type: ignore[attr-defined]
    assert a.combined_metrics == b.combined_metrics  # type: ignore[attr-defined]


# --- cross-horizon capital contention (the headline shared-capital property) -
def _trade(*, entry_ts: int, exit_ts: int, strategy: str, net: float) -> ReplayTrade:
    """Minimal closed trade carrying only the fields the synthesis reads."""
    return ReplayTrade(
        entry_ts=entry_ts, exit_ts=exit_ts, symbol="BTC-USDT", strategy=strategy,
        regime="bull_trend", side="long", entry_price=100.0, exit_price=101.0,
        notional=1_000.0, gross_pnl_usd=net, rt_fee_usd=0.0, net_pnl_usd=net,
        pnl_r=net / 50.0, exit_reason="test",
    )


def _result(trades: list[ReplayTrade], *, start_ts: int, start_eq: float) -> ReplayResult:
    curve = build_equity_curve(
        trades=trades, starting_equity=start_eq, start_ts=start_ts
    )
    return ReplayResult(
        trades=tuple(trades), equity_curve_real_fee_net=tuple(curve), metrics={}
    )


def test_swing_return_measured_against_post_scalp_loss_base() -> None:
    """A scalp LOSS that closes BEFORE a swing trade must lower the equity base
    the swing trade's RETURN is measured against — that is shared capital, not
    two parallel books. We drive the synthesis with injected per-horizon results
    so the contention is exact and deterministic.

    Scalp: -200 closing at t=10. Swing: +300 closing at t=20. With ONE shared
    base of 10_000 the swing return is 300/(10_000-200)=300/9_800. If the books
    were independent (parallel $10k) it would be 300/10_000. The combined Sharpe
    is computed from those shared-base returns, so we assert the swing return the
    curve implies uses 9_800, not 10_000.
    """
    start_eq = 10_000.0
    scalp = _result([_trade(entry_ts=1, exit_ts=10, strategy="volume_burst", net=-200.0)],
                    start_ts=0, start_eq=start_eq)
    swing = _result([_trade(entry_ts=5, exit_ts=20, strategy="tsmom", net=300.0)],
                    start_ts=0, start_eq=start_eq)

    ens = MultiHorizonReplay(
        ReplayConfig(instrument_ids=("okx:BTC-USDT",), horizons=("1m", "1H"),
                     starting_equity=start_eq)
    )
    combined = ens._synthesise({"1m": scalp, "1H": swing})  # noqa: SLF001 — test seam

    # The union curve runs ONE base: 10_000 -> 9_800 (scalp loss) -> 10_100.
    eqs = [eq for _, eq in combined.combined_curve]
    assert eqs == [10_000.0, 9_800.0, 10_100.0]
    # Contention is real: the swing's net (+300) lands on the post-scalp base of
    # 9_800, NOT on a fresh 10_000 (which would give 10_300). One capital base.
    assert combined.combined_curve[-1][1] == 10_100.0
    assert combined.combined_metrics["net_pnl"] == 100.0
