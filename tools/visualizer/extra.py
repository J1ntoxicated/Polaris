"""Extra endpoints — operations + intel data."""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DB = ROOT / "data" / "invasion.sqlite"
LOG = ROOT / "data" / "invasion.log"
ALERTS = ROOT / ".claude" / "harness_alerts"


def open_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def operations() -> dict:
    conn = open_ro()

    # Open per exchange
    open_per_exchange = {}
    for r in conn.execute("SELECT exchange, COUNT(*) n FROM trades WHERE status='open' GROUP BY exchange").fetchall():
        open_per_exchange[r["exchange"] or "unknown"] = r["n"]

    # 1h exit dist
    exit_dist_1h = []
    for r in conn.execute("""
        SELECT exit_type, COUNT(*) n, ROUND(SUM(pnl_usd),2) pnl
        FROM trades WHERE status='closed' AND exit_ts >= strftime('%s','now','-1 hour')
        GROUP BY exit_type ORDER BY n DESC
    """).fetchall():
        exit_dist_1h.append({"exit_type": r["exit_type"] or "?", "n": r["n"], "pnl": r["pnl"] or 0})

    # 24h pnl
    pnl_24h = conn.execute(
        "SELECT ROUND(COALESCE(SUM(pnl_usd),0),2) FROM trades WHERE status='closed' AND exit_ts >= strftime('%s','now','-24 hours')"
    ).fetchone()[0] or 0

    # T13 wire fires (recent log)
    t13_fires = []
    if LOG.exists():
        with LOG.open("rb") as f:
            f.seek(max(0, LOG.stat().st_size - 100_000))
            chunk = f.read().decode("utf-8", errors="ignore")
        for m in re.finditer(r"(\d{2}:\d{2}:\d{2}).*(SIZE_CAP(?:_FSM)?|DEMOTE_LOSS|SKIP_DEMOTED) (\w[\w/]+)", chunk):
            t13_fires.append({"time": m.group(1), "type": m.group(2), "ticker": m.group(3)})
        t13_fires = list(reversed(t13_fires))[:20]

    conn.close()
    return {
        "open_per_exchange": open_per_exchange,
        "exit_dist_1h": exit_dist_1h,
        "pnl_24h": pnl_24h,
        "t13_fires": t13_fires,
    }


def intel() -> dict:
    conn = open_ro()

    # Top cells (24h activity, score DESC)
    top_cells = []
    for r in conn.execute("""
        SELECT strategy_id, ticker, regime, direction, score, wr, n_trades
        FROM strategy_cell_matrix
        WHERE n_trades >= 3
        ORDER BY ABS(score) DESC LIMIT 30
    """).fetchall():
        top_cells.append({
            "strategy_id": r["strategy_id"], "ticker": r["ticker"],
            "regime": r["regime"], "direction": r["direction"],
            "score": r["score"] or 0, "wr": r["wr"] or 0, "n_trades": r["n_trades"]
        })

    # Regime × group distribution (24h)
    regime_dist = []
    for r in conn.execute("""
        SELECT regime, asset_group, COUNT(*) n,
               ROUND(AVG(CASE WHEN pnl_usd > 0 THEN 1.0 ELSE 0 END), 3) wr,
               ROUND(SUM(pnl_usd), 2) pnl
        FROM trades WHERE status='closed' AND exit_ts >= strftime('%s','now','-24 hours')
        GROUP BY regime, asset_group HAVING n >= 5
        ORDER BY pnl DESC LIMIT 25
    """).fetchall():
        regime_dist.append({
            "regime": r["regime"] or "?", "asset_group": r["asset_group"] or "?",
            "n": r["n"], "wr": r["wr"] or 0, "pnl": r["pnl"] or 0
        })

    # Top strategies
    top_strategies = []
    for r in conn.execute("""
        SELECT s.id strategy_id, s.status, COUNT(t.id) n,
               ROUND(COALESCE(SUM(t.pnl_usd), 0), 2) pnl
        FROM strategies s
        LEFT JOIN trades t ON s.id = t.strategy_id
          AND t.status='closed' AND t.exit_ts >= strftime('%s','now','-24 hours')
        GROUP BY s.id ORDER BY n DESC LIMIT 30
    """).fetchall():
        top_strategies.append({
            "strategy_id": r["strategy_id"], "status": r["status"],
            "n": r["n"], "pnl": r["pnl"] or 0
        })

    conn.close()

    # Recent alerts (last 20)
    alerts = []
    if ALERTS.exists():
        files = sorted(ALERTS.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:20]
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
                cat_m = re.search(r"^category:\s*(\S+)", text, re.MULTILINE)
                sev_m = re.search(r"^severity:\s*(\S+)", text, re.MULTILINE)
                ts_m = re.search(r"^ts_iso:\s*(\S+)", text, re.MULTILINE)
                # Body = first non-empty line after frontmatter
                body_m = re.search(r"^---\n.*?\n---\n+(.+)", text, re.DOTALL)
                body = (body_m.group(1).strip().split("\n")[0] if body_m else "")[:120]
                alerts.append({
                    "category": cat_m.group(1) if cat_m else "?",
                    "severity": sev_m.group(1) if sev_m else "?",
                    "time": (ts_m.group(1)[-8:] if ts_m else ""),
                    "body": body,
                })
            except Exception:
                pass

    return {
        "top_cells": top_cells,
        "regime_dist": regime_dist,
        "top_strategies": top_strategies,
        "alerts": alerts,
    }
