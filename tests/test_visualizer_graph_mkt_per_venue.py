"""Globe mkt-node LOD must be PER-VENUE, not a single global pool.

Regression (Jin 2026-06-25): the globe showed OKX's ~219 active tradable
tickers as only ~5 individual dots. Root cause: ``_mkt_nodes`` sorted the WHOLE
universe by a single global tier priority and kept the top ``node_cap`` (180)
across ALL venues. Alpaca's ~12.9k rows include 148 tier-S + 314 tier-A rows
that monopolised the global pool, starving OKX (only 4 tier-S rows) to ~4
individual nodes while its other 215 active rows folded into the tail haze.

The design intent is "전체 거래가능 티커(mkt universe) 점 + active 불": each venue's
full tradable shell shows as its own dots. The fix gives each venue its OWN node
budget so no venue can starve another — OKX's 219 active rows all render, while
Alpaca's huge tail still folds into the per-venue haze (render-perf preserved).

Display-only — no trading behavior touched.
"""

from __future__ import annotations

from collections import Counter

from tools.visualizer.polaris_graph import _mkt_nodes


def _universe_rows(exchange: str, n: int, *, n_24h: int = 10) -> list[dict]:
    return [
        {
            "ticker": f"{exchange.upper()}{i}-USDT",
            "exchange": exchange,
            "asset_group": "crypto",
            "n_24h": n_24h,
            "is_active": 1,
        }
        for i in range(n)
    ]


def test_okx_not_starved_by_alpaca_global_pool() -> None:
    """OKX's full active shell must render even when Alpaca floods high tiers.

    Reproduces the live shape: OKX = 219 active rows (mostly tier A/B), Alpaca =
    a large tier-S/A flood. With a single global cap the OKX rows lose the global
    sort; with a per-venue budget they all survive.
    """
    okx = _universe_rows("okx", 219)
    alpaca = _universe_rows("alpaca", 1200)
    universe = okx + alpaca

    # Alpaca rows are all hot tiers (S/A) → they win every global priority slot.
    tier_map: dict[str, str] = {}
    for u in alpaca[:148]:
        tier_map[f"alp:{u['ticker'].split('-')[0]}"] = "S"
    for u in alpaca[148 : 148 + 314]:
        tier_map[f"alp:{u['ticker'].split('-')[0]}"] = "A"
    # OKX rows have only a handful of tier-S, the rest untiered-but-active.
    for u in okx[:4]:
        tier_map[f"okx:{u['ticker'].split('-')[0]}"] = "S"

    nodes, _tail = _mkt_nodes(
        universe, signal_counts={}, tier_map=tier_map, node_cap=240, base_i=0
    )
    by_ex = Counter(n["exchange"] for n in nodes)

    # Before the fix OKX got ~4; after, all 219 OKX active rows are individual.
    assert by_ex["okx"] == 219, f"OKX starved: {by_ex}"


def test_alpaca_tail_still_folds_to_haze() -> None:
    """The huge Alpaca shell stays render-bounded: top-cap nodes + tail haze."""
    universe = _universe_rows("alpaca", 1200)
    nodes, tail = _mkt_nodes(
        universe, signal_counts={}, tier_map={}, node_cap=240, base_i=0
    )
    by_ex = Counter(n["exchange"] for n in nodes)
    assert by_ex["alpaca"] == 240, f"Alpaca not capped per-venue: {by_ex}"
    tail_by_ex = {t["exchange"]: t for t in tail}
    assert tail_by_ex["alpaca"]["count"] == 1200 - 240


def test_live_signal_always_individual_even_in_overflow_venue() -> None:
    """A real signal catch is never folded into the haze, in any venue."""
    universe = _universe_rows("alpaca", 1200)
    # one deep-tail row (rank ~1000) caught a live signal.
    hot = universe[1000]
    key = f"alp:{hot['ticker'].split('-')[0]}"
    nodes, _tail = _mkt_nodes(
        universe, signal_counts={key: 3}, tier_map={}, node_cap=240, base_i=0
    )
    ids = {n["id"] for n in nodes}
    assert f"mkt_alpaca_{hot['ticker']}" in ids


def test_base_i_indices_contiguous() -> None:
    """Emitted node ``i`` indices stay contiguous from base_i (flow refs)."""
    universe = _universe_rows("okx", 5) + _universe_rows("capital", 5)
    nodes, _tail = _mkt_nodes(
        universe, signal_counts={}, tier_map={}, node_cap=240, base_i=100
    )
    idxs = sorted(n["i"] for n in nodes)
    assert idxs == list(range(100, 100 + len(nodes)))
