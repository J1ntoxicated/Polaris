"""G2 ticker-tailored strategy assignment — regime-first POOL prioritization.

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack collapse permanently blocked.

Spec source: /debate convergence (GPT a68dfca2 + Gemini a55e65bd) — "regime-first
hybrid": a base strategy set → per-strategy regime fitness (pre-mapped) → the
ticker's LIVE regime → a dynamic fit POOL → within-pool fine-tune. The audited G2
matched a strategy to a ticker by venue/asset-class ONLY (static); this couples
the ticker's live regime into the EMIT-time RANKING so a strategy whose edge
suits THIS ticker's current regime sorts ahead of a mis-fit one in the per-tick
batch — a structural head-start into the concurrent gate fan-out (NOT a
guaranteed risk-budget reservation order; the gate awaits interleave).

POLICY (load-bearing invariants under test):
- flow_not_block: a mis-regime signal is RANKED DOWN, never dropped / skipped /
  size-cut. It still emits, still flows through the pipeline — just sorted later
  in the supervised batch (PDT precedent).
- 9-stack: the regime fit enters as ``rank_penalty`` (batch ORDER only), NEVER as
  a T4 sizing multiplier. ``focus/assignment`` is NOT a sizing input — notional
  is byte-identical for a given signal regardless of its rank.
- per-ticker tailored: the penalty depends on (strategy, THIS ticker's live
  regime) — two tickers of the same strategy on different regimes rank
  differently. No uniform per-strategy rule.
- cold-start: an unknown/crisis regime → NEUTRAL (never the worst tier).
"""

from __future__ import annotations

from polaris.core.cell_matrix.score import (
    REGIME_RANK_PENALTY_DAMPEN,
    regime_rank_penalty,
)
from polaris.core.isolation.worker import PipelineTaskSpec
from polaris.scripts._production_tick import order_specs_by_rank


def _spec(strategy_id: str, penalty: float) -> PipelineTaskSpec:
    async def _noop() -> None:  # pragma: no cover — never awaited in these tests
        return None

    return PipelineTaskSpec(
        strategy_id=strategy_id, coro_factory=_noop, rank_penalty=penalty
    )


# ---------------------------------------------------------------------------
# A — regime-first POOL ordering on the SAME ticker (the core behaviour).
# ---------------------------------------------------------------------------


def test_trend_regime_ticker_ranks_trend_strategy_ahead_of_counter_trend() -> None:
    """On a ticker whose live regime is a trend, the trend strategy is the
    best-fit POOL member → it ranks AHEAD of a counter-trend strategy on the
    SAME ticker. Both still run (flow_not_block) — only ORDER changes."""
    regime = "bull_trend"
    trend_pen = regime_rank_penalty(strategy="xau_indices_trend", regime=regime)
    counter_pen = regime_rank_penalty(strategy="rsi_bb_pullback", regime=regime)

    specs = [
        _spec("rsi_bb_pullback", counter_pen),  # mis-fit, intentionally first
        _spec("xau_indices_trend", trend_pen),  # best-fit
    ]
    ordered = order_specs_by_rank(specs)

    assert [s.strategy_id for s in ordered] == ["xau_indices_trend", "rsi_bb_pullback"]
    # flow_not_block: nothing dropped — the mis-fit strategy is still in the batch.
    assert len(ordered) == 2


def test_chop_regime_ticker_ranks_counter_trend_ahead_of_trend() -> None:
    """The SAME pair on a CHOP ticker flips: the counter-trend (mean-reversion)
    strategy is now the best fit → it ranks ahead. Proves per-ticker tailoring:
    the order depends on the ticker's live regime, not a fixed per-strategy rule."""
    regime = "chop"
    trend_pen = regime_rank_penalty(strategy="xau_indices_trend", regime=regime)
    counter_pen = regime_rank_penalty(strategy="rsi_bb_pullback", regime=regime)

    specs = [
        _spec("xau_indices_trend", trend_pen),
        _spec("rsi_bb_pullback", counter_pen),
    ]
    ordered = order_specs_by_rank(specs)

    assert [s.strategy_id for s in ordered] == ["rsi_bb_pullback", "xau_indices_trend"]
    assert len(ordered) == 2


def test_same_strategy_two_tickers_different_regime_ranks_differently() -> None:
    """The per-ticker-tailored claim: one strategy (xau_indices_trend) on TWO tickers — one
    in a trend regime, one in chop — gets DIFFERENT priority. The trend-ticker
    instance ranks ahead of the chop-ticker instance (better fit = first claim)."""
    trend_pen = regime_rank_penalty(strategy="xau_indices_trend", regime="bull_trend")
    chop_pen = regime_rank_penalty(strategy="xau_indices_trend", regime="chop")
    assert trend_pen < chop_pen  # tailored: same strategy, different ticker regime

    specs = [
        _spec("xau_indices_trend@CHOPTICKER", chop_pen),
        _spec("xau_indices_trend@TRENDTICKER", trend_pen),
    ]
    ordered = order_specs_by_rank(specs)
    assert [s.strategy_id for s in ordered] == [
        "xau_indices_trend@TRENDTICKER",
        "xau_indices_trend@CHOPTICKER",
    ]


# ---------------------------------------------------------------------------
# B — PDT integrity dominates regime fit (additive composition, correct tiers).
# ---------------------------------------------------------------------------


def test_pdt_flagged_entry_sinks_below_any_regime_misfit() -> None:
    """A PDT-flagged equity entry (integrity penalty >= 1.0) must sink BELOW a
    mere regime mis-fit (penalty < 1.0) even when the PDT entry is otherwise a
    perfect regime fit. Integrity > regime-fit secondary sort (additive scale)."""
    pdt_plus_perfect_fit = 1.0 + regime_rank_penalty(
        strategy="xau_indices_trend", regime="bull_trend"
    )  # 1.0 + 0.0
    regime_misfit_no_pdt = 0.0 + REGIME_RANK_PENALTY_DAMPEN  # < 1.0

    specs = [
        _spec("pdt_perfect_fit", pdt_plus_perfect_fit),
        _spec("regime_misfit", regime_misfit_no_pdt),
    ]
    ordered = order_specs_by_rank(specs)
    assert [s.strategy_id for s in ordered] == ["regime_misfit", "pdt_perfect_fit"]


# ---------------------------------------------------------------------------
# C — source-level lint: the regime rank-down is actually WIRED at G2 emit.
# ---------------------------------------------------------------------------


def test_regime_rank_penalty_is_wired_into_run_tick() -> None:
    """The emit loop must compose ``regime_rank_penalty`` into the spec's
    ``rank_penalty`` — otherwise the per-ticker POOL prioritization is a no-op."""
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    assert "regime_rank_penalty" in src, (
        "G2: ticker-regime rank-down must be wired into the _run_tick emit loop."
    )


def test_regime_penalty_composes_additively_with_pdt_in_run_tick() -> None:
    """Source lint: the wired ``rank_penalty`` must be ``pdt + regime`` (additive
    priority composition) — never overwrite the PDT penalty (integrity must
    survive) and never feed a sizing/notional call."""
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    assert "rank_penalty = pdt_penalty + regime_penalty" in src, (
        "G2: regime penalty must ADD to the PDT penalty (integrity preserved), "
        "not replace it."
    )


# ---------------------------------------------------------------------------
# D — supervised-batch TEETH (honest): the lower-penalty spec is CREATED FIRST.
#     This documents the ACTUAL mechanism — a creation-order head-start into the
#     concurrent gate fan-out — NOT a guaranteed budget-reservation order (the
#     gate awaits interleave; this test deliberately does NOT claim otherwise).
# ---------------------------------------------------------------------------


def test_lower_penalty_spec_coroutine_is_started_first_and_none_dropped() -> None:
    """Through the REAL ``supervise_pipeline_tasks`` TaskGroup, the better-fit
    (lower-penalty) spec's coroutine begins executing BEFORE the mis-fit one,
    and BOTH run to completion (flow_not_block — nothing dropped)."""
    import asyncio
    import sqlite3

    from polaris.core.isolation.worker import supervise_pipeline_tasks

    start_order: list[str] = []

    def _spec_recording(strategy_id: str, penalty: float) -> PipelineTaskSpec:
        async def _coro() -> None:
            start_order.append(strategy_id)  # record creation/first-run order
            await asyncio.sleep(0)  # yield so both interleave like the real path

        return PipelineTaskSpec(
            strategy_id=strategy_id, coro_factory=_coro, rank_penalty=penalty
        )

    regime = "bull_trend"
    specs = [
        _spec_recording("xau_indices_trend", regime_rank_penalty(strategy="xau_indices_trend", regime=regime)),
        _spec_recording(
            "rsi_bb_pullback",
            regime_rank_penalty(strategy="rsi_bb_pullback", regime=regime),
        ),
    ]
    # The emit loop sorts before supervising (order_specs_by_rank → supervise).
    ordered = order_specs_by_rank(specs)
    conn = sqlite3.connect(":memory:")
    results = asyncio.run(
        supervise_pipeline_tasks(ordered, conn=conn, now_ts=0)
    )
    conn.close()

    # Both ran (flow_not_block — the mis-fit was not dropped/skipped).
    assert {r["strategy_id"] for r in results} == {"xau_indices_trend", "rsi_bb_pullback"}
    assert all(r["exception"] is None for r in results)
    # The best-fit (xau_indices_trend in a trend regime) was CREATED/STARTED first.
    assert start_order[0] == "xau_indices_trend"
