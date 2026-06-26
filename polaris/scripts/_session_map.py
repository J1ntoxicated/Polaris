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
from typing import Final

__all__ = [
    "ASIA_CLOSE_MIN",
    "ASIA_OPEN_MIN",
    "EUROPE_CLOSE_MIN",
    "EUROPE_OPEN_MIN",
    "SESSION_DORMANT",
    "US_CLOSE_MIN",
    "US_OPEN_MIN",
    "WARM_LEAD_MIN",
    "instrument_session_weight",
    "session_group",
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
# They have no ``_SESSION_GROUP`` entry, so FOR WARMING ONLY they are absorbed
# into the 'us' cash-session window (their cash open IS the US RTH open). The
# TRADE weight (``instrument_session_weight``) is UNTOUCHED by this mapping.
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
