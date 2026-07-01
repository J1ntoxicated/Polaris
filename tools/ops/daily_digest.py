"""Machine-generated daily digest: numbers only, zero interpretation.

Reads the live DB read-only (ro URI + explicit close, SELECT only) over the
previous UTC day, renders ≤60 lines of tables into
vault/40_ops/digests/daily-auto/YYYY-MM-DD.md (idempotent overwrite), and
appends one line to vault/log.md (skipped when already present).

Every rendered byte is swept against REJECTION_KEYWORDS at runtime —
strategy_id/fault_type are DB-borne free strings, so the template test
alone cannot guarantee a clean digest.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from tools.ops import alerting
from tools.ops.ops_config import REJECTION_KEYWORDS, OpsConfig, iso_utc

MAX_LINES = 60
VENUE_ROWS = 4
STRATEGY_ROWS = 12
FAULT_ROWS = 5


@dataclass
class DigestData:
    day: str
    generated_date: str
    db_name: str
    closes: int = 0
    pnl_usd: float = 0.0
    pnl_usd_gross: float = 0.0
    fees_usd: float = 0.0
    opens: int = 0
    faults_total: int = 0
    risk_events: int = 0
    open_positions: int = 0
    restarts: int = 0
    wal_before: str = "-"
    wal_after: str = "-"
    ops_alerts: int = 0
    venues: list[tuple[str, int, float, float]] = field(default_factory=list)
    strategies: list[tuple[str, int, float]] = field(default_factory=list)
    faults: list[tuple[str, int]] = field(default_factory=list)


def _window(now: float) -> tuple[str, int, int]:
    """Previous UTC day [00:00, 24:00) — trigger is local, content is UTC."""
    day_dt = (datetime.fromtimestamp(now, tz=UTC) - timedelta(days=1)).date()
    start = datetime(day_dt.year, day_dt.month, day_dt.day, tzinfo=UTC)
    start_ms = int(start.timestamp() * 1000)
    return day_dt.isoformat(), start_ms, start_ms + 86_400_000


def _collect(
    conn: sqlite3.Connection, data: DigestData, start_ms: int, end_ms: int
) -> None:
    start_s, end_s = start_ms // 1000, end_ms // 1000
    close_where = "is_close = 1 AND ts_ms >= ? AND ts_ms < ?"
    window_where = "ts_ms >= ? AND ts_ms < ?"
    # NET headline (audit2 P0-2 ①): the entry (is_close=0) leg pays its own real
    # fee that the old close-leg-only SUM never counted, so a small +gross close
    # pnl could mask a net loss once the round-trip fee is included (2026-06-30:
    # +6.05 shown vs real net ≈ -108.9). ``pnl_usd`` (net) and ``fees_usd`` now
    # both sum over ALL fills in the window (both legs; is_close=0 legs carry
    # pnl_usd=0.0 so SUM(pnl_usd) is still exactly the close-leg gross) — same
    # convention as the equity headline (_daily_realised_pnl in
    # snapshot_q_equity.py). ``closes``/``pnl_usd_gross`` stay close-leg-only.
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(pnl_usd), 0)"
        f" FROM fills WHERE {close_where}", (start_ms, end_ms),
    ).fetchone()
    data.closes, data.pnl_usd_gross = int(row[0]), float(row[1])
    data.fees_usd = float(conn.execute(
        f"SELECT COALESCE(SUM(fee_usd), 0) FROM fills WHERE {window_where}",
        (start_ms, end_ms),
    ).fetchone()[0])
    data.pnl_usd = data.pnl_usd_gross - data.fees_usd
    data.venues = [
        (str(v), int(n), float(p) - float(f), float(f))
        for v, n, p, f in conn.execute(
            "SELECT venue, SUM(is_close), SUM(pnl_usd), SUM(fee_usd) FROM fills"
            f" WHERE {window_where} GROUP BY venue"
            " HAVING SUM(is_close) > 0 ORDER BY venue LIMIT ?",
            (start_ms, end_ms, VENUE_ROWS),
        )
    ]
    data.strategies = [
        (str(s), int(n), float(p))
        for s, n, p in conn.execute(
            "SELECT strategy_id, SUM(is_close), SUM(pnl_usd) - SUM(fee_usd) FROM fills"
            f" WHERE {window_where} GROUP BY strategy_id"
            " HAVING SUM(is_close) > 0"
            " ORDER BY ABS(SUM(pnl_usd) - SUM(fee_usd)) DESC LIMIT ?",
            (start_ms, end_ms, STRATEGY_ROWS),
        )
    ]
    data.opens = int(conn.execute(
        "SELECT COUNT(*) FROM fills WHERE is_close = 0 AND ts_ms >= ? AND ts_ms < ?",
        (start_ms, end_ms),
    ).fetchone()[0])
    data.faults = [
        (str(t), int(n))
        for t, n in conn.execute(
            "SELECT fault_type, COUNT(*) FROM strategy_fault_events"
            " WHERE event_ts >= ? AND event_ts < ? GROUP BY fault_type"
            " ORDER BY COUNT(*) DESC LIMIT ?", (start_s, end_s, FAULT_ROWS),
        )
    ]
    data.faults_total = int(conn.execute(
        "SELECT COUNT(*) FROM strategy_fault_events"
        " WHERE event_ts >= ? AND event_ts < ?", (start_s, end_s),
    ).fetchone()[0])
    data.risk_events = int(conn.execute(
        "SELECT COUNT(*) FROM risk_events WHERE created_ts >= ? AND created_ts < ?",
        (start_s, end_s),
    ).fetchone()[0])
    data.open_positions = int(conn.execute(
        "SELECT COUNT(*) FROM positions WHERE status = 'open'",
    ).fetchone()[0])


def _read_ops_files(cfg: OpsConfig, data: DigestData) -> None:
    """Restart/alert counts come from ops log files, not the DB."""
    try:
        lines = [
            line for line in cfg.restart_log.read_text(encoding="utf-8").splitlines()
            if line.startswith(data.day)
        ]
        data.restarts = len(lines)
        if lines:
            match = re.search(r"wal_before=([\d.]+)MB wal_after=([\d.]+)MB", lines[-1])
            if match:
                data.wal_before, data.wal_after = match.group(1), match.group(2)
    except OSError:
        pass
    with contextlib.suppress(OSError):
        data.ops_alerts = sum(
            1 for line in cfg.alerts_log.read_text(encoding="utf-8").splitlines()
            if line.startswith(data.day)
        )


def render(data: DigestData) -> str:
    lines = [
        "---",
        "type: daily-auto",
        "status: active",
        f"date_created: {data.generated_date}",
        "generated_by: tools/ops/daily_digest.py",
        f"db: {data.db_name}",
        f"window_utc: {data.day} 00:00..24:00",
        "tags: [digest, daily-auto, ops]",
        "---",
        "",
        f"# Daily auto digest — {data.day} (UTC)",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| closes | {data.closes} |",
        f"| pnl_usd | {data.pnl_usd:+.2f} |",
        f"| pnl_usd_gross | {data.pnl_usd_gross:+.2f} |",
        f"| fees_usd | {data.fees_usd:.2f} |",
        f"| opens | {data.opens} |",
        f"| faults | {data.faults_total} |",
        f"| risk_events | {data.risk_events} |",
        f"| open_positions_now | {data.open_positions} |",
        f"| restarts | {data.restarts} |",
        f"| wal_before_mb | {data.wal_before} |",
        f"| wal_after_mb | {data.wal_after} |",
        f"| ops_alerts | {data.ops_alerts} |",
    ]
    if data.venues:
        lines += ["", "## Closes by venue",
                  "| venue | closes | pnl_usd_net | fees_usd |",
                  "| --- | --- | --- | --- |"]
        lines += [f"| {v} | {n} | {p:+.2f} | {f:.2f} |" for v, n, p, f in data.venues]
    if data.strategies:
        lines += ["", f"## Closes by strategy (top {STRATEGY_ROWS})",
                  "| strategy | closes | pnl_usd_net |", "| --- | --- | --- |"]
        lines += [f"| {s} | {n} | {p:+.2f} |" for s, n, p in data.strategies]
    if data.faults:
        lines += ["", "## Faults by type", "| fault_type | count |", "| --- | --- |"]
        lines += [f"| {t} | {n} |" for t, n in data.faults]
    if len(lines) > MAX_LINES:  # defensive — section caps already guarantee this
        lines = lines[: MAX_LINES - 1] + ["(truncated)"]
    return "\n".join(lines) + "\n"


def sweep_keywords(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    for keyword in REJECTION_KEYWORDS:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        if pattern.search(text):
            hits.append(keyword)
            text = pattern.sub("[redacted]", text)
    return text, hits


def _append_vault_log(cfg: OpsConfig, data: DigestData) -> None:
    marker = f"{data.day} daily-auto"
    try:
        existing = cfg.vault_log.read_text(encoding="utf-8")
        if any(line.startswith(marker) for line in existing.splitlines()):
            return
    except OSError:
        pass
    line = (
        f"{marker} [closes={data.closes} pnl_usd={data.pnl_usd:+.2f}"
        f" opens={data.opens} faults={data.faults_total} restarts={data.restarts}]\n"
    )
    fd = os.open(cfg.vault_log, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))  # single O_APPEND write — atomic enough
    finally:
        os.close(fd)


def run(cfg: OpsConfig, *, now: float | None = None) -> int:
    now_f = time.time() if now is None else now
    day, start_ms, end_ms = _window(now_f)
    generated = datetime.fromtimestamp(now_f, tz=UTC).date().isoformat()
    cfg.digest_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.digest_dir / f"{day}.md"
    data = DigestData(day=day, generated_date=generated, db_name=cfg.db_path.name)

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
        _collect(conn, data, start_ms, end_ms)
    except sqlite3.Error as exc:
        out_path.write_text(
            "---\n"
            "type: daily-auto\n"
            "status: active\n"
            f"date_created: {generated}\n"
            "generated_by: tools/ops/daily_digest.py\n"
            "error: db_unreadable\n"
            f"window_utc: {day} 00:00..24:00\n"
            "tags: [digest, daily-auto, ops]\n"
            "---\n\n"
            f"error: db_unreadable at {iso_utc(now_f)}\n",
            encoding="utf-8",
        )
        alerting.notify(cfg, "digest_failed", f"db unreadable: {exc}")
        return 0  # launchd-normal exit; retried next day
    finally:
        if conn is not None:
            conn.close()

    _read_ops_files(cfg, data)
    text, hits = sweep_keywords(render(data))
    if hits:
        alerting.notify(
            cfg, "digest_keyword_redacted",
            f"{len(hits)} rejection keyword(s) redacted from {out_path.name}",
        )
    out_path.write_text(text, encoding="utf-8")  # idempotent overwrite
    _append_vault_log(cfg, data)
    return 0


def main() -> int:
    return run(OpsConfig.default())


if __name__ == "__main__":
    raise SystemExit(main())
