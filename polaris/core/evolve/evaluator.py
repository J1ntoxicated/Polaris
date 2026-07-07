"""P0a STEP 4 — OOS + DSR honest-N evaluator (OFFLINE).

For ONE variant strategy instance over a chosen instrument set:

  1. Split each instrument's bars into IS / OOS by BAR INDEX via
     :func:`~polaris.core.benchmark.walk_forward.walk_forward_splits`
     (``embargo_bars = warmup_bars + max_ttl``). If the horizon cannot host a
     single fold — OR the warmup consumes the whole IS window so ZERO IS trades
     are possible (FIX 5) — return a ``data_bounded`` result (NO silent shrink,
     NOT a gate failure).
  2. Run :class:`~polaris.core.replay.engine.ReplayEngine` with the variant
     INJECTED via the ``strategies=`` seam over the CONTIGUOUS prefix
     ``bars[:oos_end]`` (preserves warmup + indicator continuity through the
     engine's per-bar warmup gate), then partition the produced trades by their
     entry-bar index, ENFORCING the embargo purge (FIX 4): IS = entry idx <=
     ``max(is_index)`` AND exit idx < ``oos_start``; OOS = entry idx >=
     ``oos_start``; any trade whose entry lands in the purge band
     ``(max(is_index), oos_start)`` — or whose IS-side exit straddles into OOS —
     is DROPPED. Slicing the bar list directly would drop warmup history and
     corrupt the precomputed indicators; the index partition keeps the OOS slice
     leak-free while preserving continuity.
  3. Evaluate the 3-tier gate on the IS slice and the OOS slice separately,
     passing ``is_oos_spread = IS Sharpe − OOS Sharpe`` so the gate reports the
     overfit flag. The OOS verdict is the honest held-out edge.

DSR honest-N (multiple-testing correction)
------------------------------------------
The existing :func:`evaluate_gate` leaves ``deflated_sharpe == PSR`` (un-deflated)
for a single-strategy run — it cannot see the search breadth. We ADD the
multiple-testing penalty at THIS layer (never weakening the gate):
:func:`honest_dsr` recomputes the deflated Sharpe on the OOS net-R sample with
``trials = total_variants_searched`` and the REAL cross-variant Sharpe variance
(FIX 6a — the variance of the run's ACTUAL OOS Sharpes across the enumerated grid,
supplied by the driver via ``sr_variance``; a single variant has no dispersion so
the DSR is left un-deflated, never fabricated), using the SAME :func:`deflated_sharpe`
math the gate uses. The OOS verdict ``oos_pass`` GATES on that trial-deflated
significance (FIX 6b): ``relative AND risk_adjusted AND (honest_dsr >= 0.95 AND
lcb > 0)``. More trials can only LOWER the honest DSR (deflation is monotone in
trials), so the verdict gets STRICTER, never looser.

OFFLINE ONLY. Reads the live DB read-only (through the engine's seam) and writes
nothing — behavior-0 by construction. Never touches sizing / T4 / gate
thresholds.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from polaris.core.benchmark.baselines import (
    BaselineCurve,
    bollinger_baseline,
    buy_and_hold_baseline,
    tsmom_baseline,
)
from polaris.core.benchmark.gate import GateResult, evaluate_gate
from polaris.core.benchmark.statistics import (
    deflated_sharpe,
    kurtosis,
    sharpe_ratio,
    skewness,
)
from polaris.core.benchmark.walk_forward import WalkForwardSplit, walk_forward_splits
from polaris.core.data.schema import Bar
from polaris.core.evolve.positive_control import gate_can_discriminate, min_passable_n
from polaris.core.metrics.risk_unit import R_USD_PROXY
from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.models import ReplayConfig, ReplayResult, ReplayTrade
from polaris.strategies.base import BaseStrategy

__all__ = [
    "VariantEval",
    "build_baselines",
    "cross_variant_sr_variance",
    "evaluate_variant",
    "gate_can_discriminate",
    "honest_dsr",
    "min_passable_n",
    "split_is_oos",
]

# Honest-N deflated-Sharpe pass floor — mirrors the gate's statistical-tier
# significance threshold (gate._PSR_PASS). A future SEARCH_SPACE_EXISTS verdict
# requires the trial-deflated DSR to clear this (FIX 6b).
_HONEST_DSR_PASS: float = 0.95


def split_is_oos(
    *, n_bars: int, n_splits: int, embargo_bars: int
) -> WalkForwardSplit | None:
    """Return the FIRST purged walk-forward fold, or ``None`` if the horizon
    cannot host one with the requested embargo (honest degenerate case).

    A single held-out OOS block per symbol is the design's honest framing for the
    thin-N 1H horizon; ``n_splits`` is forwarded so a caller with a long 1m
    history can request more folds (we still take the first). Returns ``None``
    rather than silently shrinking the embargo when ``walk_forward_splits``
    reports ``()``.
    """
    splits = walk_forward_splits(
        n_bars=n_bars, n_splits=n_splits, embargo_bars=embargo_bars, anchored=True
    )
    return splits[0] if splits else None


def cross_variant_sr_variance(oos_sharpes: list[float]) -> float:
    """Sample variance (ddof=1) of the ACTUAL OOS Sharpes across the run's
    enumerated variant grid (FIX 6a) — the REAL cross-trial dispersion the
    deflated-Sharpe SR* consumes. ``< 2`` Sharpes -> 0.0 (no dispersion: a single
    variant has no multiple-testing search to correct, so the DSR is left
    un-deflated rather than fabricated).
    """
    n = len(oos_sharpes)
    if n < 2:
        return 0.0
    mean = sum(oos_sharpes) / n
    return sum((s - mean) ** 2 for s in oos_sharpes) / (n - 1)


def honest_dsr(*, net_r: list[float], trials: int, sr_variance: float = 0.0) -> float:
    """Deflated Sharpe on ``net_r`` with the cross-variant ``trials`` penalty.

    Uses the project's :func:`deflated_sharpe` (same math the gate uses).
    ``sr_variance`` is the REAL cross-variant Sharpe variance (from
    :func:`cross_variant_sr_variance`) — no self-referential proxy. With
    ``trials <= 1`` OR ``sr_variance <= 0`` the inflated benchmark SR* is 0, so
    the result equals the raw PSR (un-deflated — there was no search to correct).
    Monotone: a larger ``trials`` raises SR* and can only LOWER the result (TDD).
    ``< 2`` samples -> 0.5 (no evidence), matching the gate's PSR floor.
    """
    n = len(net_r)
    if n < 2:
        return 0.5
    sr = sharpe_ratio(net_r, periods=1.0)
    skew = skewness(net_r)
    kurt = kurtosis(net_r)
    return deflated_sharpe(
        sr=sr, n=n, skew=skew, kurt=kurt, trials=max(1, trials), sr_variance=sr_variance
    )


@dataclass(frozen=True, slots=True)
class VariantEval:
    """One variant's IS/OOS evaluation result over a chosen instrument set.

    ``oos_pass`` is the honest held-out verdict GATED on trial-deflated
    significance (FIX 6b): ``oos_gate.relative AND oos_gate.risk_adjusted AND
    (dsr >= 0.95 AND lcb > 0)`` — NOT the raw single-strategy ``oos_gate.passed``.
    ``dsr`` is the HONEST-N deflated Sharpe on the OOS net-R sample (deflated by
    ``trials_searched`` against the REAL cross-variant Sharpe variance) — stricter
    than ``oos_gate.deflated_sharpe`` (the un-deflated single-strategy gate field,
    retained for parity). ``data_bounded`` True means the horizon could not host a
    fold (or warmup consumed the IS window); all metrics are then 0 / False and
    the caller reports "data-unavailable", NOT a gate failure.
    """

    variant_id: str
    cell_key: str
    data_bounded: bool
    is_pass: bool
    oos_pass: bool
    # OOS point estimates / CIs (the honest verdict surface).
    pnl_r_mean: float
    lcb: float
    ucb: float
    dsr: float  # honest-N deflated Sharpe on OOS net-R (trials_searched penalty)
    n_trades: int
    # IS metrics + the overfit flag.
    is_sharpe: float
    oos_sharpe: float
    trials_searched: int
    # Full gate results (kept for the registry mapping + dashboard).
    is_gate: GateResult | None = None
    oos_gate: GateResult | None = None
    is_entry_ts: tuple[int, ...] = field(default_factory=tuple)
    oos_entry_ts: tuple[int, ...] = field(default_factory=tuple)
    # OOS per-trade net-R sample retained so the driver can re-deflate the DSR
    # against the REAL cross-variant Sharpe variance once the whole grid is known
    # (FIX 6a two-pass).
    oos_net_r: tuple[float, ...] = field(default_factory=tuple)


def _data_bounded_result(variant: BaseStrategy, cell_key: str, trials: int) -> VariantEval:
    return VariantEval(
        variant_id=getattr(variant, "variant_id", variant.metadata.strategy_id),
        cell_key=cell_key,
        data_bounded=True,
        is_pass=False,
        oos_pass=False,
        pnl_r_mean=0.0,
        lcb=0.0,
        ucb=0.0,
        dsr=0.5,
        n_trades=0,
        is_sharpe=0.0,
        oos_sharpe=0.0,
        trials_searched=trials,
        is_gate=None,
        oos_gate=None,
    )


def build_baselines(
    bars: list[Bar], *, starting_equity: float
) -> dict[str, BaselineCurve]:
    """Same-bars / same-fee / same-clock baselines for the relative tier.

    Mirrors ``run_replay.build_baselines``: buy&hold + naive TSMOM + naive
    Bollinger on the supplied bar slice. ``< 2`` bars -> empty (the gate then has
    no relative comparison and cannot pass tier 1)."""
    if len(bars) < 2:
        return {}
    return {
        "bh": buy_and_hold_baseline(bars, starting_equity=starting_equity),
        "tsmom": tsmom_baseline(bars, starting_equity=starting_equity),
        "bollinger": bollinger_baseline(bars, starting_equity=starting_equity),
    }


def _index_of_ts(bars: list[Bar]) -> dict[int, int]:
    return {int(b.ts): i for i, b in enumerate(bars)}


def _partition_trades(
    trades: tuple[ReplayTrade, ...],
    *,
    bars_by_instrument: dict[str, list[Bar]],
    oos_start: int,
    is_end: int | None = None,
) -> tuple[list[ReplayTrade], list[ReplayTrade]]:
    """Split trades into (IS, OOS) by ENTRY-bar index per instrument, ENFORCING
    the embargo purge (FIX 4 — no leakage across the boundary).

    A trade's entry-/exit-bar index is the position of its ``entry_ts`` /
    ``exit_ts`` in the source bar list for ``venue:symbol``. ``is_end`` is the
    last IS bar index (``max(split.is_index)``); the purge band is the open
    interval ``(is_end, oos_start)``.

      * IS  = entry idx <= is_end AND exit idx < oos_start (a trade whose exit
              straddles into the OOS block leaks held-out info -> DROPPED).
      * OOS = entry idx >= oos_start.
      * DROP any trade whose entry index lands in the purge band ``(is_end,
        oos_start)`` (the embargo).

    When ``is_end`` is ``None`` it defaults to ``oos_start - 1`` (no separate
    purge band — kept for callers that pass only the OOS cut). A trade whose
    instrument is NOT in ``bars_by_instrument`` is DROPPED — it is NEVER matched
    against another instrument's timeline (that borrow corrupts the index).
    """
    cut_is_end = is_end if is_end is not None else (oos_start - 1)
    entry_index: dict[str, dict[int, int]] = {
        inst: _index_of_ts(bars) for inst, bars in bars_by_instrument.items()
    }
    is_tr: list[ReplayTrade] = []
    oos_tr: list[ReplayTrade] = []
    for tr in trades:
        inst = f"{_venue_of(tr, bars_by_instrument)}:{tr.symbol}"
        idx_map = entry_index.get(inst)
        if idx_map is None:
            continue  # unknown instrument — never borrow another timeline.
        entry_idx = idx_map.get(int(tr.entry_ts))
        if entry_idx is None:
            continue
        if entry_idx >= oos_start:
            oos_tr.append(tr)
            continue
        if entry_idx > cut_is_end:
            continue  # entry inside the embargo purge band — DROP.
        # IS candidate: drop it if the EXIT crosses into the OOS block.
        exit_idx = idx_map.get(int(tr.exit_ts))
        if exit_idx is not None and exit_idx >= oos_start:
            continue  # cross-boundary straddle — leaks held-out info, DROP.
        is_tr.append(tr)
    return is_tr, oos_tr


def _venue_of(tr: ReplayTrade, bars_by_instrument: dict[str, list[Bar]]) -> str:
    """Resolve the venue for a trade's symbol from the bar source (single venue
    per symbol in practice; falls back to the symbol's first matching key)."""
    for inst, bars in bars_by_instrument.items():
        if bars and bars[0].symbol == tr.symbol:
            return bars[0].venue
        if inst.endswith(f":{tr.symbol}"):
            return inst.split(":", 1)[0]
    return ""


def _result_from(
    trades: list[ReplayTrade], full: ReplayResult, starting_equity: float
) -> ReplayResult:
    """Build a ReplayResult carrying only ``trades`` (metrics recomputed minimally
    for the gate's relative/turnover fields). The gate reads per-trade net-R from
    ``trades`` and a few aggregate ``metrics`` keys; we recompute the keys it
    uses (``net_pnl``, ``turnover``, ``fee_drag_real_r``) from the slice."""
    net_pnl = sum(t.net_pnl_usd for t in trades)
    turnover = sum(t.notional for t in trades)
    fee_drag = sum(t.rt_fee_usd for t in trades)
    metrics = {
        "net_pnl": net_pnl,
        "turnover": turnover,
        # ReplayTrade carries no venue (replay-engine-internal), so this
        # unifies onto risk_unit.R_USD_PROXY — the ONE documented $-per-R
        # fallback proxy — instead of a bare 50.0 literal (2026-07-07).
        "fee_drag_real_r": fee_drag / R_USD_PROXY,
        "max_dd": float(full.metrics.get("max_dd", 0.0)),
        "n": float(len(trades)),
    }
    return ReplayResult(
        trades=tuple(trades),
        equity_curve_real_fee_net=(),
        metrics=metrics,
    )


def _slice_eval_sharpe(trades: list[ReplayTrade]) -> float:
    return sharpe_ratio([t.pnl_r for t in trades], periods=1.0)


def evaluate_variant(
    *,
    variant: BaseStrategy,
    bars_by_instrument: dict[str, list[Bar]],
    config: ReplayConfig,
    n_splits: int,
    warmup_bars: int,
    max_ttl: int,
    total_variants_searched: int,
    sandbox_factory: Callable[[], sqlite3.Connection],
    cell_key: str | None = None,
    sr_variance: float = 0.0,
) -> VariantEval:
    """Evaluate ONE variant: IS/OOS split, replay, 3-tier gate + honest-N DSR.

    ``bars_by_instrument`` maps ``venue:symbol -> [Bar]`` (the chosen instrument
    set; the split horizon is the MAX bar count across instruments — the longest
    history defines the fold geometry, shorter instruments simply contribute
    fewer OOS bars). ``warmup_bars`` + ``max_ttl`` set ``embargo_bars``.
    ``total_variants_searched`` is the grid breadth behind this winner (honest-N
    deflation); ``sr_variance`` is the REAL cross-variant OOS-Sharpe variance for
    that deflation (0.0 = un-deflated single-variant path — FIX 6a). The OOS
    verdict is GATED on trial-deflated significance: ``relative AND risk_adjusted
    AND (honest_dsr >= 0.95 AND lcb > 0)`` (FIX 6b). ``sandbox_factory`` builds a
    FRESH in-memory sandbox conn per replay (the engine writes cell-stats to it
    only). Returns a ``data_bounded`` :class:`VariantEval` when the horizon cannot
    host a fold OR when warmup consumes the whole IS window (FIX 5).
    """
    variant_id = getattr(variant, "variant_id", variant.metadata.strategy_id)
    ck = cell_key if cell_key is not None else variant_id
    embargo = int(warmup_bars) + int(max_ttl)
    n_bars = max((len(b) for b in bars_by_instrument.values()), default=0)
    split = split_is_oos(n_bars=n_bars, n_splits=n_splits, embargo_bars=embargo)
    if split is None:
        return _data_bounded_result(variant, ck, total_variants_searched)

    oos_start = min(split.oos_index)
    oos_end = max(split.oos_index) + 1  # exclusive
    is_end = max(split.is_index)  # last IS bar index (FIX 4 purge band edge)
    # FIX 5: when the warmup consumes the whole IS window, ZERO IS trades are
    # structurally possible (the strategy's own warmup gate suppresses every IS
    # bar) — e.g. rsi_bb warmup 205 vs an IS window ending at bar 45. The first
    # bar at which the strategy CAN emit is index ``warmup_bars``; if that is
    # past ``is_end`` the IS window is data-starved, NOT gate-failed -> mark
    # data_bounded (excluded from the FEATURE_BOTTLENECK denominator upstream).
    if is_end < int(warmup_bars):
        return _data_bounded_result(variant, ck, total_variants_searched)
    # Contiguous prefix bars[:oos_end] preserves warmup + indicator continuity;
    # we partition the produced trades by entry-bar index afterward (no slice of
    # the bar list itself, which would corrupt the precomputed indicators).
    prefix = {
        inst: bars[:oos_end] for inst, bars in bars_by_instrument.items()
    }
    strategies: list[BaseStrategy] = [variant]
    full = ReplayEngine(config, strategies=strategies).run_with_bars(
        sandbox_factory(), {k: list(v) for k, v in prefix.items()}
    )
    is_tr, oos_tr = _partition_trades(
        full.trades, bars_by_instrument=prefix, oos_start=oos_start, is_end=is_end
    )

    starting_equity = config.starting_equity
    is_sharpe = _slice_eval_sharpe(is_tr)
    oos_sharpe = _slice_eval_sharpe(oos_tr)
    is_oos_spread = is_sharpe - oos_sharpe

    # Baselines on the matching bar slice for each window (same-clock relative
    # tier). IS baselines on bars[:oos_start]; OOS baselines on the OOS block.
    proxy = max(bars_by_instrument, key=lambda k: len(bars_by_instrument[k]))
    proxy_bars = bars_by_instrument[proxy]
    is_baselines = build_baselines(
        proxy_bars[:oos_start], starting_equity=starting_equity
    )
    oos_baselines = build_baselines(
        proxy_bars[oos_start:oos_end], starting_equity=starting_equity
    )

    is_result = _result_from(is_tr, full, starting_equity)
    oos_result = _result_from(oos_tr, full, starting_equity)
    is_gate = evaluate_gate(
        replay=is_result, baselines=is_baselines,
        trials=total_variants_searched, starting_equity=starting_equity,
        is_oos_spread=is_oos_spread,
    )
    oos_gate = evaluate_gate(
        replay=oos_result, baselines=oos_baselines,
        trials=total_variants_searched, starting_equity=starting_equity,
        is_oos_spread=is_oos_spread,
    )

    # Honest-N deflated Sharpe on the OOS sample: ADD the cross-variant
    # multiple-testing penalty the single-strategy gate cannot see, deflated
    # against the REAL cross-variant Sharpe variance (FIX 6a).
    oos_net_r = [t.pnl_r for t in oos_tr]
    dsr_honest = honest_dsr(
        net_r=oos_net_r, trials=total_variants_searched, sr_variance=sr_variance
    )
    # FIX 6b: the OOS verdict GATES on trial-deflated significance — relative AND
    # risk_adjusted (the gate's own tiers) AND (honest_dsr >= 0.95 AND lcb > 0).
    lcb = oos_gate.net_pnl_r_ci[0]
    oos_pass = bool(
        oos_gate.tiers["relative"].passed
        and oos_gate.tiers["risk_adjusted"].passed
        and dsr_honest >= _HONEST_DSR_PASS
        and lcb > 0.0
    )

    return VariantEval(
        variant_id=variant_id,
        cell_key=ck,
        data_bounded=False,
        is_pass=bool(is_gate.passed),
        oos_pass=oos_pass,
        pnl_r_mean=oos_gate.net_pnl_r_mean,
        lcb=lcb,
        ucb=oos_gate.net_pnl_r_ci[1],
        dsr=dsr_honest,
        n_trades=len(oos_tr),
        is_sharpe=is_sharpe,
        oos_sharpe=oos_sharpe,
        trials_searched=total_variants_searched,
        is_gate=is_gate,
        oos_gate=oos_gate,
        is_entry_ts=tuple(t.entry_ts for t in is_tr),
        oos_entry_ts=tuple(t.entry_ts for t in oos_tr),
        oos_net_r=tuple(oos_net_r),
    )
