"""Multi-horizon ensemble replay — independent sub-books, ONE shared capital.

Parity with the just-deployed live multi-horizon structure (commit e92bbbc):
the SAME strategies run on independent position books per horizon (scalp = 1m
tick flow, swing = 1H bar pipeline, position = 1D) holding *complementary,
independent positions* on the same symbol, accounted against a SINGLE capital
base.

This module replicates that exactly with zero new trading logic:

  * each horizon ``h`` is a plain :class:`ReplayEngine` over its native bar
    interval — independent sizing on its own sandbox, independent exit FSM,
    independent position book (a 1m scalp and a 1H swing on the same symbol
    coexist; they are NOT deduped), and
  * the combined equity curve compounds EVERY horizon's ``net_pnl_usd`` on one
    running equity via the existing :func:`build_equity_curve` over the *union*
    of all trades. Cross-horizon capital contention surfaces here: a scalp loss
    lowers the equity base the next swing trade's return is measured against.

Behaviour 0: a one-element horizon set reproduces the plain engine byte-for-byte
(the existing single-interval replay path is untouched).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from polaris.core.data.schema import Bar
from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.equity_curve import build_equity_curve, summarize_metrics
from polaris.core.replay.models import ReplayConfig, ReplayResult, ReplayTrade
from polaris.strategies.base import BaseStrategy

__all__ = ["MultiHorizonReplay", "MultiHorizonResult"]


@dataclass(frozen=True, slots=True)
class MultiHorizonResult:
    """Per-horizon sub-book results + the shared-capital synthesis.

    ``per_horizon`` keeps each horizon's full :class:`ReplayResult` (independent
    attribution — Sharpe / dd / fee drag per horizon). ``combined_trades`` is the
    union of all sub-books' trades on one global clock; ``combined_curve`` and
    ``combined_metrics`` are that union accounted on a SINGLE shared capital base.
    """

    per_horizon: dict[str, ReplayResult]
    combined_trades: tuple[ReplayTrade, ...]
    combined_curve: tuple[tuple[int, float], ...]
    combined_metrics: dict[str, float] = field(default_factory=dict)


class MultiHorizonReplay:
    """Run one independent sub-book per horizon, synthesise shared capital."""

    def __init__(
        self, config: ReplayConfig, strategies: list[BaseStrategy] | None = None
    ) -> None:
        self.config = config
        # ``horizons`` defaults to the single ``bar_interval`` so a config with
        # no horizon set still ensembles (trivially) — never a silent no-op.
        self.horizons: tuple[str, ...] = config.horizons or (config.bar_interval,)
        self._strategies = strategies

    # -- public -----------------------------------------------------------
    def run(self) -> MultiHorizonResult:
        """Production path: each horizon seeds its own live-read sandbox."""
        per_horizon: dict[str, ReplayResult] = {}
        for h in self.horizons:
            engine = ReplayEngine(self._horizon_config(h), self._strat_arg())
            per_horizon[h] = engine.run()
        return self._synthesise(per_horizon)

    def run_with_bars(
        self,
        *,
        sandbox_factory: Callable[[], sqlite3.Connection],
        bars_by_horizon: dict[str, dict[str, list[Bar]]],
    ) -> MultiHorizonResult:
        """Test seam: explicit per-horizon sandboxes + pre-loaded bars.

        ``sandbox_factory`` mints a FRESH sandbox per horizon (independent
        sizing state, mirroring the live independent sub-books). ``bars_by_horizon``
        maps each horizon interval -> ``{instrument_id: [Bar, ...]}``.
        """
        per_horizon: dict[str, ReplayResult] = {}
        for h in self.horizons:
            engine = ReplayEngine(self._horizon_config(h), self._strat_arg())
            bars = bars_by_horizon.get(h, {})
            per_horizon[h] = engine.run_with_bars(sandbox_factory(), bars)
        return self._synthesise(per_horizon)

    # -- internals --------------------------------------------------------
    def _horizon_config(self, horizon: str) -> ReplayConfig:
        """The ensemble config retargeted to one horizon's bar interval.

        ``horizons`` is cleared on the per-horizon config so the sub-engine runs
        a plain single-interval replay (no recursion)."""
        return replace(self.config, bar_interval=horizon, horizons=None)

    def _strat_arg(self) -> list[BaseStrategy] | None:
        # ReplayEngine accepts ``strategies: list[BaseStrategy] | None``. Pass
        # through (None -> all_strategies()), copying so each sub-engine owns its
        # own list (ReplayEngine mutates none, but isolation is cheap + explicit).
        if self._strategies is None:
            return None
        return list(self._strategies)

    def _synthesise(self, per_horizon: dict[str, ReplayResult]) -> MultiHorizonResult:
        """Union the sub-book trades onto ONE shared-capital equity curve."""
        all_trades: list[ReplayTrade] = [
            t for r in per_horizon.values() for t in r.trades
        ]
        # Global deterministic ordering identical to the single-book engine
        # (so a one-horizon ensemble == the plain engine byte-for-byte).
        all_trades.sort(key=lambda t: (t.exit_ts, t.entry_ts, t.symbol, t.strategy))
        start_ts = self._curve_start_ts(per_horizon)
        curve = build_equity_curve(
            trades=all_trades,
            starting_equity=self.config.starting_equity,
            start_ts=start_ts,
        )
        metrics = summarize_metrics(
            trades=all_trades, curve=curve,
            starting_equity=self.config.starting_equity,
        )
        return MultiHorizonResult(
            per_horizon=per_horizon,
            combined_trades=tuple(all_trades),
            combined_curve=tuple(curve),
            combined_metrics=metrics,
        )

    def _curve_start_ts(self, per_horizon: dict[str, ReplayResult]) -> int:
        """Earliest anchor across every sub-book's curve (shared clock)."""
        anchors = [
            r.equity_curve_real_fee_net[0][0]
            for r in per_horizon.values()
            if r.equity_curve_real_fee_net
        ]
        return min(anchors) if anchors else 0
