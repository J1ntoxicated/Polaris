"""G1 ranking-brain (B1) — per-venue z-norm + signal_density + cell_scores wiring.

DEMO/PAPER virtual capital, AGGRESSIVE bias preserved. Every change here is
flow_not_block: the liquidity floor is an eligibility-MEMBERSHIP boundary and the
z-normalization is a RANKING-ORDER correction — neither blocks an entry, cuts a
size, vetoes a signal, or introduces a ≤1 multiplier stack (no 9-stack). GPT=0.

Audit source: vault/50_research/g1_universe_gate_audit_2026-06-23.md (B1 cluster).
The cross-venue z-score mis-ordered equities (BTC's $251B flattened every equity
vol-z negative → penny names out-ranked megacaps), and ~35% of designed rank
weight was dead (signal_density 0.25 + cell 0.10 both 0.0). These tests pin the
fix: per-(venue,asset_class) z-normalization + live signal_density/cell producers.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.universe._ranking import rank_active_universe
from polaris.core.universe.schema import UniverseInstrument
from polaris.core.universe.watchlist import score_focus_candidates
from polaris.scripts._production_layers import (
    compute_signal_density_7d,
    read_cell_scores_by_instrument,
)
from polaris.storage.schema import ALL_DDL

NOW = 1_780_000_000
DAY = 86_400


def _inst(
    symbol: str,
    *,
    venue: str,
    asset_class: str,
    vol: float,
    atr_pct: float = 2.0,
    spread_bps: float = 2.0,
    depth: float = 200_000.0,
    quote_ccy: str = "USDT",
    last_price: float = 100.0,
    signal_density_7d: float = 0.0,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol.split('-')[0]}",
        asset_class=asset_class,
        quote_ccy=quote_ccy,
        state="live",
        vol_24h_usd=vol,
        spread_bps=spread_bps,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=depth,
        signal_density_7d=signal_density_7d,
        last_seen_ts=NOW,
        last_price=last_price,
    )


@pytest.fixture
def memdb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for ddl in ALL_DDL:
        conn.execute(ddl)
    return conn


# ---------------------------------------------------------------------------
# #1 per-(venue,asset_class) z-normalization in score_focus_candidates
# ---------------------------------------------------------------------------


def _focus_score_by_symbol(insts: list[UniverseInstrument]) -> dict[str, float]:
    scores = score_focus_candidates(insts)
    return {ins.symbol: scores[i] for i, ins in enumerate(insts)}


def test_megacap_outranks_penny_despite_trillion_scale_crypto() -> None:
    """Root fix: a liquid megacap must out-SCORE a penny name in the merged focus
    score, even when trillion-scale crypto vol shares the population.

    BEFORE per-venue z-norm: BTC's ~$251B inflates the single mixed-pool vol mean/
    std so NVDA/SPY vol-z go negative, equity ordering collapses onto ATR, and
    penny names (huge ATR%) out-rank megacaps. AFTER: each (venue,asset_class) is
    z-normalized within itself, so the equity vol axis is computed equity-vs-equity
    and the megacap's real dollar-volume advantage survives.
    """
    crypto = [
        _inst("BTC-USDT", venue="okx", asset_class="crypto", vol=2.7e14, atr_pct=4.0),
        _inst("ETH-USDT", venue="okx", asset_class="crypto", vol=6.1e12, atr_pct=5.0),
        *[
            _inst(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6)
            for i in range(20)
        ],
    ]
    megacap = _inst("NVDA", venue="alpaca", asset_class="equity", vol=2.1e8, atr_pct=2.0)
    penny = _inst("ABVE", venue="alpaca", asset_class="equity", vol=5.4e4, atr_pct=377.0)
    insts = crypto + [megacap, penny]

    by_sym = _focus_score_by_symbol(insts)
    assert by_sym["NVDA"] > by_sym["ABVE"], (
        "per-venue z-norm must rank the liquid megacap above the penny name; "
        "cross-venue z-score buried NVDA under penny ATR"
    )


def test_equity_vol_z_not_dragged_negative_by_crypto_scale() -> None:
    """An equity with the highest dollar-volume IN ITS CLASS must out-score a
    lower-volume same-ATR class peer — crypto's absolute scale is irrelevant to
    the equity sub-ranking once z-norm is per-group."""
    crypto = [
        _inst(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e13 - i * 1e11)
        for i in range(10)
    ]
    hi = _inst("AAPL", venue="alpaca", asset_class="equity", vol=2e8, atr_pct=1.5)
    lo = _inst("XYZ", venue="alpaca", asset_class="equity", vol=2e6, atr_pct=1.5)
    by_sym = _focus_score_by_symbol(crypto + [hi, lo])
    assert by_sym["AAPL"] > by_sym["XYZ"]


def test_single_group_ordering_unchanged_by_grouping() -> None:
    """Backward-compat: an all-okx-crypto population is one group, so per-group
    z-norm is identical to the legacy global z-norm — ordering byte-identical."""
    insts = [
        _inst(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 5e7,
              atr_pct=2.0 + i * 0.3)
        for i in range(12)
    ]
    scores = score_focus_candidates(insts)
    order = sorted(range(len(insts)), key=lambda i: scores[i], reverse=True)
    # Highest-vol crypto still leads; the global z-norm and per-group z-norm
    # coincide for a single group.
    assert insts[order[0]].symbol == "C0-USDT"


# ---------------------------------------------------------------------------
# #1 per-venue z-norm in rank_active_universe (active-set selector)
# ---------------------------------------------------------------------------


def test_active_rank_seats_top_equity_despite_crypto_scale() -> None:
    """The active-set selector must not let crypto's trillion-scale vol sweep every
    top_n slot; the strongest equity (in its own class) must survive the cut."""
    crypto = [
        _inst(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e13 - i * 1e10,
              depth=1e6)
        for i in range(20)
    ]
    strong_eq = _inst(
        "NVDA", venue="alpaca", asset_class="equity", vol=2.1e8, atr_pct=3.0,
        spread_bps=2.0, depth=0.0, quote_ccy="USD",
    )
    weak_eq = _inst(
        "TINY", venue="alpaca", asset_class="equity", vol=6e6, atr_pct=0.5,
        spread_bps=2.0, depth=0.0, quote_ccy="USD",
    )
    out = rank_active_universe(crypto + [strong_eq, weak_eq], top_n=10)
    syms = {ins.symbol for ins in out}
    assert "NVDA" in syms, (
        "per-venue z-norm in the active-set selector must seat the strongest equity "
        "rather than letting crypto absolute vol monopolize all top_n slots"
    )


def test_active_rank_single_venue_unchanged() -> None:
    """All-okx population: per-group == global → legacy top-N-by-composite holds."""
    insts = [
        _inst(f"S{i}-USDT", venue="okx", asset_class="crypto", vol=1e7 + i * 1e7,
              atr_pct=2.0 + i * 0.1)
        for i in range(30)
    ]
    out = rank_active_universe(insts, top_n=10)
    surviving = {ins.symbol for ins in out}
    assert "S29-USDT" in surviving
    assert "S0-USDT" not in surviving


# ---------------------------------------------------------------------------
# #3 signal_density_7d producer (reads `signals`)
# ---------------------------------------------------------------------------


def test_signal_density_counts_7d_window(memdb: sqlite3.Connection) -> None:
    """Producer counts emitted signals per instrument_id inside the 7d window and
    excludes anything older — activating the 0.25 rank weight that was 0.0."""
    rows = [
        # BTC: 3 signals inside the window
        ("vb", "s1", "okx:BTC-USDT", NOW - DAY),
        ("vb", "s2", "okx:BTC-USDT", NOW - 2 * DAY),
        ("vb", "s3", "okx:BTC-USDT", NOW - 6 * DAY),
        # BTC: 1 stale signal OUTSIDE the window → excluded
        ("vb", "s4", "okx:BTC-USDT", NOW - 8 * DAY),
        # ETH: 1 signal inside window
        ("vb", "s5", "okx:ETH-USDT", NOW - 1 * DAY),
    ]
    memdb.executemany(
        "INSERT INTO signals (strategy_id, signal_id, instrument_id, direction, "
        "score, thesis, ts) VALUES (?, ?, ?, 'long', 1.0, '', ?)",
        rows,
    )
    out = compute_signal_density_7d(memdb, now_ts=NOW)
    assert out["okx:BTC-USDT"] == 3.0
    assert out["okx:ETH-USDT"] == 1.0


def test_signal_density_empty_db_returns_empty(memdb: sqlite3.Connection) -> None:
    assert compute_signal_density_7d(memdb, now_ts=NOW) == {}


def test_signal_density_feeds_focus_score() -> None:
    """A symbol with active signal density must out-score an otherwise-identical
    same-class peer with zero density (the 0.25 weight is now live)."""
    hot = _inst("BTC-USDT", venue="okx", asset_class="crypto", vol=1e9,
                signal_density_7d=12.0)
    cold = _inst("LTC-USDT", venue="okx", asset_class="crypto", vol=1e9,
                 signal_density_7d=0.0)
    by_sym = _focus_score_by_symbol([hot, cold])
    assert by_sym["BTC-USDT"] > by_sym["LTC-USDT"]


# ---------------------------------------------------------------------------
# #3 cell_scores reader (aggregates cell_matrix_p0 per instrument_id)
# ---------------------------------------------------------------------------


def test_cell_scores_aggregate_per_instrument(memdb: sqlite3.Connection) -> None:
    """Reader keys cell_matrix_p0 by ``venue:symbol`` (n_eff-weighted) so the
    0.10 cell weight can finally consume the 112 live rows it was starved of."""
    cells = [
        # BTC: two cells, n_eff-weighted mean = (10*0.8 + 30*0.4)/40 = 0.5
        ("okx", "vb", "BTC-USDT", "bull", 10.0, 0.8),
        ("okx", "tsmom", "BTC-USDT", "bull", 30.0, 0.4),
        # ETH: single cell
        ("okx", "vb", "ETH-USDT", "bull", 20.0, 0.9),
        # zero n_eff → excluded
        ("okx", "vb", "DOGE-USDT", "bull", 0.0, 0.99),
    ]
    memdb.executemany(
        "INSERT INTO cell_matrix_p0 (exchange, strategy, ticker, regime, n_eff, "
        "wins_eff, pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts) "
        "VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, ?, 0)",
        cells,
    )
    out = read_cell_scores_by_instrument(memdb)
    assert out["okx:BTC-USDT"] == pytest.approx(0.5)
    assert out["okx:ETH-USDT"] == pytest.approx(0.9)
    assert "okx:DOGE-USDT" not in out


def test_cell_scores_empty_returns_empty(memdb: sqlite3.Connection) -> None:
    assert read_cell_scores_by_instrument(memdb) == {}


def test_cell_scores_feed_focus_score() -> None:
    """A symbol with a strong cell score out-scores a same-class peer with none."""
    a = _inst("BTC-USDT", venue="okx", asset_class="crypto", vol=1e9)
    b = _inst("LTC-USDT", venue="okx", asset_class="crypto", vol=1e9)
    scores = score_focus_candidates([a, b], cell_scores={"okx:BTC-USDT": 0.9})
    by_sym = {a.symbol: scores[0], b.symbol: scores[1]}
    assert by_sym["BTC-USDT"] > by_sym["LTC-USDT"]


# ---------------------------------------------------------------------------
# WATCH/TRADE decouple — resource guards (env-tunable watch caps)
# ---------------------------------------------------------------------------


def test_focus_cycle_target_env_tunable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-cycle bar-ingest watch cap is env-tunable (resource guard)."""
    from polaris.scripts._production_tick import (
        FOCUS_CYCLE_TARGET,
        _focus_cycle_target,
    )

    monkeypatch.delenv("POLARIS_FOCUS_CYCLE_TARGET", raising=False)
    assert _focus_cycle_target() == FOCUS_CYCLE_TARGET  # default (widened to 120)
    assert FOCUS_CYCLE_TARGET > 30  # widened past the old 30 watch bottleneck
    monkeypatch.setenv("POLARIS_FOCUS_CYCLE_TARGET", "75")
    assert _focus_cycle_target() == 75
    monkeypatch.setenv("POLARIS_FOCUS_CYCLE_TARGET", "garbage")
    assert _focus_cycle_target() == FOCUS_CYCLE_TARGET  # invalid → default


def test_ws_cap_unchanged_at_40() -> None:
    """WS socket stays bounded at top-40 (do NOT widen the socket on watch widen)."""
    from polaris.scripts._production_ws import WS_SYMBOLS_PER_VENUE

    assert WS_SYMBOLS_PER_VENUE == 40
