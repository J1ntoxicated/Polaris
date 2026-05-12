"""Layer 4 — Cell Matrix unit + property tests.

Spec source: vault/30_components/layer-4-cell-matrix.md.
ADR-006 patches: EWMA 7d, warmup shrinkage, dynamic quartile gate ≥20.
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.cell_matrix import (
    CELL_BASELINE_N,
    CELL_DECAY_HALF_LIFE_SEC,
    CELL_MIN_LIVE_N,
    CELL_MIN_POOL_SIZE,
    CELL_SHRINKAGE_N,
    ROUTING_BOTTOM_MULT,
    ROUTING_MID_MULT,
    ROUTING_TOP_MULT,
    CellContext,
    CellKeyP0,
    TradeClose,
    classify_quartile,
    compute_avg_pnl_r,
    compute_cell_score,
    compute_routing_mult,
    decay_factor,
    fetch_cell_stat,
    fetch_parent2_score,
    fetch_parent3_score,
    load_eligible_scores,
    load_eligible_scores_decayed,
    resolve_effective_score,
    resolve_routing_for_cell,
    update_on_trade_close,
)

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _key(ticker: str = "BTC-USDT", regime: str = "bull_trend") -> CellKeyP0:
    return CellKeyP0(exchange="okx", strategy="volume_burst", ticker=ticker, regime=regime)


def _ctx() -> CellContext:
    return CellContext(group="spot_intraday", session="asia", direction="long", liquidity_tier="high")


def _trade(*, pnl: float, won: bool | None = None, ticker: str = "BTC-USDT", ts: int = NOW) -> TradeClose:
    if won is None:
        won = pnl > 0
    return TradeClose(key=_key(ticker), context=_ctx(), pnl_r=pnl, won=won, closed_ts=ts)


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


def test_4_dim_schema_create(memdb: sqlite3.Connection) -> None:
    """All Layer 4 tables must exist after init_db (memdb fixture runs ALL_DDL)."""
    tables = {
        r[0]
        for r in memdb.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "cell_matrix_p0" in tables
    assert "cell_matrix_parent3" in tables
    assert "cell_matrix_parent2" in tables
    assert "cell_matrix_shadow_context" in tables


# ---------------------------------------------------------------------------
# Score formula (T11)
# ---------------------------------------------------------------------------


def test_score_formula_t11() -> None:
    """``score = avg_pnl_r × √n_eff / 70``."""
    score = compute_cell_score(avg_pnl_r=1.0, n_eff=70.0)
    assert score == pytest.approx(math.sqrt(70.0) / 70.0)
    score2 = compute_cell_score(avg_pnl_r=0.5, n_eff=49.0)
    assert score2 == pytest.approx(0.5 * 7.0 / 70.0)


def test_score_formula_zero_n() -> None:
    assert compute_cell_score(avg_pnl_r=1.0, n_eff=0.0) == 0.0
    assert compute_cell_score(avg_pnl_r=-1.0, n_eff=0.0) == 0.0


def test_score_formula_negative_preserved() -> None:
    """Loser cells keep negative score (no floor 0)."""
    assert compute_cell_score(avg_pnl_r=-1.0, n_eff=100.0) < 0.0


def test_compute_avg_pnl_r_safe() -> None:
    assert compute_avg_pnl_r(pnl_r_sum_eff=10.0, n_eff=5.0) == 2.0
    assert compute_avg_pnl_r(pnl_r_sum_eff=10.0, n_eff=0.0) == 0.0


# ---------------------------------------------------------------------------
# EWMA decay (7d half-life)
# ---------------------------------------------------------------------------


def test_ewma_score_decay_7d_half_life() -> None:
    """After exactly 7d, factor = 0.5."""
    factor = decay_factor(elapsed_sec=7 * 86400)
    assert factor == pytest.approx(0.5, rel=1e-9)


def test_ewma_score_decay_zero_elapsed() -> None:
    assert decay_factor(elapsed_sec=0.0) == 1.0


def test_ewma_score_decay_negative_clamps_to_no_decay() -> None:
    # Clock skew: negative elapsed should never inflate state.
    assert decay_factor(elapsed_sec=-100.0) == 1.0


def test_ewma_score_decay_invalid_half_life_raises() -> None:
    with pytest.raises(ValueError):
        decay_factor(elapsed_sec=60.0, half_life_sec=0)


# ---------------------------------------------------------------------------
# Warmup shrinkage (5 ≤ n < 20 blend)
# ---------------------------------------------------------------------------


def test_warmup_shrinkage_below_min_returns_zero() -> None:
    out = resolve_effective_score(
        cell_score=1.0,
        cell_n_eff=3.0,
        parent3_score=0.5,
        parent2_score=None,
    )
    assert out == 0.0


def test_warmup_shrinkage_blend_midrange() -> None:
    """At n=10 the blend is 0.5*cell + 0.5*parent (matches ADR-006 example)."""
    out = resolve_effective_score(
        cell_score=2.0,
        cell_n_eff=10.0,
        parent3_score=0.0,
        parent2_score=None,
    )
    assert out == pytest.approx(1.0)


def test_warmup_shrinkage_full_at_20() -> None:
    out = resolve_effective_score(
        cell_score=2.0,
        cell_n_eff=20.0,
        parent3_score=0.0,
        parent2_score=None,
    )
    assert out == 2.0


def test_warmup_shrinkage_parent_fallback_chain() -> None:
    # parent3 missing → use parent2.
    out = resolve_effective_score(
        cell_score=1.0,
        cell_n_eff=10.0,
        parent3_score=None,
        parent2_score=2.0,
    )
    assert out == pytest.approx(0.5 * 1.0 + 0.5 * 2.0)


def test_warmup_shrinkage_no_parent_zero() -> None:
    out = resolve_effective_score(
        cell_score=1.0,
        cell_n_eff=10.0,
        parent3_score=None,
        parent2_score=None,
    )
    assert out == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Dynamic quartile gate
# ---------------------------------------------------------------------------


def test_dynamic_quartile_gate_below_min_pool_neutral() -> None:
    """eligible pool < 20 → every score routes ×1.0 (cold)."""
    pool = [0.1 * i for i in range(10)]
    assert classify_quartile(0.5, eligible_scores=pool) == "cold"


def test_dynamic_quartile_gate_at_min_pool_active() -> None:
    pool = [0.01 * i for i in range(20)]
    assert classify_quartile(0.0, eligible_scores=pool) == "bottom"
    assert classify_quartile(0.19, eligible_scores=pool) == "top"
    assert classify_quartile(0.10, eligible_scores=pool) == "mid"


def test_routing_top_15_bottom_05_mid_10() -> None:
    pool = [0.01 * i for i in range(20)]
    top = compute_routing_mult(n_eff=50.0, effective_score=0.20, eligible_scores=pool)
    bot = compute_routing_mult(n_eff=50.0, effective_score=-0.5, eligible_scores=pool)
    mid = compute_routing_mult(n_eff=50.0, effective_score=0.10, eligible_scores=pool)
    assert top == ROUTING_TOP_MULT == 1.5
    assert bot == ROUTING_BOTTOM_MULT == 0.5
    assert mid == ROUTING_MID_MULT == 1.0


def test_routing_cold_n_below_min_live() -> None:
    pool = [0.01 * i for i in range(50)]
    out = compute_routing_mult(n_eff=2.0, effective_score=10.0, eligible_scores=pool)
    assert out == 1.0


def test_routing_pool_below_gate_neutral() -> None:
    out = compute_routing_mult(n_eff=50.0, effective_score=10.0, eligible_scores=[1.0] * 5)
    assert out == 1.0


# ---------------------------------------------------------------------------
# Per-trade update (atomic SQLite transaction)
# ---------------------------------------------------------------------------


def test_update_on_trade_close_writes_p0_and_parents(memdb: sqlite3.Connection) -> None:
    update_on_trade_close(memdb, _trade(pnl=1.0, ts=NOW))
    cell = fetch_cell_stat(memdb, _key())
    assert cell is not None
    assert cell.n_eff == pytest.approx(1.0)
    assert cell.pnl_r_sum_eff == pytest.approx(1.0)
    assert cell.avg_pnl_r == pytest.approx(1.0)
    assert cell.last_closed_ts == NOW

    p3 = fetch_parent3_score(memdb, _key(), now_ts=NOW)
    p2 = fetch_parent2_score(memdb, _key(), now_ts=NOW)
    assert p3 is not None and p2 is not None


def test_update_on_trade_close_accumulates_with_ewma_decay(memdb: sqlite3.Connection) -> None:
    # First trade @ NOW, second trade @ NOW + 7d → first contribution decayed by 0.5.
    update_on_trade_close(memdb, _trade(pnl=2.0, ts=NOW))
    update_on_trade_close(memdb, _trade(pnl=2.0, ts=NOW + 7 * 86400))
    cell = fetch_cell_stat(memdb, _key())
    assert cell is not None
    # n_eff = 1*0.5 + 1 = 1.5; pnl_sum = 2*0.5 + 2 = 3.0; avg = 2.0.
    assert cell.n_eff == pytest.approx(1.5, rel=1e-6)
    assert cell.pnl_r_sum_eff == pytest.approx(3.0, rel=1e-6)
    assert cell.avg_pnl_r == pytest.approx(2.0, rel=1e-6)


def test_update_on_trade_close_atomic_rollback(memdb: sqlite3.Connection) -> None:
    """Forcing a NaN pnl_r must abort the whole transaction (no partial write)."""
    with pytest.raises(ValueError):
        update_on_trade_close(memdb, _trade(pnl=float("nan"), ts=NOW))
    assert fetch_cell_stat(memdb, _key()) is None


def test_eligible_scores_pool_filters_below_min_live_n(memdb: sqlite3.Connection) -> None:
    # Insert one mature cell (>=5 trades) and one cold cell (1 trade).
    for i in range(6):
        update_on_trade_close(memdb, _trade(pnl=1.0, ticker="BTC-USDT", ts=NOW + i))
    update_on_trade_close(memdb, _trade(pnl=1.0, ticker="ETH-USDT", ts=NOW))
    pool = load_eligible_scores(memdb)
    # Only BTC-USDT (n_eff ≈ 6) should be in the eligible pool.
    assert len(pool) == 1


def test_routing_distribution_top_bottom_mid(memdb: sqlite3.Connection) -> None:
    """Build 25 cells with a wide pnl spread, then check quartile distribution."""
    for i in range(25):
        ticker = f"COIN{i:02d}-USDT"
        # Each cell receives 25 trades to clear shrinkage (n>=20).
        for j in range(25):
            tr = TradeClose(
                key=CellKeyP0("okx", "volume_burst", ticker, "bull_trend"),
                context=_ctx(),
                pnl_r=(i - 12) * 0.1,  # range -1.2 .. +1.2
                won=(i - 12) * 0.1 > 0,
                closed_ts=NOW + j,
            )
            update_on_trade_close(memdb, tr)
    pool = load_eligible_scores(memdb)
    assert len(pool) >= CELL_MIN_POOL_SIZE
    multipliers: list[float] = []
    for i in range(25):
        ticker = f"COIN{i:02d}-USDT"
        cell = fetch_cell_stat(memdb, CellKeyP0("okx", "volume_burst", ticker, "bull_trend"))
        assert cell is not None
        mult = compute_routing_mult(
            n_eff=cell.n_eff,
            effective_score=cell.score,
            eligible_scores=pool,
        )
        multipliers.append(mult)
    assert multipliers.count(ROUTING_TOP_MULT) >= 1
    assert multipliers.count(ROUTING_BOTTOM_MULT) >= 1
    assert multipliers.count(ROUTING_MID_MULT) >= 1


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=120, deadline=None)
@given(
    n_eff=st.floats(min_value=1e-3, max_value=1_000.0, allow_nan=False, allow_infinity=False),
    avg=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
)
def test_property_compute_score_finite_and_sign_consistent(n_eff: float, avg: float) -> None:
    score = compute_cell_score(avg_pnl_r=avg, n_eff=n_eff)
    assert math.isfinite(score)
    if avg == 0.0:
        assert score == 0.0
    # sign(score) == sign(avg) — skip subnormal underflow band where the
    # multiplication can flush a non-zero avg to 0.0.
    elif abs(avg) > 1e-12 and score != 0.0:
        assert (score > 0.0) == (avg > 0.0)


def test_compute_score_zero_n_returns_zero() -> None:
    score = compute_cell_score(avg_pnl_r=1.0, n_eff=0.0)
    assert score == 0.0


@settings(max_examples=80, deadline=None)
@given(
    pool_size=st.integers(min_value=0, max_value=50),
    score=st.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    n_eff=st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False),
)
def test_property_routing_mult_bounded(pool_size: int, score: float, n_eff: float) -> None:
    pool = [0.01 * i for i in range(pool_size)]
    out = compute_routing_mult(
        n_eff=n_eff,
        effective_score=score,
        eligible_scores=pool,
    )
    assert out in {ROUTING_TOP_MULT, ROUTING_BOTTOM_MULT, ROUTING_MID_MULT}
    if n_eff < CELL_MIN_LIVE_N or pool_size < CELL_MIN_POOL_SIZE:
        assert out == ROUTING_MID_MULT


def test_constants_match_spec() -> None:
    """ADR-006 + L4 spec constants pinned (regression guard)."""
    assert CELL_BASELINE_N == 70.0
    assert CELL_DECAY_HALF_LIFE_SEC == 7 * 86400
    assert CELL_MIN_LIVE_N == 5.0
    assert CELL_MIN_POOL_SIZE == 20
    assert CELL_SHRINKAGE_N == 20.0
    assert ROUTING_TOP_MULT == 1.5
    assert ROUTING_BOTTOM_MULT == 0.5
    assert ROUTING_MID_MULT == 1.0


# ---------------------------------------------------------------------------
# Production routing path (codex round 2 P0 fix — warmup wiring + read decay)
# ---------------------------------------------------------------------------


def test_resolve_routing_for_cell_below_min_live_neutral(memdb: sqlite3.Connection) -> None:
    """Cells with n_eff < 5 must route ×1.0 (cold)."""
    update_on_trade_close(memdb, _trade(pnl=1.0, ticker="SOLO-USDT", ts=NOW))
    out = resolve_routing_for_cell(
        memdb, _key("SOLO-USDT"), now_ts=NOW + 1
    )
    assert out == ROUTING_MID_MULT


def test_resolve_routing_for_cell_warmup_blends_with_parent(memdb: sqlite3.Connection) -> None:
    """5 ≤ n_eff < 20 must blend toward parent3 — raw cell.score must NOT be used.

    End-to-end check: build an eligible pool of 25 mature winning cells, then
    add a small warm cell whose raw score would land in the top quartile but
    whose parent3 (driven by 25 *separate* losing cells) is negative. With the
    blend correctly wired, the warm cell must NOT route ×1.5 (parent drags it
    down). Without the blend, the raw score would amplify the warm cell.
    """
    # 25 winners on tickers WIN00..WIN24 — these dominate the eligible pool
    # *and* feed parent3 (exchange × strategy × regime) positively.
    for i in range(25):
        ticker = f"WIN{i:02d}-USDT"
        for j in range(25):
            update_on_trade_close(
                memdb, _trade(pnl=(i - 12) * 0.1, ticker=ticker, ts=NOW + j)
            )
    # Warm cell on a *different* parent3 triple (regime=chop) so its parent3
    # is shaped by a separate set of losers. 200 strong losers vs 10 modest
    # warm winners ⇒ parent3 stays clearly negative.
    for j in range(200):
        update_on_trade_close(
            memdb,
            TradeClose(
                key=CellKeyP0("okx", "volume_burst", f"LOSE{j:02d}-USDT", "chop"),
                context=_ctx(),
                pnl_r=-2.0,
                won=False,
                closed_ts=NOW + j,
            ),
        )
    warm_key = CellKeyP0("okx", "volume_burst", "WARM-USDT", "chop")
    for j in range(10):
        update_on_trade_close(
            memdb,
            TradeClose(
                key=warm_key, context=_ctx(),
                pnl_r=1.0, won=True, closed_ts=NOW + 1000 + j,
            ),
        )
    cell = fetch_cell_stat(memdb, warm_key)
    assert cell is not None and 5 <= cell.n_eff < 20
    now_eval = NOW + 1100
    parent3 = fetch_parent3_score(memdb, warm_key, now_ts=now_eval)
    assert parent3 is not None and parent3 < 0.0

    # The end-to-end SSOT MUST blend with parent3 → warm cell is dragged toward
    # negative, so it should NOT route top-quartile (×1.5).
    out = resolve_routing_for_cell(memdb, warm_key, now_ts=now_eval)
    assert out != ROUTING_TOP_MULT, (
        f"warmup blend disabled: warm cell still routed top despite "
        f"negative parent3={parent3:.3f}, raw cell.score={cell.score:.3f}, "
        f"blended={resolve_effective_score(cell_score=cell.score, cell_n_eff=cell.n_eff, parent3_score=parent3, parent2_score=None):.3f}"
    )


def test_resolve_routing_for_cell_uses_decayed_pool(memdb: sqlite3.Connection) -> None:
    """A stale winner must drop out of the eligible pool after a 7-day idle."""
    # Build a populated pool so the gate (≥20) activates.
    for i in range(25):
        for j in range(25):
            update_on_trade_close(
                memdb,
                TradeClose(
                    key=CellKeyP0("okx", "volume_burst", f"BG{i:02d}-USDT", "bull_trend"),
                    context=_ctx(),
                    pnl_r=(i - 12) * 0.1,
                    won=(i - 12) * 0.1 > 0,
                    closed_ts=NOW + j,
                ),
            )
    pool_fresh = load_eligible_scores_decayed(memdb, now_ts=NOW + 100)
    assert len(pool_fresh) >= CELL_MIN_POOL_SIZE
    # Far-future read: every cell's n_eff decays exponentially. After 14 d
    # (two half-lives) the smallest n_eff cells fall below the 5.0 floor.
    pool_stale = load_eligible_scores_decayed(memdb, now_ts=NOW + 14 * 86400)
    # Pool MUST shrink (or at least never grow) under read-time decay.
    assert len(pool_stale) <= len(pool_fresh)


def test_parent3_score_decay_uses_sqrt_factor(memdb: sqlite3.Connection) -> None:
    """T11 score ∝ √n_eff ⇒ stored score must decay by √factor, not factor."""
    for j in range(40):
        update_on_trade_close(
            memdb, _trade(pnl=1.0, ticker="DEC-USDT", ts=NOW + j)
        )
    p_now = fetch_parent3_score(memdb, _key("DEC-USDT"), now_ts=None)
    p_7d = fetch_parent3_score(memdb, _key("DEC-USDT"), now_ts=NOW + 39 + 7 * 86400)
    assert p_now is not None and p_7d is not None
    # 7d half-life ⇒ factor = 0.5 ⇒ √factor ≈ 0.7071
    assert p_7d == pytest.approx(p_now * math.sqrt(0.5), rel=1e-3)


def test_resolve_routing_for_cell_top_quartile_amplifies(memdb: sqlite3.Connection) -> None:
    """End-to-end production check: top-quartile cell routes ×1.5 via the SSOT path."""
    for i in range(25):
        for j in range(25):
            update_on_trade_close(
                memdb,
                TradeClose(
                    key=CellKeyP0("okx", "volume_burst", f"TQ{i:02d}-USDT", "bull_trend"),
                    context=_ctx(),
                    pnl_r=(i - 12) * 0.1,
                    won=(i - 12) * 0.1 > 0,
                    closed_ts=NOW + j,
                ),
            )
    # The clearly-best cell (i=24) should pull ×1.5.
    out = resolve_routing_for_cell(
        memdb, CellKeyP0("okx", "volume_burst", "TQ24-USDT", "bull_trend"),
        now_ts=NOW + 100,
    )
    assert out == ROUTING_TOP_MULT
    # The clearly-worst cell (i=0) should pull ×0.5.
    out_low = resolve_routing_for_cell(
        memdb, CellKeyP0("okx", "volume_burst", "TQ00-USDT", "bull_trend"),
        now_ts=NOW + 100,
    )
    assert out_low == ROUTING_BOTTOM_MULT
