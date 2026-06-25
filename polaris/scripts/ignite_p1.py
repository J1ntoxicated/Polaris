"""P1.0 ignition entry point — kicks off the autonomous paper-trading loop.

Spec source:
- vault/_NOW.md (Implementation status — P1.0 ignition ready)
- vault/30_components/layer-{0..7}-*.md (per-layer wiring contract)
- vault/10_decisions/ADR-003 / ADR-004 / ADR-007

Ignition contract (Day 6 P0 — bootstrap + delegation)
=====================================================
``ignite_p1`` is a thin **boot orchestrator**: it readies persistent
state, instantiates the Layer 5 scheduler so its wiring is provably
reachable, stamps vault, and (in ``--paper`` mode) hands off to
``smoke_paper_loop.run_smoke`` which owns the per-tick pipeline.

1. Bootstrap SQLite schema (idempotent ``init_db``).
2. Layer 0 — read the persisted ``universe`` table and report the active
   focus count. The long-running 5/10-min OKX/Capital refresh loop is
   owned by P1.x and lives outside ignition; ignition only confirms the
   table is reachable so P0 smoke can proceed against existing rows.
3. Layer 5 — instantiate ``LearnerScheduler`` so the 3 P0 learners are
   wired (``len(scheduler.learners) == 3`` in the boot report). When
   ``paper=True`` the scheduler's ``run_forever`` runs as a background
   task at the configured cadence (default 1 h).
4. Layer 2 + Layer 6 + Layer 7 — owned by ``smoke_paper_loop.run_smoke``
   when ``paper=True``: the smoke loop constructs ``GateOrchestrator``
   per tick (full-pipeline mode), runs each strategy in an isolated task
   (Layer 7 invariant), and drives the live-recalc primitives off the
   per-tick close path. ``ignite_p1`` does not duplicate that wiring —
   it delegates so a single ``run_smoke`` is the SSOT for tick-time
   behaviour.
5. Dashboard v0 — refresh loop runs in a separate process / terminal
   (``python3 -m polaris.scripts.dashboard_v0``). Ignition writes the
   boot status to vault so the user can confirm without tailing logs.
6. Vault append — ``_NOW.md`` Implementation status line + ``log.md``
   chronological entry are written **before** the long-running loop so
   the report is durable even if we crash mid-loop.

Modes
-----
- ``--dry-run`` (default for CI): bootstrap only, do not start the loop.
  Returns exit code 0 on success.
- ``--paper``: start the smoke loop in full-pipeline mode for the given
  duration (default 24 h watchdog session). Persists fills + cell matrix
  + learner deltas; the dashboard reads them via SQLite.

Day 7 watchdog metrics (24 h focus — `vault/_NOW.md`):
  - Auth health (OKX 401-rate / Capital CST refresh OK).
  - Strategy isolation (no cross-pollution on exception).
  - Cell matrix accumulation (n_trades > 0).
  - Learner tunes (≥ 1 hourly trigger fire).
  - Dashboard refresh (latest snapshot ts within 10 s of now).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.core.learners import LearnerScheduler
from polaris.datastream import setup_datastream
from polaris.logging_config import DEFAULT_LOG_FILE, setup_polaris_logging
from polaris.scripts._production_layers import (
    refresh_capital_universe_once,
    refresh_focus_watchlist,
    refresh_okx_universe_once,
)
from polaris.scripts.production_paper_loop import (
    DEFAULT_DURATION_SEC as SMOKE_DURATION_SEC,
)
from polaris.scripts.production_paper_loop import (
    DEFAULT_TICK_SEC as SMOKE_TICK_SEC,
)
from polaris.scripts.production_paper_loop import (
    run_production_paper_loop,
)
from polaris.storage.schema import init_db

logger = logging.getLogger(__name__)

__all__ = [
    "IgnitionReport",
    "ignite",
    "main",
]


@dataclass(slots=True)
class IgnitionReport:
    """Boot summary returned by :func:`ignite`.

    Used by the harness + Day 7 watchdog so we can self-check that every
    layer wired up correctly. ``vault_log_appended`` is the relative path
    to the line we appended to ``vault/log.md``.
    """

    started_ts: int
    db_path: Path
    learner_count: int
    universe_focus_count: int
    smoke_exit: int | None = None
    vault_log_appended: str | None = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Boot helpers
# ---------------------------------------------------------------------------


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Lightweight .env loader — match smoke_paper_loop's behaviour."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


async def _layer0_warm(db_path: Path) -> int:
    """Layer 0 — count active universe rows (best-effort, no I/O on failure).

    Day 6 ignition does not perform a fresh OKX/Capital pull — that is owned
    by the long-running 5/10-min refresh loop scheduled in P1.x. We simply
    sample the persisted ``universe`` table so the boot report shows a
    non-zero focus_count when prior smoke runs already populated it.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM universe WHERE is_active = 1"
        )
        return int(cur.fetchone()[0] or 0)
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


async def _layer0_producer_start(conn: Any) -> int:
    """Day 8 — kick off the Layer 0 producer once during bootstrap.

    Replaces ``_layer0_warm`` for paper mode: refresh both venue universes,
    recompute the focus watchlist, then return the active-universe count for
    the boot report. Long-running cadence (OKX 5min / Capital 10min) lives
    inside ``run_production_paper_loop`` so the producer does not contend
    with ignition's own bootstrap.
    """
    okx_active = await refresh_okx_universe_once(conn)
    cap_active = await refresh_capital_universe_once(conn)
    refresh_focus_watchlist(conn)
    logger.info(
        "[ignite] layer0 producer warmed: okx_active=%d capital_active=%d",
        okx_active, cap_active,
    )
    return okx_active + cap_active


def _vault_root() -> Path:
    """Vault directory the bootstrap stamps write to.

    ``POLARIS_VAULT_DIR`` overrides (test isolation — the pytest suite points
    it at a per-test tmp vault so a fixture run can never append to the real
    ``vault/log.md`` / mutate ``vault/_NOW.md``). Unset/empty = the cwd-relative
    ``vault`` — runtime behaviour byte-identical.
    """
    return Path(os.environ.get("POLARIS_VAULT_DIR") or "vault")


def _vault_append(text: str) -> str | None:
    """Append a line to ``<vault>/log.md``; return its path on success."""
    log_path = _vault_root() / "log.md"
    if not log_path.exists():
        return None
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return str(log_path)


def _now_md_update_implementation_status(line: str) -> bool:
    """Mutate ``<vault>/_NOW.md`` Implementation status section if present."""
    p = _vault_root() / "_NOW.md"
    if not p.exists():
        return False
    txt = p.read_text(encoding="utf-8")
    marker = "## Implementation status"
    if marker not in txt:
        # Append at the end (one-time bootstrap).
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## Implementation status\n- {line}\n")
        return True
    # Replace the section body up to the next ``## `` header.
    start = txt.index(marker) + len(marker)
    rest = txt[start:]
    end_offset = rest.find("\n## ")
    if end_offset == -1:
        new_body = f"\n- {line}\n"
        p.write_text(txt[:start] + new_body, encoding="utf-8")
        return True
    new_body = f"\n- {line}\n"
    p.write_text(txt[:start] + new_body + rest[end_offset:], encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Ignition orchestrator
# ---------------------------------------------------------------------------


async def ignite(
    *,
    duration_sec: float = SMOKE_DURATION_SEC,
    tick_sec: float = SMOKE_TICK_SEC,
    db_path: Path | None = None,
    paper: bool = False,
    learner_interval_sec: int = 3600,
    real_roundtrip: bool = False,
    roundtrip_dry_run: bool = False,
    full_pipeline: bool = True,
    haiku: Any = None,
) -> IgnitionReport:
    """Kick off the P1.0 paper-trading session (or dry-run validation).

    ``paper=False`` (default) returns immediately after bootstrap so CI /
    Day 7 readiness checks run quickly. ``paper=True`` blocks for
    ``duration_sec`` while the smoke loop trades.
    """
    started = int(time.time())
    _load_dotenv()
    target_db = db_path or Path("data/polaris.sqlite")
    target_db.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(target_db)
    notes: list[str] = []

    logger.info(
        "[ignite] bootstrap started_ts=%d db=%s paper=%s full_pipeline=%s "
        "duration=%.0fs tick=%.1fs learner_interval=%ds",
        started,
        target_db,
        paper,
        full_pipeline,
        duration_sec,
        tick_sec,
        learner_interval_sec,
    )

    # 1. Layer 0 warm-up. In paper mode we run a fresh refresh so the loop
    #    has a populated universe + focus before the first tick (Day 8 spec A).
    if paper:
        universe_count = await _layer0_producer_start(conn)
    else:
        universe_count = await _layer0_warm(target_db)
    notes.append(f"layer0_focus={universe_count}")
    logger.info("[ignite] layer0 universe focus rows=%d", universe_count)

    # 2. Layer 5 — instantiate scheduler so the learner trio is committed
    #    every hour. Even in dry-run we instantiate to prove the wiring
    #    compiles + the snapshot table is reachable.
    scheduler = LearnerScheduler(conn, expected_holding_bars=20)
    notes.append(f"learners={len(scheduler.learners)}")
    logger.info(
        "[ignite] layer5 scheduler ready learners=%d ids=%s",
        len(scheduler.learners),
        [lr.learner_id for lr in scheduler.learners],
    )

    # 3. Vault stamps (write before the long-running loop so the report is
    #    durable even if we crash mid-loop).
    vault_line = (
        f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(started))} "
        f"[ignite_p1: bootstrap target_db={target_db.name} "
        f"layer0_focus={universe_count} learners={len(scheduler.learners)} "
        f"paper={paper}]"
    )
    appended = _vault_append(vault_line)
    if appended:
        notes.append(f"vault_log={appended}")
    if _now_md_update_implementation_status(
        f"P1.0 ignition fired at {time.strftime('%Y-%m-%d %H:%M', time.gmtime(started))} "
        f"(paper={paper}, full_pipeline={full_pipeline})"
    ):
        notes.append("vault_now_updated")

    smoke_exit: int | None = None
    if paper:
        # 4. Long-running smoke loop in full-pipeline mode + concurrent
        #    learner tuning. Use a TaskGroup-style pattern so a learner
        #    failure does not silently drop us into the dry-run path.
        logger.info(
            "[ignite] entering paper loop duration=%.0fs full_pipeline=%s "
            "real_roundtrip=%s roundtrip_dry_run=%s",
            duration_sec,
            full_pipeline,
            real_roundtrip,
            roundtrip_dry_run,
        )
        learner_task = asyncio.create_task(
            scheduler.run_forever(interval_sec=learner_interval_sec)
        )
        try:
            # Day 8: production paper loop — Layer 0 producer schedule, real
            # bars ingest, real MarketView/regime, G1→G8, fence + idempotent
            # order keys, real PnL on close. ``full_pipeline`` runs the full
            # pipeline by definition; ``real_roundtrip`` (P0 venue wire) now
            # threads through so open + close submit real demo orders.
            _ = full_pipeline
            _ = roundtrip_dry_run
            smoke_exit = await run_production_paper_loop(
                duration_sec=duration_sec,
                tick_sec=tick_sec,
                db_path=target_db,
                haiku=haiku,
                real_roundtrip=real_roundtrip,
            )
            logger.info(
                "[ignite] paper loop exit smoke_exit=%s elapsed=%.1fs",
                smoke_exit,
                time.time() - started,
            )
        finally:
            learner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await learner_task
    return IgnitionReport(
        started_ts=started,
        db_path=target_db,
        learner_count=len(scheduler.learners),
        universe_focus_count=universe_count,
        smoke_exit=smoke_exit,
        vault_log_appended=appended,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ignite_p1")
    parser.add_argument("--paper", action="store_true", default=False,
                        help="Start the smoke loop (else dry-run bootstrap).")
    parser.add_argument("--duration", type=float, default=SMOKE_DURATION_SEC)
    parser.add_argument("--tick", type=float, default=SMOKE_TICK_SEC)
    parser.add_argument("--db", type=Path, default=Path("data/polaris.sqlite"))
    parser.add_argument("--learner-interval-sec", type=int, default=3600)
    parser.add_argument("--full-pipeline", action="store_true", default=True)
    parser.add_argument("--no-full-pipeline", dest="full_pipeline",
                        action="store_false")
    parser.add_argument("--real-roundtrip", action="store_true", default=False)
    parser.add_argument("--roundtrip-dry-run", action="store_true", default=False)
    parser.add_argument(
        "--verbose", "-v", action="count", default=0,
        help="Verbose logging: -v=INFO (default), -vv=DEBUG.",
    )
    parser.add_argument(
        "--log-file", type=str, default=DEFAULT_LOG_FILE,
        help=f"Log file path (default: {DEFAULT_LOG_FILE}).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    log_level = "DEBUG" if args.verbose >= 2 else "INFO"
    setup_polaris_logging(level=log_level, log_file=args.log_file)
    # Per-tick / per-update DATA goes to its own rotating JSONL sink (operator
    # runtime log stays lean). No-op for callers that never boot via ignite.
    setup_datastream()
    logger.info(
        "[ignite] CLI invoked level=%s log_file=%s argv=%s",
        log_level,
        args.log_file,
        list(argv) if argv is not None else sys.argv[1:],
    )

    async def _run() -> Any:
        return await ignite(
            duration_sec=args.duration,
            tick_sec=args.tick,
            db_path=args.db,
            paper=args.paper,
            learner_interval_sec=args.learner_interval_sec,
            real_roundtrip=args.real_roundtrip,
            roundtrip_dry_run=args.roundtrip_dry_run,
            full_pipeline=args.full_pipeline,
        )

    report: IgnitionReport = asyncio.run(_run())
    print("\n=========== Polaris P1.0 ignition ===========")
    print(f"started_ts             : {report.started_ts}")
    print(f"db_path                : {report.db_path}")
    print(f"learner_count          : {report.learner_count}")
    print(f"universe_focus_count   : {report.universe_focus_count}")
    print(f"smoke_exit             : {report.smoke_exit}")
    print(f"vault_log_appended     : {report.vault_log_appended}")
    print(f"notes                  : {report.notes}")
    smoke_exit = report.smoke_exit
    if smoke_exit is None:
        return 0
    return 0 if smoke_exit == 0 else int(smoke_exit)


if __name__ == "__main__":
    sys.exit(main())
