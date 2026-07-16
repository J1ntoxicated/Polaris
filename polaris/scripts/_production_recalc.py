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

from polaris.core.isolation.reentry import bar_seconds
from polaris.core.live_recalc.exit_params import _STATE_RANK as _EXIT_STATE_RANK
from polaris.core.live_recalc.strategy_swap import (
    SwapCandidate,
    evaluate_strategy_swap,
)
from polaris.core.live_recalc.tick_recalc import (
    LIVE_RECALC_MAX_POSITIONS,
    mark_positions_dirty,
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
from polaris.core.pipeline.agents.judge_gate import (
    judge_exit_cooldown_sec,
    should_escalate_exit,
)
from polaris.core.pipeline.gate_orchestrator import log_gate_event
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_POSITION_MONITOR,
)
from polaris.core.probes.tighten_intent import (
    TIGHTEN_DEBOUNCE_DEFERRED_OVERRIDES,
    TIGHTEN_DEBOUNCE_DEFERRED_SECONDS,
    latest_probe_tighten,
    synth_tighten_stop,
)
from polaris.core.streams import resolve_stream_profile
from polaris.scripts._production_atr import (
    MIN_TF_BARS,
    native_bars_seen_since,
    strategy_timeframe,
    timeframe_atr_pct,
    timeframe_bar_rows,
)
from polaris.scripts._production_bars import BAR_TS_CLOCK_SKEW_SLACK_SEC
from polaris.scripts._production_indicators import compute_unrealized_pnl_r
from polaris.scripts._production_probe_attach import observe_probes
from polaris.scripts._production_recalc_exit import (
    _stop_atr_mult_for_strategy,
    assess_mode_for_position,
    run_precise_exit,
    run_session_forced_exit,
)
from polaris.scripts._static_ground import read_ticker_ground

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
    md_conn: sqlite3.Connection | None = None,
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

    storage-split (round 3 CRITICAL fix): ``positions``/``fills`` are
    trading-domain (stay on ``conn``); ``bars`` is marketdata-domain and is
    written ONLY to ``md_conn`` post-split. ``md_conn=None`` (legacy/test
    callers, single-DB mode) falls back to ``conn`` — byte-identical.
    """
    _md = md_conn if md_conn is not None else conn
    rows = conn.execute(
        """
        SELECT p.position_id, p.venue, p.symbol, p.underlying_group_id,
               p.strategy_id, p.entry_strategy_id, p.active_strategy_id,
               p.side, p.qty, p.opened_ts,
               p.stop_price, p.peak_price, p.trough_price, p.exit_state,
               p.entry_atr_pct, p.entry_atr_timeframe,
               p.mfe_r, p.mae_r, p.entry_regime
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
        opened_ts_raw = int(r[9])
        # FIX-2 (2026-05-30): pull volume too (newest first) so the G6 monitor
        # sees REAL market data — the prior wiring hardcoded volume_now=0.0 and
        # recent_ticks=[] for every position, blinding the monitor.
        instrument_id = f"{venue}:{symbol}"
        # Exclude FUTURE-dated bars (stale +10h Capital) so the G6 monitor /
        # exit mark never treats a +10h ghost bar as the current price.
        ts_upper = int(time.time()) + BAR_TS_CLOCK_SKEW_SLACK_SEC
        # [P0-5] Exclude PRE-ENTRY bars (ts < opened_ts) so a reconcile-import
        # (or any restart-hydrated) position never pulls stale market-closed
        # bars from before it opened into the MFE/MAE peak/trough window —
        # a session-gap window (e.g. an equity position imported after the
        # close, while the 20-bar lookback still reaches into the PRIOR
        # session) fabricated multi-R excursion off pre-entry noise. A
        # position with no bar at/after its own open (this tick, before the
        # next bar closes) degrades to the entry-price fallback below
        # (graceful, never halts).
        bar_row = _md.execute(
            """
            SELECT ts, close, high, low, volume FROM bars
            WHERE instrument_id = ? AND bar_interval = '1m'
              AND ts >= ? AND ts <= ?
            ORDER BY ts DESC LIMIT 20
            """,
            (instrument_id, opened_ts_raw, ts_upper),
        ).fetchall()
        bar_close = float(bar_row[0][1]) if bar_row else entry_price
        # P4 #2 — prefer a FRESH WS tick mid (M6: monotonic age check), else the
        # bar close. live_px returns (mid, last_ws_monotonic); a tick older than
        # the reconnect-proof threshold degrades to the bar (no flap).
        last_price = bar_close
        mid_used = False
        if quote_writer is not None:
            px = quote_writer.live_px(instrument_id)
            if px is not None:
                mid, last_ws_monotonic = px
                if (
                    mid > 0.0
                    and time.monotonic() - last_ws_monotonic < WS_EXIT_MARK_FRESH_SEC
                ):
                    last_price = mid
                    mid_used = True
        # DIA/CNC 7-day frozen-mark incident (P1 fix): the mid was missing/stale
        # for these Alpaca-held positions AND the prior wiring had no distinct
        # label for "bar close substituted for the missing mid" vs "cadence=bar".
        # mark_source stamps 'bar_fallback' ONLY when a genuine bar close (not
        # the deeper entry_price degrade below) stood in for a missing/stale mid
        # — the entry_price case (no bar at all) keeps the plain 'bar' default,
        # never fabricating a bar-fallback claim it did not actually have.
        mark_source = "bar_fallback" if not mid_used and bar_row else "bar"
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
        # Drift-measurement window ([[1d_exit_horizon_fix_2026-07-02]] follow-up):
        # momentum_drift (assess_thesis's break signal) is judged against a
        # timeframe-scaled floor, so the SIGNAL itself must be measured on the
        # SAME timeframe — else a 1D floor is compared against a 1m-window
        # drift (always sub-floor, silently disabling the CUT path). "1m"
        # reuses the in-hand ``bar_row`` (byte-identical); a non-1m strategy
        # reads its own timeframe bars, falling back to the 1m window when too
        # few timeframe bars exist yet (graceful, never halts).
        drift_bar_row = bar_row
        if tf != "1m":
            tf_atr = timeframe_atr_pct(
                _md, instrument_id=instrument_id, timeframe=tf,
                now_ts=int(time.time()), cache=tf_atr_cache,
            )
            if tf_atr is not None:
                atr_pct = tf_atr
            tf_bars = timeframe_bar_rows(
                _md, instrument_id=instrument_id, timeframe=tf,
                now_ts=int(time.time()), min_bars=MIN_TF_BARS,
            )
            if tf_bars is not None:
                drift_bar_row = tf_bars
        entry_atr_pct = None if r[14] is None else float(r[14])
        if entry_atr_pct is None:
            # Legacy row (pre-anchor) — R denominator falls back to the
            # CURRENT timeframe ATR above. DEBUG-only visibility.
            logger.debug(
                "[L6/atr] anchor missing for %s (legacy row) — current %s "
                "ATR denominates", position_id, tf,
            )
        market = _recent_market_state(drift_bar_row)
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
            opened_ts=opened_ts_raw,
            entry_price=entry_price,
            last_price=last_price,
            mark_source=mark_source,
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
            # Excursion telemetry (last persisted) + entry-regime anchor — the
            # adaptive thesis re-map inputs. NULL = unstamped/legacy (degrade safe).
            mfe_r=None if r[16] is None else float(r[16]),
            mae_r=None if r[17] is None else float(r[17]),
            entry_regime=None if r[18] is None else str(r[18]),
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


def _recent_tick_drift(recent_ticks: Any) -> float:
    """Signed price drift over the recent-tick window (newest - oldest) / oldest.

    The adaptive thesis re-map's ``momentum_drift`` input: positive = price rose
    over the window. Empty / malformed / single-tick → 0.0 (degrade safe → the
    re-map sees no momentum and never fires a break on missing data).
    """
    if not isinstance(recent_ticks, list) or len(recent_ticks) < 2:
        return 0.0
    try:
        first = float(recent_ticks[0]["close"])
        last = float(recent_ticks[-1]["close"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if first <= 0.0:
        return 0.0
    return (last - first) / first


def _horizon_seconds_for(strategy_id: str) -> int:
    """The strategy's expected holding horizon in seconds (timeframe × expected
    holding bars), or a 600s fallback for an unknown strategy."""
    from polaris.core.isolation.reentry import bar_seconds
    from polaris.strategies import STRATEGY_REGISTRY

    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return 600
    bars = max(1, int(cls.metadata.expected_holding_bars))
    return int(bar_seconds(cls.metadata.timeframe) * bars)


def _horizon_bars_for(strategy_id: str) -> int | None:
    """``StrategyMetadata.expected_holding_bars`` for the maturity gate's
    ``required_bars_for_horizon`` (``floor_bars`` short-horizon carve-out).
    ``None`` for an unregistered strategy (tick-engine signals) — the maturity
    gate's bars-seen path stays inert for those (mirrors ``_horizon_seconds_for``'s
    600s fallback: no bars-seen count is meaningful without a registered horizon)."""
    from polaris.strategies import STRATEGY_REGISTRY

    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return None
    return max(1, int(cls.metadata.expected_holding_bars))


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
    # FIX-EXIT ([[weekend_maker_honest_rerun_2026-06-28]]): the R-unit ATR
    # multiplier the pnl_r the −1.0R rail reads is denominated in. The weekend OKX
    # makers widen to 3.0 (wider R-unit distance); every other strategy keeps the
    # SSOT 2.0 → byte-identical. 🚨 The rail COEFFICIENT (max_loss_r=1.0, G6 monitor)
    # is untouched — a wider ATR unit just makes −1.0R a wider price stop.
    r_denominator_atr_pct = atr_pct if entry_atr_pct is None else entry_atr_pct
    pnl_r = compute_unrealized_pnl_r(
        side=side, entry_price=entry_price, last_price=last_price,
        atr_pct=r_denominator_atr_pct,
        stop_atr_mult=_stop_atr_mult_for_strategy(
            str(pos.get("active_strategy_id") or pos.get("strategy") or ""),
            atr_pct=r_denominator_atr_pct,
        ),
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

    # ADR-012 Slice 1 — PROBE → ENGINE → TUNING-LOG (OBSERVE-ONLY). Pure/sync,
    # called INLINE here (no await) so the FSM read-modify-write atomicity in
    # run_precise_exit is untouched. In observe mode the engine threads ZERO
    # knobs into run_precise_exit (its kwargs below stay default) — provably
    # byte-identical. Fully fail-open: a None sidecar / raising probe / fail-open
    # writer never breaks the tick. The hard rails BYPASS this framework.
    observe_probes(
        state=state, pos=pos, side=side, entry_price=entry_price,
        last_price=last_price, atr_pct=atr_pct, entry_atr_pct=entry_atr_pct,
        pnl_r=pnl_r, held_seconds=held_seconds, regime=regime, now_ts=now_ts,
        run_id=uuid.uuid4().hex,
        # DIA/CNC frozen-mark P1 fix — surface whether THIS tick's mark came
        # from the bar-close fallback (mid missing/stale) vs the plain bar
        # cadence default. Absent key (legacy/no-writer callers) keeps the
        # existing 'bar' default, byte-identical.
        mark_source=str(pos.get("mark_source", "bar")),
    )

    # Adaptive thesis re-map (bar path): gather the entry-thesis-health inputs
    # available on the bar pipeline (regime / atr_slope / recent-tick drift /
    # time) and resolve a ManagementMode. OFI / flow_confirmed are tick-only →
    # None here (degrade safe). re-map OFF → mode=None → byte-identical. EXIT
    # timing only; never size / entry / the G6 -1.0R rail.
    strategy_id = str(pos.get("active_strategy_id", pos.get("strategy", "")))
    # Maturity gate ([[waveB_sizing_params_2026-07-02]] agenda 3): NATIVE bars
    # closed on the strategy's own timeframe since this position opened — NOT
    # wall-clock (session closes / data gaps distort wall-clock "elapsed").
    # Reuses the same ``bars`` table the timeframe ATR read already queries; a
    # missing bar row / unregistered strategy degrades to 0 seen (safest — the
    # gate suppresses rather than risks judging an unmeasured thesis).
    native_tf = strategy_timeframe(strategy_id)
    # storage-split (round 4 MED fix): bars is marketdata-domain — a
    # trading-conn read sees a permanently-empty table post-split, so the
    # maturity gate always saw 0 bars (permanently suppressed).
    native_bars_seen = native_bars_seen_since(
        state.md_conn if state.md_conn is not None else conn,
        instrument_id=f"{pos['venue']}:{pos['symbol']}", timeframe=native_tf,
        open_ts=int(pos.get("opened_ts", now_ts)), now_ts=now_ts,
    )
    mode = assess_mode_for_position(
        strategy_id=strategy_id, side=side,
        mfe_r=float(pos.get("mfe_r") or 0.0),
        mae_r=float(pos.get("mae_r") or 0.0),
        pnl_r=pnl_r,
        momentum_drift=_recent_tick_drift(pos.get("recent_ticks")),
        atr_slope=float(pos.get("atr_slope", 0.0)),
        ofi=None, flow_confirmed=None,
        regime=regime, entry_regime=pos.get("entry_regime"),
        held_seconds=held_seconds,
        horizon_seconds=_horizon_seconds_for(strategy_id),
        native_bars_seen=native_bars_seen,
        native_bar_interval_seconds=bar_seconds(native_tf),
        horizon_bars=_horizon_bars_for(strategy_id),
        position_id=str(pos.get("position_id") or ""),
    )

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
        entry_atr_pct=entry_atr_pct, mode=mode,
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
    # Probe TIGHTEN consumer (POLARIS_G6_PROBE_TIGHTEN, default OFF): surface the
    # freshest OPEN probe action for THIS position from the observe-only
    # ``data/probes.sqlite`` sidecar so G6 can escalate an adverse HOLD to a tighter
    # trail (precise exit TIMING). FAIL-OPEN read (None handle / missing row → no
    # key added → byte-identical HOLD). The probe engine stays observe-only — this
    # only READS its log. ``probe_intent`` is reused below to synthesise the stop.
    probe_intent = latest_probe_tighten(
        getattr(state, "probe_conn", None), position_id=str(pos["position_id"]),
    )
    if probe_intent is not None:
        monitor_payload["probe_action"] = probe_intent.action
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
    # Conviction-pyramid writer (vault/50_research/debates/
    # conviction_pyramid_addon_notional_2026-07-10.md): ``conn`` is threaded
    # so the gate CAN evaluate the next-layer judgment, but it stays a no-op
    # (byte-identical) until ``monitor_payload["conviction_stack"]`` is
    # populated — a later dispatcher slice's responsibility (cell_quartile /
    # layer_sum_size_pct / headroom_pct sourcing from live portfolio state).
    g6_result = await position_monitor_gate(
        g6_ctx, client=g6_client, conn=conn,
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
        # P2-12: name the trigger so the lineage exit_reason isn't the 'exit'
        # fallback (observability only — no close-path behaviour change).
        await close_specific(
            conn, state=state, position_id=str(pos["position_id"]),
            now_ts=now_ts, lookup_regime=lookup_regime,
            gpt_client=gpt_client, phase=phase,
            real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
            capital_session=capital_session, close_reason="g6_stop_hit",
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
            "SELECT stop_price, peak_price, trough_price "
            "FROM positions WHERE position_id = ?",
            (str(pos["position_id"]),),
        ).fetchone()
        current_stop = (
            float(stop_row[0]) if stop_row is not None and stop_row[0] is not None
            else (last_price - 2.0 * atr_one if side == "long"
                  else last_price + 2.0 * atr_one)
        )
        # Probe TIGHTEN consumer (flag-gated): G6 escalated an adverse HOLD because
        # the freshest probe action is TIGHTEN. Synthesise a TIGHTER stop from the
        # position's anchor (peak/trough) using the probe trail_mult (NULL in observe
        # mode → ATR-N% fallback) and route it to G7's deterministic tighten rail
        # (``tighten_proposal``). flow_not_block — precise exit TIMING; the synth /
        # G7 rail both reject any LOOSENING (ratchet preserved). None synth (would
        # loosen) → fall through to the normal widen proposal below (no-op tighten).
        tighten_proposal: dict[str, Any] | None = None
        if g6_result.payload.get("tighten_intent") and probe_intent is not None:
            anchor = (
                float(stop_row[1]) if stop_row is not None and stop_row[1] is not None
                else last_price
            ) if side == "long" else (
                float(stop_row[2]) if stop_row is not None and stop_row[2] is not None
                else last_price
            )
            tighter_stop = synth_tighten_stop(
                side=side, anchor_extreme=anchor, entry_price=entry_price,
                atr_pct=atr_pct, current_stop_price=current_stop,
                probe_trail_mult=probe_intent.trail_mult,
            )
            if tighter_stop is not None:
                tighten_proposal = {
                    "side": side,
                    "current_stop_price": current_stop,
                    "proposed_stop_price": tighter_stop,
                    # 1st-increment debounce placeholders — the per-position override
                    # counter + last-override timestamp are a deferred positions-schema
                    # addition, so the local cooldown/override-cap rails are inert this
                    # increment (safe: the synth + ratchet are monotone — see
                    # tighten_intent.TIGHTEN_DEBOUNCE_DEFERRED_*).
                    "overrides_used": TIGHTEN_DEBOUNCE_DEFERRED_OVERRIDES,
                    "seconds_since_last_override": TIGHTEN_DEBOUNCE_DEFERRED_SECONDS,
                }
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
            # #32 axis-B fix: wire the SAME cell summary G3 already computes so
            # the exit judge's evidence_robustness warmth leg is no longer stuck
            # at 0.0 (the G7 payload never carried a cell_routing key before).
            conn=conn,
            venue=str(pos["venue"]),
            strategy=strategy_id,
            symbol=str(pos["symbol"]),
            regime=regime,
        )
        g7_payload_full = {**monitor_payload, **g7_payload}
        # #32 — stamp the bot's OWN fused alt-data + ground coverage + regime so the
        # per-ticker EXIT-timing AI JUDGE (G7) SEES the same information the entry
        # judge does, instead of ``n/a``. REUSES the already-materialized per-ticker
        # ground (``refresh_ticker_ground`` producer) via ``read_ticker_ground`` — no
        # re-fuse on the recalc path. Absent row → keys unset (judge renders n/a).
        # EVIDENCE only: no rail/decision branches on these keys (the judge reads
        # them; G7's widen/tighten rails are owned by the deterministic FSM).
        g7_payload_full["regime"] = regime
        # storage-split (round 3 MED fix): ticker_ground is marketdata-domain.
        _ground_conn = state.md_conn if state.md_conn is not None else conn
        _ground_row = read_ticker_ground(
            _ground_conn, f"{pos['venue']}:{pos['symbol']}"
        )
        if _ground_row is not None:
            g7_payload_full["evidence"] = _ground_row["ground"]
            g7_payload_full["ticker_ground"] = {
                "has_sentiment": _ground_row["has_sentiment"],
                "has_event": _ground_row["has_event"],
                # #32 axis-B freshness input (see run_signal stamp).
                "updated_ts": _ground_row["updated_ts"],
            }
        if tighten_proposal is not None:
            # tighten_proposal wins in adaptive_exit_gate (consumed before widen);
            # the widen_proposal stays as the no-tighten fallback shape.
            g7_payload_full["tighten_proposal"] = tighten_proposal
        # #32 axis-A — G7 EXIT judge CALL gate (anti-flooding, decision-moment-only +
        # per-position cooldown). All inputs are ALREADY computed this tick (mode /
        # stop-near / big-move / rung) → zero new fetch. ``judge_exit_escalate`` is
        # AND-composed with B (evidence robustness) inside adaptive_exit_gate; when
        # False the judge is NOT consulted and the deterministic Q9 rail flows
        # unchanged (flow_not_block). Cooldown/dedup: re-escalate the SAME position
        # only if the FSM rung advanced OR the cooldown elapsed.
        atr_one_exit = max(entry_price * atr_pct, 1e-6)
        current_rung = _EXIT_STATE_RANK.get(str(pos.get("exit_state", "open")), 0)
        last_seen = state.judge_exit_cooldowns.get(str(pos["position_id"]))
        rung_advanced = last_seen is None or current_rung > last_seen[1]
        esc_a, esc_reason = should_escalate_exit(
            mode=mode.value if mode is not None else None,
            exit_state_crossed=rung_advanced,
            last_price=last_price,
            current_stop=current_stop,
            atr_one=atr_one_exit,
            momentum_drift=_recent_tick_drift(pos.get("recent_ticks")),
            atr_slope=float(pos.get("atr_slope", 0.0)),
            volume_z=float(pos.get("volume_z", 0.0)),
        )
        cooldown_ok = (
            last_seen is None
            or rung_advanced
            or (now_ts - last_seen[0]) >= judge_exit_cooldown_sec()
        )
        escalate = esc_a and cooldown_ok
        g7_payload_full["judge_exit_escalate"] = escalate
        if escalate:
            # Record this decision-moment so the SAME rung within cooldown is deduped.
            state.judge_exit_cooldowns[str(pos["position_id"])] = (now_ts, current_rung)
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
        # #32 judge_client: the per-ticker EXIT-timing judge runs alongside the
        # deterministic G7 (shadow default logs only; the rail decision still
        # acts). None → byte-identical no-judge path.
        g7_result = await adaptive_exit_gate(
            g7_ctx, client=g7_client, shadow_conn=conn,
            judge_client=state.judge_client,
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
            # P2-12: name the trigger (observability only — no behaviour change).
            await close_specific(
                conn, state=state, position_id=str(pos["position_id"]),
                now_ts=now_ts, lookup_regime=lookup_regime,
                gpt_client=gpt_client, phase=phase,
                real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                close_reason="g7_exit_now",
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
        elif g7_result.payload.get("tightening_applied"):
            # Persist the G7-tightened stop (probe TIGHTEN consumer). The synth +
            # G7's ``can_tighten_exit`` both guaranteed it is TIGHTER (toward price);
            # the next precise-exit tick ratchets toward it and still forbids
            # loosening it. flow_not_block — a tighter trail, never a block/size cut.
            new_stop = g7_result.payload.get("stop_price")
            if new_stop is not None:
                conn.execute(
                    "UPDATE positions SET stop_price = ? WHERE position_id = ?",
                    (float(new_stop), str(pos["position_id"])),
                )
            state.recalc_tighten_applied = (
                getattr(state, "recalc_tighten_applied", 0) + 1
            )


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
        md_conn=state.md_conn,
    )
    if not positions:
        # No open positions — clear the G6 call cache so it never grows stale.
        state.g6_call_cache.prune(set())
        # #32 — drop all G7-judge cooldown anchors (no open position to dedup).
        state.judge_exit_cooldowns.clear()
        return 0
    # #15 — drop cache anchors for positions that closed since the last sweep.
    live_ids = {str(p["position_id"]) for p in positions}
    state.g6_call_cache.prune(live_ids)
    # #32 — prune the G7-judge cooldown dict for positions closed since the last
    # sweep (no schema change, no leak — the entry is written on first escalate).
    for stale_id in [k for k in state.judge_exit_cooldowns if k not in live_ids]:
        del state.judge_exit_cooldowns[stale_id]
    # writer-migration-completion design #4: hoist the per-position dirty mark
    # OUT of the evaluation loop — one batch flush BEFORE evaluation instead
    # of up to LIVE_RECALC_MAX_POSITIONS individual writes/lock-acquisitions.
    # Synchronous (no await inside the txn, shared-conn safe) → dirty state is
    # visible to the async recalc sweep exactly as before. flow_not_block: a
    # flush failure degrades (counted), it must not abort G6/G7 evaluation for
    # every position in the cycle — same fault-isolation contract this
    # function already promises for the per-position chain below.
    try:
        mark_positions_dirty(
            conn, [(pid, "tick_5s_g6", now_ts) for pid in live_ids],
        )
    except Exception as exc:  # noqa: BLE001 — flow_not_block: never abort the sweep
        logger.error(
            "[L6/g6] batch dirty-mark flush raised for %d position(s): %r",
            len(live_ids), exc,
        )
        state.fault_events += 1
    for pos in positions:
        position_id = str(pos["position_id"])
        try:
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
