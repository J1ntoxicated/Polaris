"""#48 — per-instrument trading-session map (the global session clock).

Jin's vision: "어떤 상황에서도 24시간 active 시장 다 공략" — at every hour of the
global clock SOME market is in its live cash session, and the focus seats should
ROTATE toward whatever is awake right now. The candidate sweep already ranks by
movement (#39), but it had no notion of WHICH instruments are in their active
trading session at ``now`` — so an Asia index (J225/HK50) sat at the same seat
priority at 03:00 UTC (its cash open) as at 18:00 UTC (long shut).

This module assigns each instrument a SESSION WEIGHT ∈ (0, 1]:
  * ``1.0`` — the instrument is inside its active cash session (or is 24/7 / 24-5
    and trading) → full seat priority;
  * ``SESSION_DORMANT`` (< 1.0) — outside its session → DEPRIORITIZED, NOT cut.

🚨 flow_not_block / aggressive / 9-stack:
- The weight RE-ALLOCATES focus seats (which names sit at the rank head). It is
  NEVER a sizing multiplier, NEVER stacked onto the candidate score, NEVER a
  membership cut. A dormant name is still watched (cap WIDENS the set); when its
  session returns the weight flips back to 1.0 — automatic restore, no manual
  un-block. The dormant floor is strictly POSITIVE (deprioritize, never exclude).
- OKX crypto is ALWAYS 1.0 (24/7 base focus — never off, session-gate exempt).
- A symbol with no session-group mapping → 1.0 (unknown = active, never silently
  deprioritized). A freshly-opened session is ACTIVE (1.0), so a cold-start name
  entering its first live session is NOT penalized here.

Exchange-fact windows (UTC minutes-of-day), NOT magic numbers — they are the
underlying cash-session trading hours of each regional index family:
  * Asia    00:00-08:00 UTC — ASX (AU200 ~00:00), Nikkei (J225 00:00), HSI (HK50
            ~01:30) all live inside this band.
  * Europe  07:00-16:00 UTC — LSE/Xetra/Euronext cash (08:00-16:30 local winter).
  * US      13:30-20:00 UTC — US cash RTH (09:30-16:00 ET), the SAME window the
            equity session gate + ``_candidate_sweep.RTH_*`` already use.
The symbol→session-group table is the SAME regional grouping the Yahoo index map
(``_yahoo_bars._CAPITAL_INDEX_YF``) encodes (^N225/^HSI/^AXJO = Asia, ^GDAXI/
^FTSE = Europe, ^NDX/^GSPC = US). All windows + the dormant floor are
env-overridable /debate calibration targets — never magic-in-place.

Pure, no I/O. ``now_ts`` is a UTC epoch — the map never reads the local (AEST)
machine clock. DEMO/PAPER only.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable
from typing import Final

from polaris.venues.alpaca.equity_session_gate import us_equity_session_state

__all__ = [
    "ASIA_CLOSE_MIN",
    "ASIA_OPEN_MIN",
    "EUROPE_CLOSE_MIN",
    "EUROPE_OPEN_MIN",
    "SESSION_DORMANT",
    "US_CLOSE_MIN",
    "US_OPEN_MIN",
    "WARM_LEAD_MIN",
    "equity_fetch_active",
    "instrument_session_weight",
    "session_group",
    "session_transitions",
    "session_warm_active",
]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


# Regional cash-session windows (UTC minutes-of-day). Exchange-fact trading hours,
# env-overridable /debate targets (never magic-in-place).
ASIA_OPEN_MIN: Final[int] = _env_int("POLARIS_SESSION_ASIA_OPEN_MIN", 0)
ASIA_CLOSE_MIN: Final[int] = _env_int("POLARIS_SESSION_ASIA_CLOSE_MIN", 8 * 60)
EUROPE_OPEN_MIN: Final[int] = _env_int("POLARIS_SESSION_EUROPE_OPEN_MIN", 7 * 60)
EUROPE_CLOSE_MIN: Final[int] = _env_int("POLARIS_SESSION_EUROPE_CLOSE_MIN", 16 * 60)
US_OPEN_MIN: Final[int] = _env_int("POLARIS_SESSION_US_OPEN_MIN", 13 * 60 + 30)
US_CLOSE_MIN: Final[int] = _env_int("POLARIS_SESSION_US_CLOSE_MIN", 20 * 60)

# A dormant (out-of-session) instrument keeps THIS weight — a DEPRIORITIZE floor,
# never zero (flow_not_block: present but fewer seats). Mirrors the existing
# ``_candidate_sweep.SESSION_DOWNWEIGHT`` so the two session knobs stay aligned.
SESSION_DORMANT: Final[float] = _env_float("POLARIS_SESSION_DORMANT", 0.3)

# Pre-open bar-warming lead (#66, Jin "장 열기 전부터 거래가능 바 알아서 채워야"). A
# regional cash-session symbol becomes FETCH-active (its 1m bars are pre-warmed)
# this many UTC minutes BEFORE its cash open, so the recency gate sees fresh bars
# at the open instead of a 0.5h-stale feed that skips the symbol. DATA-ONLY: this
# never advances the TRADE weight (the symbol is still SESSION_DORMANT until the
# open itself). env-overridable /debate target — mirrors the EOD-flatten lead
# pattern (``POLARIS_EOD_FLATTEN_LEAD_SEC``), never magic-in-place.
WARM_LEAD_MIN: Final[int] = _env_int("POLARIS_SESSION_WARM_LEAD_MIN", 30)

# asset_class tokens that mean "FX major" (24/5 — active any weekday hour).
_FX_CLASSES: Final[frozenset[str]] = frozenset({"forex", "fx"})

# asset_class tokens that mean "US equity" — Alpaca stock symbols (the 미장 gap).
# Two consumers: (1) #66 warming — they have no ``_SESSION_GROUP`` entry, so FOR
# WARMING ONLY they are absorbed into the 'us' cash-session window (their cash open
# IS the US RTH open); (2) the #84 data-fetch gate — the ONLY class it touches (a
# discrete RTH cash session that goes fully dark out of hours). OKX crypto / Capital
# FX / index / commodity are never equity-gated. The TRADE weight
# (``instrument_session_weight``) is UNTOUCHED by this mapping.
_EQUITY_CLASSES: Final[frozenset[str]] = frozenset({"equity", "stock", "us_equity"})

# Symbol → regional session group. Mirrors the Yahoo index map's regional split
# (``_yahoo_bars._CAPITAL_INDEX_YF``): ^N225/^HSI/^AXJO = Asia, ^GDAXI/^FTSE/
# ^FCHI/^STOXX50E/FTSEMIB/^AEX/^IBEX/^SSMI = Europe, ^NDX/^GSPC/^DJI/^RUT = US.
# A symbol absent here → no group (defaults to ALWAYS-active, flow_not_block).
_SESSION_GROUP: Final[dict[str, str]] = {
    # Asia cash indices.
    "J225": "asia",      # Nikkei 225 (^N225)
    "HK50": "asia",      # Hang Seng (^HSI)
    "AU200": "asia",     # ASX 200 (^AXJO) — bare epic spelling
    "AU200AU": "asia",   # ASX 200 (^AXJO) — Capital epic spelling
    "CN50": "asia",      # China A50 (Asia hours)
    # Europe cash indices.
    "DE40": "europe",    # DAX (^GDAXI)
    "UK100": "europe",   # FTSE 100 (^FTSE)
    "FR40": "europe",    # CAC 40 (^FCHI)
    "EU50": "europe",    # Euro Stoxx 50 (^STOXX50E)
    "IT40": "europe",    # FTSE MIB (FTSEMIB.MI)
    "NL25": "europe",    # AEX (^AEX)
    "SP35": "europe",    # IBEX 35 (^IBEX)
    "ES35": "europe",    # IBEX 35 (alt epic)
    "SW20": "europe",    # SMI (^SSMI)
    "CH20": "europe",    # SMI (alt epic)
    # US cash indices.
    "US100": "us",       # Nasdaq 100 (^NDX)
    "US500": "us",       # S&P 500 (^GSPC)
    "US30": "us",        # Dow 30 (^DJI)
    "US2000": "us",      # Russell 2000 (^RUT)
    "RTY": "us",         # Russell 2000 (alt epic)
}

# Session group → (open, close) UTC minutes-of-day.
_GROUP_WINDOW: Final[dict[str, tuple[int, int]]] = {
    "asia": (ASIA_OPEN_MIN, ASIA_CLOSE_MIN),
    "europe": (EUROPE_OPEN_MIN, EUROPE_CLOSE_MIN),
    "us": (US_OPEN_MIN, US_CLOSE_MIN),
}


def session_group(symbol: str) -> str | None:
    """Regional session group for a Capital index symbol, or None if unmapped.

    The match is case-insensitive on the bare epic spelling. ``None`` means the
    symbol has no regional cash session in the table → the caller treats it as
    ALWAYS-active (flow_not_block: unknown = active).
    """
    return _SESSION_GROUP.get(symbol.strip().upper())


def instrument_session_weight(
    venue: str, asset_class: str, symbol: str, now_ts: int | float
) -> float:
    """Per-instrument session weight ∈ (0, 1] at ``now_ts`` (UTC epoch).

    ``1.0`` = inside the active cash session (full seat priority);
    ``SESSION_DORMANT`` (< 1.0) = outside it (deprioritized, NEVER excluded).

    Rules (flow_not_block / aggressive):
    - OKX (crypto) → 1.0 always (24/7 base focus — session-gate exempt, mandate ③).
    - FX major (forex/fx) → 24/5: 1.0 any weekday hour; ``SESSION_DORMANT`` on the
      weekend (the FX book is shut).
    - A regional index/equity (J225/DE40/US100…) → 1.0 inside its group's UTC cash
      window on a weekday; ``SESSION_DORMANT`` outside it or on the weekend.
    - Any other class (commodity — global 24/5) or an UNMAPPED symbol → 1.0 on a
      weekday (unknown = active), ``SESSION_DORMANT`` on the weekend for non-crypto.
    """
    v = venue.strip().lower()
    if v == "okx":
        return 1.0  # crypto 24/7 — never session-gated (mandate ③)

    try:
        ts = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return 1.0  # unparseable clock → active (flow_not_block, never deprioritize)
    if ts < 0:
        ts = 0
    local = dt.datetime.fromtimestamp(ts, tz=dt.UTC)
    is_weekend = local.weekday() >= 5  # Sat/Sun → cash books shut
    minute = local.hour * 60 + local.minute

    cls = asset_class.strip().lower()
    if cls in _FX_CLASSES:
        # 24/5 — active any weekday hour; dormant only on the weekend.
        return SESSION_DORMANT if is_weekend else 1.0

    group = session_group(symbol)
    if group is None:
        # No regional cash session mapped (commodity, or an unmapped index) →
        # treat as always-active on weekdays (flow_not_block: unknown = active).
        return SESSION_DORMANT if is_weekend else 1.0

    if is_weekend:
        return SESSION_DORMANT
    open_min, close_min = _GROUP_WINDOW[group]
    return 1.0 if open_min <= minute < close_min else SESSION_DORMANT


def session_warm_active(
    venue: str, asset_class: str, symbol: str, now_ts: int | float
) -> bool:
    """Should this instrument's bars be pre-warmed right now? (#66 pre-open warm).

    True when ``now_ts`` (UTC epoch) falls in the symbol's pre-open WARM window
    ``[open - WARM_LEAD_MIN, close)`` on a weekday — i.e. the symbol's regional
    cash session is about to open (or is open), so its 1m bars should be fetched
    ahead of the open and stay fresh through the session. False otherwise.

    DATA WARMING ONLY — this is a SEPARATE predicate from
    ``instrument_session_weight`` (the TRADE focus weight, untouched). A symbol is
    FETCH-active ``WARM_LEAD_MIN`` before its open while still TRADE-dormant until
    the open itself; entry / sizing / exit never read this.

    Warm targets are exactly the symbols that have a discrete cash open to warm
    toward:
    - a mapped regional cash index (J225/DE40/US100…) → its group window;
    - an Alpaca US equity (the 미장 gap; no ``_SESSION_GROUP`` entry) → absorbed
      into the 'us' window FOR WARMING ONLY.
    Everything else is NOT warmed (the background grind already covers them):
    - OKX crypto (24/7, no cash open — the 5s hot path fills its 1m);
    - FX (24/5) and commodities (24/5, no single cash open);
    - an unmapped index (no known window → no computable open - LEAD);
    - any symbol on the weekend (cash books shut).
    """
    if venue.strip().lower() == "okx":
        return False  # crypto 24/7 — no cash open to warm toward (hot path fills 1m)

    try:
        ts = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return False  # unparseable clock → never warm (degrade to existing grind)
    if ts < 0:
        ts = 0
    local = dt.datetime.fromtimestamp(ts, tz=dt.UTC)
    if local.weekday() >= 5:
        return False  # weekend → cash books shut, no warming (weekend-OFF)
    minute = local.hour * 60 + local.minute

    # Alpaca US equity → 'us' window (warming-only); else the symbol's index group.
    if (venue.strip().lower() == "alpaca"
            and asset_class.strip().lower() in _EQUITY_CLASSES):
        group: str | None = "us"
    else:
        group = session_group(symbol)
    if group is None:
        return False  # FX / commodity / unmapped — no cash-open window to warm

    open_min, close_min = _GROUP_WINDOW[group]
    warm_start = open_min - WARM_LEAD_MIN
    if warm_start <= minute < close_min:
        return True
    # Wrap: an open near 00:00 UTC (Asia) pushes warm_start negative — the lead
    # tail lands in the prior evening's late minutes (e.g. 23:30-24:00 UTC).
    return warm_start < 0 and minute >= warm_start + 1440


# Bound to the real #66 ``session_warm_active`` defined directly ABOVE — so the
# warm OR in ``equity_fetch_active`` keeps the pre-open backfill alive. The
# try/except keeps signature parity with the sibling-branch base (where #66 lived
# on another branch and this fell back to ``_warm_inactive``); now #66 is present
# in the tree the ``try`` arm binds the real predicate at import. The ordering is
# load-bearing: ``def session_warm_active`` MUST precede this block, else the name
# is unbound here, the ``except NameError`` fires, the stub (always-False) binds,
# and the #84 gate would SILENTLY KILL the #66 pre-open warm (no test catches the
# import-order regression — the integration test below pins the live behaviour).
def _warm_inactive(
    venue: str, asset_class: str, symbol: str, now_ts: int | float
) -> bool:
    """Always-False fallback if the #66 ``session_warm_active`` is ever absent.

    Retained as the ``except NameError`` arm of the binding below for parity with
    the build's sibling-branch base. With #66 present in this tree the ``try`` arm
    binds the real predicate and this stub is never used. Args unused (signature
    parity with the real warm predicate).
    """
    del venue, asset_class, symbol, now_ts
    return False


_session_warm_active: Callable[[str, str, str, int | float], bool]
try:  # pragma: no cover - import-time binding; session_warm_active is defined above
    _session_warm_active = session_warm_active
except NameError:  # defensive parity only — the name is always bound in this tree
    _session_warm_active = _warm_inactive


def equity_fetch_active(
    venue: str, asset_class: str, symbol: str, now_ts: int | float
) -> bool:
    """Should this instrument's bars be FETCHED right now? (#84 equity data gate).

    A DATA-FETCH gate, NOT a trade gate. It answers one question: "is the market
    that produces this instrument's bars actually open (or about to open) right
    now?" — so the bulk + focus bar walks stop hammering Alpaca / yfinance for
    US-equity bars the closed market is not producing (the weekend 429 storm).

    Scope — EQUITY ONLY (``venue == 'alpaca'`` AND ``asset_class ∈
    {equity,stock,us_equity}``). Everything else returns ``True`` unconditionally:
    - OKX crypto (24/7 — never gated, mandate ③);
    - Capital FX / index / commodity (24/5 — their focus de-prioritisation lives
      in ``instrument_session_weight``, not in this equity-scoped fetch gate);
    - an Alpaca CRYPTO symbol (asset_class crypto on the equity venue — 24/7).

    For a US equity, fetch is active when EITHER:
    - the equity session is not fully closed (``us_equity_session_state`` is
      ``rth`` / ``pre_market`` / ``after_hours`` — paid SIP delivers extended-hours
      bars, so the data is still moving), OR
    - the #66 pre-open WARM window is active (``session_warm_active``) — so this
      gate NEVER kills the pre-open backfill that fills fresh bars before the open.

    🚨 flow_not_block: a ``False`` here means "the cash market is shut and is
    producing no new bars, so don't fetch", NOT a trade block. It never touches
    entry / size / exit / a held position; the instant RTH (or the warm window)
    returns, fetch resumes automatically. A non-finite clock degrades to ``True``
    (never skip on doubt).
    """
    if venue.strip().lower() != "alpaca":
        return True  # OKX crypto + Capital — not equity, never gated here
    if asset_class.strip().lower() not in _EQUITY_CLASSES:
        return True  # Alpaca crypto (24/7) — not the RTH-bound equity class

    try:
        ts = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return True  # unparseable clock → fetch active (flow_not_block, never skip)

    # ``us_equity_session_state`` is a pure time-of-day clock — it does not know
    # the weekday, so on Sat/Sun 08:00-09:30 ET it would still read 'pre_market'.
    # The cash book is shut all weekend, so treat the weekend as fully closed (the
    # weekend 429 storm this gate targets is precisely a Saturday). Holiday
    # weekdays are still covered: the live ``/v2/clock`` is_open (AlpacaClock,
    # holiday-aware) overrides at the adapter layer when available — this
    # deterministic clock is the per-walk floor.
    is_weekend = dt.datetime.fromtimestamp(max(0, ts), tz=dt.UTC).weekday() >= 5
    if not is_weekend and us_equity_session_state(ts) != "closed":
        return True  # weekday RTH / pre-market / after-hours — SIP bars still moving
    # Fully closed (overnight / weekend) → only the #66 pre-open warm window may
    # keep the backfill alive; otherwise skip (the market is producing no bars).
    return _session_warm_active(venue, asset_class, symbol, ts)


def session_transitions(
    prev_ts: int | float, now_ts: int | float
) -> list[tuple[str, str]]:
    """Regional cash-session OPEN/CLOSE edges crossed in ``(prev_ts, now_ts]``.

    PURE, no I/O. Returns ``[(region, "OPEN"|"CLOSE"), …]`` for every group window
    boundary whose absolute UTC epoch falls in the half-open interval after
    ``prev_ts`` up to and including ``now_ts`` — i.e. the regional market just
    opened or closed since the previous call. The caller logs these as the
    ``[session]`` operational-narrative line; this never gates trading (the TRADE
    weight is ``instrument_session_weight``, untouched).

    Edge-trigger only: a quiet mid-session gap returns ``[]`` (no per-tick spam).
    Boundaries are anchored to BOTH the UTC day of ``prev_ts`` and the UTC day of
    ``now_ts`` (NIT-B midnight-anchor guard): a focus gap that straddles UTC
    midnight — or any over-long gap (a stall-recovery, a restart) — would, under a
    now-day-only anchor, recompute a boundary that physically fell on the prior UTC
    day one day forward and MISS it; checking the prev-day anchor too catches that
    edge. Each ``(region, edge)`` is de-duplicated, so a same-day gap (the common
    case, both anchors equal) still emits each edge once. A midnight-anchored open
    (Asia 00:00 = ``day_start + 0``) is detected via the same epoch compare.
    Weekend boundaries are suppressed (cash books shut all weekend). A reversed or
    zero gap (``now_ts <= prev_ts``) returns ``[]``.
    """
    try:
        p = int(prev_ts)
        n = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return []
    if n <= p:
        return []

    # Anchor to the prev-day midnight AND the now-day midnight (NIT-B): a gap that
    # crosses UTC midnight needs both day origins so a prior-day boundary is not
    # shifted a day forward and skipped. ``dict.fromkeys`` collapses the {p_day,
    # n_day} pair to a unique, ordered set of day starts (1 entry when same day).
    day_starts: list[int] = []
    for anchor in (p, n):
        d = dt.datetime.fromtimestamp(max(0, anchor), tz=dt.UTC)
        midnight = dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC)
        day_starts.append(int(midnight.timestamp()))

    events: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for day_start in dict.fromkeys(day_starts):
        # The window's calendar day is the day_start's weekday — a prev-day anchor
        # that is a weekend day contributes no edges (cash books shut), so the
        # weekend guard is applied PER day_start, not once on ``now_ts``.
        if dt.datetime.fromtimestamp(day_start, tz=dt.UTC).weekday() >= 5:
            continue  # weekend → cash books shut, no session edges
        for region, (open_min, close_min) in _GROUP_WINDOW.items():
            open_epoch = day_start + open_min * 60
            close_epoch = day_start + close_min * 60
            if p < open_epoch <= n and (region, "OPEN") not in seen:
                events.append((region, "OPEN"))
                seen.add((region, "OPEN"))
            if p < close_epoch <= n and (region, "CLOSE") not in seen:
                events.append((region, "CLOSE"))
                seen.add((region, "CLOSE"))
    return events
