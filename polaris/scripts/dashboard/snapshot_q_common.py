"""Polaris dashboard v1 — shared snapshot-query helpers + constants.

Low-level DB helpers, session-bucket math, label/price helpers and the
display-only pricing / lookback constants shared by every ``snapshot_q_*``
section module. Split out of ``snapshot_queries.py`` to keep each module
≤500 LOC (move-only; no logic change). All queries best-effort: missing tables
/ empty data return zero-defaults so the dashboard never crashes a paper loop.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Protocol
from zoneinfo import ZoneInfo

from polaris.core.metrics.risk_unit import R_USD_PROXY

# 'Today' KPI boundary (P0-2, Jin 2026-07-02) — the dashboard operates on
# Sydney local time (per feedback_sydney_timezone), so "Today" resets at AEST
# midnight, not UTC midnight.
_SYDNEY_TZ: Final[ZoneInfo] = ZoneInfo("Australia/Sydney")

# Step M (2026-06-22): the flat ``$10`` display heuristic is retired in favour
# of the canonical risk$-based R (positions.pnl_r) on every panel, with the ONE
# shared ``R_USD_PROXY`` as the only fallback for fills that have no matched
# position. ``DEFAULT_R_USD`` is kept as a backward-compat alias to that single
# proxy so any external importer resolves to the SAME number (no $10/$50 split).
DEFAULT_R_USD: Final[float] = R_USD_PROXY
EQUITY_BUCKET_SEC: Final[int] = 5 * 60       # legacy 5-min bucket (24h fallback)
EQUITY_LOOKBACK_SEC: Final[int] = 24 * 3600  # legacy 24h window (fallback only)
GATE_FUNNEL_LOOKBACK_SEC: Final[int] = 3600
GPT_LOOKBACK_SEC: Final[int] = 3600
LEARNER_DELTA_LOOKBACK_SEC: Final[int] = 3600

# Session-anchored curve config (Jin 2026-05-29): all PnL/DD/Sharpe lookbacks
# start at the session anchor (clean restart → DB is the current session), not
# a rolling 24h window. The session is split into N buckets, floor 60s each.
EQUITY_TARGET_BUCKETS: Final[int] = 288      # target resolution across the session
EQUITY_MIN_BUCKET_SEC: Final[int] = 60       # floor bucket size

# GPT pricing (USD per 1K tokens) — gpt-5-mini & gpt-5.5 ballpark for projection.
GPT_PRICE_PER_1K: Final[Mapping[str, float]] = {
    "gpt-5-mini": 0.000150,
    "gpt-5.5": 0.005,
    "gpt": 0.000150,           # default mini for unlabelled rows
}
GPT_TOKENS_PER_CALL: Final[int] = 1500       # heuristic — average prompt+completion

# Per-stream AI-cost price table (USD per 1K tokens). DISPLAY-ONLY: feeds the
# read-only cost-monitoring breakdown, never sizing/gating. Keyed on the
# ``gate_events.model_used`` labels the orchestrator actually emits — ``gpt`` =
# GPT-mini (P0), ``gpt_p1``/``haiku`` = the P1 model, ``python`` /
# ``python_fast_path`` = deterministic gate (no LLM, $0), ``cached`` = served
# from cache ($0). This is the AUDIT's price table (Jin /debate-tunable), not a
# venue billing source of truth. Unknown labels fall back to the mini price
# (``_model_price``) so a new label is never silently free.
MODEL_PRICE_PER_1K: Final[Mapping[str, float]] = {
    "gpt": 0.000150,            # GPT-mini (P0)
    "gpt-5-mini": 0.000150,
    "gpt_p1": 0.000150,         # P1 tier downgraded to gpt-5-mini (Jin 2026-05-31)
    "gpt-5.5": 0.005,           # legacy price kept for any pre-downgrade rows
    "haiku": 0.005,             # legacy P1 label — priced at the P1 tier
    "python": 0.0,              # deterministic gate — no LLM call
    "python_fast_path": 0.0,
    "cached": 0.0,              # cache hit — no incremental cost
}
SLIPPAGE_BPS_DIVISOR: Final[float] = 10_000.0  # bps → fraction of notional


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _safe_query(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []


def _now_s() -> int:
    return int(time.time())


def _session_start_ms(conn: sqlite3.Connection, *, now_s: int) -> int:
    """Session anchor in ms = earliest fill in the DB (clean restart → the DB is
    the current session). Falls back to ``now`` when no fills exist yet.

    All session-scoped PnL/equity/DD/Sharpe lookbacks start here instead of a
    rolling 24h window (Jin 2026-05-29).
    """
    rows = _safe_query(conn, "SELECT MIN(ts_ms) FROM fills")
    if rows and rows[0][0] is not None:
        return int(rows[0][0])
    return now_s * 1000


def _today_start_ms(conn: sqlite3.Connection, *, now_s: int) -> int:
    """'Today' KPI boundary (ms) = max(session_start, latest AEST midnight).

    ``_session_start_ms`` alone (the whole-uptime session anchor) is correct
    for the equity curve / DD / Sharpe / strategy-stats lookbacks (Jin
    2026-05-29 — those intentionally span the full session, however long).
    But the "Today" headline (``daily_pnl_usd`` on the board) previously used
    the SAME anchor, which after multi-day uptime silently accumulates days of
    PnL under a "Today" label. This floors that ONE lookback at the later of
    the session start or the most recent Sydney-local midnight, so "Today"
    never spans more than ~24h. The raw session-wide sum is preserved
    separately (``session_pnl_usd`` / ``session_trades`` on the snapshot).
    """
    session_start_ms = _session_start_ms(conn, now_s=now_s)
    aest_midnight = datetime.fromtimestamp(now_s, tz=_SYDNEY_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    aest_midnight_ms = int(aest_midnight.timestamp() * 1000)
    return max(session_start_ms, aest_midnight_ms)


def _session_buckets(session_start_s: int, now_s: int) -> tuple[list[int], int]:
    """Split [session_start, now] into bucket end-timestamps (s) + bucket size.

    Session length is divided into ``EQUITY_TARGET_BUCKETS`` buckets, each at
    least ``EQUITY_MIN_BUCKET_SEC`` wide. Returns (bucket_ts, bucket_sec).
    """
    span = max(1, now_s - session_start_s)
    bucket_sec = max(EQUITY_MIN_BUCKET_SEC, span // EQUITY_TARGET_BUCKETS)
    n_buckets = max(1, (span + bucket_sec - 1) // bucket_sec)
    bucket_ts = [session_start_s + (i + 1) * bucket_sec for i in range(n_buckets)]
    return bucket_ts, bucket_sec


def _strategy_label(s: str | None) -> str:
    return (s or "?")[:18]


def _symbol_from_inst(inst: str | None) -> str:
    if not inst:
        return "?"
    return inst.split(":", 1)[-1] if ":" in inst else inst


def _model_price(model: str | None) -> float:
    """USD per 1K tokens for a ``gate_events.model_used`` label (display only).

    Unknown / unlabelled models fall back to the mini price so a new model id
    is never silently treated as free. ``python`` / ``cached`` map to 0.0.
    """
    if not model:
        return MODEL_PRICE_PER_1K["gpt"]
    return MODEL_PRICE_PER_1K.get(str(model), MODEL_PRICE_PER_1K["gpt"])


# ---------------------------------------------------------------------------
# Quote-currency display label (issue 4: J225 71,847 is JPY, not raw)
# ---------------------------------------------------------------------------

# Supported non-USD quote currencies — mirrors the trading path's
# ``_QUOTE_RATE_EPICS`` keys (``_production_capital_sizing``). A symbol whose
# derived quote ccy is NOT in this set degrades to "USD" (no special symbol),
# so a stray label can never imply a currency the venue doesn't quote in. This
# is a DISPLAY mirror of that set, not a second source of truth for sizing.
_SUPPORTED_QUOTE_CCY: Final[frozenset[str]] = frozenset({
    "JPY", "EUR", "GBP", "AUD", "NZD", "CHF", "CAD", "NOK", "SEK", "DKK",
    "ZAR", "SGD", "HKD", "CNH", "PLN",
})

# Capital index/CFD epics whose quote ccy is NOT derivable from the symbol
# string (an FX pair gives its quote in the trailing 3 chars; an index name
# does not). The true value lives only in the live market-detail payload the
# read-only dashboard cannot fetch (constraint_translator parses
# ``instrument.currency`` — e.g. J225 is JPY-quoted). This is the curated
# display map for the well-known index epics in the live Capital universe;
# everything not listed defaults to USD (US100/US30/US500/RTY/NYFANG/DXY/VIX
# are USD-quoted, so the default is correct for them). Display-only.
_CAPITAL_INDEX_QUOTE_CCY: Final[Mapping[str, str]] = {
    "J225": "JPY",       # Nikkei 225
    "EU50": "EUR",       # EuroStoxx 50
    "DE40": "EUR",       # DAX
    "FR40": "EUR",       # CAC 40
    "IT40": "EUR",       # FTSE MIB
    "NL25": "EUR",       # AEX
    "SP35": "EUR",       # IBEX 35 (Spain)
    "UK100": "GBP",      # FTSE 100
    "HK50": "HKD",       # Hang Seng
    "CN50": "CNH",       # China A50
    "AU200AU": "AUD",    # ASX 200
    "SG25": "SGD",       # Straits Times
    "SW20": "CHF",       # SMI (Switzerland)
}


def _quote_ccy_for_symbol(venue: str, symbol: str, universe_quote: str) -> str:
    """Best-effort DISPLAY quote currency for a price label (issue 4).

    OKX: trust ``universe.quote_ccy`` (discovery parses the real pair quote, so
    USDT/USD/etc are correct). Capital: discovery stamps a "USD" placeholder for
    every CFD, so derive the true quote from the epic — a 6-char FX pair gives
    its quote in the trailing 3 chars (AUDJPY → JPY); a known index epic uses the
    curated map (J225 → JPY); everything else (commodity, equity, USD index)
    stays USD. Only currencies the venue actually quotes in (the supported set)
    are returned; anything else degrades to "USD". Never feeds sizing/FX-conv —
    the price cell symbol only.
    """
    v = (venue or "").lower()
    if v != "capital":
        q = (universe_quote or "USD").upper()
        return q if q else "USD"
    sym = (symbol or "").upper()
    idx = _CAPITAL_INDEX_QUOTE_CCY.get(sym)
    if idx is not None:
        return idx
    # FX pair: a 6-letter all-alpha epic quotes in its trailing 3 chars.
    if len(sym) == 6 and sym.isalpha():
        quote = sym[3:]
        if quote in _SUPPORTED_QUOTE_CCY:
            return quote
        return "USD"
    return "USD"


# ---------------------------------------------------------------------------
# Quote→USD conversion rate (#50: a J225 uPnL/notional is JPY, not USD)
# ---------------------------------------------------------------------------

# DISPLAY mirror of the trading path's ``_QUOTE_RATE_EPICS``
# (``_production_capital_sizing``): quote ccy → (conversion-pair epic, invert).
# ``invert`` means the bar quotes USD-per-quote (USDJPY = JPY per USD), so the
# quote→USD multiplier is ``1/close``; a non-inverted pair (EURUSD) already IS
# the quote→USD multiplier. Reads the latest ``capital:<PAIR>`` 1m bar close —
# the SAME source the trading path uses — so the dashboard agrees with sizing.
# Display-only; never feeds sizing/gating/FX-conversion in a trading path.
_USD_EQUIVALENT_QUOTES: Final[frozenset[str]] = frozenset({"", "USD", "USDT", "USDC"})
_QUOTE_RATE_PAIRS: Final[Mapping[str, tuple[str, bool]]] = {
    "JPY": ("USDJPY", True), "EUR": ("EURUSD", False), "GBP": ("GBPUSD", False),
    "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False), "CHF": ("USDCHF", True),
    "CAD": ("USDCAD", True), "NOK": ("USDNOK", True), "SEK": ("USDSEK", True),
    "DKK": ("USDDKK", True), "ZAR": ("USDZAR", True), "SGD": ("USDSGD", True),
    "HKD": ("USDHKD", True), "CNH": ("USDCNH", True), "PLN": ("USDPLN", True),
}


def _quote_usd_rate(conn: sqlite3.Connection, quote_ccy: str) -> float:
    """quote-ccy → USD multiplier for the display layer (graceful, never raises).

    USD-equivalents (USD/USDT/USDC/"") → 1.0. Otherwise read the latest 1m bar
    close of the conversion pair (``capital:<PAIR>``) and invert when the pair
    quotes USD-per-quote (USDJPY). A missing / non-positive / unknown rate
    degrades to 1.0 — the same graceful fallback the trading path uses, so a
    new venue/ccy is never silently zeroed (the cell then shows the raw quote
    magnitude, which is the pre-#50 behaviour, not a crash)."""
    q = (quote_ccy or "").upper()
    if q in _USD_EQUIVALENT_QUOTES:
        return 1.0
    pair = _QUOTE_RATE_PAIRS.get(q)
    if pair is None:
        return 1.0
    epic, invert = pair
    rows = _safe_query(
        conn,
        "SELECT close FROM bars WHERE instrument_id = ? AND "
        "bar_interval = '1m' ORDER BY ts DESC LIMIT 1",
        (f"capital:{epic}",),
    )
    if not rows or rows[0][0] is None:
        return 1.0
    px = float(rows[0][0])
    if px <= 0.0:
        return 1.0
    return (1.0 / px) if invert else px


# ---------------------------------------------------------------------------
# Symbol sparkline (Jin 2026-06-25) — per-row recent-close mini history
# ---------------------------------------------------------------------------

# Max closes embedded per row for the inline sparkline. A short series keeps the
# snapshot payload tiny (the board only draws a ~60px trend), so 30 is plenty.
SPARK_POINTS: Final[int] = 30
# Recent-close window per (instrument, interval) the spark query keeps. A bit
# more than SPARK_POINTS so a downsample has anchors; capped low so the query
# stays cheap on the display path.
_SPARK_FETCH_LIMIT: Final[int] = 60


def _downsample(series: list[float], n: int) -> list[float]:
    """Evenly thin ``series`` to at most ``n`` points, keeping first + last.

    Stride sampling (not averaging) so the spark traces the real closes; the
    oldest and newest anchors are always preserved so the trend direction the
    board colours on is honest. ``len <= n`` passes through unchanged.
    """
    m = len(series)
    if m <= n or n <= 0:
        return series
    if n == 1:
        return [series[-1]]
    step = (m - 1) / (n - 1)
    out = [series[round(i * step)] for i in range(n)]
    out[-1] = series[-1]  # guarantee the newest anchor survives rounding
    return out


def _spark_series(
    conn: sqlite3.Connection,
    instruments: list[str],
    *,
    n: int = SPARK_POINTS,
) -> dict[str, list[float]]:
    """{instrument_id: recent closes (oldest→newest)} for the row sparklines.

    ONE window query over ``bars`` for the requested instruments (the displayed
    positions / trades / tickers), bounded to ``_SPARK_FETCH_LIMIT`` rows per
    (instrument, interval). Per instrument the FRESHEST interval (newest stored
    ts) wins, so a symbol with both stale daily + fresh intraday bars sparks the
    fresh series, never a mixed one. FUTURE-dated bars (the +10h Capital
    AEST-naive bug) are excluded, mirroring ``_last_prices``. The series is then
    downsampled to ``n`` points. DISPLAY-ONLY — never feeds sizing/gating/exit;
    a missing/locked bars table or an absent instrument degrades to an empty
    list (graceful). The bars cache is Yahoo-primary, already populated.
    """
    insts = [i for i in dict.fromkeys(instruments) if i]
    if not insts:
        return {}
    placeholders = ",".join("?" for _ in insts)
    now_s = _now_s()
    # ROW_NUMBER bounds the scan to the newest few bars per (instrument,
    # interval) without an unbounded full-history read. SQLite >= 3.25 (the
    # bundled stdlib build) supports window functions; _safe_query degrades to
    # [] on any error → empty sparks, never a crash.
    rows = _safe_query(
        conn,
        f"""SELECT instrument_id, bar_interval, ts, close FROM (
                SELECT instrument_id, bar_interval, ts, close,
                       ROW_NUMBER() OVER (
                           PARTITION BY instrument_id, bar_interval
                           ORDER BY ts DESC
                       ) AS rn
                FROM bars
                WHERE instrument_id IN ({placeholders}) AND ts <= ?
            ) WHERE rn <= ?
            ORDER BY instrument_id, bar_interval, ts ASC""",
        (*insts, now_s, _SPARK_FETCH_LIMIT),
    )
    # Bucket closes by (instrument, interval), tracking the newest ts per bucket
    # so we can pick the freshest interval per instrument.
    by_key: dict[tuple[str, str], list[float]] = {}
    newest_ts: dict[tuple[str, str], int] = {}
    for inst, interval, ts, close in rows:
        key = (str(inst), str(interval))
        by_key.setdefault(key, []).append(float(close or 0.0))
        newest_ts[key] = max(newest_ts.get(key, 0), int(ts or 0))
    # Per instrument pick the interval whose latest bar is newest (tiebreak: the
    # longer series). The query already ordered ts ASC, so each bucket is
    # oldest→newest.
    best: dict[str, tuple[str, int]] = {}
    for (inst, interval), latest in newest_ts.items():
        cur = best.get(inst)
        if cur is None or latest > cur[1] or (
            latest == cur[1]
            and len(by_key[(inst, interval)]) > len(by_key[(inst, cur[0])])
        ):
            best[inst] = (interval, latest)
    return {
        inst: _downsample(by_key[(inst, interval)], n)
        for inst, (interval, _latest) in best.items()
    }


class _SparkRow(Protocol):
    """A snapshot row that carries a (venue, symbol) and a mutable ``spark``."""

    venue: str
    symbol: str
    spark: list[float]


def _attach_sparks(
    conn: sqlite3.Connection,
    *row_groups: Sequence[_SparkRow],
) -> None:
    """Embed the recent-close sparkline on every (venue, symbol) row in place.

    ONE ``_spark_series`` query covers all groups (positions / ticker_stats /
    recent_trades) so there is no per-row fetch; each row's ``spark`` is set to
    the symbol's series (empty when the bars cache has nothing — graceful).
    Display-only — never feeds sizing/gating/exit.
    """
    insts = [f"{r.venue}:{r.symbol}" for grp in row_groups for r in grp]
    spark_by_inst = _spark_series(conn, insts)
    for grp in row_groups:
        for r in grp:
            r.spark = spark_by_inst.get(f"{r.venue}:{r.symbol}", [])

