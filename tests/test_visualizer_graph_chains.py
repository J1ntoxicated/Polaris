"""Glove-pathway restore — ``build_graph`` trade_chains / lifecycle_paths.

Regression: ``polaris_graph.build_graph`` hard-coded ``trade_chains: []`` and
``lifecycle_paths: []`` so the sphere's "glove" pathway draw functions
(``drawTradeChains`` + ``highlightLifecycle`` in sphere-render.js) always
received empty arrays and drew nothing, even with live positions on the globe.

This test asserts that, when the snapshot has live trades (open positions) and
recent closes, ``build_graph`` now emits NON-empty ``trade_chains`` and
``lifecycle_paths`` with the keys the draw functions consume, and that every
node referenced by a chain / path actually exists in the emitted ``nodes`` list
(``trade_chains[].chain`` = positional indices into ``nodes``;
``lifecycle_paths[].node_ids`` = node ``id`` strings).

Display-only — no trading behavior touched.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.storage.schema import ALL_DDL
from tools.visualizer.polaris_graph import build_graph


def _mkdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _seed_open_position(
    db_path: Path,
    *,
    position_id: str,
    venue: str = "okx",
    symbol: str = "SOL-USDT",
    strategy_id: str = "tsmom",
    side: str = "long",
    qty: float = 10.0,
    opened_ts: int,
) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        """
        INSERT INTO positions
            (position_id, venue, symbol, underlying_group_id, strategy_id,
             entry_strategy_id, active_strategy_id, side, qty, status,
             opened_ts, swap_count)
        VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, 'open', ?, 0)
        """,
        (position_id, venue, symbol, strategy_id, strategy_id, strategy_id,
         side, qty, opened_ts),
    )
    # An open fill so the entry-price lookup resolves a real entry.
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, ?, ?, ?, 'buy', 1000.0, 100.0, 7.0, 1.0, ?, ?, NULL, 0.0, "
        "        0, ?, 1000.0, 'filled')",
        (f"of_{position_id}", venue, f"{venue}:{symbol}", strategy_id,
         opened_ts * 1000, f"oo_{position_id}", qty),
    )
    conn.close()


def _seed_close(
    db_path: Path,
    *,
    fill_id: str,
    venue: str = "okx",
    symbol: str = "ADA-USDT",
    strategy_id: str = "tsmom",
    pnl_usd: float,
    ts_ms: int,
) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, ?, ?, ?, 'sell', 1000.0, 110.0, 7.0, 1.0, ?, ?, NULL, ?, "
        "        1, 9.0, 1000.0, 'filled')",
        (fill_id, venue, f"{venue}:{symbol}", strategy_id, ts_ms,
         f"oc_{fill_id}", pnl_usd),
    )
    conn.close()


def test_empty_db_keeps_chains_empty(tmp_path: Path) -> None:
    """No positions / closes → chains + paths are empty (graceful, no crash)."""
    db_path = _mkdb(tmp_path)
    g = build_graph(db_path)
    assert g["trade_chains"] == []
    assert g["lifecycle_paths"] == []


def test_live_trades_populate_trade_chains(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    now = int(time.time())
    _seed_open_position(db_path, position_id="p1", symbol="SOL-USDT",
                        strategy_id="tsmom", opened_ts=now - 300)
    g = build_graph(db_path)

    chains = g["trade_chains"]
    assert chains, "trade_chains must be non-empty when live trades exist"

    node_count = len(g["nodes"])
    required = {"trade_id", "chain", "ticker", "pnl_usd", "pnl_pct",
                "strength", "win_rate"}
    for tc in chains:
        assert required <= set(tc), f"missing keys: {required - set(tc)}"
        chain = tc["chain"]
        assert isinstance(chain, list) and len(chain) >= 2, (
            "chain must be a multi-node sequence (strat → … → position)"
        )
        # Every chain entry is a resolvable positional index into nodes.
        for idx in chain:
            assert isinstance(idx, int)
            assert 0 <= idx < node_count, f"chain idx {idx} out of range"
        # The chain must terminate at the position node for this trade.
        last = g["nodes"][chain[-1]]
        assert last["cluster"] == "pos"
        assert last["ticker"] == tc["ticker"]


def test_lifecycle_paths_resolvable_node_ids(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    now = int(time.time())
    _seed_open_position(db_path, position_id="p1", symbol="SOL-USDT",
                        strategy_id="tsmom", opened_ts=now - 300)
    _seed_close(db_path, fill_id="c1", symbol="ADA-USDT",
                strategy_id="rsi_bb", pnl_usd=12.0, ts_ms=(now - 120) * 1000)
    g = build_graph(db_path)

    paths = g["lifecycle_paths"]
    assert paths, "lifecycle_paths must be non-empty for open + recent closes"

    id_set = {n["id"] for n in g["nodes"]}
    for p in paths:
        assert "trigger_ticker" in p
        assert "trigger_strategy" in p
        assert "node_ids" in p
        node_ids = p["node_ids"]
        assert isinstance(node_ids, list) and node_ids, "node_ids must be non-empty"
        # Every referenced id must exist among the emitted graph nodes so the
        # client idMap lookup (highlightLifecycle) resolves it.
        for nid in node_ids:
            assert nid in id_set, f"lifecycle node id {nid!r} not in graph nodes"

    # An open position contributes a lifecycle path keyed on its ticker.
    open_tickers = {p["trigger_ticker"] for p in paths}
    assert "SOL" in open_tickers


def test_chain_starts_at_strategy_node(tmp_path: Path) -> None:
    """Chain must start at the source-strategy node (provenance origin)."""
    db_path = _mkdb(tmp_path)
    now = int(time.time())
    _seed_open_position(db_path, position_id="p1", symbol="SOL-USDT",
                        strategy_id="tsmom", opened_ts=now - 300)
    g = build_graph(db_path)
    chains = g["trade_chains"]
    assert chains
    head = g["nodes"][chains[0]["chain"][0]]
    assert head["cluster"] == "strat"
    assert head["label"] == "tsmom"
