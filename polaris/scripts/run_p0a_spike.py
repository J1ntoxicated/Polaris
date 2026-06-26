"""P0a STEP 5 — KILL-spike driver + VERDICT (OFFLINE CLI + importable core).

OFFLINE ONLY. Touches NO live trading / sizing / T4 chain / gate thresholds.
behavior-0 by construction: this driver only enumerates bounded-numeric config
variants of EXISTING strategies, runs the OFFLINE :class:`ReplayEngine` over them
via the ``strategies=`` seam, evaluates the EXISTING 3-tier benchmark gate on a
HELD-OUT (OOS) bar-index slice, and persists each variant×cell outcome to a
SEPARATE ``data/p0a_registry.sqlite`` (NEW db). ``data/polaris_live.sqlite`` is
opened READ-ONLY as a bar source and is never written.

The spike answers ONE question: does a bounded numeric search over the existing
TA feature space yield configs that pass the real-fee gate on held-out data?

POSITIVE CONTROL (FIX 1, keystone). A 0-pass result is only FEATURE exhaustion
if the gate can PROVABLY certify a strong edge at the run's realised held-out N.
We synthesise a STRONG low-noise +0.2R edge at the run's representative OOS N and
check whether the gate's statistical tier passes (:func:`gate_can_discriminate`).
If even that fails, the instrument is VALIDATION-STARVED (the held-out N is too
thin to certify ANY edge), not feature-exhausted. P0a may NOT claim
FEATURE_BOTTLENECK unless the positive control passes.

  - positive control FAILS    -> ``VALIDATION_STARVED`` (the gate cannot pass a
        strong edge at the available N — accumulate held-out bars, do NOT
        conclude the feature space is dead).
  - mostly data_bounded       -> ``DATA_BOUNDED`` (insufficient bar history).
  - pc PASSES & IS pass-rate 0 (with >= K_min evaluable variants)
                              -> ``FEATURE_BOTTLENECK`` (TA feature space
        exhausted; redirect to P0b/P1, do NOT build a generator).
  - pc PASSES & IS>0 & OOS 0  -> ``OVERFIT_NO_GENERALIZATION`` (held-out edge
        vanishes).
  - pc PASSES & IS>0 & OOS>0  -> ``SEARCH_SPACE_EXISTS`` (trial-deflated held-out
        edge exists — a P2 generator is justified).

``~0`` is defined explicitly: a pass count of ZERO, printed alongside the raw
numbers behind the verdict.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from polaris.core.data.schema import Bar
from polaris.core.evolve import (
    TrialRow,
    VariantEval,
    cross_variant_sr_variance,
    enumerate_grid,
    evaluate_variant,
    gate_can_discriminate,
    make_variant,
    min_passable_n,
    open_registry,
    record_trial,
)
from polaris.core.replay.engine import load_bars
from polaris.core.replay.models import ReplayConfig
from polaris.scripts._p0a_verdict import K_MIN_EVALUABLE as K_MIN_EVALUABLE
from polaris.scripts._p0a_verdict import SpikeVerdict, _classify
from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.base import BaseStrategy

__all__ = [
    "DEFAULT_INSTRUMENTS",
    "SpikeVerdict",
    "StrategySpec",
    "main",
    "run_spike",
]

# Design-recommended OFFLINE instrument pools (design.bar_history). 1m ->
# volume_burst (multi-fold OOS); 1H -> trend/breakout strategies pooled across
# the top OKX 1H symbols (single OOS block, thin-N visible). Equity (alpaca 1D)
# is data-unavailable in polaris_live.sqlite -> those specs report DATA_BOUNDED.
DEFAULT_INSTRUMENTS: dict[str, tuple[tuple[str, ...], str]] = {
    # strategy_id -> (instrument_ids, bar_interval)
    "volume_burst": (
        ("okx:BTC-USDT", "okx:ALGO-USDT", "okx:INJ-USDT", "okx:NEAR-USDT", "okx:ETH-USDT"),
        "1m",
    ),
    "rsi_bb_pullback": (
        ("okx:BTC-USDT", "okx:ETH-USDT", "okx:ALGO-USDT", "okx:INJ-USDT", "okx:ADA-USDT"),
        "1H",
    ),
    "spot_donchian": (
        ("okx:BTC-USDT", "okx:FLOKI-USDT", "okx:HYPE-USDT", "okx:ALGO-USDT",
         "okx:ETH-USDT", "okx:INJ-USDT", "okx:ADA-USDT"),
        "1H",
    ),
    "fx_breakout_basket": (("capital:EURUSD_W",), "1H"),
    "xau_indices_trend": (("capital:US100",), "1H"),
}

@dataclass(frozen=True, slots=True)
class StrategySpec:
    """One strategy's OFFLINE search spec: which knobs, which bars, which split.

    ``bars_by_instrument`` maps ``venue:symbol -> [Bar]`` (the pooled instrument
    set; the fold geometry uses the longest history). ``warmup_bars`` + ``max_ttl``
    set the embargo. ``exchange`` is the venue tag for the cell_key. The grid
    comes from ``PARAM_BOUNDS[strategy_id]`` via ``enumerate_grid``."""

    strategy_id: str
    base_cls: type[BaseStrategy]
    exchange: str
    bars_by_instrument: dict[str, list[Bar]]
    n_splits: int
    warmup_bars: int
    max_ttl: int


def _cell_key(*, exchange: str, strategy: str, pool: str, regime: str) -> str:
    """Canonical ``exchange|strategy|ticker|regime`` (mirrors CellKeyP0).

    The OOS evaluation pools across the instrument set (thin-N 1H), so the
    ``ticker`` slot carries the pool tag and ``regime`` is ``mixed`` (the OOS
    block spans multiple confirmed regimes; per-regime partition would over-split
    the already-thin OOS N — the design's honest framing)."""
    return f"{exchange}|{strategy}|{pool}|{regime}"


def _pool_tag(bars_by_instrument: dict[str, list[Bar]]) -> str:
    """Stable pool identifier from the instrument keys (symbols only)."""
    syms = sorted(k.split(":", 1)[-1] for k in bars_by_instrument)
    if not syms:
        return "EMPTY"
    if len(syms) == 1:
        return syms[0]
    return f"POOL[{'+'.join(syms)}]"


def _trial_row(
    *,
    res: VariantEval,
    overrides: dict[str, float],
    spec: StrategySpec,
    cell_key: str,
    pool: str,
    variant_run_id: str,
    created_ts: int,
) -> TrialRow:
    """Map a VariantEval onto a registry TrialRow.

    ``is_oos_spread`` comes from the OOS gate (0.0 when data_bounded — no gate).
    ``param_json`` is the variant's override dict; ``variant_id`` its stable id.
    """
    spread = res.oos_gate.is_oos_spread if res.oos_gate is not None else 0.0
    return TrialRow(
        cell_key=cell_key,
        variant_run_id=variant_run_id,
        exchange=spec.exchange,
        strategy=spec.strategy_id,
        ticker=pool,
        regime="mixed",
        variant_id=res.variant_id,
        param_json=json.dumps(overrides, sort_keys=True),
        is_pass=int(res.is_pass),
        oos_pass=int(res.oos_pass),
        n_trades=int(res.n_trades),
        pnl_r_mean=float(res.pnl_r_mean),
        lcb=float(res.lcb),
        ucb=float(res.ucb),
        dsr=float(res.dsr),
        trials_searched=int(res.trials_searched),
        is_oos_spread=float(spread),
        created_ts=created_ts,
    )


def _grid_for(spec: StrategySpec) -> tuple[list[BaseStrategy], int]:
    """Variants to evaluate + the ENTRY-search-breadth count for ``spec``.

    Returns ``(variants, entry_trials)``. A strategy WITH an entry-set knob gets
    its full PARAM_BOUNDS grid and ``entry_trials = len(grid)``. A strategy with
    NO entry-set knob (empty grid) still gets its DEFAULT config evaluated (1
    trial) but contributes ``entry_trials = 0`` search breadth (FIX 2 — reported
    honestly; ``trials_searched`` counts only real entry-varying configs)."""
    variants, _truncated = enumerate_grid(spec.base_cls)
    if variants:
        # The seam carries BaseStrategy instances (dynamic subclasses); the
        # StrategyVariant Protocol IS-A BaseStrategy at runtime.
        out = [cast(BaseStrategy, v) for v in variants]
        return out, len(out)
    default = cast(BaseStrategy, make_variant(spec.base_cls, {}))
    return [default], 0


def run_spike(
    *,
    specs: list[StrategySpec],
    registry_path: str | Path,
    sandbox_factory: Callable[[], sqlite3.Connection],
    run_id: str | None = None,
    created_ts: int | None = None,
) -> SpikeVerdict:
    """Run the OFFLINE KILL-spike over ``specs`` and return the verdict.

    Two-pass per strategy (FIX 6a): PASS 1 evaluates every variant on the
    held-out OOS slice through the EXISTING gate (provisional un-deflated DSR),
    collecting each evaluable variant's OOS Sharpe; PASS 2 re-deflates each
    variant's DSR + re-gates ``oos_pass`` against the REAL cross-variant Sharpe
    variance. The positive control (FIX 1) is computed at the run's
    representative OOS N (the MAX OOS n_trades across evaluable variants) and
    GATES the verdict. Persists one row per variant×cell to ``registry_path``
    (NEW db only). Pure w.r.t. injected db path + sandbox factory."""
    rid = run_id if run_id is not None else uuid.uuid4().hex
    ts = int(created_ts if created_ts is not None else time.time())
    conn = open_registry(registry_path)
    n_variants = 0
    data_bounded = 0
    is_pass = 0
    oos_pass = 0
    trials_searched = 0
    evaluable_nondegenerate = 0
    max_oos_n = 0
    per_strategy: list[tuple[str, int, int, int]] = []
    try:
        for spec in specs:
            variants, entry_trials = _grid_for(spec)
            trials_searched += entry_trials
            pool = _pool_tag(spec.bars_by_instrument)
            ck = _cell_key(
                exchange=spec.exchange, strategy=spec.strategy_id,
                pool=pool, regime="mixed",
            )
            # PASS 1: evaluate (provisional un-deflated DSR, sr_variance=0.0).
            pass1: list[tuple[BaseStrategy, VariantEval]] = []
            for variant in variants:
                res = evaluate_variant(
                    variant=variant,
                    bars_by_instrument=spec.bars_by_instrument,
                    config=_spec_config(spec),
                    n_splits=spec.n_splits,
                    warmup_bars=spec.warmup_bars,
                    max_ttl=spec.max_ttl,
                    total_variants_searched=entry_trials,
                    sandbox_factory=sandbox_factory,
                    cell_key=ck,
                )
                pass1.append((variant, res))
            # REAL cross-variant Sharpe variance from this cell's enumerated grid
            # (FIX 6a): non-data-bounded variants with >= 2 OOS trades. A single
            # variant (or none) -> Var 0.0 -> DSR un-deflated (no search to
            # correct), rather than fabricated.
            oos_sharpes = [
                r.oos_sharpe for _v, r in pass1
                if not r.data_bounded and r.n_trades >= 2
            ]
            sr_var = cross_variant_sr_variance(oos_sharpes)
            # PASS 2: re-deflate + re-gate against the real variance, persist.
            s_n = s_is = s_oos = 0
            for variant, prov in pass1:
                res = prov
                if sr_var > 0.0 and entry_trials > 1 and not prov.data_bounded:
                    res = evaluate_variant(
                        variant=variant,
                        bars_by_instrument=spec.bars_by_instrument,
                        config=_spec_config(spec),
                        n_splits=spec.n_splits,
                        warmup_bars=spec.warmup_bars,
                        max_ttl=spec.max_ttl,
                        total_variants_searched=entry_trials,
                        sandbox_factory=sandbox_factory,
                        cell_key=ck,
                        sr_variance=sr_var,
                    )
                overrides = dict(getattr(variant, "param_overrides", {}))
                row = _trial_row(
                    res=res, overrides=overrides,
                    spec=spec, cell_key=ck, pool=pool,
                    variant_run_id=uuid.uuid4().hex,
                    created_ts=ts,
                )
                record_trial(conn, row)
                n_variants += 1
                s_n += 1
                if res.data_bounded:
                    data_bounded += 1
                else:
                    if res.n_trades >= 2:
                        evaluable_nondegenerate += 1
                    max_oos_n = max(max_oos_n, res.n_trades)
                if res.is_pass:
                    is_pass += 1
                    s_is += 1
                if res.oos_pass:
                    oos_pass += 1
                    s_oos += 1
            per_strategy.append((spec.strategy_id, s_n, s_is, s_oos))
    finally:
        conn.close()

    # KEYSTONE positive control (FIX 1): can the gate certify a STRONG edge at the
    # run's most generous realised held-out N? If not, the verdict is starved.
    pc_n = int(max_oos_n)
    pc_passed = gate_can_discriminate(n_trades=pc_n) if pc_n >= 2 else False
    mpn = min_passable_n()

    label, rationale = _classify(
        n_variants=n_variants, data_bounded_count=data_bounded,
        is_pass_count=is_pass, oos_pass_count=oos_pass,
        positive_control_passed=pc_passed, positive_control_n=pc_n,
        min_passable_n=mpn, evaluable_nondegenerate=evaluable_nondegenerate,
    )
    zero_threshold = 1.0 / trials_searched if trials_searched else 1.0
    return SpikeVerdict(
        label=label,
        run_id=rid,
        n_variants=n_variants,
        trials_searched=trials_searched,
        data_bounded_count=data_bounded,
        is_pass_count=is_pass,
        oos_pass_count=oos_pass,
        is_pass_rate=(is_pass / n_variants if n_variants else 0.0),
        oos_pass_rate=(oos_pass / n_variants if n_variants else 0.0),
        zero_threshold=zero_threshold,
        positive_control_passed=pc_passed,
        positive_control_n=pc_n,
        min_passable_n=mpn,
        evaluable_count=evaluable_nondegenerate,
        per_strategy=tuple(per_strategy),
        rationale=rationale,
    )


def _spec_config(spec: StrategySpec, *, live_db_path: str = "data/polaris_live.sqlite") -> ReplayConfig:
    """A ReplayConfig for a spec. ``live_db_path`` is unused by ``run_with_bars``
    (bars are injected) but kept for reproducibility / the read-only default."""
    interval = "1H"
    for _inst, bars in spec.bars_by_instrument.items():
        if bars:
            interval = bars[0].bar_interval
            break
    return ReplayConfig(
        instrument_ids=tuple(spec.bars_by_instrument.keys()),
        bar_interval=interval,
        starting_equity=10_000.0,
        live_db_path=live_db_path,
    )


# ---------------------------------------------------------------------------
# CLI: build specs from PARAM_BOUNDS + design instruments, load bars read-only.
# ---------------------------------------------------------------------------


def _max_ttl_for(strategy_id: str) -> int:
    """Embargo-padding bars: the strategy's expected holding horizon (a proxy for
    the longest a position lives before the precise-exit FSM closes it). ``ttl_bars``
    is no longer a search knob (inert-in-replay), so the embargo uses the design
    holding horizon instead of the removed grid key — a single trade never
    straddles the IS/OOS purge."""
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return 4
    return max(4, int(cls.metadata.expected_holding_bars))


def build_specs(
    live_db_path: str, *, n_splits: int = 1, strategies: list[str] | None = None
) -> list[StrategySpec]:
    """Load read-only bars from the live DB and build one spec per in-scope
    strategy (design-recommended instrument pools). The live DB is opened with
    ``mode=ro`` (write-protected). Strategies with zero bars still yield a spec
    (it reports DATA_BOUNDED honestly)."""
    ids = strategies if strategies is not None else list(DEFAULT_INSTRUMENTS)
    ro = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
    specs: list[StrategySpec] = []
    try:
        for sid in ids:
            if sid not in STRATEGY_REGISTRY or sid not in DEFAULT_INSTRUMENTS:
                continue
            insts, interval = DEFAULT_INSTRUMENTS[sid]
            bars = load_bars(
                ro, instrument_ids=insts, bar_interval=interval,
                start_ts=None, end_ts=None,
            )
            base_cls = STRATEGY_REGISTRY[sid]
            specs.append(
                StrategySpec(
                    strategy_id=sid,
                    base_cls=base_cls,
                    exchange=base_cls.metadata.venue,
                    bars_by_instrument=bars,
                    n_splits=n_splits,
                    warmup_bars=int(base_cls.metadata.warmup_bars),
                    max_ttl=_max_ttl_for(sid),
                )
            )
    finally:
        ro.close()
    return specs


def _sandbox_factory() -> sqlite3.Connection:
    """A FRESH in-memory sandbox conn with the storage schema (engine writes its
    cell-stats here only — never touches the live DB)."""
    from polaris.storage.schema import ALL_DDL

    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="run_p0a_spike",
        description="OFFLINE P0a KILL-spike: bounded config-variant search + held-out gate verdict",
    )
    p.add_argument(
        "--live-db", default="data/polaris_live.sqlite",
        help="live DB (READ-ONLY bar source; never written)",
    )
    p.add_argument(
        "--registry", default="data/p0a_registry.sqlite",
        help="NEW p0a registry DB (the ONLY write target)",
    )
    p.add_argument("--n-splits", type=int, default=1, help="walk-forward splits (1 = single OOS block)")
    p.add_argument(
        "--strategies", nargs="*", default=None,
        help="subset of strategy_ids (default = all in-scope)",
    )
    args = p.parse_args(sys.argv[1:] if argv is None else argv)

    specs = build_specs(args.live_db, n_splits=int(args.n_splits), strategies=args.strategies)
    verdict = run_spike(
        specs=specs,
        registry_path=args.registry,
        sandbox_factory=_sandbox_factory,
    )
    out = {
        "verdict": verdict.label,
        "rationale": verdict.rationale,
        "run_id": verdict.run_id,
        "n_variants": verdict.n_variants,
        "trials_searched": verdict.trials_searched,
        "data_bounded_count": verdict.data_bounded_count,
        "is_pass_count": verdict.is_pass_count,
        "oos_pass_count": verdict.oos_pass_count,
        "is_pass_rate": verdict.is_pass_rate,
        "oos_pass_rate": verdict.oos_pass_rate,
        "zero_threshold_1_over_trials": verdict.zero_threshold,
        "positive_control_passed": verdict.positive_control_passed,
        "positive_control_n": verdict.positive_control_n,
        "min_passable_n": verdict.min_passable_n,
        "evaluable_nondegenerate_count": verdict.evaluable_count,
        "per_strategy": [
            {"strategy": s, "n": n, "is_pass": i, "oos_pass": o}
            for (s, n, i, o) in verdict.per_strategy
        ],
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
