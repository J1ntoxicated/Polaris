"""Deterministic replay engine — live pipeline over persisted bars (behaviour 0).

The engine reuses the **live** seams verbatim (zero forks):

  * ``build_real_market_view`` — windowed bars ``[:i+1]`` -> indicators.
  * ``compute_real_regime_signal`` — pure label/strength (the stateful
    ``compute_and_flip_regime`` is NOT called; an in-memory 2-close confirm
    mirrors the flip latency without writing ``regime_state``).
  * ``strategy.generate_raw_signal`` — per-strategy raw signal.
  * ``build_sizer_payload`` + ``compute_size`` — the exact G5 sizing path,
    fed the **sandbox** conn so cell/session/regime reads hit the snapshot only.
  * ``evaluate_exit`` — the precise-exit FSM, ticked per subsequent bar.
  * ``update_on_trade_close`` — cell-matrix update, on the sandbox conn only.

Fills are **next-bar open** (no same-bar look-ahead) with half-spread slippage
and round-trip REAL fees. One open position per (instrument, strategy) at a
time keeps the replay deterministic without re-implementing portfolio state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from polaris.core.cell_matrix import CellContext, CellKeyP0
from polaris.core.cell_matrix.schema import TradeClose
from polaris.core.cell_matrix.update import update_on_trade_close
from polaris.core.data.schema import Bar
from polaris.core.live_recalc.exit_engine import ExitState, evaluate_exit, init_exit_state
from polaris.core.pipeline._sizer_payload import build_sizer_payload
from polaris.core.replay.equity_curve import build_equity_curve, summarize_metrics
from polaris.core.replay.fill_model import (
    apply_slippage,
    gross_pnl_usd,
    pnl_to_r,
    round_trip_fee_usd,
)
from polaris.core.replay.models import ReplayConfig, ReplayResult, ReplayTrade
from polaris.core.replay.sandbox_db import seed_sandbox
from polaris.core.sizing import SignalIntent, compute_size
from polaris.core.streams import resolve_stream
from polaris.scripts._production_indicators import (
    build_real_market_view,
    compute_real_regime_signal,
)
from polaris.scripts._production_recalc_exit import _loser_timeout_for_strategy
from polaris.strategies import all_strategies
from polaris.strategies.base import BaseStrategy, RawSignal

__all__ = ["ReplayEngine"]

# Regime flip confirm: the live loop requires N consecutive candidate closes
# before flipping. Mirror that latency in-memory (no regime_state write).
_REGIME_CONFIRM_CLOSES = 2


@dataclass(slots=True)
class _OpenPosition:
    """In-flight replay position (one per instrument+strategy)."""

    raw_signal: RawSignal
    strategy: BaseStrategy
    side: str
    entry_ts: int
    entry_price: float
    notional: float
    qty: float
    atr_pct: float
    regime: str
    exit_state: ExitState
    venue: str
    symbol: str


class ReplayEngine:
    """Replay one config over the live-seeded sandbox -> ``ReplayResult``."""

    def __init__(
        self, config: ReplayConfig, strategies: list[BaseStrategy] | None = None
    ) -> None:
        self.config = config
        # behaviour 0: the sole production caller (run_replay.py) passes no
        # ``strategies=`` -> default None -> ``all_strategies()`` reproduces the
        # current path. The P0a variant layer injects wrapper instances here.
        self.strategies = all_strategies() if strategies is None else list(strategies)

    # -- public -----------------------------------------------------------
    def run(self) -> ReplayResult:
        sandbox = seed_sandbox(self.config.live_db_path)
        try:
            return self._run_on_conn(sandbox)
        finally:
            sandbox.close()

    def run_with_bars(
        self, sandbox: sqlite3.Connection, bars_by_instrument: dict[str, list[Bar]]
    ) -> ReplayResult:
        """Test seam: run on an explicit sandbox conn + pre-loaded bars.

        Lets tests inject deterministic fixtures + a fresh sandbox without
        touching the live DB. Production ``run`` reads bars from the read-only
        ATTACH inside ``_run_on_conn``.
        """
        return self._run_on_conn(sandbox, bars_by_instrument=bars_by_instrument)

    # -- internals --------------------------------------------------------
    def _run_on_conn(
        self,
        sandbox: sqlite3.Connection,
        *,
        bars_by_instrument: dict[str, list[Bar]] | None = None,
    ) -> ReplayResult:
        if bars_by_instrument is None:
            bars_by_instrument = self._load_bars()
        trades: list[ReplayTrade] = []
        start_ts = self._curve_start_ts(bars_by_instrument)
        # Deterministic instrument order (sorted) -> stable trade ordering.
        for instrument_id in sorted(bars_by_instrument):
            bars = bars_by_instrument[instrument_id]
            if len(bars) < 2:
                continue
            trades.extend(self._replay_instrument(sandbox, bars))
        # Global deterministic ordering by (exit_ts, entry_ts, symbol, strategy).
        trades.sort(key=lambda t: (t.exit_ts, t.entry_ts, t.symbol, t.strategy))
        curve = build_equity_curve(
            trades=trades,
            starting_equity=self.config.starting_equity,
            start_ts=start_ts,
        )
        metrics = summarize_metrics(
            trades=trades, curve=curve, starting_equity=self.config.starting_equity
        )
        return ReplayResult(
            trades=tuple(trades),
            equity_curve_real_fee_net=tuple(curve),
            metrics=metrics,
        )

    def _curve_start_ts(self, bars_by_instrument: dict[str, list[Bar]]) -> int:
        first = min(
            (b[0].ts for b in bars_by_instrument.values() if b), default=0
        )
        return int(first)

    def _replay_instrument(
        self, sandbox: sqlite3.Connection, bars: list[Bar]
    ) -> list[ReplayTrade]:
        venue = bars[0].venue
        symbol = bars[0].symbol
        # asset_class is the underlying_group_id prefix ("forex:EURUSD" → forex)
        # so replay regime/MarketView are vol-normalized per-class too. Unknown /
        # prefixless groups degrade to the generic floor (no crash).
        group_id = bars[0].underlying_group_id or ""
        asset_class = group_id.split(":", 1)[0] if ":" in group_id else "crypto"
        try:
            track = resolve_stream(venue).track
        except KeyError:
            return []
        trades: list[ReplayTrade] = []
        # One open position per strategy (independent sub-books on the same bars).
        open_by_strategy: dict[str, _OpenPosition] = {}
        regime_label = "chop"
        candidate = ""
        candidate_count = 0
        n = len(bars)
        for i in range(n):
            window = bars[: i + 1]
            # (a) regime signal (pure) + in-memory 2-close confirm.
            label, _strength, _evi = compute_real_regime_signal(
                window, asset_class=asset_class
            )
            regime_label, candidate, candidate_count = self._confirm_regime(
                current=regime_label, signal=label,
                candidate=candidate, count=candidate_count,
            )
            # (b) exit pass for every open position (uses THIS bar's close).
            cur = bars[i]
            for strat_id in list(open_by_strategy):
                pos = open_by_strategy[strat_id]
                closed = self._tick_exit(sandbox, pos, cur)
                if closed is not None:
                    trades.append(closed)
                    del open_by_strategy[strat_id]
            # (c) signal -> next-bar open entry (skip the last bar: no next bar).
            if i >= n - 1:
                continue
            mv = build_real_market_view(
                venue=venue, symbol=symbol,
                timeframe=self.config.bar_interval, bars=window,
                spread_bps=max(0.0, cur.spread_bps_close),
                asset_class=asset_class,
            )
            for strat in self.strategies:
                if strat.metadata.strategy_id in open_by_strategy:
                    continue
                if not strat.warmup_ok(mv):
                    continue
                raw = strat.generate_raw_signal(mv)
                if raw is None:
                    continue
                new_pos = self._open_position(
                    sandbox, strat=strat, raw=raw, bars=bars, i=i,
                    venue=venue, symbol=symbol, regime=regime_label, track=track,
                    market_view=mv,
                )
                if new_pos is not None:
                    open_by_strategy[strat.metadata.strategy_id] = new_pos
        # Force-close anything still open at the final bar (mark-to-close).
        last = bars[-1]
        for pos in open_by_strategy.values():
            trades.append(self._close_position(sandbox, pos, last, "end_of_data"))
        return trades

    def _confirm_regime(
        self, *, current: str, signal: str, candidate: str, count: int
    ) -> tuple[str, str, int]:
        """In-memory N-close confirm (mirrors live flip latency; no DB write)."""
        if signal == current:
            return current, "", 0
        if signal == candidate:
            count += 1
        else:
            candidate, count = signal, 1
        if count >= _REGIME_CONFIRM_CLOSES:
            return signal, "", 0
        return current, candidate, count

    def _open_position(
        self,
        sandbox: sqlite3.Connection,
        *,
        strat: BaseStrategy,
        raw: RawSignal,
        bars: list[Bar],
        i: int,
        venue: str,
        symbol: str,
        regime: str,
        track: str,
        market_view: object,
    ) -> _OpenPosition | None:
        meta = strat.metadata
        payload = build_sizer_payload(
            raw_signal=raw,
            venue=venue,
            symbol=symbol,
            instrument_id=bars[0].instrument_id,
            underlying_group_id=bars[0].underlying_group_id,
            asset_class=meta.asset_class,
            regime=regime,
            track=track,  # type: ignore[arg-type]
            listing_age_hours=10_000.0,  # mature instrument (no listing watchdog)
            leverage=1.0,
            equity_usd=self.config.starting_equity,
            conn=sandbox,
            now_ts=int(bars[i].ts),
        )
        intent: SignalIntent = payload["signal_intent"]
        sized = compute_size(
            sandbox,
            intent=intent,
            risk_state=payload["risk_state"],
            portfolio=payload["portfolio"],
            now_ts=int(bars[i].ts),
        )
        notional = sized.final_notional_usd
        if notional <= 0.0:
            return None
        fill_bar = bars[i + 1]  # next-bar open (no same-bar look-ahead)
        entry_price = apply_slippage(
            raw_price=float(fill_bar.open),
            spread_bps=max(0.0, fill_bar.spread_bps_close),
            side=raw.side, is_entry=True,
        )
        if entry_price <= 0.0:
            return None
        qty = notional / entry_price
        atr_pct = float(getattr(market_view, "atr_pct", 0.0) or 0.0)
        return _OpenPosition(
            raw_signal=raw,
            strategy=strat,
            side=raw.side,
            entry_ts=int(fill_bar.ts),
            entry_price=entry_price,
            notional=notional,
            qty=qty,
            atr_pct=atr_pct,
            regime=regime,
            exit_state=init_exit_state(entry_price=entry_price, side=raw.side),
            venue=venue,
            symbol=symbol,
        )

    def _tick_exit(
        self, sandbox: sqlite3.Connection, pos: _OpenPosition, bar: Bar
    ) -> ReplayTrade | None:
        """Advance the exit FSM with this bar's close; close if it fires."""
        if int(bar.ts) <= pos.entry_ts:
            return None  # entry bar itself — no exit on the fill bar
        last_price = float(bar.close)
        # Provisional pnl_r at this bar (gross, for the FSM's protected-BEP rail).
        gross = gross_pnl_usd(
            side=pos.side, entry_price=pos.entry_price,
            exit_price=last_price, qty=pos.qty,
        )
        pnl_r_now = pnl_to_r(gross)
        held_seconds = int(bar.ts) - pos.entry_ts
        decision = evaluate_exit(
            prev=pos.exit_state,
            side=pos.side,
            entry_price=pos.entry_price,
            last_price=last_price,
            atr_pct=pos.atr_pct,
            pnl_r=pnl_r_now,
            held_seconds=held_seconds,
            loser_timeout_sec=_loser_timeout_for_strategy(
                pos.strategy.metadata.strategy_id
            ),
        )
        pos.exit_state = decision.state
        if not decision.close:
            return None
        return self._close_position(sandbox, pos, bar, decision.close_reason or "exit")

    def _close_position(
        self, sandbox: sqlite3.Connection, pos: _OpenPosition, bar: Bar, reason: str
    ) -> ReplayTrade:
        exit_price = apply_slippage(
            raw_price=float(bar.close),
            spread_bps=max(0.0, bar.spread_bps_close),
            side=pos.side, is_entry=False,
        )
        gross = gross_pnl_usd(
            side=pos.side, entry_price=pos.entry_price,
            exit_price=exit_price, qty=pos.qty,
        )
        exit_notional = abs(exit_price * pos.qty)
        rt_fee = round_trip_fee_usd(
            venue=pos.venue, entry_notional=pos.notional, exit_notional=exit_notional,
        )
        net = gross - rt_fee
        pnl_r = pnl_to_r(net)
        won = net > 0.0
        # Cell-matrix update on the SANDBOX conn only (live cell_stats immutable).
        update_on_trade_close(
            sandbox,
            trade=TradeClose(
                key=CellKeyP0(
                    exchange=pos.venue, strategy=pos.strategy.metadata.strategy_id,
                    ticker=pos.symbol, regime=pos.regime,
                ),
                context=CellContext(
                    group=_safe_product_class(pos.venue),
                    session="asia", direction=pos.side, liquidity_tier="top",
                ),
                pnl_r=pnl_r, won=won, closed_ts=int(bar.ts),
            ),
        )
        return ReplayTrade(
            entry_ts=pos.entry_ts,
            exit_ts=int(bar.ts),
            symbol=pos.symbol,
            strategy=pos.strategy.metadata.strategy_id,
            regime=pos.regime,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            notional=pos.notional,
            gross_pnl_usd=gross,
            rt_fee_usd=rt_fee,
            net_pnl_usd=net,
            pnl_r=pnl_r,
            exit_reason=reason,
        )

    def _load_bars(self) -> dict[str, list[Bar]]:
        """Read bars from the live DB read-only (ATTACH-free, separate ro conn)."""
        ro = sqlite3.connect(f"file:{self.config.live_db_path}?mode=ro", uri=True)
        try:
            return load_bars(
                ro,
                instrument_ids=self.config.instrument_ids,
                bar_interval=self.config.bar_interval,
                start_ts=self.config.start_ts,
                end_ts=self.config.end_ts,
            )
        finally:
            ro.close()


def _safe_product_class(venue: str) -> str:
    try:
        return resolve_stream(venue).product_class
    except KeyError:
        return venue


def load_bars(
    conn: sqlite3.Connection,
    *,
    instrument_ids: tuple[str, ...],
    bar_interval: str,
    start_ts: int | None,
    end_ts: int | None,
) -> dict[str, list[Bar]]:
    """Read ``bars`` rows -> ``{instrument_id: [Bar, ...]}`` ordered by ts."""
    clauses = ["bar_interval = ?"]
    params: list[object] = [bar_interval]
    if instrument_ids:
        placeholders = ",".join("?" for _ in instrument_ids)
        clauses.append(f"instrument_id IN ({placeholders})")
        params.extend(instrument_ids)
    if start_ts is not None:
        clauses.append("ts >= ?")
        params.append(int(start_ts))
    if end_ts is not None:
        clauses.append("ts <= ?")
        params.append(int(end_ts))
    sql = (
        "SELECT instrument_id, underlying_group_id, venue, symbol, bar_interval, "
        "ts, open, high, low, close, volume, notional_usd, trade_count, vwap, "
        "bid_close, ask_close, spread_bps_close, source "
        f"FROM bars WHERE {' AND '.join(clauses)} ORDER BY instrument_id, ts"
    )
    out: dict[str, list[Bar]] = {}
    for r in conn.execute(sql, params):
        bar = Bar(
            instrument_id=str(r[0]), underlying_group_id=str(r[1]), venue=str(r[2]),
            symbol=str(r[3]), bar_interval=str(r[4]), ts=int(r[5]),
            open=float(r[6]), high=float(r[7]), low=float(r[8]), close=float(r[9]),
            volume=float(r[10]), notional_usd=float(r[11]), trade_count=int(r[12]),
            vwap=float(r[13]), bid_close=float(r[14]), ask_close=float(r[15]),
            spread_bps_close=float(r[16]), source=str(r[17]),
        )
        out.setdefault(bar.instrument_id, []).append(bar)
    return out
