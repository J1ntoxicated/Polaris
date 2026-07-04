"""Layer 0 — cowork watchlist-intel seed reader (Track C / Alpaca, additive).

DEMO/PAPER only. Reads the cowork-produced feed at ``data/intel/alpaca_seed.json``
(contract: ``cowork/watchlist_intel/CONTRACT.md``) and returns a symbol → seed_tag
map for the universe union + signal seed_tag stamping. This is a rank-uplift /
additive-candidate SIGNAL only — it never blocks, removes, or reorders an
existing universe candidate (flow_not_block / aggressive_always_profit).

Fail-safe by construction: any of {file absent, malformed JSON, expired
``expiry_ts``, missing ``expiry_ts``, ``_sample: true`` fixture} degrades to an
EMPTY result (the bot's normal universe, unseeded) — never an exception, never a
stuck/stale seed. Individual malformed candidate rows are skipped without
failing the whole file (one bad row must not sink the good ones).

Cached by (path, mtime) so the discovery-cadence hot path does not re-read +
re-parse the file on every call when it hasn't changed on disk.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# Repo-relative drop path (CONTRACT.md) — new ``data/intel/`` namespace, mirrors
# the learner-snapshot Path-constant convention (``LEARNER_SNAPSHOT_DIR``).
DEFAULT_ALPACA_SEED_PATH: Final[Path] = Path("data/intel/alpaca_seed.json")

# thesis_tag allowed values (CONTRACT.md) — a candidate with any other tag is a
# malformed row (skipped, not fatal).
ALLOWED_THESIS_TAGS: Final[frozenset[str]] = frozenset(
    {
        "pead_high",
        "pead",
        "analyst_revision",
        "sector_rs",
        "volume_breakout",
        "insider_buy",
        "short_squeeze",
    }
)


@dataclass(frozen=True, slots=True)
class IntelSeedResult:
    """Loaded seed feed — symbol → seed_tag map + the symbol set (union input)."""

    seed_tags: dict[str, str] = field(default_factory=dict)
    symbols: frozenset[str] = frozenset()


_EMPTY_RESULT: Final[IntelSeedResult] = IntelSeedResult()

# mtime-keyed cache: {resolved_path_str: (mtime, IntelSeedResult)}. Module-level
# (single-process bot) — a changed mtime (new cowork run overwrites the file)
# invalidates; a missing file is cached too (avoids a stat() + open() attempt
# storm when the feed simply hasn't been produced yet).
_CACHE: dict[str, tuple[float, IntelSeedResult]] = {}


def _parse_iso8601_utc(value: Any) -> float | None:
    """Parse an ISO-8601 UTC timestamp (``...Z`` or ``+00:00``) to epoch seconds."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _validate_candidate(raw: Any) -> tuple[str, str] | None:
    """Validate one ``candidates[]`` row → ``(symbol, thesis_tag)`` or ``None``.

    A bad row (missing/wrong-typed field, out-of-range score, unknown
    thesis_tag, no evidence URL) is SKIPPED — never raises, never fails the rest
    of the file (CONTRACT hygiene: "no evidence URL -> drop", score in [0,1]).
    """
    if not isinstance(raw, dict):
        return None
    symbol = raw.get("symbol")
    thesis_tag = raw.get("thesis_tag")
    score = raw.get("score")
    evidence = raw.get("evidence")
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    if not isinstance(thesis_tag, str) or thesis_tag not in ALLOWED_THESIS_TAGS:
        return None
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    if not (0.0 <= float(score) <= 1.0):
        return None
    if not isinstance(evidence, list) or not evidence:
        return None
    if not all(isinstance(u, str) and u.strip() for u in evidence):
        return None
    return symbol.strip().upper(), thesis_tag


def _parse_feed(raw_text: str, *, now_ts: int) -> IntelSeedResult:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("[intel_seed] malformed JSON — treating feed as absent: %r", exc)
        return _EMPTY_RESULT
    if not isinstance(payload, dict):
        logger.warning("[intel_seed] feed root is not an object — ignored")
        return _EMPTY_RESULT
    # Sample/illustrative fixture (example_watchlist_intel.json convention) —
    # never seeds, regardless of otherwise-valid shape.
    if payload.get("_sample") is True:
        return _EMPTY_RESULT
    expiry_epoch = _parse_iso8601_utc(payload.get("expiry_ts"))
    # Fail-safe: absent OR unparsable expiry_ts means freshness can't be proven
    # → treat as absent (never seed off an un-timestamped file).
    if expiry_epoch is None or expiry_epoch <= float(now_ts):
        return _EMPTY_RESULT
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return _EMPTY_RESULT
    seed_tags: dict[str, str] = {}
    skipped = 0
    for raw_candidate in candidates:
        validated = _validate_candidate(raw_candidate)
        if validated is None:
            skipped += 1
            continue
        symbol, thesis_tag = validated
        seed_tags[symbol] = thesis_tag
    if skipped:
        logger.warning(
            "[intel_seed] skipped %d malformed candidate row(s) (kept %d)",
            skipped, len(seed_tags),
        )
    return IntelSeedResult(seed_tags=seed_tags, symbols=frozenset(seed_tags))


def load_intel_seed(
    path: Path | str | None = None,
    *,
    now_ts: int | None = None,
) -> IntelSeedResult:
    """Load + validate the cowork Alpaca intel seed feed (fail-safe, cached).

    Returns ``IntelSeedResult(seed_tags={}, symbols=frozenset())`` for: file
    absent, malformed JSON, non-object root, ``_sample: true``, expired/missing
    ``expiry_ts``, or a non-list ``candidates``. Individual malformed candidate
    rows are dropped without affecting the rest. Re-reads only when the file's
    mtime has changed since the last call for this path (hot-path cache).

    ``path`` defaults to the MODULE-LEVEL ``DEFAULT_ALPACA_SEED_PATH`` read at
    CALL time (not bound into the signature) so a test's
    ``monkeypatch.setattr(intel_seed, "DEFAULT_ALPACA_SEED_PATH", ...)``
    actually takes effect — a literal default-arg would freeze the original
    Path object at function-definition time and silently ignore the patch.
    """
    resolved = Path(path if path is not None else DEFAULT_ALPACA_SEED_PATH)
    ts = now_ts if now_ts is not None else int(time.time())
    cache_key = str(resolved)
    try:
        mtime = resolved.stat().st_mtime
    except OSError:
        # Missing file (or unreadable) — quiet no-op, matches CONTRACT's
        # "file absent = quiet no-op" fail-safe. Cache the miss too so a hot
        # discovery loop doesn't stat() every tick when the feed never lands.
        _CACHE[cache_key] = (-1.0, _EMPTY_RESULT)
        return _EMPTY_RESULT

    cached = _CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        raw_text = resolved.read_text()
    except OSError as exc:
        logger.warning("[intel_seed] read failed — treating feed as absent: %r", exc)
        _CACHE[cache_key] = (mtime, _EMPTY_RESULT)
        return _EMPTY_RESULT

    result = _parse_feed(raw_text, now_ts=ts)
    _CACHE[cache_key] = (mtime, result)
    return result


__all__ = [
    "ALLOWED_THESIS_TAGS",
    "DEFAULT_ALPACA_SEED_PATH",
    "IntelSeedResult",
    "load_intel_seed",
]
