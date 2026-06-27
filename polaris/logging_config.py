"""Polaris centralized logging config.

Spec source:
- Jin mandate 2026-05-07 ("로그에 안 찍히는건 없는거니까 다 찍히게 하고")
- vault/_NOW.md (P1 verbose logging patch — every core decision must be observable)

Format: ``<ISO-UTC ts>.<ms>Z [LEVEL] <module>:<lineno> <message>``.
Levels:
- DEBUG — per-decision detail (signal-by-signal, fill-by-fill)
- INFO  — per-event milestones (gate decisions, sizing outcomes, learner commits)
- WARN  — degraded paths (fail-open, snapshot rotate, token refresh)
- ERROR — block / abort (gate KILL, circuit halt)

Output: unbuffered stdout + optional rotating-style file handler.
The file handler appends to ``data/paper/polaris_runtime.log`` when invoked
with a ``log_file`` argument; rotation cap is left to operator (logrotate /
manual truncate) so the writer never blocks on rename.

Security: this module **does not** log values. Callers must scrub credentials
(API keys, CST, X-SECURITY-TOKEN, OKX passphrase) before passing them to a
``logger.*`` call. The convention is to log ``ord_id`` / ``deal_ref`` /
``client_order_id`` instead of the auth tokens themselves.
"""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

__all__ = [
    "DEFAULT_DATEFMT",
    "DEFAULT_FORMAT",
    "DEFAULT_LOG_FILE",
    "RUNTIME_LOG_BACKUP_COUNT",
    "RUNTIME_LOG_MAX_BYTES",
    "setup_polaris_logging",
]

DEFAULT_FORMAT: Final[str] = (
    "%(asctime)s.%(msecs)03dZ [%(levelname)s] %(name)s:%(lineno)d %(message)s"
)
DEFAULT_DATEFMT: Final[str] = "%Y-%m-%dT%H:%M:%S"
DEFAULT_LOG_FILE: Final[str] = "data/paper/polaris_runtime.log"

# Runtime-log rotation ceiling (bytes) + retained backups. The runtime log is
# now LEAN (the per-tick data firehose is routed to ``polaris.datastream``), so
# a 64 MB × (5 + 1) = 384 MB on-disk cap is ample. This is the unbounded-growth
# backstop: the previous plain ``FileHandler`` grew to 128 MB with NO rotation
# (an ENOSPC risk). Operators keep the same path; only the writer rotates.
RUNTIME_LOG_MAX_BYTES: Final[int] = 64 * 1024 * 1024
RUNTIME_LOG_BACKUP_COUNT: Final[int] = 5


def setup_polaris_logging(
    level: str = "INFO",
    log_file: str | None = None,
    *,
    fmt: str = DEFAULT_FORMAT,
    datefmt: str = DEFAULT_DATEFMT,
    force: bool = True,
    max_bytes: int = RUNTIME_LOG_MAX_BYTES,
    backup_count: int = RUNTIME_LOG_BACKUP_COUNT,
) -> None:
    """Install handlers + format on the root logger.

    Idempotent across calls when ``force=True`` (default): existing handlers
    are dropped so re-invoking from tests / re-entry is safe.

    Parameters
    ----------
    level
        ``DEBUG`` / ``INFO`` / ``WARN`` / ``ERROR`` (case-insensitive).
    log_file
        When provided, append to this path; parent dir is created on demand.
        ``None`` (default) sends logs only to stdout.
    fmt / datefmt
        Override the canonical format. The default emits ISO-UTC ts with
        millisecond precision so we can sort events deterministically.
    force
        Forwarded to ``logging.basicConfig`` — drop existing root handlers
        first. Tests rely on this so the latest call wins.
    max_bytes / backup_count
        ``RotatingFileHandler`` cap + retained backups for the file handler
        (unbounded-growth backstop). ``max_bytes=0`` disables size rotation.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                mode="a",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=force,
    )
    # UTC timestamps so multi-host log merging is unambiguous (we never want
    # a host-local TZ to silently shift a 2026-05-07 trade onto 2026-05-08).
    logging.Formatter.converter = time.gmtime
    # Suppress noisy 3rd-party DEBUG output (httpx/httpcore emit ~30 lines
    # per OKX REST call; websockets logs EVERY inbound quote frame — under -vv
    # that was 95% of the live log, ~2k lines/min, an I/O + logging-lock load
    # that starved the tick loop on quote floods). yfinance + its peewee-backed
    # tz cache log every history()/get()/_make_request()/SQL at DEBUG — once the
    # full-universe Yahoo bar fill (STEP①) started, that was 79% of the live log
    # (1580/2000 lines), the same disk + logging-lock pollution. Polaris venue
    # adapters re-log the salient outcome at DEBUG so we keep observability
    # without the drown.
    for noisy in ("httpx", "httpcore", "asyncio", "websockets", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # yfinance is pinned harder: it emits "$X: possibly delisted; no price data
    # found" at ERROR from inside its own history() for an unmapped/delisted
    # ticker, and ERROR ≥ WARNING leaks past the WARNING pin above (one line per
    # bar-period per such symbol). The Polaris wrapper (_yahoo_bars) already
    # fallbacks + per-period caches (degrade-never-crash), so CRITICAL silences
    # the library noise with zero behaviour change — the bar still flows.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
