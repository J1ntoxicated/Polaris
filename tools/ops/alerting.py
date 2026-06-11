"""Local-only alerting: ops_alerts.log (always) + macOS banner (throttled).

No external service. The log file is the source of truth; the osascript
banner is best-effort and its failure is swallowed.
"""

from __future__ import annotations

import contextlib
import subprocess
import time

from tools.ops.ops_config import (
    ALERT_THROTTLE_SEC,
    OpsConfig,
    iso_utc,
    load_state,
    save_state,
)


def _send_banner(text: str) -> None:
    safe = text.replace('"', "'")
    subprocess.run(
        ["osascript", "-e", f'display notification "{safe}" with title "Polaris"'],
        capture_output=True,
        timeout=10,
        check=False,
    )


def notify(
    cfg: OpsConfig,
    key: str,
    msg: str,
    *,
    throttle_sec: float = ALERT_THROTTLE_SEC,
    now: float | None = None,
) -> bool:
    """Append to ops_alerts.log (always) and banner unless key is throttled.

    Returns True when the banner was (attempted to be) sent, False when
    suppressed by the per-key throttle.
    """
    now_f = time.time() if now is None else now
    cfg.ops_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.alerts_log, "a", encoding="utf-8") as fh:
        fh.write(f"{iso_utc(now_f)} [{key}] {msg}\n")

    state = load_state(cfg.state_path)
    alerts = state.get("alerts")
    if not isinstance(alerts, dict):
        alerts = {}
    last = alerts.get(key)
    if isinstance(last, int | float) and now_f - float(last) < throttle_sec:
        return False
    # Banner loss is acceptable — the log is the truth source.
    with contextlib.suppress(Exception):
        _send_banner(f"{key}: {msg}")
    alerts[key] = now_f
    state["alerts"] = alerts
    save_state(cfg.state_path, state)
    return True
