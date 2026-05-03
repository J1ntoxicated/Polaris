"""30-minute unified cron job (Jin 2026-04-27).

모든 30분 주기 작업을 단일 entry 로 묶음. crontab 단일화 → drift 방지.

작업:
1. Vault DB sync (cell snapshot, strategy_cell_matrix → vault md)
2. Visualizer snapshot (graph.json refresh)
3. Bot health check (PID + log freshness, log 만 — restart 안 함)

NOTE: market_context (scripts/update_market_context.py) 는 file 없음 —
기존 crontab entry 가 항상 fail 중이었음. 본 통합에서 제거.

Daily archive (3 3 * * *) 는 별도 유지.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG = ROOT / "data" / "cron_30m.log"
PYTHON = "/opt/homebrew/bin/python3"

# Hold-time buckets for loser positions (label, upper-bound hours).
# Last bucket uses inf as catch-all.
HOLD_BUCKETS: tuple[tuple[str, float], ...] = (
    ("<30m", 0.5),
    ("30m-2h", 2.0),
    ("2h-12h", 12.0),
    ("12-24h", 24.0),
    ("24h+", float("inf")),
)

JSONL_BLOAT_THRESHOLD_MB = 100
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MED": 2, "LOW": 3}


def _log_append(text: str) -> None:
    """Append a single block of text to the unified log."""
    with LOG.open("a") as f:
        f.write(text)


def run(label: str, cmd: list[str], timeout: int = 600) -> bool:
    """Run a subprocess; capture stdout/stderr to unified log."""
    ts = datetime.datetime.now().isoformat()
    try:
        result = subprocess.run(
            cmd, cwd=ROOT, timeout=timeout,
            capture_output=True, text=True, check=False,
        )
    except subprocess.TimeoutExpired:
        _log_append(f"\n=== {ts} {label} TIMEOUT ({timeout}s) ===\n")
        return False
    except Exception as e:
        _log_append(f"\n=== {ts} {label} EXCEPTION: {e} ===\n")
        return False

    parts = [f"\n=== {ts} {label} (rc={result.returncode}) ===\n"]
    if result.stdout:
        parts.append(result.stdout[-2000:])
    if result.stderr:
        parts.append("STDERR: " + result.stderr[-1000:])
    _log_append("".join(parts))
    return result.returncode == 0


def health_check() -> str:
    """Bot PID alive + log freshness probe (read-only, no restart)."""
    ts = datetime.datetime.now().isoformat()
    out = [f"=== {ts} health_check ==="]

    try:
        ps = subprocess.run(
            ["pgrep", "-f", "invasion --headless"],
            capture_output=True, text=True, timeout=10,
        )
        out.append(f"bot_pid: {ps.stdout.strip() or 'NONE'}")
    except Exception as e:
        out.append(f"bot_pid_check_fail: {e}")

    log_path = ROOT / "data" / "invasion.log"
    if log_path.exists():
        age = datetime.datetime.now().timestamp() - log_path.stat().st_mtime
        out.append(f"invasion_log_age_sec: {age:.0f}")
    return "\n".join(out)


def _hold_bucket(hours: float) -> str:
    """Return the hold-time bucket label for the given hour count."""
    for label, upper in HOLD_BUCKETS:
        if hours < upper:
            return label
    return HOLD_BUCKETS[-1][0]


def open_position_loss_attribution() -> str:
    """Open position 의 unrealized loss 심화 분석 (Jin 2026-04-27).

    portfolio_state.json (live unrealized) 읽어서 cell pattern 추출.
    매 30m 자동 — Jin "오픈 포지션 로스 심화 항상" 요청.
    """
    pf = ROOT / "data" / "portfolio_state.json"
    if not pf.exists():
        return ""

    try:
        with pf.open() as f:
            ps = json.load(f)
        positions = list((ps.get("positions") or {}).values())
        if not positions:
            return ""

        for p in positions:
            pnl_pct = p.get("pnl_pct") or 0
            size = p.get("size_usd") or 0
            p["_pnl_usd"] = (pnl_pct / 100.0) * size

        losers = [p for p in positions if p["_pnl_usd"] < -0.5]
        winners = [p for p in positions if p["_pnl_usd"] > 0.5]
        loss_total = sum(p["_pnl_usd"] for p in losers)
        win_total = sum(p["_pnl_usd"] for p in winners)
        net = loss_total + win_total
        flat = len(positions) - len(losers) - len(winners)

        out = [
            "=== Open position loss attribution ===",
            f"  Total: {len(positions)}  Losers: {len(losers)}  Winners: {len(winners)}  Flat: {flat}",
            f"  Unrealized: loss ${loss_total:.2f}  win ${win_total:.2f}  NET ${net:+.2f}",
        ]

        # Cell aggregate (loser positions grouped by exchange/group/direction/strategy_root)
        by_cell: dict[tuple[str, str, str, str], dict] = defaultdict(
            lambda: {"n": 0, "loss": 0.0, "tickers": []}
        )
        for p in losers:
            sid = p.get("strategy_id") or ""
            root = "_".join(sid.split("_")[:2]) if sid else "unknown"
            key = (
                p.get("exchange", "?"),
                p.get("asset_group", "?"),
                p.get("direction", "?"),
                root,
            )
            cell = by_cell[key]
            cell["n"] += 1
            cell["loss"] += p["_pnl_usd"]
            cell["tickers"].append(p.get("ticker"))

        if by_cell:
            out.append("  Loser cells (top 5):")
            top_cells = sorted(by_cell.items(), key=lambda x: x[1]["loss"])[:5]
            for key, v in top_cells:
                tk = ",".join((v["tickers"] or [])[:3])
                out.append(
                    f"    {key[0]:<5} {key[1]:<11} {key[2]:<6} {key[3]:<26} "
                    f"n={v['n']:<2} ${v['loss']:>+7.2f}  [{tk}]"
                )

        # Hold-time distribution for loser positions
        buckets: dict[str, int] = {label: 0 for label, _ in HOLD_BUCKETS}
        for p in losers:
            hours = (p.get("age_s") or 0) / 3600
            buckets[_hold_bucket(hours)] += 1
        out.append("  Loser hold: " + " / ".join(f"{k}={v}" for k, v in buckets.items()))
    except Exception as e:
        return f"=== Open position loss attribution ERROR: {e} ==="

    return "\n".join(out)


def jsonl_bloat_check() -> str:
    """JSONL bloat watcher (Jin 2026-04-27 INSIGHT-015): 100MB+ files → warn.

    Rotation 자동은 안 함 (bot 가 file handle 살아있으면 sparse 위험).
    Dev-coder dispatch 권고 — dual write 코드 제거가 근본 fix.
    """
    data_dir = ROOT / "data"
    if not data_dir.exists():
        return ""

    bloat = []
    for f in data_dir.glob("*.jsonl"):
        size_mb = f.stat().st_size / 1e6
        if size_mb > JSONL_BLOAT_THRESHOLD_MB:
            bloat.append((f.name, size_mb))
    if not bloat:
        return ""

    bloat.sort(key=lambda x: -x[1])
    out = ["=== JSONL bloat WARN (>100MB) ==="]
    for name, size in bloat:
        out.append(f"  {size:>6.0f}MB  {name}")
    out.append("→ INSIGHT-015 권고: dual-write 코드 제거 + sqlite single SSOT")
    return "\n".join(out)


def vault_active_insights() -> str:
    """Active INSIGHT (status: open) 자동 surface — Harness 와 통합 (Jin 2026-04-27).

    매 30m cron tick 마다 vault 의 open INSIGHT list → digest 에 부착.
    다음 세션 Claude / Harness 가 즉시 actionable items 파악.
    """
    insight_dir = ROOT / "vault" / "03_knowledge" / "insights"
    if not insight_dir.exists():
        return ""

    open_insights: list[tuple[str, str]] = []
    for f in sorted(insight_dir.glob("INSIGHT-*.md"), reverse=True):
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        head = content[:500]
        if "status: open" not in head:
            continue
        sev = "?"
        for line in head.split("\n"):
            if line.startswith("severity:"):
                sev = line.split(":", 1)[1].strip()
                break
        open_insights.append((sev, f.stem))

    if not open_insights:
        return ""

    open_insights.sort(key=lambda x: SEVERITY_ORDER.get(x[0], 99))
    out = ["=== Active INSIGHTs (status=open) ==="]
    for sev, name in open_insights[:10]:
        out.append(f"  [{sev:<8}] {name}")
    out.append(f"  Total open: {len(open_insights)}")
    return "\n".join(out)


# ── Wave Effect / Block Paradigm / Cell Learning monitoring (Jin 2026-04-28) ──
# 5 sections — Wave 1-7 deploy 효과 자동 measurement.
# Block 0: read-only SQL/log scan, no mutation.
# Vault refs: [[INSIGHT-024]] [[INSIGHT-025]] [[INSIGHT-026]] cron 30m unified.

DB_PATH = ROOT / "data" / "invasion.sqlite"

# 6 trend strategies (Wave 7) — Spec mandate.
TREND_STRATEGIES: tuple[str, ...] = (
    "crypto_momentum_reversal_g1_gauss",
    "contrarian_commodity_g55_gauss",
    "contrarian_commodity_g54_ai",
    "contrarian_commodity_g1_bayes",
    "contrarian_commodity_g53_ai",
    "contrarian_commodity_g57_bayes",
)


def _open_db() -> sqlite3.Connection | None:
    """Open invasion.sqlite read-only style; return None on failure."""
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        conn.execute("PRAGMA query_only = ON")
        return conn
    except Exception:
        return None


def _snapshot_wave_effect(conn: sqlite3.Connection) -> str:
    """Section A — Wave 1-7 효과 measurement (24h commodity, 7d Pd short, 1h NET, 6 trend trade_count)."""
    lines = ["=== Wave Effect Snapshot ==="]
    try:
        cur = conn.execute(
            "SELECT COUNT(*), ROUND(COALESCE(SUM(pnl_usd),0),2), "
            "ROUND(COALESCE(AVG(pnl_usd),0),2), "
            "ROUND(COALESCE(AVG(CASE WHEN pnl_usd>0 THEN 1.0 ELSE 0.0 END),0)*100,1) "
            "FROM trades WHERE asset_group='commodity' AND status='closed' "
            "AND exit_ts >= strftime('%s','now')-86400"
        )
        n, pnl, avg, wr = cur.fetchone()
        lines.append(f"24h commodity: n={n} NET={pnl} avg={avg} WR={wr}%")
    except Exception as e:
        lines.append(f"24h commodity ERR: {e}")

    try:
        cur = conn.execute(
            "SELECT COUNT(*), ROUND(COALESCE(SUM(pnl_usd),0),2), "
            "ROUND(COALESCE(AVG(CASE WHEN pnl_usd>0 THEN 1.0 ELSE 0.0 END),0)*100,1) "
            "FROM trades WHERE ticker='Palladium' AND direction='short' "
            "AND status='closed' AND exit_ts >= strftime('%s','now')-604800"
        )
        n, pnl, wr = cur.fetchone()
        lines.append(f"7d Palladium short: n={n} NET={pnl} WR={wr}%")
    except Exception as e:
        lines.append(f"7d Palladium short ERR: {e}")

    try:
        cur = conn.execute(
            "SELECT COUNT(*), ROUND(COALESCE(SUM(pnl_usd),0),2), "
            "ROUND(COALESCE(AVG(CASE WHEN pnl_usd>0 THEN 1.0 ELSE 0.0 END),0)*100,1) "
            "FROM trades WHERE status='closed' "
            "AND exit_ts >= strftime('%s','now')-3600"
        )
        n, pnl, wr = cur.fetchone()
        lines.append(f"1h NET: n={n} pnl={pnl} WR={wr}%")
    except Exception as e:
        lines.append(f"1h NET ERR: {e}")

    try:
        placeholders = ",".join("?" * len(TREND_STRATEGIES))
        cur = conn.execute(
            f"SELECT name, trade_count FROM strategies WHERE name IN ({placeholders})",
            TREND_STRATEGIES,
        )
        rows = cur.fetchall()
        if rows:
            counts = ", ".join(
                f"{name.split('_')[-2]}_{name.split('_')[-1]}={tc}"
                for name, tc in rows
            )
            lines.append(f"6 trend strategies trade_count: {counts}")
        else:
            lines.append("6 trend strategies: none found")
    except Exception as e:
        lines.append(f"6 trend strategies ERR: {e}")

    return "\n".join(lines)


def _snapshot_strategies_count(conn: sqlite3.Connection) -> str:
    """Section B — Strategies count by status + by group (active)."""
    lines = ["=== Strategies Count Trend ==="]
    try:
        cur = conn.execute("SELECT status, COUNT(*) FROM strategies GROUP BY status")
        by_status = dict(cur.fetchall())
        total = sum(by_status.values())
        lines.append(
            f"Total: {total} | active: {by_status.get('active', 0)} | "
            f"disabled: {by_status.get('disabled', 0)} | "
            f"retired: {by_status.get('retired', 0)}"
        )
    except Exception as e:
        lines.append(f"by_status ERR: {e}")

    try:
        cur = conn.execute(
            "SELECT match_groups, COUNT(*) FROM strategies "
            "WHERE status='active' GROUP BY match_groups"
        )
        group_counts: dict[str, int] = {}
        for mg, n in cur.fetchall():
            try:
                groups = json.loads(mg or "[]")
                if groups:
                    primary = groups[0]
                    group_counts[primary] = group_counts.get(primary, 0) + n
            except Exception:
                continue
        if group_counts:
            gs = " / ".join(f"{g}={n}" for g, n in sorted(group_counts.items()))
            lines.append(f"By group (active): {gs}")
    except Exception as e:
        lines.append(f"by_group ERR: {e}")

    return "\n".join(lines)


def _snapshot_cell_learning(conn: sqlite3.Connection) -> str:
    """Section C — Cell learning progression (Wave 5C 4 컬럼 timeline)."""
    lines = ["=== Cell Learning Progression (Wave 5C) ==="]
    try:
        cur = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN optimal_max_hold_sec IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN optimal_trail_activate IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN optimal_bep_activate IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN optimal_hard_stop_pct IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM strategy_cell_matrix"
        )
        total, mh, tr, bep, st = cur.fetchone()
        if total:
            lines.append(f"Total: {total}")
            lines.append(f"  max_hold: {mh} ({100 * mh / total:.0f}%)")
            lines.append(f"  trail:    {tr} ({100 * tr / total:.0f}%)")
            lines.append(f"  bep:      {bep} ({100 * bep / total:.0f}%)")
            lines.append(f"  stop:     {st} ({100 * st / total:.0f}%)")
        else:
            lines.append("Total: 0 (cell matrix empty)")
    except Exception as e:
        lines.append(f"cell learning ERR: {e}")

    return "\n".join(lines)


def _snapshot_block_paradigm() -> str:
    """Section D — Block paradigm 0 verify (last ~500KB invasion.log scan)."""
    log_path = ROOT / "data" / "invasion.log"
    if not log_path.exists():
        return "=== Block Paradigm 0 Verify ===\n(log missing)"

    try:
        with log_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 500_000))
            tail = f.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return f"=== Block Paradigm 0 Verify ===\n(log read err: {e})"

    counts = {
        "Tournament rounds": len(re.findall(r"TOURNAMENT.*Round #", tail)),
        "ENDANGERED": len(re.findall(r"ENDANGERED", tail)),
        "ELIMINATED_LOG": len(re.findall(r"ELIMINATED_LOG", tail)),
        "FITNESS_LOW_LOG": len(re.findall(r"FITNESS_LOW_LOG", tail)),
        "STOP_WR_LOW_LOG": len(re.findall(r"STOP_WR_LOW_LOG", tail)),
        "PROTECTED": len(re.findall(r"PROTECTED \(last active", tail)),
        "pruning DEPRECATED": len(re.findall(r"pruning DEPRECATED", tail)),
    }
    lines = ["=== Block Paradigm 0 Verify (last ~500KB log) ==="]
    for k, v in counts.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _snapshot_strategy_fitness(conn: sqlite3.Connection) -> str:
    """Section E — Per-strategy fitness Top/Bottom 5 + jin_review_flag rows."""
    lines = ["=== Per-Strategy Fitness Top/Bottom 5 ==="]
    try:
        cur = conn.execute(
            "SELECT name, fitness, trade_count FROM strategies "
            "WHERE status='active' AND trade_count >= 10 "
            "ORDER BY fitness DESC LIMIT 5"
        )
        lines.append("Top 5:")
        rows = cur.fetchall()
        if not rows:
            lines.append("  (none — trade_count<10 across all active)")
        for name, fit, tc in rows:
            fit_s = f"{fit:.1f}" if fit is not None else "-"
            lines.append(f"  {name}: fit={fit_s} trades={tc}")

        cur = conn.execute(
            "SELECT name, fitness, trade_count FROM strategies "
            "WHERE status='active' AND trade_count >= 10 "
            "ORDER BY fitness ASC LIMIT 5"
        )
        lines.append("Bottom 5:")
        rows = cur.fetchall()
        if not rows:
            lines.append("  (none)")
        for name, fit, tc in rows:
            fit_s = f"{fit:.1f}" if fit is not None else "-"
            lines.append(f"  {name}: fit={fit_s} trades={tc}")

        cur = conn.execute(
            "SELECT name, fitness, trade_count FROM strategies "
            "WHERE jin_review_flag=1 ORDER BY trade_count DESC"
        )
        rows = cur.fetchall()
        if rows:
            lines.append("jin_review_flag=1:")
            for name, fit, tc in rows:
                fit_s = f"{fit:.1f}" if fit is not None else "-"
                lines.append(f"  {name}: fit={fit_s} trades={tc}")
    except Exception as e:
        lines.append(f"strategy fitness ERR: {e}")

    return "\n".join(lines)


def wave_monitoring_block() -> str:
    """5-section composite — Wave effect + count + cell learning + block paradigm + fitness.

    Returns one combined log block; empty string when DB missing.
    """
    conn = _open_db()
    sections: list[str] = []
    if conn is not None:
        try:
            sections.append(_snapshot_wave_effect(conn))
            sections.append(_snapshot_strategies_count(conn))
            sections.append(_snapshot_cell_learning(conn))
        finally:
            try:
                conn.close()
            except Exception:
                pass
    sections.append(_snapshot_block_paradigm())
    if conn is not None:
        conn2 = _open_db()
        if conn2 is not None:
            try:
                sections.append(_snapshot_strategy_fitness(conn2))
            finally:
                try:
                    conn2.close()
                except Exception:
                    pass
    return "\n\n".join(s for s in sections if s)


def vault_digest_append(
    loss_attr: str,
    bloat: str,
    health: str,
    active_insights: str | None = None,
    wave_block: str | None = None,
) -> None:
    """Cron tick 결과를 vault digest 에 append (Jin 2026-04-27 효과적 vault 사용).

    매 30m fire 마다 vault/04_ops/digests/cron-{date}.md 에 short section append.
    다음 세션 Claude 가 vault read 만으로 cron history 재구성 가능.

    `active_insights` 가 None 이면 디스크에서 다시 읽음 (backward-compat).
    """
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    ts = now.strftime("%H:%M")

    digest = ROOT / "vault" / "04_ops" / "digests" / f"cron-{today}.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    if not digest.exists():
        digest.write_text(
            f"---\nentity_type: digest\nentity_id: cron_{today.replace('-', '_')}\n"
            f"auto: true\ndo_not_edit: true\ndate: {today}\n"
            f"tags: [cron, digest, auto, vault_native]\n---\n\n"
            f"# Cron unified ticks {today}\n\n"
            f"Auto-generated by `tools/cron_30m_unified.py` 매 30m. "
            f"단일 scheduler 통합 (Jin 2026-04-27 ITEM-271).\n\n"
        )

    if active_insights is None:
        active_insights = vault_active_insights()

    section = [f"## {ts}\n"]
    for block in (health, loss_attr, bloat, active_insights, wave_block):
        if block:
            section.append("```\n" + block + "\n```")
    section.append("")
    with digest.open("a") as f:
        f.write("\n".join(section) + "\n")


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    start_ts = datetime.datetime.now().isoformat()
    health = health_check()
    loss_attr = open_position_loss_attribution()
    bloat = jsonl_bloat_check()
    active_insights = vault_active_insights()  # Jin v4 통합: Harness 와 vault link
    # Jin 2026-04-28 monitoring expansion: Wave 1-7 효과 자동 measurement.
    try:
        wave_block = wave_monitoring_block()
    except Exception as e:
        wave_block = f"=== Wave monitoring ERROR ===\n{e}"

    header = [f"\n\n========== 30m unified cron START {start_ts} ==========\n", health, "\n"]
    for block in (loss_attr, bloat, active_insights, wave_block):
        if block:
            header.append(block + "\n")
    _log_append("".join(header))

    # 1. Vault DB sync
    run("vault_db_sync", [PYTHON, "-m", "tools.db_views_export"], timeout=300)
    # 2. Visualizer snapshot
    run("visualizer_snapshot", [PYTHON, "-m", "tools.visualizer.snapshot"], timeout=120)
    # 3. Vault digest append (Jin 2026-04-27: vault 효과적 사용)
    try:
        vault_digest_append(loss_attr, bloat, health, active_insights, wave_block)
    except Exception as e:
        _log_append(f"vault_digest_append err: {e}\n")

    end_ts = datetime.datetime.now().isoformat()
    _log_append(f"========== 30m unified cron END {end_ts} ==========\n")


if __name__ == "__main__":
    main()
