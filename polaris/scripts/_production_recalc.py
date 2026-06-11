"""Day 9 F2 — live recalc loop with G6/G7 GPT per-position invocation.

Spec source: vault/30_components/layer-6-live-recalc.md (Q1 5s cadence) +
layer-2-per-gate-pipeline.md (Q3 G6/G7 P1=GPT) + ADR-004 (per-gate AI).

Design (Jin 2026-05-07 mandate):
- Per dirty active position, build a fresh G6 payload from real DB state
  (positions row + recent bars) and call ``position_monitor_gate`` with the
  GPT client (P1) so every monitored position receives a GPT decision per
  5s tick — replaces the old "G6 fires once at entry, then dead" wiring.
- G6 EXIT_NOW emits a *specific* close (by ``position_id`` /
  ``contribution_id``) so the close path is no longer FIFO ``oldest pop``.
- G6 ADJUST_EXIT triggers G7 (also GPT P1) to widen/hold.
- G6 SWAP_STRATEGY hands off to Layer 6 SSOT
  ``polaris.core.live_recalc.strategy_swap.evaluate_strategy_swap``.
- #26: deterministic precise-exit engine runs per tick BEFORE G6 (see
  ``_production_recalc_exit.run_precise_exit``) — ATR-trail / FSM / loser
  timeout. EXPECTANCY only: no size change, no entry block, no halt.
P0 fallback (``phase="P0"``): the GPT client is suppressed at the call site so
G6/G7 emit deterministic Python decisions; the module stays wired for Layer 4/5
telemetry parity (one gate_events row per active position per tick).
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.core.live_recalc.strategy_swap import (
    SwapCandidate,
    evaluate_strategy_swap,
)
from polaris.core.live_recalc.tick_recalc import (
    LIVE_RECALC_MAX_POSITIONS,
    mark_position_dirty,
)
from polaris.core.pipeline import (
    GateContext,
    GateDecision,
    SignalLifecycle,
    build_exit_payload,
    build_monitor_payload,
)
from polaris.core.pipeline.agents import (
    adaptive_exit_gate,
    position_monitor_gate,
)
from polaris.core.pipeline.gate_orchestrator import log_gate_event
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_POSITION_MONITOR,
)
from polaris.core.streams import resolve_stream_profile
from polaris.scripts._production_atr import strategy_timeframe, timeframe_atr_pct
from polaris.scripts._production_bars import BAR_TS_CLOCK_SKEW_SLACK_SEC
from polaris.scripts._production_indicators import compute_unrealized_pnl_r
from polaris.scripts._production_recalc_exit import (
    run_precise_exit,
    run_session_forced_exit,
)

if TYPE_CHECKING:
    from polaris.scripts._smoke_fills import SimulatedTrade
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

# P4 #2 — WS exit-mark freshness threshold (M6). Judged on ``time.monotonic()``
# against ``QuoteTickWriter.live_px`` (never venue ts). Set ABOVE the WS reconnect
# worst-case (BACKOFF_CAP_SEC=30s) so a single reconnect cannot flap the exit
# mark between the WS tick and the bar close. A tick older than this → bar close
# fallback (graceful degrade, never halts).
WS_EXIT_MARK_FRESH_SEC = 35.0

__all__ = [
    "ActivePositionRow",
    "find_open_trade_by_position_id",
    "load_active_position_rows",
    "recalc_active_positions",
]


# ---------------------------------------------------------------------------
# Helpers — read live position state from SQLite
# ---------------------------------------------------------------------------


class ActivePositionRow(dict[str, Any]):
    """Position row joined with its entry fill price + recent bar close."""


def load_active_position_rows(
    conn: sqlite3.Connection,
    *,
    limit: int = LIVE_RECALC_MAX_POSITIONS,
    quote_writer: Any = None,
    tf_atr_cache: dict[tuple[str, str], tuple[float, int]] | None = None,
) -> list[ActivePositionRow]:
    """Read active positions + matching entry fill + most-recent bar close.

    P4 #2 (LIVE exit mark): when ``quote_writer`` is supplied and carries a FRESH
    WS tick (monotonic age < ``WS_EXIT_MARK_FRESH_SEC``, M6) for the instrument,
    ``last_price`` is the live WS mid so the precise-exit engine + G6 react to
    real-time price. No fresh tick (no WS / stale / reconnecting) → the bar close
    fallback (graceful degrade, never halts — AGGRESSIVE invariant).

    Timeframe-aligned exit ruler: ``atr_pct`` is read on the ACTIVE strategy's
    own timeframe (1H tsmom → 1H ATR; unregistered tick-engine ids → the 1m
    window, byte-identical pre-fix). ``entry_atr_pct`` / ``entry_atr_timeframe``
    expose the entry-time R anchor; a legacy NULL anchor sets
    ``anchor_missing=True`` and the consumer denominates by the current
    timeframe ATR instead (graceful, never halts).
    """
    rows = conn.execute(
        """
        SELECT p.position_id, p.venue, p.symbol, p.underlying_group_id,
               p.strategy_id, p.entry_strategy_id, p.active_strategy_id,
               p.side, p.qty, p.opened_ts,
               p.stop_price, p.peak_price, p.trough_price, p.exit_state,
               p.entry_atr_pct, p.entry_atr_timeframe
        FROM positions p
        WHERE p.status NOT IN ('closed','cancelled','reconciled')
        ORDER BY p.opened_ts DESC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[ActivePositionRow] = []
    for r in rows:
        position_id = str(r[0])
        venue = str(r[1])
        symbol = str(r[2])
        # Entry fill via contribution_id == position_id (Day 8 P0 fix
        # contract).
        fill_row = conn.execute(
            """
            SELECT fill_price, size_usd FROM fills
            WHERE contribution_id = ? AND instrument_id = ? AND is_close = 0
            ORDER BY ts_ms ASC LIMIT 1
            """,
            (position_id, f"{venue}:{symbol}"),
        ).fetchone()
        if fill_row is None:
            continue
        entry_price = float(fill_row[0])
        size_usd = float(fill_row[1])
        # FIX-2 (2026-05-30): pull volume too (newest first) so the G6 monitor
        # sees REAL market data — the prior wiring hardcoded volume_now=0.0 and
        # recent_ticks=[] for every position, blinding the monitor.
        instrument_id = f"{venue}:{symbol}"
        # Exclude FUTURE-dated bars (stale +10h Capital) so the G6 monitor /
        # exit mark never treats a +10h ghost bar as the current price.
        ts_upper = int(time.time()) + BAR_TS_CLOCK_SKEW_SLACK_SEC
        bar_row = conn.execute(
            """
            SELECT ts, close, high, low, volume FROM bars
            WHERE instrument_id = ? AND bar_interval = '1m' AND ts <= ?
            ORDER BY ts DESC LIMIT 20
            """,
            (instrument_id, ts_upper),
        ).fetchall()
        bar_close = float(bar_row[0][1]) if bar_row else entry_price
        # P4 #2 — prefer a FRESH WS tick mid (M6: monotonic age check), else the
        # bar close. live_px returns (mid, last_ws_monotonic); a tick older than
        # the reconnect-proof threshold degrades to the bar (no flap).
        last_price = bar_close
        if quote_writer is not None:
            px = quote_writer.live_px(instrument_id)
            if px is not None:
                mid, last_ws_monotonic = px
                if (
                    mid > 0.0
                    and time.monotonic() - last_ws_monotonic < WS_EXIT_MARK_FRESH_SEC
                ):
                    last_price = mid
        atr_samples = [
            (float(br[2]) - float(br[3])) / float(br[1])
            for br in bar_row
            if float(br[1]) > 0.0
        ]
        atr_pct = sum(atr_samples) / len(atr_samples) if atr_samples else 0.005
        # Timeframe-aligned ruler: a non-1m strategy reads its OWN timeframe
        # ATR (trail width / G6 atr_pct / G7 widen step share one ruler). "1m"
        # keeps the in-hand window above — byte-identical, no second query.
        active_strategy_id = str(r[6] or r[4])
        tf = strategy_timeframe(active_strategy_id)
        if tf != "1m":
            tf_atr = timeframe_atr_pct(
                conn, instrument_id=instrument_id, timeframe=tf,
                now_ts=int(time.time()), cache=tf_atr_cache,
            )
            if tf_atr is not None:
                atr_pct = tf_atr
        entry_atr_pct = None if r[14] is None else float(r[14])
        if entry_atr_pct is None:
            # Legacy row (pre-anchor) — R denominator falls back to the
            # CURRENT timeframe ATR above. DEBUG-only visibility.
            logger.debug(
                "[L6/atr] anchor missing for %s (legacy row) — current %s "
                "ATR denominates", position_id, tf,
            )
        market = _recent_market_state(bar_row)
        ap = ActivePositionRow()
        ap.update(
            position_id=position_id,
            venue=venue,
            symbol=symbol,
            underlying_group_id=str(r[3] or ""),
            strategy=str(r[4]),
            entry_strategy_id=str(r[5] or r[4]),
            active_strategy_id=str(r[6] or r[4]),
            side=str(r[7]),
            qty=float(r[8]),
            opened_ts=int(r[9]),
            entry_price=entry_price,
            last_price=last_price,
            size_usd=size_usd,
            atr_pct=atr_pct,
            correlation_group=str(r[3] or ""),
            volume_now=market["volume_now"],
            volume_z=market["volume_z"],
            atr_slope=market["atr_slope"],
            recent_ticks=market["recent_ticks"],
            # #26 precise-exit tracked state (NULL until first tick populates).
            stop_price=None if r[10] is None else float(r[10]),
            peak_price=None if r[11] is None else float(r[11]),
            trough_price=None if r[12] is None else float(r[12]),
            exit_state=str(r[13]) if r[13] is not None else "open",
            # Entry-time ATR anchor (R-unit denominator; NULL = legacy row).
            entry_atr_pct=entry_atr_pct,
            entry_atr_timeframe=None if r[15] is None else str(r[15]),
            anchor_missing=entry_atr_pct is None,
        )
        out.append(ap)
    return out


def _recent_market_state(
    bar_row: list[tuple[Any, ...]],
) -> dict[str, Any]:
    """FIX-2 — derive real G6 market inputs from the recent bar rows.

    ``bar_row`` is ``(ts, close, high, low, volume)`` newest-first. Returns the
    live volume of the latest bar, its z-score over the trailing window, the
    ATR slope (recent-half ATR% vs older-half ATR%), and a newest-last list of
    recent ticks ``{ts, close, volume}`` for the G6 prompt. Empty input → all
    neutral (0.0 / empty list) so the monitor stays fail-open.
    """
    if not bar_row:
        return {"volume_now": 0.0, "volume_z": 0.0, "atr_slope": 0.0, "recent_ticks": []}
    # Oldest-first views for slope / z windows.
    closes = [float(br[1]) for br in reversed(bar_row)]
    highs = [float(br[2]) for br in reversed(bar_row)]
    lows = [float(br[3]) for br in reversed(bar_row)]
    volumes = [float(br[4]) for br in reversed(bar_row)]
    volume_now = volumes[-1]
    # Volume z-score of the latest bar over the trailing window (exclude last).
    trailing = volumes[:-1]
    volume_z = 0.0
    if len(trailing) >= 2:
        mu = sum(trailing) / len(trailing)
        var = sum((v - mu) ** 2 for v in trailing) / (len(trailing) - 1)
        sd = var ** 0.5
        if sd > 0.0:
            volume_z = (volume_now - mu) / sd
    # ATR slope: mean true-range% of the recent half minus the older half.
    atr_slope = _atr_slope(closes, highs, lows)
    recent_ticks = [
        {"ts": int(br[0]), "close": float(br[1]), "volume": float(br[4])}
        for br in reversed(bar_row[:10])  # newest-last, cap to last 10
    ]
    return {
        "volume_now": volume_now,
        "volume_z": volume_z,
        "atr_slope": atr_slope,
        "recent_ticks": recent_ticks,
    }


def _atr_slope(
    closes: list[float], highs: list[float], lows: list[float]
) -> float:
    """Recent-half mean TR% minus older-half mean TR% (positive = expanding)."""
    n = len(closes)
    if n < 4:
        return 0.0
    tr_pct: list[float] = []
    for i in range(1, n):
        if closes[i] <= 0.0:
            continue
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_pct.append(tr / closes[i])
    if len(tr_pct) < 2:
        return 0.0
    half = len(tr_pct) // 2
    older = tr_pct[:half]
    recent = tr_pct[half:]
    older_mean = sum(older) / len(older) if older else 0.0
    recent_mean = sum(recent) / len(recent) if recent else 0.0
    return recent_mean - older_mean


# ---------------------------------------------------------------------------
# Per-position G6/G7 GPT invocation
# ---------------------------------------------------------------------------


async def _evaluate_position(
    *,
    conn: sqlite3.Connection,
    state: ProdLoopState,
    pos: ActivePositionRow,
    regime: str,
    gpt_client: Any | None,
    now_ts: int,
    close_specific: Callable[..., Any],
    lookup_regime: Callable[[sqlite3.Connection, str, str], str],
    phase: str,
    tick_idx: int = 0,
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    capital_session: Any = None,
    alpaca_adapter: Any = None,
) -> None:
    """Build payload + call G6, then G7 / close / swap based on decision."""
    side = str(pos.get("side", "long"))
    entry_price = float(pos.get("entry_price", 0.0))
    last_price = float(pos.get("last_price", entry_price))
    atr_pct = max(float(pos.get("atr_pct", 0.005)), 1e-4)
    # Entry-time ATR anchor — denominates pnl_r/mfe_r/mae_r so a volatility
    # contraction cannot shrink the R unit mid-life. Legacy NULL anchor →
    # the current timeframe ATR (graceful fallback, pre-anchor behaviour).
    anchor_raw = pos.get("entry_atr_pct")
    entry_atr_pct = None if anchor_raw is None else max(float(anchor_raw), 1e-4)
    held_seconds = max(0, now_ts - int(pos.get("opened_ts", now_ts)))
    pnl_r = compute_unrealized_pnl_r(
        side=side, entry_price=entry_price, last_price=last_price,
        atr_pct=atr_pct if entry_atr_pct is None else entry_atr_pct,
    )

    # Phase 3 — per-stream session-close RAIL (CALENDAR INTEGRITY, not a P&L
    # throttle). BEFORE the #26 FSM: a calendar-forced flat is unconditional
    # venue reality (weekend/RTH close imminent), so it pre-empts. always_on (A)
    # NEVER fires → A byte-identical. Fires on TIME only — never pnl/drawdown.
    if await run_session_forced_exit(
        conn=conn, state=state, pos=pos, pnl_r=pnl_r, now_ts=now_ts,
        close_specific=close_specific, lookup_regime=lookup_regime,
        gpt_client=gpt_client, phase=phase, real_roundtrip=real_roundtrip,
        okx_adapter=okx_adapter, capital_session=capital_session,
        alpaca_adapter=alpaca_adapter,
    ):
        return

    # #26 — precise exits FIRST (deterministic, every tick): track excursion,
    # ratchet the ATR-trailing stop, advance the MFE FSM, close on trail-stop /
    # protected-BEP / loser-timeout. If it fires, the position is gone — skip G6.
    # State resume (stop/peak/trough/FSM) is OWNED by run_precise_exit's fresh
    # row read — the ``pos`` snapshot fields are a no-row fallback only.
    closed = await run_precise_exit(
        conn=conn, state=state, pos=pos, side=side, entry_price=entry_price,
        last_price=last_price, atr_pct=atr_pct, pnl_r=pnl_r,
        held_seconds=held_seconds, now_ts=now_ts, close_specific=close_specific,
        lookup_regime=lookup_regime, gpt_client=gpt_client, phase=phase,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        entry_atr_pct=entry_atr_pct,
    )
    if closed:
        return

    monitor_payload = build_monitor_payload(
        position={
            "venue": pos["venue"],
            "symbol": pos["symbol"],
            "side": side,
            "strategy": pos.get("active_strategy_id", pos.get("strategy")),
            "correlation_group": pos.get("correlation_group", ""),
            "entry_price": entry_price,
            "last_price": last_price,
            "held_seconds": held_seconds,
            "cell_score": 0.0,
        },
        unrealized_pnl_r=pnl_r,
        max_loss_r=1.0,
    )
    # FIX-2 (2026-05-30): feed REAL recent-bar data into the G6 market_view +
    # recent_ticks instead of the prior hardcoded volume_now=0.0 / []. The
    # monitor now sees the live volume, its z-score, the ATR slope, and the
    # last-N closes so it can decide HOLD vs ADJUST_EXIT vs EXIT_NOW on actual
    # market action. Data-correctness only — never a throttle or size dampen.
    monitor_payload["market_view"] = {
        "regime": regime,
        "atr_pct": atr_pct,
        "volume_now": float(pos.get("volume_now", 0.0)),
        "volume_z": float(pos.get("volume_z", 0.0)),
        "atr_slope": float(pos.get("atr_slope", 0.0)),
    }
    recent_ticks_obj = pos.get("recent_ticks", [])
    monitor_payload["recent_ticks"] = (
        recent_ticks_obj if isinstance(recent_ticks_obj, list) else []
    )
    # Gate architecture Phase 0: per-stream seam, resolved once from the
    # position's venue and threaded through G6/G7 (read-but-no-decision in P0).
    stream_profile = resolve_stream_profile(str(pos["venue"]))
    g6_ctx = GateContext(
        run_id=uuid.uuid4().hex,
        signal_id=str(pos["position_id"]),
        position_id=str(pos["position_id"]),
        gate_id=GATE_POSITION_MONITOR,
        venue=str(pos["venue"]),
        symbol=str(pos["symbol"]),
        strategy_id=str(pos.get("active_strategy_id", pos.get("strategy"))),
        payload=monitor_payload,
        started_ts=now_ts,
        state=SignalLifecycle.MONITORED,
        stream_profile=stream_profile,
    )
    g6_client = gpt_client if phase == "P1" else None
    g6_result = await position_monitor_gate(
        g6_ctx, client=g6_client,
        call_cache=state.g6_call_cache, tick_idx=tick_idx,
    )
    log_gate_event(conn, g6_ctx, g6_result)
    state.recalc_g6_calls = getattr(state, "recalc_g6_calls", 0) + 1
    # #15 — count reused (no-call) decisions for the GPT-cost telemetry.
    if g6_result.model_used == "python_fast_path":
        state.recalc_g6_skipped = getattr(state, "recalc_g6_skipped", 0) + 1

    if g6_result.decision == GateDecision.EXIT_NOW:
        # G6 EXIT_NOW (INFO): the AI-monitor exit decision + its reason, before
        # the close fires. Log only — does not alter the close path below.
        logger.info(
            "[L6/g6] close %s:%s trade_id=%s decision=EXIT_NOW reason=%s "
            "pnl_r=%.2f model=%s",
            pos["venue"], pos["symbol"], pos["position_id"],
            g6_result.payload.get("reason", "-"), pnl_r, g6_result.model_used,
        )
        # F2.b — specific position close (no FIFO oldest pop).
        await close_specific(
            conn, state=state, position_id=str(pos["position_id"]),
            now_ts=now_ts, lookup_regime=lookup_regime,
            gpt_client=gpt_client, phase=phase,
            real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
            capital_session=capital_session,
        )
        state.recalc_exit_now = getattr(state, "recalc_exit_now", 0) + 1
        return

    if g6_result.decision == GateDecision.SWAP_STRATEGY:
        # Layer 6 SSOT — strategy_swap evaluator (Q8 + Q3 §swap).
        candidate_payload = g6_result.payload.get("swap") or {}
        active_strat = str(pos.get("active_strategy_id", pos.get("strategy")))
        cand = SwapCandidate(
            position_id=str(pos["position_id"]),
            from_strategy_id=active_strat,
            to_strategy_id=str(candidate_payload.get("strategy", active_strat)),
            venue=str(pos["venue"]),
            symbol=str(pos["symbol"]),
            side=side,
            from_correlation_group=str(pos.get("correlation_group", "")),
            to_correlation_group=str(
                candidate_payload.get("correlation_group", pos.get("correlation_group", ""))
            ),
        )
        evaluate_strategy_swap(conn, candidate=cand, now_ts=now_ts, apply=False)
        state.recalc_swap = getattr(state, "recalc_swap", 0) + 1
        return

    if g6_result.decision == GateDecision.ADJUST_EXIT:
        # #26 — current stop = the PERSISTED ATR-trailing stop the precise-exit
        # engine ratcheted (not the old hardcoded entry*0.99); G7's Q9 rail only
        # ever WIDENS. last_price fallback when the engine hasn't set a stop yet.
        atr_one = max(entry_price * atr_pct, 1e-6)
        stop_row = conn.execute(
            "SELECT stop_price FROM positions WHERE position_id = ?",
            (str(pos["position_id"]),),
        ).fetchone()
        current_stop = (
            float(stop_row[0]) if stop_row is not None and stop_row[0] is not None
            else (last_price - 2.0 * atr_one if side == "long"
                  else last_price + 2.0 * atr_one)
        )
        proposed_stop = (
            current_stop - atr_one if side == "long" else current_stop + atr_one
        )
        g7_payload = build_exit_payload(
            side=side,
            current_stop_price=current_stop,
            proposed_stop_price=proposed_stop,
            entry_price=entry_price,
            unrealized_pnl_r=pnl_r,
            max_loss_r=1.0,
            overrides_used=0,
            seconds_since_last_override=60,
        )
        g7_payload_full = {**monitor_payload, **g7_payload}
        # G7 divergence SHADOW site tag (instrumentation only): read by the
        # shadow logger so live-recalc rows are separable from other call
        # sites in analysis. Never read by any decision.
        g7_payload_full["g7_shadow_site"] = "live_recalc"
        g7_ctx = GateContext(
            run_id=g6_ctx.run_id,
            signal_id=g6_ctx.signal_id,
            position_id=g6_ctx.position_id,
            gate_id=GATE_ADAPTIVE_EXIT,
            venue=g6_ctx.venue,
            symbol=g6_ctx.symbol,
            strategy_id=g6_ctx.strategy_id,
            payload=g7_payload_full,
            started_ts=now_ts,
            state=SignalLifecycle.MONITORED,
            stream_profile=stream_profile,
        )
        g7_client = gpt_client if phase == "P1" else None
        # shadow_conn (instrumentation only): rails-vs-GPT divergence row per
        # P1 GPT call; the returned decision is byte-identical (P0 skips).
        g7_result = await adaptive_exit_gate(
            g7_ctx, client=g7_client, shadow_conn=conn,
        )
        log_gate_event(conn, g7_ctx, g7_result)
        state.recalc_g7_calls = getattr(state, "recalc_g7_calls", 0) + 1
        # 🔴 BUG FIX: G7 EXIT_NOW was SILENTLY DROPPED (only widening_applied was
        # checked). It must now actually close the specific position (no halt).
        if g7_result.decision == GateDecision.EXIT_NOW:
            # G7 EXIT_NOW (INFO): the adaptive-exit gate decided to close instead
            # of widen — surface its reason. Log only; close path unchanged.
            logger.info(
                "[L6/g7] close %s:%s trade_id=%s decision=EXIT_NOW reason=%s "
                "pnl_r=%.2f model=%s",
                pos["venue"], pos["symbol"], pos["position_id"],
                g7_result.payload.get("reason", "-"), pnl_r, g7_result.model_used,
            )
            await close_specific(
                conn, state=state, position_id=str(pos["position_id"]),
                now_ts=now_ts, lookup_regime=lookup_regime,
                gpt_client=gpt_client, phase=phase,
                real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
            )
            state.recalc_exit_now = getattr(state, "recalc_exit_now", 0) + 1
            return
        if g7_result.payload.get("widening_applied"):
            # Persist the G7-widened stop (only ever looser in the winner's
            # favour) so the precise-exit trail respects it next tick.
            new_stop = g7_result.payload.get("stop_price")
            if new_stop is not None:
                conn.execute(
                    "UPDATE positions SET stop_price = ? WHERE position_id = ?",
                    (float(new_stop), str(pos["position_id"])),
                )
            state.recalc_widen_applied = getattr(state, "recalc_widen_applied", 0) + 1


async def recalc_active_positions(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    now_ts: int,
    gpt_client: Any | None,
    phase: str,
    lookup_regime: Callable[[sqlite3.Connection, str, str], str],
    close_specific: Callable[..., Any],
    tick_idx: int = 0,
    max_positions: int = LIVE_RECALC_MAX_POSITIONS,
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    capital_session: Any = None,
    alpaca_adapter: Any = None,
) -> int:
    """Sweep every active position through G6 (and G7 if ADJUST_EXIT).

    Returns number of positions evaluated. Each call also marks the position
    dirty (5s tick proxy) so Layer 6 telemetry stays consistent with the
    scheduled cadence even when G6 returns HOLD.

    Fault-isolated per position: an exception in one position's G6/G7 chain
    increments ``state.fault_events`` but does not block the sweep.
    """
    positions = load_active_position_rows(
        conn, limit=max_positions, quote_writer=state.quote_writer,
        tf_atr_cache=getattr(state, "tf_atr_cache", None),
    )
    if not positions:
        # No open positions — clear the G6 call cache so it never grows stale.
        state.g6_call_cache.prune(set())
        return 0
    # #15 — drop cache anchors for positions that closed since the last sweep.
    state.g6_call_cache.prune({str(p["position_id"]) for p in positions})
    for pos in positions:
        position_id = str(pos["position_id"])
        try:
            mark_position_dirty(
                conn, position_id=position_id, reason="tick_5s_g6", now_ts=now_ts,
            )
            regime = lookup_regime(conn, str(pos["venue"]), str(pos["symbol"]))
            await _evaluate_position(
                conn=conn, state=state, pos=pos, regime=regime,
                gpt_client=gpt_client, now_ts=now_ts,
                close_specific=close_specific, lookup_regime=lookup_regime,
                phase=phase, tick_idx=tick_idx, real_roundtrip=real_roundtrip,
                okx_adapter=okx_adapter, capital_session=capital_session,
                alpaca_adapter=alpaca_adapter,
            )
        except Exception as exc:  # noqa: BLE001 — fault isolate per position
            logger.error(
                "[L6/g6] recalc raised for position %s: %r", position_id, exc,
            )
            state.fault_events += 1
    return len(positions)


def find_open_trade_by_position_id(
    state: ProdLoopState, position_id: str,
) -> SimulatedTrade | None:
    """Locate the open trade for a specific position_id (no FIFO pop).

    Used by ``close_specific_position`` to honour the G6 EXIT_NOW
    contract — close the position the model decided about, not the FIFO
    oldest one.
    """
    for trade in state.open_trades:
        if trade.position_id == position_id:
            return trade
    return None
