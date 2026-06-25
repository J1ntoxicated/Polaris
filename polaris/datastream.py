"""Polaris data-stream sink — structured per-tick / per-update records.

Spec source:
- Jin directive 2026-06-25: "운영로그 ↔ 데이터로그 분리, 데이터는 따로 기록해
  배치 모니터링" (operational log vs data log split; the data goes to its own
  parseable sink for batch monitoring).

The runtime log (``data/paper/polaris_runtime.log`` via ``logging_config``) is
the OPERATOR surface — ERROR / ops WARNING / fills / gate open-close / lifecycle.
The high-cardinality per-tick / per-update DATA (regime-active membership,
seam2-confirm cfg, NO_FIRE verdicts, cell + learner updates) is firehose volume
(472k INFO lines / 128 MB / 0 rotation on the live log) that buries the ~9 real
ERROR signals. That data is routed HERE instead: a dedicated ``polaris.datastream``
logger (``propagate=False`` so it never reaches the runtime root) writing one
JSON object per line (JSONL) to a rotating file, so a batch monitor can ``jq`` /
stream it without parsing free-form prose.

Behaviour-neutral: this module only ROUTES log emission. No trade decision,
DB write, order, or signal value changes — ``emit`` is a structured-logging
helper, not a control-flow hook. When ``setup_datastream`` was never called
(tests, replay, smoke), ``emit`` is a no-op (the dedicated logger has no
handler and ``propagate=False``), so nothing is written and nothing leaks to
the runtime log.

Rotation: ``RotatingFileHandler(maxBytes, backupCount)`` caps total on-disk
size (the firehose previously grew unbounded → an ENOSPC risk). The default
ceiling is ``_DATASTREAM_MAX_BYTES * (backupCount + 1)``.
"""

from __future__ import annotations

import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final

__all__ = [
    "DATASTREAM_DEFAULT_FILE",
    "DATASTREAM_LOGGER_NAME",
    "emit",
    "setup_datastream",
]

DATASTREAM_LOGGER_NAME: Final[str] = "polaris.datastream"
DATASTREAM_DEFAULT_FILE: Final[str] = "data/paper/data_stream.jsonl"

# Per-file rotation ceiling (bytes) + how many rotated backups to keep. The
# data stream is the high-volume sink, so the cap is generous but BOUNDED:
# 128 MB × (5 + 1) = 768 MB max on disk, then the oldest is dropped. This is
# the unbounded-growth backstop (the live runtime log hit 128 MB with no cap).
_DATASTREAM_MAX_BYTES: Final[int] = 128 * 1024 * 1024
_DATASTREAM_BACKUP_COUNT: Final[int] = 5


class _JsonlFormatter(logging.Formatter):
    """Emit each record's ``msg`` (already a JSON string) verbatim, one per line.

    ``emit`` serialises the structured payload to JSON and passes it as the log
    message, so the formatter just returns it — no prefix, no level, no module
    (those live inside the JSON object). This keeps every line a standalone,
    ``json.loads``-able record for batch tooling.
    """

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


def setup_datastream(
    log_file: str | None = DATASTREAM_DEFAULT_FILE,
    *,
    max_bytes: int = _DATASTREAM_MAX_BYTES,
    backup_count: int = _DATASTREAM_BACKUP_COUNT,
) -> None:
    """Install the rotating JSONL handler on the ``polaris.datastream`` logger.

    Idempotent: existing handlers are dropped first so re-invoking from a
    daily restart / re-entry is safe. ``propagate=False`` keeps data records
    OFF the runtime root logger (operator surface stays lean).

    Parameters
    ----------
    log_file
        JSONL output path; parent dir is created on demand. ``None`` installs
        no handler (data stream disabled — every ``emit`` becomes a no-op).
    max_bytes / backup_count
        ``RotatingFileHandler`` cap + retained backups (unbounded-growth
        backstop). ``max_bytes=0`` disables size rotation (handler still set).
    """
    lg = logging.getLogger(DATASTREAM_LOGGER_NAME)
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()
    lg.propagate = False
    lg.setLevel(logging.INFO)
    if log_file is None:
        return
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(_JsonlFormatter())
    lg.addHandler(handler)


def emit(event: str, **fields: Any) -> None:
    """Write ONE structured data record (JSONL) to the data-stream sink.

    No-op when ``setup_datastream`` was never called (no handler +
    ``propagate=False`` → nothing written, nothing leaks to the runtime log).
    ``ts`` is stamped automatically (epoch seconds, float) unless the caller
    supplies it. Field values must be JSON-serialisable; a non-serialisable
    value falls back to ``str`` so a data record never raises into the hot path.
    """
    lg = logging.getLogger(DATASTREAM_LOGGER_NAME)
    if not lg.handlers:
        return
    payload: dict[str, Any] = {"ts": time.time(), "event": event}
    payload.update(fields)
    try:
        line = json.dumps(payload, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        # ``default=str`` covers non-serialisable VALUES, but a pathological
        # non-scalar dict KEY (or a value that re-raises) could still trip
        # ``json.dumps``. A data record must NEVER raise into the hot path
        # (tick loop / trade close / bar read), so fall back to a stringified
        # record rather than propagate.
        line = json.dumps(
            {"ts": payload["ts"], "event": event, "raw": str(fields)},
            separators=(",", ":"),
        )
    lg.info(line)
