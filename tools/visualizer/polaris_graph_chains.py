"""Glove-pathway builders for the space visualizer (DEMO/PAPER, display-only).

Split out of ``polaris_graph`` (keep each module ≤500 LOC) — the two functions
that restore the sphere's "glove" pathways from live trades + recent closes:

  - ``build_trade_chains``    → ``graph.json["trade_chains"]`` (drawTradeChains)
  - ``build_lifecycle_paths`` → ``graph.json["lifecycle_paths"]`` (highlightLifecycle)

Both reference node ids ``polaris_graph.build_graph`` already emits; they never
size, order, throttle, or gate anything. Aggressive bias is preserved — nothing
here touches sizing or risk.
"""

from __future__ import annotations

from typing import Any


def build_trade_chains(
    live_trades: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    *,
    regime: str,
) -> list[dict[str, Any]]:
    """Trade chains for the sphere's glove pathway (``drawTradeChains``).

    One chain per live (open) trade. ``chain`` is a sequence of POSITIONAL
    indices into ``nodes`` (the engine addresses ``projected[chain[s]]``, where
    ``projected`` is parallel to ``nodes``), forming the provenance pathway

        source-strategy → regime → signal-watch / market → position

    using node ids ``build_graph`` already emits (``strat_{sid}``,
    ``reg_regime_{regime}``, ``watch_{venue}_{ticker}`` / ``mkt_{ticker}``,
    ``pos_{venue}_{ticker}_{i}``). Only resolvable nodes are included, and the
    chain always terminates at the trade's own position node. ``strength`` rides
    on |pnl_pct| (display intensity); ``win_rate`` is 0.0 here (no per-trade
    history in the snapshot). Display-only — no trading behavior touched.
    """
    idx_by_id = {n["id"]: i for i, n in enumerate(nodes)}
    chains: list[dict[str, Any]] = []
    for tr in live_trades:
        venue = tr["exchange"]
        ticker = tr["ticker"]
        sid = tr["strategy_id"]
        pos_id = next(
            (n["id"] for n in nodes
             if n["cluster"] == "pos"
             and n.get("trade_id") == tr["trade_id"]),
            None,
        )
        if pos_id is None:
            continue
        # Provenance order: strat → regime → watch/mkt → pos. Each link is added
        # only if the referenced node exists in the graph.
        candidate_ids = [
            f"strat_{sid}",
            f"reg_regime_{regime}",
            f"watch_{venue}_{ticker}",
            f"mkt_{ticker}",
            pos_id,
        ]
        seen: set[str] = set()
        chain: list[int] = []
        for nid in candidate_ids:
            if nid in seen:
                continue
            i = idx_by_id.get(nid)
            if i is not None:
                chain.append(i)
                seen.add(nid)
        # Always anchor on the position node even if intermediates were missing.
        if not chain or chain[-1] != idx_by_id[pos_id]:
            chain.append(idx_by_id[pos_id])
        if len(chain) < 2:
            continue
        pnl_pct = float(tr["pnl_pct"])
        strength = round(min(1.0, 0.4 + abs(pnl_pct) / 5.0), 4)
        chains.append(
            {
                "trade_id": tr["trade_id"],
                "chain": chain,
                "ticker": ticker,
                "pnl_usd": tr["pnl_usd"],
                "pnl_pct": tr["pnl_pct"],
                "strength": strength,
                "win_rate": 0.0,
            }
        )
    return chains


def build_lifecycle_paths(
    live_trades: list[dict[str, Any]],
    closes: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    *,
    regime: str,
) -> list[dict[str, Any]]:
    """Lifecycle paths for the click → ``highlightLifecycle`` lookup.

    One path per open trade + recent close. ``node_ids`` is a sequence of node
    **id strings** (the engine resolves them via its own id→index map), tracing

        market → signal-watch → strategy → regime → position

    Only ids already present in ``nodes`` are emitted so the client lookup
    always resolves. ``trigger_ticker`` / ``trigger_strategy`` key the click
    match (clicked POS ticker or STRAT label). Display-only.
    """
    id_set = {n["id"] for n in nodes}

    def _path(venue: str, ticker: str, sid: str, tail_id: str | None) -> list[str]:
        ordered = [
            f"mkt_{ticker}",
            f"watch_{venue}_{ticker}",
            f"strat_{sid}",
            f"reg_regime_{regime}",
        ]
        if tail_id is not None:
            ordered.append(tail_id)
        out: list[str] = []
        seen: set[str] = set()
        for nid in ordered:
            if nid in seen:
                continue
            if nid in id_set:
                out.append(nid)
                seen.add(nid)
        return out

    paths: list[dict[str, Any]] = []
    for tr in live_trades:
        venue = tr["exchange"]
        ticker = tr["ticker"]
        sid = tr["strategy_id"]
        pos_id = next(
            (n["id"] for n in nodes
             if n["cluster"] == "pos"
             and n.get("trade_id") == tr["trade_id"]),
            None,
        )
        node_ids = _path(venue, ticker, sid, pos_id)
        if not node_ids:
            continue
        paths.append(
            {
                "trigger_ticker": ticker,
                "trigger_strategy": sid,
                "kind": "open",
                "node_ids": node_ids,
            }
        )
    for c in closes:
        venue = c["exchange"]
        ticker = c["ticker"]
        # Recent closes carry no strategy_id; tail on the exit_tally node so the
        # path still terminates on a real graph node when one exists.
        tail = f"exit_tally_{c['exit_type']}"
        node_ids = _path(venue, ticker, "", tail if tail in id_set else None)
        if not node_ids:
            continue
        paths.append(
            {
                "trigger_ticker": ticker,
                "trigger_strategy": "",
                "kind": "closed",
                "node_ids": node_ids,
            }
        )
    return paths
