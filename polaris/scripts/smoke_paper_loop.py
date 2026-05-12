"""Day 5 paper-loop smoke (~60 s simulated, optional real-venue probes).

Tick (5 s):
- Layer 1 fetch bars (OKX REST real / Capital stub) → 7 strategies emit
  signals → Layer 2 G3 validator (stub GPT) → simulated fill via
  ``fill_normalizer`` → close oldest open trade per tick → Layer 4 cell
  matrix + Layer 5 learner updates → vault append.

Run: ``python3 -m polaris.scripts.smoke_paper_loop``. Add ``--real-okx`` for
a real BTC-USDT demo IOC (10 USDT) on us.okx.com, ``--real-capital`` for a
100-lot EURUSD open+close on the Capital demo.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import persist_fill
from polaris.core.learners import ClosedTrade, LearnerScheduler
from polaris.core.lifecycle.recover import hydrate_open_positions
from polaris.core.pipeline import (
    GateOrchestrator,
    build_exit_payload,
    build_monitor_payload,
    build_sizer_payload,
    build_validator_payload,
    build_watcher_payload,
)
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.gate_state import (
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.logging_config import DEFAULT_LOG_FILE, setup_polaris_logging
from polaris.scripts._smoke_fills import (
    SimulatedTrade,
    simulate_close,
    simulate_open_fill,
)
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.scripts._smoke_real_probes import real_capital_probe, real_okx_probe
from polaris.scripts._smoke_real_roundtrip import (
    run_capital_round_trip,
    run_okx_round_trip,
)
from polaris.storage.schema import init_db
from polaris.strategies import (
    BarView,
    BaseStrategy,
    FXBreakoutBasketStrategy,
    MarketView,
    RawSignal,
    RSIBBPullbackStrategy,
    SessionBreakoutStrategy,
    SpotDonchianStrategy,
    TSMOMStrategy,
    VolumeBurstStrategy,
    XAUIndicesTrendStrategy,
)
from polaris.venues.okx import fetch_okx_bars

logger = logging.getLogger(__name__)

DOTENV_PATH = Path(".env")
SMOKE_TICK_SEC = 5.0
SMOKE_DURATION_SEC = 60.0


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------


def _load_dotenv(path: Path = DOTENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Universe stub (Day 1 wires real Layer 0; for smoke we hard-code 4 + 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FocusEntry:
    venue: str
    symbol: str
    timeframe: str
    asset_class: str


FOCUS: tuple[FocusEntry, ...] = (
    FocusEntry("okx", "BTC-USDT", "1m", "crypto"),
    FocusEntry("okx", "ETH-USDT", "1m", "crypto"),
    FocusEntry("capital", "EURUSD", "1m", "forex"),
    FocusEntry("capital", "GOLD", "1m", "commodity"),
)


# ---------------------------------------------------------------------------
# Bar fetch
# ---------------------------------------------------------------------------


async def _fetch_bars_for_symbol(entry: FocusEntry, *, limit: int = 200) -> list[BarView]:
    """Fetch 1m bars and downcast to BarView (best-effort; stub on failure)."""
    if entry.venue == "okx":
        try:
            bars = await fetch_okx_bars(
                entry.symbol, bar_interval="1m", limit=limit, asset_class=entry.asset_class
            )
        except Exception as exc:  # noqa: BLE001 — fall back to stub
            logger.warning("[bars] okx %s fetch failed: %r — falling back to stub", entry.symbol, exc)
            return _stub_bars(limit)
        return [
            BarView(ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume)
            for b in reversed(bars)  # OKX returns newest first → flip
        ]
    # Capital path is auth-heavy; for smoke we use a deterministic stub so the
    # loop runs without venue creds. Real session probe is in test_capital_*.
    return _stub_bars(limit, base=1.10 if entry.symbol == "EURUSD" else 2050.0)


def _stub_bars(n: int, *, base: float = 60_000.0, drift: float = 0.5) -> list[BarView]:
    return [
        BarView(
            ts=int(time.time()) - (n - i) * 60,
            open=base + i * drift - 0.5,
            high=base + i * drift + 1.0,
            low=base + i * drift - 1.0,
            close=base + i * drift,
            volume=2000.0,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Strategy bundles per venue
# ---------------------------------------------------------------------------


def _okx_strategies() -> list[BaseStrategy]:
    return [
        VolumeBurstStrategy(),
        TSMOMStrategy(),
        RSIBBPullbackStrategy(),
        SpotDonchianStrategy(),
    ]


def _capital_strategies() -> list[BaseStrategy]:
    return [
        FXBreakoutBasketStrategy(),
        XAUIndicesTrendStrategy(),
        SessionBreakoutStrategy(),
    ]


# ---------------------------------------------------------------------------
# MarketView builder (smoke-friendly — synthesises just enough indicators
# for happy-path emission of every strategy)
# ---------------------------------------------------------------------------


def _build_market_view(
    *, entry: FocusEntry, bars: list[BarView]
) -> MarketView:
    last = bars[-1] if bars else BarView(0, 0, 0, 0, 0, 0)
    # Force the last bar to look like a high-conviction breakout so smoke
    # produces signals deterministically. This mirrors Day 4's smoke pattern.
    boosted = BarView(
        ts=last.ts + 60,
        open=last.open,
        high=last.high * 1.01 if last.high > 0 else last.high,
        low=last.low,
        close=last.close * 1.01 if last.close > 0 else last.close,
        volume=last.volume * 3.0,
    )
    bars = list(bars[:-1]) + [boosted] if bars else [boosted]
    last_price = boosted.close
    return MarketView(
        symbol=entry.symbol,
        venue=entry.venue,
        timeframe=entry.timeframe,
        bars=bars,
        last_price=last_price,
        spread_bps=2.0,
        atr_pct=0.001,
        volume_z=3.5,
        rsi_14=22.0,
        bb_lower=last_price * 0.99,
        bb_upper=last_price * 1.05,
        bb_middle=last_price,
        ma_200=last_price * 0.9,
        adx_14=30.0,
        momentum_20bar=0.10,
        donchian_high_40=last_price * 0.99,
        donchian_low_40=last_price * 0.95,
        donchian_high_30=last_price * 0.99,
        donchian_low_30=last_price * 0.95,
        session_open_price=last_price * 0.99,
        session_open_ts=int(time.time()) - 1800,
        session_atr=last_price * 0.005,
        is_session_open_window=True,
    )


# ---------------------------------------------------------------------------
# Tick orchestration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoopState:
    fills_open: list[Fill]
    fills_close: list[Fill]
    open_trades: list[SimulatedTrade]
    closed_trades: list[SimulatedTrade]
    signals_emitted: int = 0
    pipeline_runs: int = 0
    pipeline_kills: int = 0
    gate_pass_counts: dict[int, int] = field(default_factory=dict)
    fills_persisted: int = 0
    full_pipeline_runs: int = 0
    full_pipeline_sized: int = 0


async def _validate_signal(
    strategy: BaseStrategy,
    entry: FocusEntry,
    sig: RawSignal,
    haiku: StubGPTClient,
    state: LoopState,
) -> bool:
    """Run G3 (signal validator) on one signal. Return True on PASS."""
    ctx = GateContext(
        run_id=uuid.uuid4().hex,
        signal_id=sig.signal_id,
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue=entry.venue,
        symbol=entry.symbol,
        strategy_id=strategy.metadata.strategy_id,
        payload={
            "signal_id": sig.signal_id,
            "side": sig.side,
            "strength": sig.strength,
            "thesis_tag": sig.thesis_tag,
            "raw_signal": {
                "strategy_id": sig.strategy_id,
                "symbol": sig.symbol,
                "side": sig.side,
                "strength": sig.strength,
                "sizing_hint": sig.sizing_hint,
                "ttl_bars": sig.ttl_bars,
                "thesis_tag": sig.thesis_tag,
            },
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )
    result = await signal_validator_gate(ctx, client=haiku)
    state.pipeline_runs += 1
    if result.decision == GateDecision.KILL:
        state.pipeline_kills += 1
        return False
    return True


async def _run_full_pipeline(
    *,
    strategy: BaseStrategy,
    entry: FocusEntry,
    sig: RawSignal,
    bars: list[BarView],
    conn: Any,
    haiku: StubGPTClient,
    state: LoopState,
) -> None:
    """G3 -> G4 -> G5 -> G6 -> G7 chain with production-shaped payloads.

    Day 6 plumbing:
    - G3 input: build_validator_payload (raw signal + cell + baseline + recent).
    - G4 input: build_watcher_payload (validated signal stamped by G3).
    - G5 input: build_sizer_payload (SignalIntent + StrategyRiskState +
      PortfolioState).
    - G6 input: build_monitor_payload (position + R-multiples).
    - G7 input: build_exit_payload (widen proposal).

    Each gate's output payload is stamped into ``ctx.payload`` so the next
    gate sees it (the orchestrator handles propagation).
    """
    instrument_id = f"{entry.venue}:{entry.symbol}"
    asset_class = entry.asset_class
    regime = "bull_trend"
    track: Any = "A"
    last_price = bars[-1].close if bars else 100.0

    # Compose G3 payload first; subsequent payloads layer on top.
    g3_payload = build_validator_payload(
        raw_signal=sig,
        venue=entry.venue,
        symbol=entry.symbol,
        instrument_id=instrument_id,
        regime=regime,
        conn=conn,
    )
    g4_payload = build_watcher_payload(
        spread_bps=2.0,
        baseline_p50_spread_bps=2.5,
        listing_age_hours=24 * 365.0,  # mature symbol
        recent_reject_in_6h=False,
        session_open_shock_window=False,
        tick_window=[],
    )
    g5_payload = build_sizer_payload(
        raw_signal=sig,
        venue=entry.venue,
        symbol=entry.symbol,
        instrument_id=instrument_id,
        underlying_group_id=entry.symbol.split("-")[0],
        asset_class=asset_class,
        regime=regime,
        track=track,
        listing_age_hours=24 * 365.0,
        leverage=1.0 if entry.venue == "okx" else 30.0,
        equity_usd=10_000.0,
        conn=conn,
    )

    payload: dict[str, Any] = {
        "signal_id": sig.signal_id,
        **g3_payload,
        **g4_payload,
        **g5_payload,
    }
    ctx = GateContext(
        run_id=uuid.uuid4().hex,
        signal_id=sig.signal_id,
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue=entry.venue,
        symbol=entry.symbol,
        strategy_id=strategy.metadata.strategy_id,
        payload=payload,
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )
    orch = GateOrchestrator(conn=conn, haiku_client=haiku)
    results = await orch.run(ctx, start_gate=GATE_SIGNAL_VALIDATOR)
    state.full_pipeline_runs += 1
    decisions: dict[int, str] = {}
    for r in results:
        # Use the last seen ctx.gate_id as a fallback; results don't carry id.
        # Each result already advanced the lifecycle, so we count by decision.
        decisions[len(decisions) + 3] = str(r.decision)
    # Track which gates passed for visibility.
    state.pipeline_runs += len(results)
    if any(r.decision == GateDecision.KILL for r in results):
        state.pipeline_kills += 1

    # Did sizing succeed? G5 emits SIZED when final_risk_pct > 0.
    sized_payload: dict[str, Any] | None = None
    for r in results:
        if r.decision == GateDecision.SIZED:
            sized_payload = r.payload.get("sized")
            state.full_pipeline_sized += 1
            break
    if sized_payload is None:
        return

    # Translate the sizing decision into a Fill via simulate_open_fill.
    # The notional is bounded by single_trade_cap × equity in build_sizer_payload.
    notional = float(sized_payload.get("final_notional_usd", 50.0))
    notional = max(10.0, min(notional, 5_000.0))
    fill, trade = simulate_open_fill(
        signal=sig, venue=entry.venue, last_price=last_price, notional_usd=notional
    )
    state.fills_open.append(fill)
    state.open_trades.append(trade)
    try:
        persist_fill(conn, fill, is_close=False)
        state.fills_persisted += 1
    except Exception as exc:  # noqa: BLE001 — must not abort smoke
        logger.error("[full-pipeline] persist_fill failed: %r", exc)

    # G6 / G7: simulate a 1-tick monitor + exit decision so the lifecycle
    # advances. Use a position dict matching G6 schema.
    position = {
        "venue": entry.venue,
        "symbol": entry.symbol,
        "side": sig.side,
        "strategy": sig.strategy_id,
        "correlation_group": entry.symbol.split("-")[0],
        "entry_price": last_price,
        "qty": notional / max(last_price, 1e-6),
    }
    g6_payload = build_monitor_payload(
        position=position,
        unrealized_pnl_r=0.2,  # mid-trade — neither stop nor widen
        max_loss_r=1.0,
    )
    g7_payload = build_exit_payload(
        side=sig.side,
        current_stop_price=last_price * (0.99 if sig.side == "long" else 1.01),
        proposed_stop_price=last_price * (0.985 if sig.side == "long" else 1.015),
        entry_price=last_price,
        unrealized_pnl_r=0.2,
        max_loss_r=1.0,
        overrides_used=0,
        seconds_since_last_override=60,
    )
    # Drive G6 + G7 directly so we exercise the full chain without re-running G3.
    from polaris.core.pipeline.agents import (
        adaptive_exit_gate,
        position_monitor_gate,
    )
    from polaris.core.pipeline.gate_state import (
        GATE_ADAPTIVE_EXIT,
        GATE_POSITION_MONITOR,
    )

    g6_ctx = GateContext(
        run_id=ctx.run_id,
        signal_id=sig.signal_id,
        position_id=f"pos_{sig.signal_id[:10]}",
        gate_id=GATE_POSITION_MONITOR,
        venue=entry.venue,
        symbol=entry.symbol,
        strategy_id=sig.strategy_id,
        payload=g6_payload,
        started_ts=int(time.time()),
        state=SignalLifecycle.SIZED,
    )
    g6_result = await position_monitor_gate(g6_ctx)
    state.gate_pass_counts[6] = state.gate_pass_counts.get(6, 0) + 1
    g7_ctx = GateContext(
        run_id=ctx.run_id,
        signal_id=sig.signal_id,
        position_id=g6_ctx.position_id,
        gate_id=GATE_ADAPTIVE_EXIT,
        venue=entry.venue,
        symbol=entry.symbol,
        strategy_id=sig.strategy_id,
        payload={**g6_payload, **g7_payload, "current_stop_price": g7_payload["current_stop_price"]},
        started_ts=int(time.time()),
        state=(
            SignalLifecycle.MONITORED
            if g6_result.decision == GateDecision.HOLD
            else SignalLifecycle.ACTIVE
        ),
    )
    await adaptive_exit_gate(g7_ctx)
    state.gate_pass_counts[7] = state.gate_pass_counts.get(7, 0) + 1


def _close_oldest_trade(
    state: LoopState, *, conn: Any, tick_idx: int
) -> None:
    """Force-close the oldest open trade and propagate to L4 + L5."""
    trade = state.open_trades.pop(0)
    # Aggressive bias: assume positive PnL so learners + cell_matrix get a win.
    signed_drift = 1.005 if trade.side == "long" else 0.995
    close_fill = simulate_close(trade, exit_price=trade.entry_price * signed_drift)
    trade.closed = True
    trade.pnl_r = 1.0
    state.fills_close.append(close_fill)
    state.closed_trades.append(trade)
    now_ts = int(time.time())
    # Day 6: persist the close-side Fill row with PnL.
    try:
        # Approximate per-trade USD PnL from R-units × notional × ATR proxy.
        pnl_usd = trade.pnl_r * trade.notional_usd * 0.005
        persist_fill(conn, close_fill, is_close=True, pnl_usd=pnl_usd)
        state.fills_persisted += 1
    except Exception as exc:  # noqa: BLE001
        logger.error("[tick %d] persist_fill close failed: %r", tick_idx, exc)
    try:
        update_on_trade_close(
            conn,
            trade=TradeClose(
                key=CellKeyP0(
                    exchange=trade.venue,
                    strategy=trade.strategy_id,
                    ticker=trade.symbol,
                    regime="bull_trend",
                ),
                context=CellContext(
                    group="spot" if trade.venue == "okx" else "cfd",
                    session="asia",
                    direction=trade.side,
                    liquidity_tier="top",
                ),
                pnl_r=trade.pnl_r,
                won=trade.pnl_r > 0.0,
                closed_ts=now_ts,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[tick %d] cell matrix update failed: %r", tick_idx, exc)
    sched = LearnerScheduler(conn, expected_holding_bars=20)
    closed = ClosedTrade(
        trade_id=trade.signal_id,
        strategy_id=trade.strategy_id,
        ticker=trade.symbol,
        venue=trade.venue,
        regime="bull_trend",
        session="asia",
        pnl_r=trade.pnl_r,
        won=trade.pnl_r > 0.0,
        holding_bars=20,
        closed_ts=now_ts,
    )
    for learner in sched.learners:
        learner.update(closed, now_ts=now_ts)


async def _run_strategy_for_focus(
    strategy: BaseStrategy,
    entry: FocusEntry,
    bars: list[BarView],
) -> RawSignal | None:
    """Per-strategy isolation: a single ``generate_raw_signal`` is awaited in
    its own task so a misbehaving strategy cannot poison its peers."""
    if strategy.metadata.venue != entry.venue:
        return None
    if strategy.metadata.asset_class not in (entry.asset_class, "spot", "fx", "index", "commodity"):
        # Strategies declare asset class loosely; we let mismatches fall through.
        pass
    mv = _build_market_view(entry=entry, bars=bars)
    try:
        return strategy.generate_raw_signal(mv)
    except Exception as exc:  # noqa: BLE001 — isolation invariant
        logger.error(
            "[strategy] %s raised on %s: %r",
            strategy.metadata.strategy_id,
            entry.symbol,
            exc,
        )
        return None


async def _run_one_tick(
    *,
    state: LoopState,
    conn: Any,
    haiku: StubGPTClient,
    bars_cache: dict[str, list[BarView]],
    tick_idx: int,
    full_pipeline: bool = False,
) -> None:
    logger.info("[tick %d] starting cycle", tick_idx)
    # Fetch bars for every focus entry concurrently.
    bar_results = await asyncio.gather(
        *(_fetch_bars_for_symbol(e) for e in FOCUS), return_exceptions=True
    )
    for entry, bars in zip(FOCUS, bar_results, strict=False):
        if isinstance(bars, BaseException) or not bars:
            bars_cache[f"{entry.venue}:{entry.symbol}"] = _stub_bars(120)
            continue
        bars_cache[f"{entry.venue}:{entry.symbol}"] = bars
    # Run all 7 strategies × 4 focus entries in isolated tasks.
    okx_strats = _okx_strategies()
    cap_strats = _capital_strategies()
    tasks: list[asyncio.Task[RawSignal | None]] = []
    pairs: list[tuple[BaseStrategy, FocusEntry]] = []
    for entry in FOCUS:
        bars = bars_cache.get(f"{entry.venue}:{entry.symbol}") or []
        strats = okx_strats if entry.venue == "okx" else cap_strats
        for s in strats:
            pairs.append((s, entry))
            tasks.append(asyncio.create_task(_run_strategy_for_focus(s, entry, bars)))
    raw_signals = await asyncio.gather(*tasks)
    emitted = [(p, sig) for p, sig in zip(pairs, raw_signals, strict=False) if sig is not None]
    state.signals_emitted += len(emitted)
    logger.info("[tick %d] strategies emitted %d raw signals", tick_idx, len(emitted))
    # Day 6: full pipeline mode walks G3 -> G4 -> G5 -> G6 -> G7 with
    # production-shaped payloads (build_*_payload). Day 5 backward-compat
    # mode keeps the G3-only path so existing smoke tests still cover that
    # surface.
    for (strategy, entry), sig in emitted[:3]:  # cap per tick to keep loop snappy
        bars = bars_cache.get(f"{entry.venue}:{entry.symbol}") or []
        if full_pipeline:
            await _run_full_pipeline(
                strategy=strategy, entry=entry, sig=sig, bars=bars,
                conn=conn, haiku=haiku, state=state,
            )
            continue
        passed = await _validate_signal(strategy, entry, sig, haiku, state)
        if not passed:
            continue
        last_price = bars[-1].close if bars else 100.0
        fill, trade = simulate_open_fill(
            signal=sig, venue=entry.venue, last_price=last_price, notional_usd=50.0
        )
        state.fills_open.append(fill)
        state.open_trades.append(trade)
        try:
            persist_fill(conn, fill, is_close=False)
            state.fills_persisted += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("[tick %d] persist_fill open failed: %r", tick_idx, exc)
    # Force-close one open trade per tick so we exercise the close path.
    if state.open_trades:
        _close_oldest_trade(state, conn=conn, tick_idx=tick_idx)
    logger.info(
        "[tick %d] done — open=%d closed=%d kills=%d",
        tick_idx,
        len(state.open_trades),
        len(state.closed_trades),
        state.pipeline_kills,
    )


# ---------------------------------------------------------------------------
# Vault append
# ---------------------------------------------------------------------------


def _append_vault(state: LoopState, *, real_okx: dict[str, Any], real_capital: dict[str, Any]) -> Path:
    today = time.strftime("%Y-%m-%d")
    daily_dir = Path("vault/40_ops/daily")
    daily_dir.mkdir(parents=True, exist_ok=True)
    p = daily_dir / f"{today}.md"
    header_needed = not p.exists()
    block = [
        "",
        f"## Day 5 paper loop smoke — {time.strftime('%H:%M:%S')}",
        "",
        f"- signals_emitted: {state.signals_emitted}",
        f"- pipeline_runs: {state.pipeline_runs}",
        f"- pipeline_kills: {state.pipeline_kills}",
        f"- fills_open: {len(state.fills_open)}",
        f"- fills_close: {len(state.fills_close)}",
        f"- closed_trades: {len(state.closed_trades)}",
        f"- real_okx: {real_okx}",
        f"- real_capital: {real_capital}",
    ]
    text = ("\n".join(block) + "\n")
    if header_needed:
        text = (
            "---\n"
            f"date: {today}\n"
            "type: daily-ops\n"
            "tags: [polaris, daily, p0-sprint]\n"
            "---\n\n# Polaris daily ops log\n"
        ) + text
    with p.open("a", encoding="utf-8") as fh:
        fh.write(text)
    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_smoke(
    *,
    duration_sec: float = SMOKE_DURATION_SEC,
    tick_sec: float = SMOKE_TICK_SEC,
    real_okx: bool = False,
    real_capital: bool = False,
    full_pipeline: bool = False,
    real_roundtrip: bool = False,
    roundtrip_dry_run: bool = False,
    db_path: Path | None = None,
) -> int:
    """Run the smoke loop. ``db_path`` defaults to ``data/polaris.sqlite``;
    ``ignite_p1`` passes its own path so paper-mode persists to the same DB
    that bootstrap created (codex Day 6 P0 fix)."""
    _load_dotenv()
    if db_path is None:
        db_path = Path("data/polaris.sqlite")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)
    haiku = StubGPTClient()
    state = LoopState(
        fills_open=[], fills_close=[], open_trades=[], closed_trades=[]
    )
    try:
        state.open_trades.extend(hydrate_open_positions(conn))
    except Exception:
        logger.exception(
            "[hydrate] hydrate_open_positions failed — OPEN positions not restored"
        )
        raise
    if state.open_trades:
        logger.info(
            "[hydrate] restored %d open trades from prior session",
            len(state.open_trades),
        )
    bars_cache: dict[str, list[BarView]] = {}
    deadline = time.monotonic() + duration_sec
    tick_idx = 0
    while time.monotonic() < deadline:
        tick_idx += 1
        try:
            await _run_one_tick(
                state=state, conn=conn, haiku=haiku, bars_cache=bars_cache,
                tick_idx=tick_idx, full_pipeline=full_pipeline,
            )
        except Exception as exc:  # noqa: BLE001 — smoke must not abort
            logger.error("[tick %d] error: %r", tick_idx, exc)
        await asyncio.sleep(tick_sec)
    real_okx_result: dict[str, Any] = {"skipped": True}
    real_capital_result: dict[str, Any] = {"skipped": True}
    real_round_trip_okx: dict[str, Any] = {"skipped": True}
    real_round_trip_cap: dict[str, Any] = {"skipped": True}
    if real_okx:
        real_okx_result = await real_okx_probe()
    if real_capital:
        real_capital_result = await real_capital_probe()
    if real_roundtrip:
        real_round_trip_okx = await run_okx_round_trip(
            conn=conn, dry_run=roundtrip_dry_run,
        )
        real_round_trip_cap = await run_capital_round_trip(
            conn=conn, dry_run=roundtrip_dry_run,
        )
    logger.info("=========== Day 6 smoke summary ===========")
    logger.info("full_pipeline_mode: %s", full_pipeline)
    logger.info("signals_emitted   : %d", state.signals_emitted)
    logger.info("pipeline_runs     : %d", state.pipeline_runs)
    logger.info("pipeline_kills    : %d", state.pipeline_kills)
    logger.info("full_pipeline_runs: %d", state.full_pipeline_runs)
    logger.info("full_pipeline_sized: %d", state.full_pipeline_sized)
    logger.info("gate_pass_counts  : %s", dict(state.gate_pass_counts))
    logger.info("fills_open        : %d", len(state.fills_open))
    logger.info("fills_close       : %d", len(state.fills_close))
    logger.info("fills_persisted   : %d", state.fills_persisted)
    logger.info("closed_trades     : %d", len(state.closed_trades))
    logger.info("gpt_calls(stub)  : %d", haiku.call_count)
    logger.info("real_okx          : %s", real_okx_result)
    logger.info("real_capital      : %s", real_capital_result)
    logger.info("real_rt_okx       : %s", real_round_trip_okx)
    logger.info("real_rt_cap       : %s", real_round_trip_cap)
    vault_path = _append_vault(
        state, real_okx=real_okx_result, real_capital=real_capital_result
    )
    logger.info("vault appended    : %s", vault_path)
    return 0 if state.closed_trades else 1


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smoke_paper_loop")
    parser.add_argument("--duration", type=float, default=SMOKE_DURATION_SEC)
    parser.add_argument("--tick", type=float, default=SMOKE_TICK_SEC)
    parser.add_argument("--real-okx", action="store_true", default=False)
    parser.add_argument("--real-capital", action="store_true", default=False)
    parser.add_argument("--full-pipeline", action="store_true", default=False)
    parser.add_argument("--real-roundtrip", action="store_true", default=False)
    parser.add_argument("--roundtrip-dry-run", action="store_true", default=False)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--verbose", "-v", action="count", default=0,
        help="Verbose logging: -v=INFO (default), -vv=DEBUG.",
    )
    parser.add_argument(
        "--log-file", type=str, default=DEFAULT_LOG_FILE,
        help=f"Log file path (default: {DEFAULT_LOG_FILE}).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    # Wire root logger so standalone smoke runs aren't silent (the default
    # WARNING level would swallow every INFO emitted by the pipeline). When
    # invoked via ignite_p1 the parent already set this up; force=True keeps
    # the latest call winning so the parent's handlers are honoured.
    log_level = "DEBUG" if args.verbose >= 2 else "INFO"
    setup_polaris_logging(level=log_level, log_file=args.log_file)
    return asyncio.run(
        run_smoke(
            duration_sec=args.duration,
            tick_sec=args.tick,
            real_okx=args.real_okx,
            real_capital=args.real_capital,
            full_pipeline=args.full_pipeline,
            real_roundtrip=args.real_roundtrip,
            roundtrip_dry_run=args.roundtrip_dry_run,
            db_path=args.db,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
