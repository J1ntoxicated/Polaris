"""Polling watchdog (launchd StartInterval=300): liveness + health, no KeepAlive.

One pass: sentinel → no-op (24h reminder only); bot alive+matched → health
checks (pure observation — every threshold alerts, none throttles or kills
the bot); dead → adopt a strict-match orphan or restart via botctl.start.
Self-timeout 120s so a hung pass can never pile up.
"""

from __future__ import annotations

import os
import signal
import time
from types import FrameType

from tools.ops import alerting, botctl, log_scan
from tools.ops.ops_config import (
    MANUAL_REMINDER_SEC,
    RESTART_ALERT_THROTTLE_SEC,
    WAL_ALERT_BYTES,
    WEDGE_AGE_SEC,
    OpsConfig,
)


def decide(*, sentinel: bool, pid_alive: bool, cmd_match: bool, orphan: bool) -> str:
    """Pure decision: none | health | adopt | start. Sentinel always wins."""
    if sentinel:
        return "none"
    if pid_alive and cmd_match:
        return "health"
    return "adopt" if orphan else "start"


def health_checks(cfg: OpsConfig, *, now: float | None = None) -> list[str]:
    """Observation only — alerts never block, throttle, or kill the bot."""
    now_f = time.time() if now is None else now
    fired: list[str] = []

    def alert(key: str, msg: str) -> None:
        fired.append(key)
        alerting.notify(cfg, key, msg)

    try:
        age = now_f - os.stat(cfg.bot_log).st_mtime
        if age > WEDGE_AGE_SEC:
            alert("bot_wedged", f"pid alive but log silent {age:.0f}s — NOT killing"
                                " (live process is human territory)")
    except FileNotFoundError:
        alert("bot_log_missing", f"{cfg.bot_log} absent while bot alive (pre-fixed-"
                                 "path run? resolves at next restart)")

    counts = log_scan.scan(cfg)
    if counts["stall"] >= 3:
        alert("stall_burst", f"{counts['stall']} tick-engine STALLs this cycle")
    if counts["db_lock"] >= 3:
        alert("db_lock_burst", f"{counts['db_lock']} 'database is locked' this cycle")
    if counts["ws_gave_up"] >= 1:
        alert("ws_gave_up", "WS gave up → REST-only mode (realtime-price principle"
                            " violated) — restart recovers WS")
    if counts["ws_error"] >= 5:
        alert("ws_flapping", f"{counts['ws_error']} WS connection errors this cycle")
    # pts-classes group G — telemetry only, no burst threshold: each
    # occurrence is its own falsifiable capital-routing signal (not a flap
    # pattern like ws_error/stall), so >=1 alerts (mirrors ws_gave_up).
    if counts["exec_starved"] >= 1:
        alert("exec_starved", f"{counts['exec_starved']} EXEC_STARVED transition"
                              " no-ops this cycle — a (venue,strategy) track has"
                              " too little fill evidence to judge (observation"
                              " only, never blocks the strategy)")
    if counts["probe_fee_exhausted"] >= 1:
        alert("probe_fee_exhausted",
              f"{counts['probe_fee_exhausted']} PROVE candidate(s) lost a probe"
              " slot to the 24h fee budget this cycle (capital-routing outcome,"
              " never a block — unslotted candidates keep signaling/learning)")

    try:
        wal = os.stat(cfg.wal_path).st_size
        if wal > WAL_ALERT_BYTES:
            alert("wal_oversize", f"WAL {wal / 1048576:.0f}MB > "
                                  f"{WAL_ALERT_BYTES / 1048576:.0f}MB")
    except FileNotFoundError:
        pass
    return fired


def run_once(cfg: OpsConfig, *, now: float | None = None) -> str:
    now_f = time.time() if now is None else now

    if cfg.sentinel.exists():
        alerting.notify(
            cfg, "manual_stop_reminder",
            "MANUAL_STOP active (bot intentionally down)",
            throttle_sec=MANUAL_REMINDER_SEC,
        )
        return "none"

    pid = botctl.read_pidfile(cfg)
    alive = pid is not None and botctl.pid_alive(pid)
    cmd = botctl.ps_command(pid) if alive and pid is not None else None
    match = cmd is not None and botctl.is_bot_command(cmd)
    orphans = botctl.find_bot_processes(exclude=botctl.ancestor_pids())

    action = decide(sentinel=False, pid_alive=alive, cmd_match=match,
                    orphan=bool(orphans))
    if action == "health":
        health_checks(cfg, now=now_f)
        return "health"
    if action == "adopt":
        opid, ocmd = orphans[0]
        botctl.write_pidfile(cfg, opid)
        alerting.notify(
            cfg, "bot_adopted",
            f"live bot found without valid pidfile — adopted pid={opid} argv={ocmd}",
        )
        return "adopt"

    # start path: account the death first, honour flap backoff (interval
    # relaxation only — never a halt; integrity-only philosophy).
    botctl.note_unexpected_death(cfg, now=now_f)
    if botctl.in_backoff(cfg, now=now_f):
        return "backoff"
    if botctl.start(cfg, manual=False, now=now_f):
        alerting.notify(
            cfg, "bot_dead_restarted", "bot was down — restarted",
            throttle_sec=RESTART_ALERT_THROTTLE_SEC,
        )
        return "start"
    return "start_failed"


def _timeout(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(2)


def main() -> int:
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(120)  # self-timeout: a wedged pass exits before the next one
    cfg = OpsConfig.default()
    action = run_once(cfg)
    print(f"[watchdog] action={action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
