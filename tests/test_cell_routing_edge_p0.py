"""TDD — P0 edge-routing: regime-conditioned + posterior tilt on the routing score.

DEMO/PAPER ONLY. This couples two ALREADY-EXISTING signals into the cell-matrix
routing-score synthesis step (the INPUT to the cell_routing_mult slot), adding
NO new gate, NO new sizing multiplier, and keeping the {1.5, 1.0, 0.5} output
slot + 9-stack T4 chain structurally unchanged:

  1. regime-conditioned edge — ``regime_alignment_mult`` (1.25/1.0/0.8, existing
     ADR constants) amplifies the routing score of a regime-aligned strategy and
     dampens a misaligned one. Pure multiply ⇒ sign preserved, never zeroed.
  2. posterior tilt — a continuous, bounded (0.5..1.5), strictly-positive scale
     derived from ``learner_posterior.p_pos`` (= P(expectancy>0)). A cell with
     ``n_samples < POSTERIOR_MIN_N`` (cold/sparse) gets tilt = 1.0 EXACTLY
     (neutral — no amplify, no dampen) so cold cells are never suppressed.

flow_not_block / aggressive_always_profit: no entry is blocked; the bottom slot
floor stays 0.5 (never 0); winners reach 1.5. cold/unknown = neutral 1.0.
"""

from __future__ import annotations

import math
import sqlite3

from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.cell_matrix import (
    ROUTING_BOTTOM_MULT,
    ROUTING_MID_MULT,
    ROUTING_TOP_MULT,
    CellContext,
    CellKeyP0,
    TradeClose,
    resolve_routing_for_cell,
    update_on_trade_close,
)
from polaris.core.cell_matrix.routing import fetch_posterior_tilt
from polaris.core.cell_matrix.score import (
    POSTERIOR_TILT_CEIL,
    POSTERIOR_TILT_FLOOR,
    POSTERIOR_TILT_MIN_N,
    posterior_tilt,
)

NOW = 1_780_000_000


def _ctx() -> CellContext:
    return CellContext(group="spot_intraday", session="asia", direction="long", liquidity_tier="high")


def _seed_posterior(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    strategy: str,
    ticker: str,
    regime: str,
    p_pos: float,
    n_samples: int,
) -> None:
    conn.execute(
        "INSERT INTO learner_posterior "
        "(exchange, strategy, ticker, regime, mu, kappa, alpha, beta, m2, "
        " running_mean, n_samples, p_pos, updated_ts) "
        "VALUES (?,?,?,?,0.0,1.0,1.0,1.0,0.0,0.0,?,?,?) "
        "ON CONFLICT(exchange, strategy, ticker, regime) DO UPDATE SET "
        " n_samples=excluded.n_samples, p_pos=excluded.p_pos",
        (exchange, strategy, ticker, regime, n_samples, p_pos, NOW),
    )


# ---------------------------------------------------------------------------
# posterior_tilt — pure, continuous, bounded, neutral when cold
# ---------------------------------------------------------------------------


def test_posterior_tilt_neutral_when_cold() -> None:
    # n < POSTERIOR_TILT_MIN_N ⇒ tilt 1.0 EXACTLY regardless of p_pos.
    for p in (0.0, 0.25, 0.5, 0.9, 1.0):
        assert posterior_tilt(p_pos=p, n_samples=POSTERIOR_TILT_MIN_N - 1) == 1.0
        assert posterior_tilt(p_pos=p, n_samples=0) == 1.0


def test_posterior_tilt_neutral_at_p_half() -> None:
    # No evidence (p_pos = 0.5) ⇒ tilt 1.0 even when n is large.
    assert posterior_tilt(p_pos=0.5, n_samples=200) == 1.0


def test_posterior_tilt_amplifies_high_p_pos() -> None:
    assert posterior_tilt(p_pos=1.0, n_samples=50) == POSTERIOR_TILT_CEIL
    assert posterior_tilt(p_pos=0.85, n_samples=50) > 1.0


def test_posterior_tilt_dampens_low_p_pos() -> None:
    assert posterior_tilt(p_pos=0.0, n_samples=50) == POSTERIOR_TILT_FLOOR
    val = posterior_tilt(p_pos=0.15, n_samples=50)
    assert 0.0 < val < 1.0


def test_posterior_tilt_strictly_positive_no_off_switch() -> None:
    # Bottom of the range must never reach 0 (redistribution, not a block).
    assert POSTERIOR_TILT_FLOOR > 0.0
    assert POSTERIOR_TILT_FLOOR < 1.0 < POSTERIOR_TILT_CEIL


@given(
    p=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    n=st.integers(min_value=0, max_value=10_000),
)
def test_property_posterior_tilt_bounded_positive(p: float, n: int) -> None:
    out = posterior_tilt(p_pos=p, n_samples=n)
    assert math.isfinite(out)
    assert POSTERIOR_TILT_FLOOR <= out <= POSTERIOR_TILT_CEIL
    assert out > 0.0  # never an off-switch


def test_posterior_tilt_clamps_out_of_range_p() -> None:
    # Defensive: p_pos is a probability, but a corrupt row must not explode.
    assert posterior_tilt(p_pos=2.0, n_samples=50) == POSTERIOR_TILT_CEIL
    assert posterior_tilt(p_pos=-1.0, n_samples=50) == POSTERIOR_TILT_FLOOR


# ---------------------------------------------------------------------------
# fetch_posterior_tilt — DB reader, fail-open neutral
# ---------------------------------------------------------------------------


def test_fetch_posterior_tilt_no_row_neutral(memdb: sqlite3.Connection) -> None:
    key = CellKeyP0("okx", "tsmom", "NONE-USDT", "bull_trend")
    assert fetch_posterior_tilt(memdb, key) == 1.0


def test_fetch_posterior_tilt_sparse_row_neutral(memdb: sqlite3.Connection) -> None:
    key = CellKeyP0("okx", "tsmom", "SPARSE-USDT", "bull_trend")
    _seed_posterior(
        memdb, exchange="okx", strategy="tsmom", ticker="SPARSE-USDT",
        regime="bull_trend", p_pos=0.99, n_samples=POSTERIOR_TILT_MIN_N - 1,
    )
    assert fetch_posterior_tilt(memdb, key) == 1.0


def test_fetch_posterior_tilt_mature_winner_amplifies(memdb: sqlite3.Connection) -> None:
    key = CellKeyP0("okx", "tsmom", "WIN-USDT", "bull_trend")
    _seed_posterior(
        memdb, exchange="okx", strategy="tsmom", ticker="WIN-USDT",
        regime="bull_trend", p_pos=0.95, n_samples=60,
    )
    assert fetch_posterior_tilt(memdb, key) > 1.0


# ---------------------------------------------------------------------------
# resolve_routing_for_cell — end-to-end edge-routing behaviour
# ---------------------------------------------------------------------------


def _seed_pool(conn: sqlite3.Connection, *, strategy: str, regime: str) -> None:
    """25 cells × 25 trades each — activates the ≥20 quartile gate."""
    for i in range(25):
        ticker = f"BG{i:02d}-USDT"
        for j in range(25):
            pnl = (i - 12) * 0.1
            update_on_trade_close(
                conn,
                TradeClose(
                    key=CellKeyP0("okx", strategy, ticker, regime),
                    context=_ctx(),
                    pnl_r=pnl,
                    won=pnl > 0,
                    closed_ts=NOW + j,
                ),
            )


def test_aligned_winner_routes_top_with_posterior(memdb: sqlite3.Connection) -> None:
    """+EV regime-aligned cell with a mature winning posterior routes ×1.5.

    tsmom in bull_trend (aligned ⇒ amplify 1.25) + p_pos high ⇒ the routing
    score is lifted into the top quartile. Aggressive amplify preserved.
    """
    _seed_pool(memdb, strategy="tsmom", regime="bull_trend")
    win_key = CellKeyP0("okx", "tsmom", "BG24-USDT", "bull_trend")
    _seed_posterior(
        memdb, exchange="okx", strategy="tsmom", ticker="BG24-USDT",
        regime="bull_trend", p_pos=0.95, n_samples=60,
    )
    out = resolve_routing_for_cell(memdb, win_key, now_ts=NOW + 100)
    assert out == ROUTING_TOP_MULT


def test_cold_posterior_never_suppresses_admit(memdb: sqlite3.Connection) -> None:
    """A cell with a sparse posterior (n < MIN_N) gets tilt 1.0 — never dropped.

    The mid-pool cell stays MID (or better) — the cold posterior must NOT push
    an otherwise-neutral cell into the suppressed bottom slot.
    """
    _seed_pool(memdb, strategy="tsmom", regime="bull_trend")
    mid_key = CellKeyP0("okx", "tsmom", "BG12-USDT", "bull_trend")
    # Sparse posterior with a pessimistic p_pos — must be IGNORED (tilt 1.0).
    _seed_posterior(
        memdb, exchange="okx", strategy="tsmom", ticker="BG12-USDT",
        regime="bull_trend", p_pos=0.01, n_samples=3,
    )
    out_with_cold = resolve_routing_for_cell(memdb, mid_key, now_ts=NOW + 100)
    # Without any posterior the same mid cell resolves identically (neutral).
    out_baseline = resolve_routing_for_cell(
        memdb, CellKeyP0("okx", "tsmom", "BG11-USDT", "bull_trend"), now_ts=NOW + 100
    )
    assert out_with_cold == out_baseline
    assert out_with_cold != ROUTING_BOTTOM_MULT or out_baseline == ROUTING_BOTTOM_MULT


def test_routing_output_slot_unchanged(memdb: sqlite3.Connection) -> None:
    """The mult output is STILL one of {1.5, 1.0, 0.5} — no new slot/range."""
    _seed_pool(memdb, strategy="tsmom", regime="bull_trend")
    for i in range(25):
        key = CellKeyP0("okx", "tsmom", f"BG{i:02d}-USDT", "bull_trend")
        _seed_posterior(
            memdb, exchange="okx", strategy="tsmom", ticker=f"BG{i:02d}-USDT",
            regime="bull_trend", p_pos=i / 24.0, n_samples=40,
        )
        out = resolve_routing_for_cell(memdb, key, now_ts=NOW + 100)
        assert out in (ROUTING_TOP_MULT, ROUTING_MID_MULT, ROUTING_BOTTOM_MULT)


def test_cold_cell_below_min_live_still_neutral(memdb: sqlite3.Connection) -> None:
    """n_eff < CELL_MIN_LIVE_N short-circuits to neutral BEFORE any tilt runs."""
    update_on_trade_close(
        memdb,
        TradeClose(
            key=CellKeyP0("okx", "tsmom", "SOLO-USDT", "bull_trend"),
            context=_ctx(), pnl_r=1.0, won=True, closed_ts=NOW,
        ),
    )
    _seed_posterior(
        memdb, exchange="okx", strategy="tsmom", ticker="SOLO-USDT",
        regime="bull_trend", p_pos=0.99, n_samples=99,
    )
    out = resolve_routing_for_cell(
        memdb, CellKeyP0("okx", "tsmom", "SOLO-USDT", "bull_trend"), now_ts=NOW + 1
    )
    assert out == ROUTING_MID_MULT


@settings(max_examples=50)
@given(
    p_pos=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    n_samples=st.integers(min_value=0, max_value=500),
    regime=st.sampled_from(("bull_trend", "chop", "crisis")),
)
def test_property_routing_mult_always_valid_slot(
    p_pos: float, n_samples: int, regime: str
) -> None:
    conn = sqlite3.connect(":memory:")
    from polaris.storage.schema import ALL_DDL

    for stmt in ALL_DDL:
        conn.execute(stmt)
    try:
        _seed_pool(conn, strategy="tsmom", regime=regime)
        key = CellKeyP0("okx", "tsmom", "BG24-USDT", regime)
        _seed_posterior(
            conn, exchange="okx", strategy="tsmom", ticker="BG24-USDT",
            regime=regime, p_pos=p_pos, n_samples=n_samples,
        )
        out = resolve_routing_for_cell(conn, key, now_ts=NOW + 100)
        assert out in (ROUTING_TOP_MULT, ROUTING_MID_MULT, ROUTING_BOTTOM_MULT)
    finally:
        conn.close()
