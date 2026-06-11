"""Incremental bot-log scan: count health markers in new bytes only.

State ({inode, offset} under "log_scan" in ops_state.json) makes each
5-minute watchdog pass read only the fresh tail; an inode change
(rotation) or shrink resets the offset.
"""

from __future__ import annotations

import os
import re

from tools.ops.ops_config import OpsConfig, load_state, save_state

MARKER_PATTERNS: dict[str, re.Pattern[str]] = {
    "stall": re.compile(r"\[tick-engine\] STALL detected"),
    "db_lock": re.compile(r"database is locked"),
    "ws_error": re.compile(r"ws\] connection error"),
    "ws_gave_up": re.compile(r"giving up, REST-only mode"),
}


def scan(cfg: OpsConfig) -> dict[str, int]:
    counts = {key: 0 for key in MARKER_PATTERNS}
    try:
        st = os.stat(cfg.bot_log)
    except FileNotFoundError:
        return counts

    state = load_state(cfg.state_path)
    prev = state.get("log_scan")
    if not isinstance(prev, dict):
        prev = {}
    offset = prev.get("offset", 0)
    if (
        prev.get("inode") != st.st_ino
        or not isinstance(offset, int)
        or offset > st.st_size
    ):
        offset = 0

    with open(cfg.bot_log, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
        new_offset = fh.tell()

    for line in data.decode("utf-8", errors="replace").splitlines():
        for key, pattern in MARKER_PATTERNS.items():
            if pattern.search(line):
                counts[key] += 1

    state["log_scan"] = {"inode": st.st_ino, "offset": new_offset}
    save_state(cfg.state_path, state)
    return counts
