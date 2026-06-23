"""Increment 1 — CONSUME (focus rank + trade-eligibility) + DECOUPLE (watch≠trade).

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack ban.

Proves the entrance judgment is actually CONSUMED (not "researched, unapplied"):
  1. ``opportunity_score`` lifts a candidate's focus rank (the new rank term).
  2. ``opportunity_score`` + ``trade_eligible`` persist on ``watchlist_focus``.
  3. ``get_focus_targets(eligible_only=True)`` returns the TRADE subset while the
     default (watch) returns the FULL focus — decouple, flow-preserving.
  4. A held position stays force-seated in BOTH sets (never starved).
"""

from __future__ import annotations

from polaris.core.universe.schema import FocusSelection, UniverseInstrument
from polaris.core.universe.watchlist import (
    compute_dynamic_focus,
    persist_focus,
    score_focus_candidates,
)
from polaris.scripts._production_layers import get_focus_targets
from polaris.storage.schema import init_db


def _ins(symbol: str, *, vol: float = 1e8, atr: float = 3.0) -> UniverseInstrument:
    return UniverseInstrument(
        venue="okx",
        symbol=symbol,
        instrument_id=f"okx:{symbol}",
        underlying_group_id=f"crypto:{symbol}",
        asset_class="crypto",
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=vol,
        spread_bps=2.0,
        atr_24h_pct=atr,
        depth_10bps_usd=1e6,
    )


def test_opportunity_score_lifts_focus_rank() -> None:
    universe = [_ins("AAA"), _ins("BBB"), _ins("CCC")]
    # Without opportunity scores, the three are near-identical (same vol/atr).
    base = score_focus_candidates(universe)
    # Give CCC a strong opportunity_score; it must out-score the others.
    opp = {"okx:AAA": 0.1, "okx:BBB": 0.1, "okx:CCC": 0.95}
    lifted = score_focus_candidates(universe, opportunity_scores=opp)
    assert lifted[2] > base[2]
    assert lifted[2] > lifted[0]


def test_no_opportunity_scores_is_byte_identical() -> None:
    universe = [_ins("AAA", vol=5e8), _ins("BBB", vol=1e8)]
    assert score_focus_candidates(universe) == score_focus_candidates(
        universe, opportunity_scores=None
    )


def test_focus_persists_opportunity_and_eligibility() -> None:
    universe = [_ins("AAA", vol=5e8), _ins("BBB", vol=1e7)]
    opp = {"okx:AAA": 0.9, "okx:BBB": 0.05}
    elig = {"okx:AAA": True, "okx:BBB": False}
    focus = compute_dynamic_focus(
        universe, cycle_ts=1000, opportunity_scores=opp, trade_eligible=elig
    )
    by_sym = {f.symbol: f for f in focus}
    assert by_sym["AAA"].opportunity_score == 0.9
    assert by_sym["AAA"].trade_eligible is True
    assert by_sym["BBB"].trade_eligible is False

    conn = init_db(":memory:")
    persist_focus(conn, focus)
    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT symbol, opportunity_score, trade_eligible FROM watchlist_focus"
        )
    }
    assert rows["AAA"] == (0.9, 1)
    assert rows["BBB"][1] == 0  # trade_eligible=False persisted as 0
    conn.close()


def test_decouple_watch_is_superset_of_trade() -> None:
    conn = init_db(":memory:")
    focus = [
        FocusSelection(1000, "okx", "AAA", 5.0, 1, "core",
                       opportunity_score=0.9, trade_eligible=True),
        FocusSelection(1000, "okx", "BBB", 4.0, 2, "satellite",
                       opportunity_score=0.1, trade_eligible=False),
        FocusSelection(1000, "okx", "CCC", 3.0, 3, "satellite",
                       opportunity_score=0.5, trade_eligible=True),
    ]
    persist_focus(conn, focus)

    watch = get_focus_targets(conn, cycle_ts=1000)
    trade = get_focus_targets(conn, cycle_ts=1000, eligible_only=True)
    watch_syms = {s for _v, s, _a, _g in watch}
    trade_syms = {s for _v, s, _a, _g in trade}

    # WATCH = full focus (flow_not_block: every valid symbol stays observed).
    assert watch_syms == {"AAA", "BBB", "CCC"}
    # TRADE = eligible subset (BBB dropped from the trade set, still watched).
    assert trade_syms == {"AAA", "CCC"}
    assert "BBB" in watch_syms and "BBB" not in trade_syms


def test_held_position_force_seated_in_both_sets() -> None:
    conn = init_db(":memory:")
    # Focus has only AAA (eligible). DDD is held but NOT in focus and would be
    # ineligible — it must still appear in BOTH watch and trade sets (never starved).
    persist_focus(
        conn,
        [FocusSelection(1000, "okx", "AAA", 5.0, 1, "core",
                        opportunity_score=0.9, trade_eligible=True)],
    )
    conn.execute(
        "INSERT INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status) "
        "VALUES ('p1','okx','DDD','crypto:DDD','s','s','s','long',1.0,'open')"
    )
    watch = {s for _v, s, _a, _g in get_focus_targets(conn, cycle_ts=1000)}
    trade = {
        s for _v, s, _a, _g in get_focus_targets(conn, cycle_ts=1000, eligible_only=True)
    }
    assert "DDD" in watch
    assert "DDD" in trade  # held name is force-seated even into the trade set
    conn.close()
