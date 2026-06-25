"""Hardening #5 (2026-06-23) — LOUD, validated asset_class fallback (SSOT).

A position/bar whose ``underlying_group_id`` lacks a KNOWN canonical prefix (or
whose universe JOIN yields a NULL asset_class) used to fall back SILENTLY to a
flat ``'crypto'`` — a mis-prefixed or aged-out instrument was invisible AND
mis-classed (Capital CFDs lumped into crypto). This module makes the fallback:

- VALIDATED — the resolved ``':'``-prefix must be in :data:`KNOWN_ASSET_CLASSES`;
  a garbage prefix is treated as a fallback, never silently accepted;
- LOUD — a WARN log + an IDEMPOTENT ``asset_class_fallback`` ``risk_events`` row
  (one per distinct venue/symbol/source) so the dashboard can COUNT it;
- venue-CORRECT — the default is per-venue (OKX→crypto, Capital→forex,
  Alpaca→equity) instead of a flat ``'crypto'``.

Pure observability + a correct-er default. No entry is blocked/sized/exited
(flow_not_block intact). The ``pnl_usd`` dollar truth is never touched.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from typing import Final

from polaris.datastream import emit as datastream_emit

logger = logging.getLogger(__name__)

# Fallback-WARN dedup (logging only). The ``risk_events`` row is already
# idempotent (``INSERT OR IGNORE`` on ``acfb:source:venue:symbol``), but the
# WARN re-fired on every read of the same mis-classed instrument (14k identical
# lines on the live log). This module-level set records which fallbacks have
# already been WARNed so the runtime log gets ONE per distinct instrument and
# the per-read detail goes to the datastream sink. Mirrors the DB idempotency
# key. Single-process loop; does not gate any decision.
_FALLBACK_WARNED: set[tuple[str, str, str]] = set()

# The canonical asset_class vocabulary (Layer 1 group_id prefixes). A resolved
# prefix outside this set is a mis-prefix, not a real class.
KNOWN_ASSET_CLASSES: Final[frozenset[str]] = frozenset(
    {"crypto", "forex", "index", "commodity", "equity"}
)

# Venue-correct default when neither the universe JOIN nor a KNOWN group_id
# prefix resolves a class. Capital is a CFD venue (forex/index/commodity) — a
# flat 'crypto' was wrong; 'forex' is the safe non-crypto default.
_VENUE_DEFAULT_CLASS: Final[dict[str, str]] = {
    "okx": "crypto",
    "capital": "forex",
    "alpaca": "equity",
}


def _venue_default(venue: str) -> str:
    return _VENUE_DEFAULT_CLASS.get((venue or "").lower(), "crypto")


def _record_fallback(
    conn: sqlite3.Connection | None,
    *,
    venue: str,
    symbol: str,
    source: str,
    group_id: str,
    resolved: str,
) -> None:
    """WARN + append ONE idempotent ``asset_class_fallback`` risk_events row.

    The ``risk_event_id`` is deterministic (``acfb:source:venue:symbol``) so
    repeated reads for the same fallback instrument collapse to a single row
    (``INSERT OR IGNORE``) — the count is the number of DISTINCT mis-classed
    instruments, not a per-read flood. FAIL-OPEN: a missing table / any sqlite
    error is swallowed (a telemetry write must never break the read path).
    """
    dedup_key = (source, venue, symbol)
    # Dedup: ONE WARN per distinct instrument (mirrors the DB idempotency key);
    # the per-read detail goes to the datastream sink.
    if dedup_key not in _FALLBACK_WARNED:
        _FALLBACK_WARNED.add(dedup_key)
        logger.warning(
            "[asset_class] fallback (%s): %s:%s group_id=%r → %s "
            "(no universe class / unknown prefix)",
            source, venue, symbol, group_id, resolved,
        )
    datastream_emit(
        "asset_class/fallback",
        source=source,
        venue=venue,
        symbol=symbol,
        group_id=group_id,
        resolved=resolved,
    )
    if conn is None:
        return
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "INSERT OR IGNORE INTO risk_events "
            "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
            "VALUES (?, '', 'asset_class_fallback', strftime('%s','now'), ?)",
            (
                f"acfb:{source}:{venue}:{symbol}",
                f'{{"venue":"{venue}","symbol":"{symbol}",'
                f'"group_id":"{group_id}","resolved":"{resolved}","source":"{source}"}}',
            ),
        )


def resolve_asset_class(
    *,
    universe_class: str | None,
    venue: str,
    symbol: str,
    group_id: str,
    source: str,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Resolve a validated asset_class, recording a LOUD fallback when needed.

    Resolution order:
    1. ``universe_class`` when present AND in :data:`KNOWN_ASSET_CLASSES`.
    2. the ``group_id`` ``':'``-prefix when in :data:`KNOWN_ASSET_CLASSES`
       (LOUD fallback — the universe JOIN was empty/aged-out).
    3. the venue-correct default (LOUD fallback — neither resolved a known class).

    A non-None ``conn`` enables the idempotent ``asset_class_fallback`` counter.
    """
    if universe_class and universe_class in KNOWN_ASSET_CLASSES:
        return universe_class
    prefix = group_id.split(":", 1)[0] if ":" in group_id else ""
    if prefix in KNOWN_ASSET_CLASSES:
        # Universe JOIN was empty (aged-out held name) but the canonical prefix
        # is valid — LOUD (the instrument fell out of the active universe).
        _record_fallback(
            conn, venue=venue, symbol=symbol, source=source,
            group_id=group_id, resolved=prefix,
        )
        return prefix
    # Garbage / missing prefix → venue-correct default, counted not accepted.
    resolved = _venue_default(venue)
    _record_fallback(
        conn, venue=venue, symbol=symbol, source=source,
        group_id=group_id, resolved=resolved,
    )
    return resolved


def resolve_bar_asset_class(
    *,
    venue: str,
    symbol: str,
    group_id: str,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Bar-path asset_class: the canonical ``group_id`` prefix is the PRIMARY SSOT.

    Unlike :func:`resolve_asset_class` (held positions, where the universe JOIN is
    primary and the prefix is a fallback), the 1m-bar baseline partitions BY the
    canonical ``underlying_group_id`` prefix (P2.1) — a KNOWN prefix is the normal
    path and stays SILENT. Only a MISSING/garbage prefix is a LOUD, validated,
    venue-correct fallback (WARN + idempotent counter). No behavior change for the
    overwhelming common case (a well-formed ``crypto:`` / ``forex:`` group_id).
    """
    prefix = group_id.split(":", 1)[0] if ":" in group_id else ""
    if prefix in KNOWN_ASSET_CLASSES:
        return prefix
    resolved = _venue_default(venue)
    _record_fallback(
        conn, venue=venue, symbol=symbol, source="bar_baseline",
        group_id=group_id, resolved=resolved,
    )
    return resolved
