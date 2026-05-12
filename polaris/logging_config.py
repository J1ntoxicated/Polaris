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
from pathlib import Path
from typing import Final

__all__ = [
    "DEFAULT_DATEFMT",
    "DEFAULT_FORMAT",
    "DEFAULT_LOG_FILE",
    "setup_polaris_logging",
]

DEFAULT_FORMAT: Final[str] = (
    "%(asctime)s.%(msecs)03dZ [%(levelname)s] %(name)s:%(lineno)d %(message)s"
)
DEFAULT_DATEFMT: Final[str] = "%Y-%m-%dT%H:%M:%S"
DEFAULT_LOG_FILE: Final[str] = "data/paper/polaris_runtime.log"


def setup_polaris_logging(
    level: str = "INFO",
    log_file: str | None = None,
    *,
    fmt: str = DEFAULT_FORMAT,
    datefmt: str = DEFAULT_DATEFMT,
    force: bool = True,
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
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, mode="a", encoding="utf-8"))

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
    # per OKX REST call). Polaris venue adapters re-log the salient outcome
    # at DEBUG so we keep observability without drowning the core layer.
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
