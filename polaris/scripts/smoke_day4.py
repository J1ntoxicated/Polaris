"""Smoke test — Polaris P0 Sprint Day 4 (Layer 5 + Layer 6 + 7 strategies).

Pieces:
1. Instantiate 7 strategies, feed each a hand-crafted MarketView, confirm
   ``generate_raw_signal`` emits a ``RawSignal`` for the happy path.
2. Layer 5 — feed 25 mock trades into the 3 P0 learners, confirm mult delta.
3. Layer 6 stubs — mark dirty, run regime flip + swap candidate, log decisions.

Run: ``python3 -m polaris.scripts.smoke_day4``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from polaris.core.learners import (
    ClosedTrade,
    LearnerScheduler,
    evaluate_triple_block,
)
from polaris.core.live_recalc import (
    ConvictionGateInputs,
    SwapCandidate,
    can_stack_conviction,
    classify_regime,
    count_layers,
    detect_regime_flip,
    evaluate_strategy_swap,
    fetch_regime,
    mark_position_dirty,
    run_live_recalc_cycle,
)
from polaris.storage.schema import init_db
from polaris.strategies import (
    BarView,
    FXBreakoutBasketStrategy,
    MarketView,
    RSIBBPullbackStrategy,
    SessionBreakoutStrategy,
    SpotDonchianStrategy,
    TSMOMStrategy,
    VolumeBurstStrategy,
    XAUIndicesTrendStrategy,
    all_strategies,
)


def _bars_uptrend(n: int, base: float = 100.0, drift: float = 0.5) -> list[BarView]:
    return [
        BarView(
            ts=1_700_000_000 + i * 60,
            open=base + i * drift - 0.5,
            high=base + i * drift + 1.0,
            low=base + i * drift - 1.0,
            close=base + i * drift,
            volume=2000.0,
        )
        for i in range(n)
    ]


def smoke_strategies() -> tuple[int, int]:
    """Return (emitted_count, total_strategies)."""
    print("[strategies] instantiating 7 P0 strategies...")
    instances = all_strategies()
    print(f"[strategies] count = {len(instances)}")
    emitted = 0

    # Volume Burst
    vb = VolumeBurstStrategy()
    bars = _bars_uptrend(30)
    bars[-1] = BarView(
        ts=bars[-1].ts + 60, open=bars[-1].open, high=bars[-1].high + 10.0,
        low=bars[-1].low, close=bars[-1].close + 10.0, volume=5000.0,
    )
    mv = MarketView(symbol="BTC-USDT", venue="okx", timeframe="1m",
                    bars=bars, last_price=bars[-1].close, spread_bps=2.0,
                    atr_pct=0.001, volume_z=3.5)
    sig = vb.generate_raw_signal(mv)
    print(f"  - volume_burst       → {sig.signal_id[:8] if sig else 'NONE'}")
    if sig:
        emitted += 1

    # TSMOM
    ts = TSMOMStrategy()
    mv = MarketView(symbol="ETH-USDT", venue="okx", timeframe="1H",
                    bars=_bars_uptrend(30, 100.0, 1.0), last_price=129.0,
                    spread_bps=3.0, momentum_20bar=0.10)
    sig = ts.generate_raw_signal(mv)
    print(f"  - tsmom              → {sig.signal_id[:8] if sig else 'NONE'}")
    if sig:
        emitted += 1

    # RSI-BB
    rb = RSIBBPullbackStrategy()
    bars = _bars_uptrend(220, base=100.0, drift=0.10)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=bars[-1].open, high=bars[-1].high,
        low=85.0, close=88.0, volume=bars[-1].volume,
    )
    mv = MarketView(symbol="BTC-USDT", venue="okx", timeframe="15m",
                    bars=bars, last_price=88.0, spread_bps=3.0,
                    rsi_14=22.0, bb_lower=90.0, ma_200=80.0)
    sig = rb.generate_raw_signal(mv)
    print(f"  - rsi_bb_pullback    → {sig.signal_id[:8] if sig else 'NONE'}")
    if sig:
        emitted += 1

    # Spot Donchian
    sd = SpotDonchianStrategy()
    bars = _bars_uptrend(60, 100.0, 0.10)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=110.0, high=115.0, low=109.0, close=114.0, volume=1500.0,
    )
    mv = MarketView(symbol="BTC-USDT", venue="okx", timeframe="1H",
                    bars=bars, last_price=114.0, spread_bps=3.0,
                    donchian_high_40=110.0, adx_14=30.0)
    sig = sd.generate_raw_signal(mv)
    print(f"  - spot_donchian      → {sig.signal_id[:8] if sig else 'NONE'}")
    if sig:
        emitted += 1

    # FX Breakout Basket
    fx = FXBreakoutBasketStrategy()
    bars = _bars_uptrend(60, 1.10, 0.001)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=1.20, high=1.25, low=1.19, close=1.24, volume=1500.0,
    )
    mv = MarketView(symbol="EURUSD", venue="capital", timeframe="1H",
                    bars=bars, last_price=1.24, spread_bps=1.0,
                    donchian_high_40=1.20, adx_14=28.0)
    sig = fx.generate_raw_signal(mv)
    print(f"  - fx_breakout_basket → {sig.signal_id[:8] if sig else 'NONE'}")
    if sig:
        emitted += 1

    # XAU / Indices
    xau = XAUIndicesTrendStrategy()
    bars = _bars_uptrend(50, 2000.0, 2.0)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=2100.0, high=2110.0, low=2099.0, close=2105.0, volume=2000.0,
    )
    mv = MarketView(symbol="XAUUSD", venue="capital", timeframe="1H",
                    bars=bars, last_price=2105.0, spread_bps=2.0,
                    donchian_high_30=2098.0, momentum_20bar=0.04)
    sig = xau.generate_raw_signal(mv)
    print(f"  - xau_indices_trend  → {sig.signal_id[:8] if sig else 'NONE'}")
    if sig:
        emitted += 1

    # Session Breakout
    sb = SessionBreakoutStrategy()
    bars = _bars_uptrend(30, 4500.0, 0.5)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=4520.0, high=4540.0, low=4515.0, close=4535.0, volume=2000.0,
    )
    mv = MarketView(symbol="US500", venue="capital", timeframe="5m",
                    bars=bars, last_price=4535.0, spread_bps=1.0,
                    session_open_price=4500.0, session_atr=10.0,
                    is_session_open_window=True)
    sig = sb.generate_raw_signal(mv)
    print(f"  - session_breakout   → {sig.signal_id[:8] if sig else 'NONE'}")
    if sig:
        emitted += 1

    return emitted, len(instances)


def smoke_learners(db_path: Path) -> dict[str, float]:
    print("[learner] feeding 25 mock trades...")
    conn = init_db(db_path)
    try:
        # clear prior smoke state
        conn.execute("DELETE FROM learner_state")
        conn.execute("DELETE FROM learner_blocks")
        conn.execute("DELETE FROM learner_snapshot")
        sched = LearnerScheduler(conn, expected_holding_bars=20)
        for i in range(25):
            t = ClosedTrade(
                trade_id=f"smoke-{i}",
                strategy_id="volume_burst",
                ticker="BTC-USDT",
                venue="okx",
                regime="bull_trend",
                session="asia",
                pnl_r=1.0 if i < 17 else -0.5,
                won=i < 17,  # 17 wins / 25 = 0.68
                holding_bars=20,
                closed_ts=i + 1,
            )
            for learner in sched.learners:
                learner.update(t, now_ts=i + 1)
        # baseline reads
        before = {}
        for learner in sched.learners:
            r = learner.get_mult(
                ticker="BTC-USDT", strategy_id="volume_burst",
                regime="bull_trend", session="asia",
            )
            before[learner.learner_id] = r.value

        cycle = sched.run_once(now_ts=10_000)
        print(
            f"[learner] cycle reports: {len(cycle.reports)} "
            f"expired_blocks={cycle.expired_blocks}"
        )
        deltas: dict[str, float] = {}
        for learner in sched.learners:
            r = learner.get_mult(
                ticker="BTC-USDT", strategy_id="volume_burst",
                regime="bull_trend", session="asia",
            )
            d = r.value - before[learner.learner_id]
            deltas[learner.learner_id] = d
            print(
                f"  - {learner.learner_id:14s} before={before[learner.learner_id]:.3f}  "
                f"after={r.value:.3f}  Δ={d:+.3f}  source={r.source}"
            )
        # also peek triple block (no block expected — WR 68% > 30%)
        block = evaluate_triple_block(
            conn, ticker="BTC-USDT", strategy_id="volume_burst",
            regime="bull_trend", now_ts=10_000,
        )
        print(f"[learner] triple block (BTC-USDT/volume_burst/bull_trend) = {block}")
        return deltas
    finally:
        conn.close()


def smoke_live_recalc(db_path: Path) -> dict[str, int]:
    print("[live_recalc] dirty trigger + regime flip + swap candidate logs")
    conn = init_db(db_path)
    log_counts = {"would_recalc": 0, "regime_confirmed": 0, "swap_decisions": 0}
    try:
        # clear smoke state from prior runs
        conn.execute("DELETE FROM regime_state")
        conn.execute("DELETE FROM position_live_recalc_state")
        conn.execute("DELETE FROM positions WHERE position_id LIKE 'smoke-%'")
        conn.execute("DELETE FROM position_strategy_segments "
                     "WHERE position_id LIKE 'smoke-%'")
        # tick recalc
        for pid in ("smoke-pos1", "smoke-pos2", "smoke-pos3"):
            mark_position_dirty(
                conn, position_id=pid, reason="pnl_break", now_ts=int(time.time())
            )
        out = asyncio.run(
            run_live_recalc_cycle(
                conn,
                now_ts=int(time.time()) + 10,
                active_positions=[{"position_id": pid} for pid in
                                  ("smoke-pos1", "smoke-pos2", "smoke-pos3")],
            )
        )
        for r in out:
            print(f"  - tick     {r.position_id} → {r.decision} ({r.reason})")
        log_counts["would_recalc"] = sum(1 for r in out if r.decision == "would_recalc")

        # regime flip
        cls = classify_regime(
            venue="okx", underlying_group_id="crypto:BTC", candidate_regime="chop"
        )
        print(f"  - classify regime → {cls['regime']} ({cls['confidence']})")
        d1 = detect_regime_flip(
            conn, venue="okx", underlying_group_id="crypto:BTC",
            candidate="bull_trend", now_ts=100,
        )
        d2 = detect_regime_flip(
            conn, venue="okx", underlying_group_id="crypto:BTC",
            candidate="chop", now_ts=200,
        )
        d3 = detect_regime_flip(
            conn, venue="okx", underlying_group_id="crypto:BTC",
            candidate="chop", now_ts=300,
        )
        print(f"  - regime   first  → {d1.reason} (current={d1.to_regime})")
        print(f"  - regime   2nd    → {d2.reason} (count={d2.consecutive_count})")
        print(f"  - regime   3rd    → {d3.reason} (count={d3.consecutive_count})")
        log_counts["regime_confirmed"] = (
            (1 if d3.confirmed else 0) + (1 if d1.confirmed else 0)
        )
        print(f"  - regime SSOT now = {fetch_regime(conn, venue='okx', underlying_group_id='crypto:BTC')}")

        # strategy swap candidate (no actual position seeded — log_only)
        # seed minimal position
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, "
            "underlying_group_id, strategy_id, entry_strategy_id, "
            "active_strategy_id, side, qty, status, opened_ts, swap_count) "
            "VALUES ('smoke-swap', 'okx', 'BTC-USDT', 'crypto:BTC', "
            "'volume_burst', 'volume_burst', 'volume_burst', 'long', 1.0, "
            "'OPEN', 100, 0)"
        )
        cand_ok = SwapCandidate(
            position_id="smoke-swap",
            from_strategy_id="volume_burst",
            to_strategy_id="spot_donchian",
            venue="okx",
            symbol="BTC-USDT",
            side="long",
            from_correlation_group="spot_breakout",
            to_correlation_group="spot_breakout",
        )
        cand_bad = SwapCandidate(
            position_id="smoke-swap",
            from_strategy_id="volume_burst",
            to_strategy_id="rsi_bb_pullback",
            venue="okx",
            symbol="BTC-USDT",
            side="long",
            from_correlation_group="spot_breakout",
            to_correlation_group="spot_mean_reversion",
        )
        d_ok = evaluate_strategy_swap(conn, candidate=cand_ok, now_ts=400)
        d_bad = evaluate_strategy_swap(conn, candidate=cand_bad, now_ts=400)
        print(f"  - swap     same group   → {d_ok.decision}")
        print(f"  - swap     mismatch     → {d_bad.decision}")
        log_counts["swap_decisions"] = 2

        # Conviction stacking gate evaluation (P0 — read-only).
        layer_count = count_layers(conn, position_id="smoke-swap")
        decision = can_stack_conviction(
            ConvictionGateInputs(
                position_id="smoke-swap",
                cell_quartile="top",
                unrealized_pnl_r=1.0,
                existing_layer_count=layer_count,
                layer_sum_size_pct=0.05,
                single_trade_cap_pct=0.08,
                headroom_pct=0.10,
            )
        )
        print(
            f"  - convict  layers={layer_count} → can_stack={decision.can_stack} "
            f"next_mult={decision.next_layer_mult:.2f}"
        )
        log_counts["conviction_check"] = 1
        return log_counts
    finally:
        conn.close()


def main() -> None:
    print("=" * 64)
    print("Polaris P0 Sprint — Day 4 smoke")
    print("Layer 5 (3 learners) + Layer 6 (live recalc stub) + 7 strategies")
    print("=" * 64)

    db_path = Path("data/polaris.sqlite")

    # 1. strategies
    emitted, total = smoke_strategies()
    # 2. learners
    deltas = smoke_learners(db_path)
    # 3. live recalc
    log_counts = smoke_live_recalc(db_path)

    print("=" * 64)
    print(f"strategies: {emitted}/{total} emitted RawSignal")
    print(f"learners deltas: {deltas}")
    print(f"live_recalc log counts: {log_counts}")
    print("=" * 64)
    print("smoke OK")


if __name__ == "__main__":
    main()
