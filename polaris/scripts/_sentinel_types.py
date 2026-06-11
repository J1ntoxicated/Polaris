"""Sentinel shared types + session clocks — W1 live audit (observe-only).

DEMO/PAPER only. Split out of ``_sentinel_checks`` to keep both files under
the 500 LOC project limit. Holds the ``Finding`` record, env-overridable
``Thresholds`` (display/audit knobs only — nothing here feeds sizing or
entry decisions) and the deterministic session-calendar helpers.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from polaris.core.live_recalc.session_exit_rail import (
    _fx_in_session,
    _minutes_to_rth_close,
)
from polaris.core.streams.config import resolve_stream

__all__ = [
    "SEV_CRITICAL",
    "SEV_INFO",
    "SEV_WARN",
    "Finding",
    "Thresholds",
    "in_session",
]

SEV_INFO = "info"
SEV_WARN = "warn"
SEV_CRITICAL = "critical"

_NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class Finding:
    """One detected invariant/quality violation (observe-only record)."""

    check_id: str
    severity: str  # info | warn | critical
    subject: str  # dedup key together with check_id (venue/position_id/...)
    summary: str
    detail: dict[str, Any]


def _env_num(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Check thresholds — defaults per spec §4, env-overridable (display/audit
    knobs only; nothing here feeds sizing or entry decisions)."""

    s1_warn_sec: float = 30.0
    s1_crit_sec: float = 120.0
    s2_lookback_sec: int = 21600
    s2_grace_sec: int = 120
    s3_lookback_sec: int = 21600
    s3_tol_sec: int = 600
    s3_grace_sec: int = 120
    s3_reject_window_sec: int = 3600
    s3_reject_warn_n: int = 3
    s3_fault_window_sec: int = 3600
    s3_fault_crit_n: int = 30
    s6_window_sec: int = 600
    s6_no_inflow_sec: int = 300
    s6_min_ticks_for_size: int = 50

    @classmethod
    def from_env(cls) -> Thresholds:
        return cls(
            s1_warn_sec=_env_num("POLARIS_SENTINEL_S1_WARN_SEC", 30.0),
            s1_crit_sec=_env_num("POLARIS_SENTINEL_S1_CRIT_SEC", 120.0),
            s2_lookback_sec=int(_env_num("POLARIS_SENTINEL_S2_LOOKBACK_SEC", 21600)),
            s2_grace_sec=int(_env_num("POLARIS_SENTINEL_S2_GRACE_SEC", 120)),
            s3_lookback_sec=int(_env_num("POLARIS_SENTINEL_S3_LOOKBACK_SEC", 21600)),
            s3_tol_sec=int(_env_num("POLARIS_SENTINEL_S3_TOL_SEC", 600)),
            s3_grace_sec=int(_env_num("POLARIS_SENTINEL_S3_GRACE_SEC", 120)),
            s3_reject_window_sec=int(
                _env_num("POLARIS_SENTINEL_S3_REJECT_WINDOW_SEC", 3600)
            ),
            s3_reject_warn_n=int(_env_num("POLARIS_SENTINEL_S3_REJECT_WARN_N", 3)),
            s3_fault_window_sec=int(
                _env_num("POLARIS_SENTINEL_S3_FAULT_WINDOW_SEC", 3600)
            ),
            s3_fault_crit_n=int(_env_num("POLARIS_SENTINEL_S3_FAULT_CRIT_N", 30)),
            s6_window_sec=int(_env_num("POLARIS_SENTINEL_S6_WINDOW_SEC", 600)),
            s6_no_inflow_sec=int(_env_num("POLARIS_SENTINEL_S6_NO_INFLOW_SEC", 300)),
            s6_min_ticks_for_size=int(
                _env_num("POLARIS_SENTINEL_S6_MIN_TICKS_FOR_SIZE", 50)
            ),
        )


# ---------------------------------------------------------------------------
# Session awareness (reuse the existing deterministic calendar clocks)
# ---------------------------------------------------------------------------


def in_session(calendar: str, now_ts: int) -> bool:
    """Is the venue's market in-session at ``now_ts``?

    Reuses the session_exit_rail clocks (same boundaries as the bot):
    ``fx_indices_cal`` → not in the Fri 22:00 → Sun 22:00 UTC weekend window;
    ``us_equity_cal`` → weekday AND inside RTH [09:30, 16:00) ET;
    ``always_on`` / unknown → always in-session (judge everything).
    """
    cal = (calendar or "").strip().lower()
    if cal == "fx_indices_cal":
        return _fx_in_session(now_ts)
    if cal == "us_equity_cal":
        local = dt.datetime.fromtimestamp(int(now_ts), tz=dt.UTC).astimezone(_NY_TZ)
        return local.weekday() < 5 and _minutes_to_rth_close(now_ts) is not None
    return True


def _venue_calendar(venue: str) -> str:
    try:
        return resolve_stream(venue).session_calendar
    except KeyError:
        return "always_on"
