"""Ops automation SSOT: paths, timings, start command, shared state helpers.

Every automation entry point (watchdog / daily restart / daily digest /
manual wrappers) goes through this single config so there is exactly one
pidfile convention, one sentinel, one start command (CLAUDE.md Quick
reference, absolute paths) on this machine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_DIR = Path("/Users/jinyoon/Projects/Polaris")

# --- timings (seconds) -----------------------------------------------------
ALERT_THROTTLE_SEC = 1800.0          # default per-key banner throttle
MANUAL_REMINDER_SEC = 86400.0        # MANUAL_STOP "still down" reminder
RESTART_ALERT_THROTTLE_SEC = 300.0   # bot_dead_restarted: dedup within one cycle
LOCK_STALE_SEC = 600.0               # stale start.lock: age AND owner-dead required
START_VERIFY_SEC = 20.0              # post-spawn survival check delay
STOP_WAIT_SEC = 60                   # SIGTERM → exit wait (1s polls); NEVER escalate
RESTART_SETTLE_SEC = 120.0           # daily restart: wait before wal_after stat
WEDGE_AGE_SEC = 600.0                # alive PID + silent log → alert only
FLAP_UPTIME_SEC = 300.0              # death within 300s of start = flap event
FLAP_WINDOW_SEC = 3600.0             # 3 flap events inside 1h …
FLAP_COUNT = 3
FLAP_BACKOFF_SEC = 1800.0            # … → retry interval relaxes to 30min (no halt)
WAL_ALERT_BYTES = 512 * 1024 * 1024  # early warning well before daily restart
LOG_KEEP = 14                        # rotated bot logs retained
DURATION_SEC = 172_800               # 48h backstop; daily restart is the cadence

# Rejection keywords (CLAUDE.md mandate): swept at runtime over the rendered
# digest because strategy_id/fault_type are DB-borne free strings.
REJECTION_KEYWORDS: tuple[str, ...] = (
    "12주",
    "90d gate",
    "monthly review",
    "regrets/",
    "posture standard",
    "regulatory cap",
    "professional risk",
    "real-money safety",
    "fractional Kelly is too aggressive in practice",
    "표본 부족 risk",
    "표본부족",
)


@dataclass(frozen=True)
class OpsConfig:
    project_dir: Path
    db_path: Path
    bot_log: Path
    pidfile: Path
    sentinel: Path
    ops_dir: Path
    vault_dir: Path
    python_bin: Path
    duration_sec: int = DURATION_SEC

    # --- derived paths
    @property
    def state_path(self) -> Path:
        return self.ops_dir / "ops_state.json"

    @property
    def alerts_log(self) -> Path:
        return self.ops_dir / "ops_alerts.log"

    @property
    def restart_log(self) -> Path:
        return self.ops_dir / "ops_restart.log"

    @property
    def spawn_log(self) -> Path:
        return self.ops_dir / "bot_spawn.log"

    @property
    def lock_dir(self) -> Path:
        return self.ops_dir / "start.lock"

    @property
    def wal_path(self) -> Path:
        return self.db_path.with_name(self.db_path.name + "-wal")

    @property
    def env_file(self) -> Path:
        return self.project_dir / ".env"

    @property
    def digest_dir(self) -> Path:
        return self.vault_dir / "40_ops" / "digests" / "daily-auto"

    @property
    def vault_log(self) -> Path:
        return self.vault_dir / "log.md"

    @property
    def start_cmd(self) -> list[str]:
        """CLAUDE.md Quick reference start command — absolute paths only."""
        return [
            str(self.python_bin),
            "-m", "polaris.scripts.ignite_p1",
            "--paper",
            "--duration", str(self.duration_sec),
            "--tick", "5",
            "--full-pipeline",
            "--real-roundtrip",
            "--db", str(self.db_path),
            "-vv",
            "--log-file", str(self.bot_log),
        ]

    @classmethod
    def default(cls) -> OpsConfig:
        vault_env = os.environ.get("POLARIS_VAULT_DIR")
        return cls(
            project_dir=PROJECT_DIR,
            db_path=PROJECT_DIR / "data" / "polaris_live.sqlite",
            bot_log=PROJECT_DIR / "data" / "paper" / "polaris_runtime.log",
            pidfile=PROJECT_DIR / "data" / "paper" / "production.pid",
            sentinel=PROJECT_DIR / "data" / "paper" / "MANUAL_STOP",
            ops_dir=PROJECT_DIR / "data" / "paper" / "ops",
            vault_dir=Path(vault_env) if vault_env else PROJECT_DIR / "vault",
            python_bin=PROJECT_DIR / ".venv" / "bin" / "python",
        )


# --- shared json state (tmp+rename atomic) ---------------------------------

def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
