"""Daily graceful restart: SIGTERM → wait → rotate log → retention → start.

Runs at 07:30 local (launchd). The DB is opened ONLY inside the confirmed-down
window (after ``botctl.stop`` returns True) to prune raw/history streams and
reclaim the WAL — never on the stop-timeout abort path. Never force-kills: a
stop timeout aborts the whole run with an alert, leaving the existing process
untouched (zero duplicate instances by construction).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

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


def _rotate_one_log(base: Path, *, day: str) -> None:
    """Date-rotate ``base`` (if present) + prune its dated backups to LOG_KEEP.

    Bot-down window only — no live-write mv race. The dated rotation preserves
    content (never a truncate/delete of the live file), so unique tracebacks in
    ``bot_spawn.log`` survive.
    """
    if base.exists():
        os.replace(base, base.with_name(f"{base.name}.{day}"))
    rotated = sorted(base.parent.glob(base.name + ".*"))
    for old in rotated[:-LOG_KEEP]:
        old.unlink(missing_ok=True)


def _rotate_logs(cfg: OpsConfig, *, now: float) -> None:
    """Only called in the bot-down window — no live-write mv race possible.

    Rotates BOTH the runtime log (``bot_log``) and the spawn log
    (``spawn_log``). ``bot_spawn.log`` is the subprocess stdout/stderr sink
    (``botctl._spawn`` opens it append-only) and previously had NO rotation
    wired — it leaked to 1.5 G on the live box. It carries unique
    pre-logging-init crash tracebacks, so it is date-rotated (content-preserving)
    on the same LOG_KEEP cap as runtime, never truncated.
    """
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y%m%d")
    _rotate_one_log(cfg.bot_log, day=day)
    _rotate_one_log(cfg.spawn_log, day=day)


def _run_retention(cfg: OpsConfig, *, now: float) -> None:
    """Prune raw/history streams + reclaim the WAL — bot-down window only.

    Non-fatal: any failure is logged + alerted but never aborts the restart
    (data hygiene must not block bringing the bot back up). The reclaiming WAL
    checkpoint is safe here because the bot is confirmed stopped (no concurrent
    writer to contend the exclusive lock).

    storage-split (round 4 fix): runs TWICE — the trading db_path scoped to
    ``RETENTION_SPEC_TRADING`` (gate_events), and (when the marketdata sibling
    has booted) the marketdata sibling scoped to ``RETENTION_SPEC_MARKETDATA``
    (bars/baseline/watchlist_focus/shadow/altdata — the actual firehose that
    accumulates post-split). A single combined-spec pass against only
    ``cfg.db_path`` either pruned the wrong permanently-stale copy or left the
    marketdata file's WAL to grow unbounded (never checkpointed).

    P3 promotion (2026-07-16, vault/50_research/built-not-wired-audit.md) —
    probe-sidecar WAL hygiene gap: ``data/probes.sqlite`` IS pruned live every
    30min (``retention_producer._prune_probe_blocking``, wired 2026-07-09), but
    NOTHING ever ran its RECLAIMING checkpoint (only PASSIVE auto-checkpoints
    at sqlite's default 1000-page threshold) — the trading/marketdata siblings
    get one here every day, the probe sidecar never did. A third pass (prune +
    reclaim, same ``checkpoint_wal`` this function already uses for the other
    two) closes that gap; failure here is independently caught (own try/except)
    so a probe-DB fault can never suppress the trading/marketdata hygiene above.
    """
    try:
        from polaris.storage.retention import (
            RETENTION_SPEC_MARKETDATA,
            RETENTION_SPEC_TRADING,
            run_retention_job,
        )
        from polaris.storage.schema_marketdata import marketdata_db_path_for

        result = run_retention_job(
            cfg.db_path, now_ts=int(now), spec=RETENTION_SPEC_TRADING,
        )
        deleted = {k: v for k, v in result.items() if not k.startswith("__")}
        md_deleted: dict[str, int] = {}
        md_path = marketdata_db_path_for(cfg.db_path)
        if md_path.exists():
            md_result = run_retention_job(
                md_path, now_ts=int(now), spec=RETENTION_SPEC_MARKETDATA,
            )
            md_deleted = {k: v for k, v in md_result.items() if not k.startswith("__")}
        with open(cfg.restart_log, "a", encoding="utf-8") as fh:
            fh.write(
                f"{iso_utc(now)} retention deleted={deleted} md_deleted={md_deleted}\n"
            )
    except Exception as exc:  # noqa: BLE001 — hygiene must never block restart
        alerting.notify(
            cfg, "retention_failed",
            f"retention/WAL hygiene skipped (non-fatal): {exc}",
        )

    # Probe sidecar (P3 promotion — see the docstring note above): own
    # try/except so a fault here can NEVER suppress the trading/marketdata
    # hygiene pass above (already committed by this point).
    try:
        from polaris.core.probes.tuning_log import open_probe_db
        from polaris.storage.retention import checkpoint_wal, run_probe_retention

        probe_path = cfg.db_path.parent / "probes.sqlite"
        if probe_path.exists():
            pconn = open_probe_db(probe_path)
            try:
                probe_deleted = run_probe_retention(pconn)
            finally:
                pconn.close()
            checkpoint_wal(probe_path)
            with open(cfg.restart_log, "a", encoding="utf-8") as fh:
                fh.write(f"{iso_utc(now)} probe_retention deleted={probe_deleted}\n")
    except Exception as exc:  # noqa: BLE001 — hygiene must never block restart
        alerting.notify(
            cfg, "probe_retention_failed",
            f"probe-sidecar WAL hygiene skipped (non-fatal): {exc}",
        )


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
        _run_retention(cfg, now=now_f)  # prune raw/history streams + reclaim WAL
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
