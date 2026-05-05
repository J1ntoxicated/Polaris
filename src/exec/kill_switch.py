"""Kill switch — global emergency entry halt (file-based + in-memory).

Phase 14: Simple file-based flag for emergency halt of new entries.
Both paper and live runners check this before placing orders.

Three sources (any active → halt):
    1. In-memory flag (set via set_kill_switch(True))
    2. File at KILL_SWITCH_PATH (touch to activate, rm to deactivate)
    3. Env var POLARIS_KILL_SWITCH=1

The file path can be customized via env POLARIS_KILL_SWITCH_PATH.
This is simple by design — emergency response, no fancy plumbing.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_KILL_SWITCH_PATH = Path("/tmp/polaris_kill_switch")

_in_memory_active: bool = False


def _get_kill_switch_path() -> Path:
    p = os.environ.get("POLARIS_KILL_SWITCH_PATH")
    return Path(p) if p else DEFAULT_KILL_SWITCH_PATH


def is_kill_switch_active() -> bool:
    """Return True if any kill switch source is active.

    Cheap to call (file existence check + env var check). Used per-tick.
    """
    if _in_memory_active:
        return True
    if os.environ.get("POLARIS_KILL_SWITCH") == "1":
        return True
    if _get_kill_switch_path().exists():
        return True
    return False


def set_kill_switch(active: bool, persist: bool = False) -> None:
    """Toggle the in-memory kill switch.

    persist=True writes (or removes) the file flag too — survives restart.
    """
    global _in_memory_active
    _in_memory_active = active
    if persist:
        path = _get_kill_switch_path()
        if active:
            path.touch()
        elif path.exists():
            path.unlink()


def reset_kill_switch() -> None:
    """Test helper — clear in-memory flag + remove file."""
    global _in_memory_active
    _in_memory_active = False
    p = _get_kill_switch_path()
    if p.exists():
        p.unlink()
