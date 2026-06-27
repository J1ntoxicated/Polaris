"""④ #12 technical store — write-after-compute persistence of the full indicator
set, single-row last-write-wins, read back as judge/probe evidence.

DEMO/PAPER only. AGGRESSIVE bias preserved — EVIDENCE-ONLY/advisory: nothing here
gates an entry/size/exit or touches a sizing multiplier (flow_not_block).

Why this module exists
----------------------
``build_real_market_view`` computes a rich indicator set (rsi/adx/bb/donchian/
atr/ema/momentum) every tick, but it is consumed ONLY by ``generate_raw_signal``
and then dropped — the AI judge's "technicals" evidence is just the 3-metric
ATR/size/volume baseline. This store PERSISTS the already-computed indicators so
the judge (and any probe) can read them as evidence.

Design (the non-negotiables)
----------------------------
* WRITE-AFTER-COMPUTE, never re-compute: ``extract_technicals_from_mv`` pulls the
  numbers straight off the ``MarketView`` the strategy already saw → no second
  source of truth and ZERO rate-limit consumption (pure CPU + SQLite; the OKX
  candles bucket is never touched here).
* SINGLE-ROW LWW: PK = (instrument_id, bar_interval, indicator). A re-write
  OVERWRITES the same row, so the table is bounded by
  instruments × intervals × indicators and can never grow unbounded (#5 guard,
  mirrors the ``quote_ticks`` collapse).
* CURRENCY: every row carries ``computed_ts`` + ``source_bar_ts`` so a stale value
  is visibly stale to the judge (evidence-currency #68/#69), never silently 'current'.
* DEGRADE-NEVER-CRASH: a write/read against a DB without the table is a logged
  no-op (returns 0 / {}), so a missing migration can never crash the tick.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "STORED_INDICATORS",
    "extract_technicals_from_mv",
    "read_technicals",
    "technicals_summary",
    "upsert_technicals",
]

# The MarketView fields persisted (the rich set the judge could not previously
# see). A None field is dropped (not stored as NULL noise) so the table holds
# only indicators that were actually computed for the symbol/interval.
STORED_INDICATORS: tuple[str, ...] = (
    "rsi_14",
    "atr_pct",
    "volume_z",
    "adx_14",
    "momentum_20bar",
    "bb_lower",
    "bb_middle",
    "bb_upper",
    "ma_200",
    "donchian_high_40",
    "donchian_low_40",
    "donchian_high_30",
    "donchian_low_30",
    "ema_20",
    "ema_50",
    "ema_cross",
    "trend_efficiency",
)


def extract_technicals_from_mv(mv: Any) -> dict[str, float]:
    """Pull the persisted indicator set off a ``MarketView`` (no re-compute).

    Reads the SAME numbers the strategy saw — the anti-double-source-of-truth
    guarantee. ``None`` fields are dropped. ``volume_z`` / ``atr_pct`` are floats
    (default 0.0 on the dataclass) and are kept as-is. Non-finite values are
    skipped (a NaN must never reach the store / judge). Pure / no I/O.
    """
    out: dict[str, float] = {}
    for name in STORED_INDICATORS:
        val = getattr(mv, name, None)
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
            continue
        out[name] = f
    return out


def upsert_technicals(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    bar_interval: str,
    values: dict[str, float],
    computed_ts: int,
    source_bar_ts: int,
) -> int:
    """Single-row LWW upsert of ``values`` for one (instrument, interval).

    One row PER indicator (PK = instrument_id + bar_interval + indicator) so a
    re-write overwrites in place — the table is BOUNDED (no append). Returns the
    number of rows written. DEGRADE-NEVER-CRASH: a missing table / sqlite error
    is a logged no-op returning 0 (the tick never crashes on a hygiene write).
    """
    if not values:
        return 0
    rows = [
        (instrument_id, bar_interval, name, float(v), int(computed_ts), int(source_bar_ts))
        for name, v in values.items()
    ]
    try:
        conn.executemany(
            """
            INSERT INTO ticker_technicals
                (instrument_id, bar_interval, indicator, value,
                 computed_ts, source_bar_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, bar_interval, indicator)
            DO UPDATE SET value=excluded.value,
                          computed_ts=excluded.computed_ts,
                          source_bar_ts=excluded.source_bar_ts
            """,
            rows,
        )
    except sqlite3.Error as exc:
        logger.debug(
            "[technicals] upsert skipped (degrade) %s/%s: %r",
            instrument_id, bar_interval, exc,
        )
        return 0
    return len(rows)


def read_technicals(
    conn: sqlite3.Connection, *, instrument_id: str, bar_interval: str
) -> dict[str, dict[str, float]]:
    """Read the stored indicators for (instrument, interval), with currency stamps.

    Returns ``{indicator: {value, computed_ts, source_bar_ts}}`` (empty on a miss
    or a missing table — graceful judge n/a). DEGRADE-NEVER-CRASH.
    """
    try:
        cur = conn.execute(
            """
            SELECT indicator, value, computed_ts, source_bar_ts
            FROM ticker_technicals
            WHERE instrument_id = ? AND bar_interval = ?
            """,
            (instrument_id, bar_interval),
        )
        out: dict[str, dict[str, float]] = {}
        for ind, val, cts, sbts in cur.fetchall():
            out[str(ind)] = {
                "value": float(val),
                "computed_ts": int(cts),
                "source_bar_ts": int(sbts),
            }
        return out
    except sqlite3.Error as exc:
        logger.debug(
            "[technicals] read skipped (degrade) %s/%s: %r",
            instrument_id, bar_interval, exc,
        )
        return {}


def technicals_summary(
    conn: sqlite3.Connection, *, instrument_id: str, bar_interval: str
) -> dict[str, Any]:
    """Compact judge-ready dict: ``{indicator: value, ..., source_bar_ts: <oldest>}``.

    Flattens ``read_technicals`` to bare values for the evidence prompt and
    appends the OLDEST contributing ``source_bar_ts`` as a single currency stamp
    (so the judge can self-discount a stale technical set, mirroring the macro/COT
    age pattern). Empty dict on a miss (the evidence block then renders nothing).
    """
    stored = read_technicals(
        conn, instrument_id=instrument_id, bar_interval=bar_interval
    )
    if not stored:
        return {}
    out: dict[str, Any] = {name: rec["value"] for name, rec in stored.items()}
    out["source_bar_ts"] = min(int(rec["source_bar_ts"]) for rec in stored.values())
    return out
