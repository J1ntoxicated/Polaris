"""Alpaca market calendar + PDT data provider.

Spec source:
- Alpaca ``GET /v2/clock`` — ``is_open`` / ``next_open`` / ``next_close`` /
  ``timestamp`` (US equity RTH session state).
- Alpaca ``GET /v2/account`` — ``daytrade_count`` / ``pattern_day_trader``
  (the data the T13 PDT gate consumes; the GATE itself is T13, not here).

This module is a thin DATA PROVIDER: it parses the venue payloads into small
frozen structs and supplies the equity-session ``is_open`` flag (cached ~1 min
on the adapter). It does NOT decide anything — no gate, no throttle. Equity
streams are session-bound by venue reality (market hours), which is an external
calendar fact, not a defensive size dampener.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "ALPACA_CLOCK_PATH",
    "AlpacaClock",
    "parse_account_pdt",
    "parse_clock",
]

ALPACA_CLOCK_PATH: Final[str] = "/v2/clock"
# Clock is cached on the adapter for this long (the session edge moves on the
# minute scale; ~1 min keeps the open/closed flag fresh without per-tick HTTP).
CLOCK_CACHE_TTL_SEC: Final[float] = 60.0


@dataclass(frozen=True, slots=True)
class AlpacaClock:
    """Parsed ``/v2/clock`` snapshot (US-equity session state)."""

    is_open: bool
    next_open: str
    next_close: str
    timestamp: str


def parse_clock(body: dict[str, Any]) -> AlpacaClock:
    """Parse a ``/v2/clock`` payload into an :class:`AlpacaClock`."""
    return AlpacaClock(
        is_open=bool(body.get("is_open")),
        next_open=str(body.get("next_open") or ""),
        next_close=str(body.get("next_close") or ""),
        timestamp=str(body.get("timestamp") or ""),
    )


def parse_account_pdt(body: dict[str, Any]) -> tuple[int, bool]:
    """Extract ``(daytrade_count, pattern_day_trader)`` from ``/v2/account``.

    Pure parse — supplies the T13 PDT gate its inputs. No decision is made here.
    """
    try:
        dt_count = int(body.get("daytrade_count") or 0)
    except (TypeError, ValueError):
        dt_count = 0
    return dt_count, bool(body.get("pattern_day_trader"))
