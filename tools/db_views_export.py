"""DB sync — sqlite → vault/50_cells/ markdown export.

WAL-safe (read-only connection), atomic write, watermark embedded.

Run: python -m tools.db_views_export
Cron: hourly (TZ=Australia/Sydney)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from tools._md_writer import (
    build_frontmatter,
    markdown_table,
    now_utc,
    safe_filename,
    write_atomic,
)

PROJECT_ROOT = Path(__file__).parent.parent
DB = PROJECT_ROOT / "data" / "invasion.sqlite"
VAULT = PROJECT_ROOT / "vault"
VAULT_CELLS = VAULT / "02_live" / "cells"
VAULT_STRATEGIES = VAULT / "02_live/strategies"
VAULT_TICKERS = VAULT / "02_live/tickers"
VAULT_REGIMES = VAULT / "02_live" / "regimes"
VAULT_EXIT_PATTERNS = VAULT / "02_live" / "exits"


def open_ro_consistent() -> sqlite3.Connection:
    """Read-only WAL-safe connection.

    Codex Top-8: WAL DB consistent export. mode=ro 만으로 부족하지만,
    Bot 가 정기 wal_checkpoint 수행하므로 reader 는 latest committed state 봄.
    """
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def get_watermark(conn: sqlite3.Connection) -> dict:
    """Snapshot freshness watermark — 모든 sync md 에 embed."""
    return {
        "trades_max_exit_ts": conn.execute(
            "SELECT MAX(exit_ts) FROM trades WHERE status='closed'"
        ).fetchone()[0],
        "cells_count": conn.execute(
            "SELECT COUNT(*) FROM strategy_cell_matrix"
        ).fetchone()[0],
        "cells_avg_score": conn.execute(
            "SELECT AVG(score) FROM strategy_cell_matrix"
        ).fetchone()[0],
        "trades_open": conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='open'"
        ).fetchone()[0],
    }


def _common_frontmatter(entity_type: str, entity_id: str, wm: dict, **extra) -> dict:
    """Shared frontmatter — entity schema 표준 + watermark."""
    fm = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "auto": True,
        "do_not_edit": True,
        "last_synced": now_utc(),
        "watermark": wm,
        "tags": [f"db", f"type/{entity_type}"],
    }
    fm.update(extra)
    return fm


def write_snapshot_latest(conn: sqlite3.Connection, wm: dict) -> None:
    rows = conn.execute("""
        SELECT exchange, asset_group, session, regime, strategy_id,
               direction, ticker, liquidity_tier,
               score, n_trades, wr, avg_pnl,
               optimal_trail_activate, optimal_max_hold_sec, optimal_hard_stop_pct
        FROM strategy_cell_matrix
        ORDER BY score DESC
    """).fetchall()

    md = build_frontmatter(_common_frontmatter(
        "db_snapshot", "cells_latest", wm,
        table="strategy_cell_matrix",
        row_count=len(rows),
        sources=["sqlite:strategy_cell_matrix"],
    ))
    md += f"\n# Strategy Cell Matrix Snapshot\n\n"
    md += f"> {len(rows)} cells, score DESC. Source: `strategy_cell_matrix`.\n"
    md += f"> Watermark: latest_trade_exit_ts={wm['trades_max_exit_ts']}, "
    md += f"avg_score={wm['cells_avg_score']:.3f}, open_trades={wm['trades_open']}\n\n"

    if rows:
        cols = list(rows[0].keys())
        md += markdown_table(rows, cols)

    write_atomic(VAULT_CELLS / "snapshot_latest.md", md)


def write_by_strategy(conn: sqlite3.Connection, wm: dict) -> None:
    """One md per strategy_id — canonical at `vault/02_strategies/<id>.md`.

    Vault 구체화 (Jin 14:27): canonical entity path 로 통일 → [[<strategy>]] wikilink 매칭.
    Legacy `50_cells/by_strategy/` 는 redirect placeholder 만 유지.
    """
    strategies = [r[0] for r in conn.execute(
        "SELECT DISTINCT strategy_id FROM strategy_cell_matrix WHERE strategy_id IS NOT NULL"
    ).fetchall()]

    out_dir = VAULT_STRATEGIES
    out_dir.mkdir(parents=True, exist_ok=True)

    for sid in strategies:
        rows = conn.execute("""
            SELECT exchange, asset_group, regime, direction, ticker, session, liquidity_tier,
                   score, n_trades, wr, avg_pnl,
                   optimal_trail_activate, optimal_max_hold_sec, optimal_hard_stop_pct
            FROM strategy_cell_matrix
            WHERE strategy_id = ?
            ORDER BY score DESC
        """, (sid,)).fetchall()

        # Aggregate stats
        total_n = sum(r["n_trades"] or 0 for r in rows)
        avg_wr = sum((r["wr"] or 0) for r in rows) / len(rows) if rows else 0
        avg_score = sum((r["score"] or 0) for r in rows) / len(rows) if rows else 0

        # Auto backlinks: extract unique tickers, regimes, asset_groups
        tickers = sorted({r["ticker"] for r in rows if r["ticker"]})
        regimes = sorted({r["regime"] for r in rows if r["regime"]})

        md = build_frontmatter(_common_frontmatter(
            "strategy", sid, wm,
            cell_count=len(rows),
            total_trades=total_n,
            avg_wr=round(avg_wr, 3),
            avg_score=round(avg_score, 3),
            relations={
                "uses_tickers": tickers[:30],
                "uses_regimes": regimes,
            },
            sources=["sqlite:strategy_cell_matrix"],
        ))
        md += f"\n# Strategy: `{sid}`\n\n"
        md += f"> {len(rows)} cells, total_trades={total_n}, avg_wr={avg_wr:.3f}, avg_score={avg_score:.3f}\n\n"
        md += f"**Tickers used** ({len(tickers)}): {', '.join(f'[[{t}]]' for t in tickers[:20])}"
        if len(tickers) > 20:
            md += f" ... +{len(tickers)-20}"
        md += "\n\n"
        md += f"**Regimes**: {', '.join(f'[[{r}]]' for r in regimes)}\n\n"
        md += "## Cells (score DESC)\n\n"

        if rows:
            cols = list(rows[0].keys())
            md += markdown_table(rows, cols)

        # Strategy lineage section
        lin = _strategy_lineage(conn, sid)
        md += "\n## Lineage — Recent Trades (24h)\n\n"
        if lin["recent_trades"]:
            for t in lin["recent_trades"][:10]:
                ticker_link = f"[[{t['ticker']}]]" if t['ticker'] else "?"
                regime_link = f"[[{t['regime']}]]" if t['regime'] else "?"
                exit_link = f"[[{t['exit_type']}]]" if t['exit_type'] else "?"
                md += (
                    f"- `{t['id']}` — {ticker_link} {t['direction']} / "
                    f"{regime_link} → {exit_link} / "
                    f"size ${t['sz']:.0f} / pnl ${t['pnl']:.2f} / hold {t['h']:.0f}s\n"
                )
        else:
            md += "_(no trades in 24h)_\n"

        md += "\n## Regime Distribution (7d)\n\n"
        if lin["regime_dist_7d"]:
            md += "| Regime | N | Sum PnL | Avg PnL |\n|---|---|---|---|\n"
            for r in lin["regime_dist_7d"]:
                if r["regime"]:
                    md += f"| [[{r['regime']}]] | {r['n']} | ${r['sum_pnl']:.2f} | ${r['avg_pnl']:.3f} |\n"

        md += "\n## Exit Type Distribution (7d)\n\n"
        if lin["exit_dist_7d"]:
            md += "| Exit Type | N | Sum PnL |\n|---|---|---|\n"
            for e in lin["exit_dist_7d"]:
                if e["exit_type"]:
                    md += f"| [[{e['exit_type']}]] | {e['n']} | ${e['sum_pnl']:.2f} |\n"

        write_atomic(out_dir / f"{safe_filename(sid)}.md", md)


def _strategy_lineage(conn: sqlite3.Connection, strategy_id: str) -> dict:
    """Recent trades + exit/regime/ticker mix for a strategy (last 24h)."""
    trades = conn.execute("""
        SELECT id, ticker, regime, direction, exit_type,
               ROUND(size_usd, 0) sz, ROUND(pnl_usd, 2) pnl,
               ROUND(hold_seconds, 0) h, exit_ts
        FROM trades
        WHERE strategy_id = ? AND status = 'closed'
          AND exit_ts >= strftime('%s', 'now', '-24 hours')
        ORDER BY exit_ts DESC LIMIT 15
    """, (strategy_id,)).fetchall()

    regime_dist = conn.execute("""
        SELECT regime, COUNT(*) n, ROUND(SUM(pnl_usd), 2) sum_pnl,
               ROUND(AVG(pnl_usd), 3) avg_pnl
        FROM trades
        WHERE strategy_id = ? AND status = 'closed'
          AND exit_ts >= strftime('%s', 'now', '-7 days')
          AND regime IS NOT NULL
        GROUP BY regime ORDER BY n DESC
    """, (strategy_id,)).fetchall()

    exit_dist = conn.execute("""
        SELECT exit_type, COUNT(*) n, ROUND(SUM(pnl_usd), 2) sum_pnl
        FROM trades
        WHERE strategy_id = ? AND status = 'closed'
          AND exit_ts >= strftime('%s', 'now', '-7 days')
        GROUP BY exit_type ORDER BY n DESC
    """, (strategy_id,)).fetchall()

    return {
        "recent_trades": trades,
        "regime_dist_7d": regime_dist,
        "exit_dist_7d": exit_dist,
    }


def _ticker_lineage(conn: sqlite3.Connection, ticker: str) -> dict:
    """Recent trades + signal/exit lineage for a ticker (last 24h)."""
    trades = conn.execute("""
        SELECT id, strategy_id, regime, direction, exit_type,
               ROUND(size_usd, 0) sz, ROUND(pnl_usd, 2) pnl,
               ROUND(hold_seconds, 0) h, exit_ts
        FROM trades
        WHERE ticker = ? AND status = 'closed'
          AND exit_ts >= strftime('%s', 'now', '-24 hours')
        ORDER BY exit_ts DESC LIMIT 20
    """, (ticker,)).fetchall()

    exit_dist = conn.execute("""
        SELECT exit_type, COUNT(*) n, ROUND(SUM(pnl_usd), 2) sum_pnl
        FROM trades
        WHERE ticker = ? AND status = 'closed'
          AND exit_ts >= strftime('%s', 'now', '-7 days')
        GROUP BY exit_type ORDER BY n DESC
    """, (ticker,)).fetchall()

    return {"recent_trades": trades, "exit_dist_7d": exit_dist}


def write_by_ticker(conn: sqlite3.Connection, wm: dict) -> None:
    """One md per ticker — canonical at `vault/03_tickers/<symbol>.md`."""
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM strategy_cell_matrix WHERE ticker IS NOT NULL"
    ).fetchall()]

    out_dir = VAULT_TICKERS
    out_dir.mkdir(parents=True, exist_ok=True)

    for ticker in tickers:
        rows = conn.execute("""
            SELECT strategy_id, exchange, asset_group, regime, direction,
                   score, n_trades, wr, avg_pnl
            FROM strategy_cell_matrix
            WHERE ticker = ?
            ORDER BY score DESC
        """, (ticker,)).fetchall()

        total_n = sum(r["n_trades"] or 0 for r in rows)
        strategies = sorted({r["strategy_id"] for r in rows if r["strategy_id"]})

        md = build_frontmatter(_common_frontmatter(
            "ticker", ticker, wm,
            cell_count=len(rows),
            total_trades=total_n,
            relations={
                "traded_by_strategies": strategies[:30],
            },
            sources=["sqlite:strategy_cell_matrix"],
        ))
        md += f"\n# Ticker: `{ticker}`\n\n"
        md += f"> {len(rows)} cells across {len(strategies)} strategies, total_trades={total_n}\n\n"
        md += f"**Strategies**: {', '.join(f'[[{s}]]' for s in strategies[:20])}"
        if len(strategies) > 20:
            md += f" ... +{len(strategies)-20}"
        md += "\n\n## Cells (score DESC)\n\n"

        if rows:
            cols = list(rows[0].keys())
            md += markdown_table(rows, cols)

        # Lineage section — recent trades + exit distribution
        lin = _ticker_lineage(conn, ticker)
        md += "\n## Lineage — Recent Trades (24h)\n\n"
        if lin["recent_trades"]:
            for t in lin["recent_trades"][:10]:
                strategy_link = f"[[{t['strategy_id']}]]" if t['strategy_id'] else "?"
                regime_link = f"[[{t['regime']}]]" if t['regime'] else "?"
                exit_link = f"[[{t['exit_type']}]]" if t['exit_type'] else "?"
                md += (
                    f"- `{t['id']}` — {strategy_link} / {regime_link} / "
                    f"{t['direction']} → {exit_link} / "
                    f"size ${t['sz']:.0f} / pnl ${t['pnl']:.2f} / hold {t['h']:.0f}s\n"
                )
        else:
            md += "_(no trades in 24h)_\n"

        md += "\n## Exit Type Distribution (7d)\n\n"
        if lin["exit_dist_7d"]:
            md += "| Exit Type | N | Sum PnL |\n|---|---|---|\n"
            for e in lin["exit_dist_7d"]:
                if e["exit_type"]:
                    md += f"| [[{e['exit_type']}]] | {e['n']} | ${e['sum_pnl']:.2f} |\n"

        write_atomic(out_dir / f"{safe_filename(ticker)}.md", md)


def write_by_regime(conn: sqlite3.Connection, wm: dict) -> None:
    """One md per regime — canonical at `vault/70_regimes/<name>.md`."""
    regimes = [r[0] for r in conn.execute(
        "SELECT DISTINCT regime FROM strategy_cell_matrix WHERE regime IS NOT NULL"
    ).fetchall()]

    out_dir = VAULT_REGIMES
    out_dir.mkdir(parents=True, exist_ok=True)

    for regime in regimes:
        rows = conn.execute("""
            SELECT strategy_id, ticker, exchange, asset_group, direction,
                   score, n_trades, wr, avg_pnl
            FROM strategy_cell_matrix
            WHERE regime = ?
            ORDER BY score DESC LIMIT 100
        """, (regime,)).fetchall()

        total_n = sum(r["n_trades"] or 0 for r in rows)
        strategies = sorted({r["strategy_id"] for r in rows if r["strategy_id"]})

        md = build_frontmatter(_common_frontmatter(
            "regime", regime, wm,
            cell_count=len(rows),
            total_trades=total_n,
            relations={
                "active_strategies": strategies[:30],
            },
            sources=["sqlite:strategy_cell_matrix"],
        ))
        md += f"\n# Regime: `{regime}`\n\n"
        md += f"> Top 100 cells (score DESC), {len(strategies)} strategies, total_trades={total_n}\n\n"
        md += f"**Active Strategies**: {', '.join(f'[[{s}]]' for s in strategies[:20])}"
        if len(strategies) > 20:
            md += f" ... +{len(strategies)-20}"
        md += "\n\n## Top Cells\n\n"

        if rows:
            cols = list(rows[0].keys())
            md += markdown_table(rows, cols)

        write_atomic(out_dir / f"{safe_filename(regime)}.md", md)


def write_index(conn: sqlite3.Connection, wm: dict) -> None:
    """`vault/50_cells/_index.md` — Dataview rollup hub."""
    md = build_frontmatter({
        "entity_type": "index",
        "entity_id": "cells_index",
        "auto": True,
        "do_not_edit": True,
        "last_synced": now_utc(),
        "watermark": wm,
        "tags": ["cell", "index"],
    })
    md += "\n# Cells Index\n\n"
    md += f"> {wm['cells_count']} cells, avg_score={wm['cells_avg_score']:.3f}, open_trades={wm['trades_open']}\n"
    md += f"> Latest trade exit_ts: {wm['trades_max_exit_ts']}\n\n"

    md += "## Top 20 Winners (by score)\n\n"
    md += "```dataview\n"
    md += "TABLE entity_id as Strategy, total_trades, avg_wr, avg_score\n"
    md += 'FROM "50_cells/by_strategy"\n'
    md += "SORT avg_score DESC\n"
    md += "LIMIT 20\n"
    md += "```\n\n"

    md += "## All Tickers\n\n"
    md += "```dataview\n"
    md += "TABLE entity_id as Ticker, cell_count, total_trades\n"
    md += 'FROM "50_cells/by_ticker"\n'
    md += "SORT total_trades DESC\n"
    md += "LIMIT 30\n"
    md += "```\n\n"

    md += "## Regimes\n\n"
    md += "```dataview\n"
    md += "TABLE entity_id as Regime, cell_count, total_trades\n"
    md += 'FROM "50_cells/by_regime"\n'
    md += "```\n\n"

    md += "## Live Queries (sqlite MCP)\n\n"
    md += "- Demote 자격 (cum_pnl_24h ≤ -$30): see `[[_meta/sqlite_mcp_query_cookbook#Q4]]`\n"
    md += "- ITEM-145 패턴 (large size × max_hold): see `[[_meta/sqlite_mcp_query_cookbook#Q3]]`\n"
    md += "- Exit type distribution: see `[[_meta/sqlite_mcp_query_cookbook#Q6]]`\n"

    write_atomic(VAULT_CELLS / "_index.md", md)


def write_exit_patterns(conn: sqlite3.Connection, wm: dict) -> None:
    """Exit pattern entity files — canonical at `vault/60_exit_patterns/<type>.md`.

    Aggregates trades by exit_type (last 7d) — TIME/TRAIL/STOP/BEP/TP/MAX_HOLD.
    """
    rows = conn.execute("""
        SELECT exit_type,
               COUNT(*) cnt,
               ROUND(SUM(pnl_usd), 2) sum_pnl,
               ROUND(AVG(pnl_usd), 3) avg_pnl,
               ROUND(AVG(CASE WHEN pnl_usd > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) wr,
               ROUND(MIN(pnl_usd), 2) min_pnl,
               ROUND(MAX(pnl_usd), 2) max_pnl
        FROM trades
        WHERE status = 'closed' AND exit_ts >= strftime('%s','now','-7 days')
          AND exit_type IS NOT NULL
        GROUP BY exit_type
        ORDER BY cnt DESC
    """).fetchall()

    out_dir = VAULT_EXIT_PATTERNS
    out_dir.mkdir(parents=True, exist_ok=True)

    all_types = []
    for r in rows:
        et = r["exit_type"]
        if not et:
            continue
        all_types.append(et)

        # Top losers/winners for this exit_type
        losers = conn.execute("""
            SELECT ticker, strategy_id, ROUND(size_usd,0) sz, ROUND(pnl_usd,2) pnl, hold_seconds h
            FROM trades
            WHERE exit_type = ? AND status='closed' AND exit_ts >= strftime('%s','now','-7 days')
            ORDER BY pnl_usd ASC LIMIT 10
        """, (et,)).fetchall()
        winners = conn.execute("""
            SELECT ticker, strategy_id, ROUND(size_usd,0) sz, ROUND(pnl_usd,2) pnl, hold_seconds h
            FROM trades
            WHERE exit_type = ? AND status='closed' AND exit_ts >= strftime('%s','now','-7 days')
            ORDER BY pnl_usd DESC LIMIT 10
        """, (et,)).fetchall()

        # Top tickers using this exit type
        top_tickers = conn.execute("""
            SELECT ticker, COUNT(*) n, ROUND(SUM(pnl_usd), 2) sum_pnl
            FROM trades
            WHERE exit_type = ? AND status='closed' AND exit_ts >= strftime('%s','now','-7 days')
            GROUP BY ticker ORDER BY n DESC LIMIT 15
        """, (et,)).fetchall()

        md = build_frontmatter(_common_frontmatter(
            "exit_pattern", et, wm,
            count_7d=r["cnt"],
            sum_pnl_7d=r["sum_pnl"],
            avg_pnl_7d=r["avg_pnl"],
            wr_7d=r["wr"],
            min_pnl=r["min_pnl"],
            max_pnl=r["max_pnl"],
            relations={
                "top_loser_tickers": list({l["ticker"] for l in losers if l["ticker"]}),
                "top_winner_tickers": list({w["ticker"] for w in winners if w["ticker"]}),
                "top_n_tickers": list({t["ticker"] for t in top_tickers if t["ticker"]}),
            },
            sources=["sqlite:trades"],
        ))
        md += f"\n# Exit Pattern: `{et}`\n\n"
        md += f"> 7d: {r['cnt']} trades, sum=${r['sum_pnl']:.2f}, avg=${r['avg_pnl']:.3f}, WR={r['wr']:.1f}%\n"
        md += f"> Range: [{r['min_pnl']:.2f}, {r['max_pnl']:.2f}]\n\n"

        md += "## Top 10 Losers (7d)\n\n"
        if losers:
            md += markdown_table(losers, list(losers[0].keys()))
        md += "\n## Top 10 Winners (7d)\n\n"
        if winners:
            md += markdown_table(winners, list(winners[0].keys()))
        md += "\n## Top Tickers (count)\n\n"
        if top_tickers:
            md += "| Ticker | N | Sum PnL |\n|---|---|---|\n"
            for t in top_tickers:
                md += f"| [[{t['ticker']}]] | {t['n']} | ${t['sum_pnl']:.2f} |\n"

        write_atomic(out_dir / f"{safe_filename(et)}.md", md)

    # Index
    md = build_frontmatter({
        "entity_type": "index",
        "entity_id": "exit_patterns_index",
        "auto": True,
        "do_not_edit": True,
        "last_synced": now_utc(),
        "watermark": wm,
        "tags": ["exit_pattern", "index"],
    })
    md += "\n# Exit Patterns Index (7d)\n\n"
    md += "```dataview\n"
    md += "TABLE count_7d, sum_pnl_7d, avg_pnl_7d, wr_7d\n"
    md += 'FROM "60_exit_patterns"\n'
    md += "WHERE entity_type = \"exit_pattern\"\n"
    md += "SORT count_7d DESC\n"
    md += "```\n\n"
    md += "## Patterns\n\n"
    for et in all_types:
        md += f"- [[{et}]]\n"
    write_atomic(out_dir / "_index.md", md)


def sync_db() -> dict:
    """Main entry — full DB sync.

    Returns watermark + summary stats.
    """
    if not DB.exists():
        raise FileNotFoundError(f"DB not found: {DB}")

    conn = open_ro_consistent()
    try:
        wm = get_watermark(conn)
        write_snapshot_latest(conn, wm)
        write_by_strategy(conn, wm)        # → vault/02_strategies/
        write_by_ticker(conn, wm)           # → vault/03_tickers/
        write_by_regime(conn, wm)           # → vault/70_regimes/
        write_exit_patterns(conn, wm)       # → vault/60_exit_patterns/ (NEW)
        write_index(conn, wm)
    finally:
        conn.close()

    return wm


if __name__ == "__main__":
    import time
    start = time.time()
    wm = sync_db()
    elapsed = time.time() - start
    print(f"[db_views] sync complete @ {now_utc()}")
    print(f"  cells={wm['cells_count']}, avg_score={wm['cells_avg_score']:.3f}")
    print(f"  open_trades={wm['trades_open']}, latest_exit={wm['trades_max_exit_ts']}")
    print(f"  elapsed={elapsed:.2f}s")
