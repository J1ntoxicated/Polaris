"""Daily graceful restart: SIGTERM → wait → rotate log → start. WAL recovery.

Runs at 07:30 local (launchd). Never opens the database (WAL size is read
via os.stat only); never force-kills: a stop timeout aborts the whole run
with an alert, leaving the existing process untouched (zero duplicate
instances by construction).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

from tools.ops import alerting, botctl
from tools.ops.ops_config import (
    LOG_KEEP,
    RESTART_SETTLE_SEC,
    OpsConfig,
    iso_utc,
)


def _wal_mb(cfg: OpsConfig) -> float:
    try:
        return os.stat(cfg.wal_path).st_size / 1048576
    except FileNotFoundError:
        return 0.0


def _rotate_logs(cfg: OpsConfig, *, now: float) -> None:
    """Only called in the bot-down window — no live-write mv race possible."""
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y%m%d")
    if cfg.bot_log.exists():
        os.replace(cfg.bot_log, cfg.bot_log.with_name(f"{cfg.bot_log.name}.{day}"))
    rotated = sorted(cfg.bot_log.parent.glob(cfg.bot_log.name + ".*"))
    for old in rotated[:-LOG_KEEP]:
        old.unlink(missing_ok=True)


def run(cfg: OpsConfig, *, now: float | None = None) -> str:
    now_f = time.time() if now is None else now

    if cfg.sentinel.exists():
        return "sentinel_skip"  # manual stop respected — zero action

    if not botctl.acquire_lock(cfg, now=now_f):
        alerting.notify(cfg, "restart_lock_busy",
                        "start.lock held — daily restart skipped this run")
        return "lock_busy"
    try:
        wal_before = _wal_mb(cfg)
        if not botctl.stop(cfg, manual=False):
            alerting.notify(
                cfg, "restart_stop_timeout",
                "bot did not exit within 60s of SIGTERM — daily restart aborted"
                " (no forced kill, no new instance; existing process untouched)",
            )
            return "stop_timeout"
        _rotate_logs(cfg, now=now_f)  # bot confirmed down — safe window
        if not botctl.start(cfg, manual=False, have_lock=True, now=now_f):
            return "start_failed"
        botctl._sleep(RESTART_SETTLE_SEC)
        wal_after = _wal_mb(cfg)
        pid = botctl.read_pidfile(cfg)
        cfg.ops_dir.mkdir(parents=True, exist_ok=True)
        with open(cfg.restart_log, "a", encoding="utf-8") as fh:
            fh.write(
                f"{iso_utc(now_f)} wal_before={wal_before:.1f}MB"
                f" wal_after={wal_after:.1f}MB pid={pid}\n"
            )
        return "ok"
    finally:
        botctl.release_lock(cfg)


def main() -> int:
    action = run(OpsConfig.default())
    print(f"[daily-restart] action={action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
