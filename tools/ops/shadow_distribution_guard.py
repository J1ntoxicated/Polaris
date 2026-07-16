"""Shadow-channel distribution guard + input fingerprint (W1 item 4, read-only).

design-monitoring.md W1 §4 [R1-B5]: row counts alone miss "쌓이고는 있는데
이상한 데이터" (e.g. calibration ``predicted_p_pos`` collapsed to one constant,
or a NaN flood) — this module is the detection REPORT, not a gate (never
blocks/sizes/filters anything, DEMO/PAPER instrumentation only).

``distribution_summary`` / ``symbol_concentration`` / ``dedup_ratio`` are pure
functions over already-fetched rows (fixture-testable, zero DB I/O) — the only
DB access is ``read_channel_rows`` (SELECT-only, ``mode=ro`` at the caller,
bounded by LIMIT) and ``main`` (CLI report, prints to stdout, writes nothing).

Input fingerprint: ``query_fingerprint`` hashes the EXACT canonical SELECT
text used to read a channel — the same string feeds both the DB read and the
hash (single source, no drift between what was read and what was stamped).
This is a schema/query VERSION marker, not a secret: printing it every run
lets a human/agent eyeball whether two runs' fingerprints for the SAME
channel diverge (design-monitoring.md's "섀도우-실경로 분기 감지" signal — the
monitoring query's shape moved out from under the live writer). Nothing is
persisted for cross-run comparison (신규 write 표면 0).
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

SAMPLE_LIMIT: Final[int] = 2000  # bounded recent-row read — cheap even on a hot table


@dataclass(frozen=True)
class DistributionSummary:
    n: int
    n_valid: int
    n_missing: int  # None or NaN count
    mean: float | None
    stddev: float | None
    n_distinct: int
    dominant_value: float | None
    dominant_share: float  # fraction of VALID rows at the single most-common value


def distribution_summary(values: Sequence[float | int | None]) -> DistributionSummary:
    """Numeric distribution over ``values`` — mean/stddev/NaN-or-None ratio +
    "all-identical" detector (``dominant_share == 1.0`` with ``n_valid > 1``).
    Empty/all-missing input degrades to a zero-ish summary, never raises."""
    n = len(values)
    valid: list[float] = []
    n_missing = 0
    for v in values:
        if v is None:
            n_missing += 1
            continue
        fv = float(v)
        if math.isnan(fv):
            n_missing += 1
            continue
        valid.append(fv)
    n_valid = len(valid)
    if n_valid == 0:
        return DistributionSummary(n, 0, n_missing, None, None, 0, None, 0.0)
    mean = sum(valid) / n_valid
    variance = sum((v - mean) ** 2 for v in valid) / n_valid
    counts = Counter(valid)
    dominant_value, dominant_n = counts.most_common(1)[0]
    return DistributionSummary(
        n=n, n_valid=n_valid, n_missing=n_missing, mean=mean,
        stddev=math.sqrt(variance), n_distinct=len(counts),
        dominant_value=dominant_value, dominant_share=dominant_n / n_valid,
    )


def symbol_concentration(symbols: Sequence[str]) -> tuple[str, float]:
    """(top_symbol, share_of_rows) — top-symbol concentration. ``("", 0.0)``
    when ``symbols`` is empty."""
    if not symbols:
        return "", 0.0
    counts = Counter(symbols)
    top, top_n = counts.most_common(1)[0]
    return top, top_n / len(symbols)


def dedup_ratio(keys: Sequence[Any]) -> float:
    """``1 - n_distinct/n_total``: 0.0 = no duplication, closer to 1.0 = heavy
    duplication under ``keys``. 0.0 when ``keys`` is empty (nothing to dedup)."""
    if not keys:
        return 0.0
    return 1.0 - (len(set(keys)) / len(keys))


def query_fingerprint(query: str) -> str:
    """First 12 hex chars of sha256(whitespace-normalized ``query``) — a
    schema/query VERSION stamp (see module docstring), not a secret hash."""
    normalized = " ".join(query.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class ChannelQuery:
    table: str
    value_col: str  # numeric column profiled (mean/stddev/all-identical/NaN%)
    symbol_col: str  # categorical column profiled for top-symbol concentration
    dedup_col: str | None  # natural dedup key, or None (no channel-specific one)


# Per-channel column choice — design-monitoring.md W1 §4's own worked example
# ("calibration predicted_p 전부 동일값/NaN 비율") fixes calibration_pairs'
# value_col; the rest follow the same shape (each channel's own numeric
# shadow-measurement column + its symbol column). news_timing_shadow's
# dedup_col is dedup_group_id — the syndicate-dedup key the table was BUILT
# around (news_timing_shadow.py), the one channel with a natural non-PK key.
CHANNEL_QUERIES: Final[dict[str, ChannelQuery]] = {
    "calibration_pairs": ChannelQuery(
        "calibration_pairs", "predicted_p_pos", "ticker", None),
    "vwap_timing_shadow": ChannelQuery(
        "vwap_timing_shadow", "session_vwap_distance_bps", "symbol", None),
    "news_timing_shadow": ChannelQuery(
        "news_timing_shadow", "sentiment", "symbol", "dedup_group_id"),
    "sector_rank_shadow": ChannelQuery(
        "sector_rank_shadow", "momentum_z", "sector_etf_symbol", None),
    "gate_shadow_events": ChannelQuery(
        "gate_shadow_events", "technical_scalar", "symbol", None),
    "meta_labels": ChannelQuery("meta_labels", "r", "ticker", None),
}


def canonical_query(cq: ChannelQuery, *, limit: int = SAMPLE_LIMIT) -> str:
    """The ONE canonical SELECT for a channel — read AND fingerprint both use
    this exact text (single source, no read/hash drift)."""
    dedup_expr = cq.dedup_col if cq.dedup_col is not None else "NULL"
    return (
        f"SELECT {cq.value_col} AS value, {cq.symbol_col} AS symbol,"
        f" {dedup_expr} AS dedup_key FROM {cq.table}"
        f" ORDER BY created_ts DESC LIMIT {limit}"
    )


@dataclass(frozen=True)
class ChannelDistributionReport:
    channel: str
    fingerprint: str
    distribution: DistributionSummary
    top_symbol: str
    top_symbol_share: float
    dedup_ratio: float


def channel_distribution_report(
    channel: str, rows: Sequence[Mapping[str, Any]], *, query: str,
) -> ChannelDistributionReport:
    """Pure — ``rows`` are already-fetched ``{"value", "symbol", "dedup_key"}``
    mappings (fixture-testable, no DB I/O here; see ``read_channel_rows``)."""
    values = [r.get("value") for r in rows]
    symbols = [str(r.get("symbol") or "") for r in rows]
    dedup_keys = [r.get("dedup_key") for r in rows if r.get("dedup_key") is not None]
    dist = distribution_summary(values)
    top, top_share = symbol_concentration(symbols)
    return ChannelDistributionReport(
        channel=channel, fingerprint=query_fingerprint(query), distribution=dist,
        top_symbol=top, top_symbol_share=top_share, dedup_ratio=dedup_ratio(dedup_keys),
    )


def read_channel_rows(
    conn: sqlite3.Connection, channel: str, *, limit: int = SAMPLE_LIMIT,
) -> tuple[list[dict[str, Any]], str]:
    """Read-only (SELECT-only; caller opens ``conn`` ``mode=ro``). Returns
    ``(rows, canonical_query_text)`` — the text is what ``query_fingerprint``
    hashes, so the printed fingerprint always matches what was actually read."""
    query = canonical_query(CHANNEL_QUERIES[channel], limit=limit)
    cur = conn.execute(query)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    return rows, query


def _format_report(r: ChannelDistributionReport) -> str:
    d = r.distribution
    mean_s = f"{d.mean:.4f}" if d.mean is not None else "NULL"
    std_s = f"{d.stddev:.4f}" if d.stddev is not None else "NULL"
    nan_pct = (d.n_missing / d.n * 100.0) if d.n else 0.0
    return (
        f"channel={r.channel} fingerprint={r.fingerprint} n={d.n}"
        f" nan_or_none_pct={nan_pct:.1f} mean={mean_s} stddev={std_s}"
        f" n_distinct={d.n_distinct} dominant_share={d.dominant_share:.3f}"
        f" top_symbol={r.top_symbol}({r.top_symbol_share:.3f})"
        f" dedup_ratio={r.dedup_ratio:.3f}"
    )


def run_report(
    conn: sqlite3.Connection, *, md_conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Read-only over all 6 channels; one deterministic line per channel.

    storage-split: ``gate_shadow_events`` is trading-domain (same-txn joined
    with ``signals``) and always reads ``conn``; every other channel is
    marketdata-domain and reads via ``md_conn`` when supplied (falls back to
    ``conn`` — byte-identical for existing single-conn callers/tests).
    """
    lines = []
    for channel in CHANNEL_QUERIES:
        source_conn = (
            conn if channel == "gate_shadow_events"
            else (md_conn if md_conn is not None else conn)
        )
        rows, query = read_channel_rows(source_conn, channel)
        report = channel_distribution_report(channel, rows, query=query)
        lines.append(_format_report(report))
    return lines


def main() -> int:
    from polaris.storage.schema_marketdata import marketdata_db_path_for
    from tools.ops.ops_config import OpsConfig

    cfg = OpsConfig.default()
    conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    md_path = marketdata_db_path_for(cfg.db_path)
    md_conn = (
        sqlite3.connect(f"file:{md_path}?mode=ro", uri=True)
        if md_path.exists() else None
    )
    try:
        for line in run_report(conn, md_conn=md_conn):
            print(line)
    finally:
        conn.close()
        if md_conn is not None:
            md_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
