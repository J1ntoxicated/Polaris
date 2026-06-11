"""Sentinel — deterministic live-audit sidecar (W1, observe-only). DEMO/PAPER.

Spec SSOT: .claude/plans/organic_ops_ai_free_2026-06-11.md §4. The sidecar
never touches the bot: the live DB is opened with a ``file:...?mode=ro`` URI
(zero locks, zero writes) and all output goes to a SEPARATE
``data/sentinel.sqlite`` (zero contention with the bot writer). It records
and exposes findings only — no halt/throttle/block semantics of any kind.

Process model:
    python3 -m polaris.scripts.sentinel --db data/polaris_live.sqlite \
        --sentinel-db data/sentinel.sqlite --interval 45
``--once`` runs a single pass (tests / manual). Loop mode writes a PID file
(``data/paper/sentinel.pid``), logs to ``data/paper/sentinel.log`` and exits
gracefully on SIGTERM/SIGINT.

Checks v1 = S1/S2/S3/S4/S6/S7 (see ``_sentinel_checks``). S5 (reconcile
drift) is deferred to v1.1 — no reconcile output table exists in the live
schema. Dedup unit = (check_id, subject): recurrence updates ``last_ts``; a
clean pass resolves the finding (``status='resolved'``) — zero spam. An
errored check never resolves its own previous findings (no false all-clear).
Findings persist + state save + heartbeat are ONE transaction per pass — a
mid-write crash can never advance the state snapshot alone.

``sentinel_state`` changes are additive (new keys + legacy S4 value
fallback): an existing sentinel.sqlite keeps working without migration.

Deferred to v1.1 (deliberate scope cuts):
  - exchange-holiday calendar (session clocks cover weekend/RTH only)
  - findings/runs retention pruning (sentinel.sqlite grows unbounded)
  - S1 full-table MAX(ts) scan cost (fine at current volume; index later)
  - single-instance guard (two sentinels would double-write findings)
  - S4 NULL-pass watermark gap (a stop that goes NULL for one pass and
    returns re-seeds the watermark at the returned value — a ratchet
    violation inside that window is not flagged)
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

from polaris.scripts._sentinel_checks import (
    Thresholds,
    check_s1_price_freshness,
    check_s2_exit_fill_parity,
    check_s3_entry_parity_rejects,
    check_s4_stop_ratchet,
    check_s6_feature_availability,
    check_s7_zombie_drain,
)

__all__ = ["RunResult", "main", "open_live_ro", "open_sentinel_db", "run_once"]

log = logging.getLogger("polaris.sentinel")

_S2_STATE_KEY = "s2_pending"
_S4_STATE_KEY = "s4_stop_snapshot"
_S7_STATE_KEY = "s7_reconciled_ids"

SENTINEL_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS sentinel_findings (
        finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_id TEXT NOT NULL,
        severity TEXT NOT NULL,
        subject TEXT NOT NULL,
        summary TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}',
        first_ts INTEGER NOT NULL,
        last_ts INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        resolved_ts INTEGER
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_sentinel_findings_active
        ON sentinel_findings(check_id, subject) WHERE status = 'active';
    """,
    """
    CREATE TABLE IF NOT EXISTS sentinel_runs (
        ts INTEGER NOT NULL,
        checks_run INTEGER NOT NULL,
        findings_active INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sentinel_state (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    );
    """,
)


def open_live_ro(path: Path | str) -> sqlite3.Connection:
    """Open the LIVE bot DB strictly read-only (URI mode=ro — the observe-only
    guarantee: any write attempt raises ``sqlite3.OperationalError``)."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)


def open_sentinel_db(path: Path | str) -> sqlite3.Connection:
    """Open (and bootstrap) the SEPARATE sentinel output DB."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, isolation_level=None, timeout=10.0)
    for stmt in SENTINEL_DDL:
        conn.execute(stmt)
    return conn


def _load_state(sconn: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = sconn.execute(
        "SELECT value_json FROM sentinel_state WHERE key=?", (key,)
    ).fetchone()
    if row is None:
        return {}
    try:
        loaded = json.loads(row[0])
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_state(sconn: sqlite3.Connection, key: str, value: dict[str, Any]) -> None:
    sconn.execute(
        "INSERT OR REPLACE INTO sentinel_state (key, value_json) VALUES (?, ?)",
        (key, json.dumps(value, sort_keys=True)),
    )


def _persist_findings(
    sconn: sqlite3.Connection,
    ran_check_ids: list[str],
    findings: list[Any],
    now_ts: int,
) -> int:
    """Dedup-upsert findings + resolve cleared ones; return active count.

    Dedup unit = (check_id, subject): an existing active row gets ``last_ts``
    (+ severity/summary/detail) refreshed — never a second row. Active rows
    of checks that RAN this pass but were not re-emitted flip to
    ``resolved``. Checks that errored are absent from ``ran_check_ids`` so
    their findings stay active (no false all-clear).
    """
    emitted: set[tuple[str, str]] = set()
    for f in findings:
        emitted.add((f.check_id, f.subject))
        row = sconn.execute(
            "SELECT finding_id FROM sentinel_findings "
            "WHERE check_id=? AND subject=? AND status='active'",
            (f.check_id, f.subject),
        ).fetchone()
        detail_json = json.dumps(f.detail, sort_keys=True)
        if row is not None:
            sconn.execute(
                "UPDATE sentinel_findings SET severity=?, summary=?, "
                "detail_json=?, last_ts=? WHERE finding_id=?",
                (f.severity, f.summary, detail_json, now_ts, row[0]),
            )
        else:
            sconn.execute(
                "INSERT INTO sentinel_findings (check_id, severity, subject, "
                "summary, detail_json, first_ts, last_ts, status) "
                "VALUES (?,?,?,?,?,?,?,'active')",
                (f.check_id, f.severity, f.subject, f.summary, detail_json,
                 now_ts, now_ts),
            )
    if ran_check_ids:
        marks = ",".join("?" for _ in ran_check_ids)
        for finding_id, check_id, subject in sconn.execute(
            f"SELECT finding_id, check_id, subject FROM sentinel_findings "
            f"WHERE status='active' AND check_id IN ({marks})",
            ran_check_ids,
        ).fetchall():
            if (str(check_id), str(subject)) not in emitted:
                sconn.execute(
                    "UPDATE sentinel_findings SET status='resolved', "
                    "resolved_ts=? WHERE finding_id=?",
                    (now_ts, finding_id),
                )
    active = sconn.execute(
        "SELECT COUNT(*) FROM sentinel_findings WHERE status='active'"
    ).fetchone()[0]
    return int(active)


@dataclass(slots=True)
class RunResult:
    """Outcome of one sentinel pass (heartbeat mirror)."""

    checks_run: int
    findings_active: int
    duration_ms: int
    errors: dict[str, str] = field(default_factory=dict)


def run_once(
    live_db: Path | str,
    sentinel_db: Path | str,
    *,
    now_ts: int | None = None,
    thresholds: Thresholds | None = None,
) -> RunResult:
    """One audit pass: run all checks (isolated), persist findings + heartbeat.

    ``now_ts`` is injectable for deterministic tests; defaults to wall clock.
    Each check failure is captured in ``RunResult.errors`` and never
    propagates to other checks (spec: 실패는 다른 체크에 비전파).
    """
    now = int(time.time()) if now_ts is None else int(now_ts)
    th = thresholds if thresholds is not None else Thresholds.from_env()
    t0 = time.monotonic()
    errors: dict[str, str] = {}
    findings: list[Any] = []
    ran: list[str] = []

    state_updates: dict[str, dict[str, Any]] = {}
    live = open_live_ro(live_db)
    sconn = open_sentinel_db(sentinel_db)
    try:
        stateless_checks = (
            ("S1", check_s1_price_freshness),
            ("S3", check_s3_entry_parity_rejects),
            ("S6", check_s6_feature_availability),
        )
        for check_id, fn in stateless_checks:
            try:
                findings.extend(fn(live, now, th))
                ran.append(check_id)
            except Exception as exc:  # noqa: BLE001 — isolate per check
                errors[check_id] = f"{type(exc).__name__}: {exc}"
        stateful_checks = (
            ("S2", check_s2_exit_fill_parity, _S2_STATE_KEY),
            ("S4", check_s4_stop_ratchet, _S4_STATE_KEY),
            ("S7", check_s7_zombie_drain, _S7_STATE_KEY),
        )
        for check_id, sfn, state_key in stateful_checks:
            try:
                prev = _load_state(sconn, state_key)
                sf, new_state = sfn(live, now, th, prev)
                findings.extend(sf)
                state_updates[state_key] = new_state
                ran.append(check_id)
            except Exception as exc:  # noqa: BLE001 — isolate per check
                errors[check_id] = f"{type(exc).__name__}: {exc}"

        # F6: findings + state + heartbeat commit atomically — a crash
        # mid-write can never advance the state snapshot alone.
        sconn.execute("BEGIN")
        try:
            active = _persist_findings(sconn, ran, findings, now)
            for state_key, value in state_updates.items():
                _save_state(sconn, state_key, value)
            duration_ms = int((time.monotonic() - t0) * 1000)
            sconn.execute(
                "INSERT INTO sentinel_runs (ts, checks_run, findings_active, "
                "duration_ms) VALUES (?,?,?,?)",
                (now, len(ran), active, duration_ms),
            )
            sconn.execute("COMMIT")
        except BaseException:
            sconn.execute("ROLLBACK")
            raise
    finally:
        live.close()
        sconn.close()
    return RunResult(
        checks_run=len(ran),
        findings_active=active,
        duration_ms=duration_ms,
        errors=errors,
    )


def _setup_logging(log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Polaris Sentinel — observe-only live audit sidecar "
        "(DEMO/PAPER; live DB read-only, findings to data/sentinel.sqlite)"
    )
    parser.add_argument("--db", default="data/polaris_live.sqlite")
    parser.add_argument("--sentinel-db", default="data/sentinel.sqlite")
    parser.add_argument("--interval", type=float, default=45.0)
    parser.add_argument("--once", action="store_true",
                        help="single pass (tests/manual), no pid/log file")
    parser.add_argument("--pid-file", default="data/paper/sentinel.pid")
    parser.add_argument("--log-file", default="data/paper/sentinel.log")
    args = parser.parse_args(argv)

    if args.once:
        _setup_logging(None)
        res = run_once(args.db, args.sentinel_db)
        log.info(
            "sentinel --once: checks=%d active=%d dur=%dms errors=%s",
            res.checks_run, res.findings_active, res.duration_ms, res.errors,
        )
        return 1 if res.errors else 0  # F8: surface check errors to cron/CI

    _setup_logging(Path(args.log_file))
    stop = threading.Event()

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("signal %d received — graceful stop", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    pid_path = Path(args.pid_file)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    import os as _os

    pid_path.write_text(str(_os.getpid()), encoding="utf-8")
    log.info(
        "sentinel start: live=%s out=%s interval=%.1fs pid=%s (observe-only)",
        args.db, args.sentinel_db, args.interval, pid_path,
    )
    try:
        while not stop.is_set():
            try:
                res = run_once(args.db, args.sentinel_db)
                log.info(
                    "pass: checks=%d active=%d dur=%dms%s",
                    res.checks_run, res.findings_active, res.duration_ms,
                    f" errors={res.errors}" if res.errors else "",
                )
            except Exception:  # noqa: BLE001 — sidecar must never die mid-loop
                log.exception("sentinel pass failed")
            stop.wait(args.interval)
    finally:
        pid_path.unlink(missing_ok=True)
        log.info("sentinel stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
