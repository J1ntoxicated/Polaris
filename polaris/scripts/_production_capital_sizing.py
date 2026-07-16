"""Bug C fix — T4 notional → per-epic Capital lot translation (DEMO/PAPER).

The production Capital entry previously submitted ``size=MIN_CAPITAL_LOT``
(1.0 lot) for EVERY order, ignoring the T4 sized notional (live evidence: 216
entry fills, every ``size_usd`` exactly $200.00 = 1.0 lot × pip-default 10 ×
leverage 20). This module owns the wiring that EXPRESSES the T4 output at the
venue — it adds NO sizing multiplier (the T4 chain — continuous scalar × tier
amplifier × cell mult → single hard-cap min() clip — is untouched):

* ``CapitalConstraintCache`` — lazy per-epic TTL cache of ``/markets/{epic}``
  dealing rules (single-flight, stale-serve, negative cooldown, reject-evict).
  A warmed epic costs the order path ZERO network calls (429 guard).
* ``translate_capital_order`` — T4 USD notional → venue lots via the cached
  constraint (+ quote→USD when the epic is not USD-quoted: bars first, the
  cached market-detail snapshot mid second). Returns ``None`` on ANY
  degradation so the caller keeps the CURRENT legacy 1-lot path — entries
  always flow (flow_not_block), the gap stays visible via the state counter.
* ``capital_close_contract_factor`` — peek-only (no I/O) factor so the close
  fill's ``size_usd`` records real exposure too.
* ``maybe_evict_on_reject`` — a size/step-shaped venue reject evicts the
  (possibly stale) constraint, bypassing the cooldown, so the next attempt
  force-refetches fresh dealing rules.

Venue semantics calibrated on REAL demo payloads (tests/fixtures/
capital_markets, RO GET probe 2026-06-11 — no orders): ``size`` is UNDERLYING
UNITS (``lotSize``=1, ``valueOfOnePip`` absent), so one unit's USD exposure =
price × lotSize × quote→USD. Leverage scales MARGIN only — never exposure.

Min-deal policy (review blocker fix): the fence is a LEDGER — it compares
``requested_notional`` against nothing. Hard caps bind at T4 sizing time off
``position_risk_state``, which (post-fix) records REAL exposure, so a venue
min-deal bump above the T4 ask is ACCEPTED + logged (observability only) and
self-corrects on the NEXT entry's sizing. No new block/clip is introduced.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from polaris.scripts._production_bars import BAR_TS_CLOCK_SKEW_SLACK_SEC
from polaris.venues.capital.constraint_translator import (
    CAPITAL_MARKET_DETAIL_PATH,
    CapitalMarketConstraint,
    payload_to_constraint,
    size_usd_to_lots,
    unit_notional_usd,
)

if TYPE_CHECKING:
    from polaris.scripts._production_state import ProdLoopState

logger = logging.getLogger(__name__)

__all__ = [
    "CapitalConstraintCache",
    "CapitalOrderPlan",
    "capital_close_contract_factor",
    "maybe_evict_on_reject",
    "translate_capital_order",
]

CONSTRAINT_TTL_SEC: Final[float] = 3600.0  # dealingRules are quasi-static
NEGATIVE_COOLDOWN_SEC: Final[float] = 120.0  # fetch-fail backoff (429/5xx guard)
FETCH_TIMEOUT_SEC: Final[float] = 5.0  # cold-miss timebox inside the order path
_MIN_BUMP_WARN_RATIO: Final[float] = 3.0  # submit/T4 ratio that escalates to WARN

# quote ccy → (Capital FX conversion epic, invert). Resolution order: bars
# (free, no network) → the cached market-detail snapshot mid (≤1 fetch/h via
# the SAME cache) → None (degrade to the legacy path — a wrong rate would
# corrupt the recorded exposure ~155× for JPY, the cross-instrument-PnL class).
_USD_EQUIVALENTS: Final[frozenset[str]] = frozenset({"", "USD", "USDT", "USDC"})
_QUOTE_RATE_EPICS: Final[dict[str, tuple[str, bool]]] = {
    "JPY": ("USDJPY", True), "EUR": ("EURUSD", False), "GBP": ("GBPUSD", False),
    "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False), "CHF": ("USDCHF", True),
    "CAD": ("USDCAD", True), "NOK": ("USDNOK", True), "SEK": ("USDSEK", True),
    "DKK": ("USDDKK", True), "ZAR": ("USDZAR", True), "SGD": ("USDSGD", True),
    "HKD": ("USDHKD", True), "CNH": ("USDCNH", True), "PLN": ("USDPLN", True),
}
# Venue reject text that points at a stale size/step constraint.
_SIZE_REJECT_MARKERS: Final[tuple[str, ...]] = ("SIZE", "STEP", "MINIMUM", "MAXIMUM")


@dataclass(frozen=True, slots=True)
class CapitalOrderPlan:
    """A T4 notional translated into a submittable Capital order."""

    lots: float
    unit_notional_usd: float
    effective_notional_usd: float  # lots × unit — what fence/risk rows record
    contract_factor_usd: float  # lotSize × quote→USD — fill size_usd factor


@dataclass(slots=True)
class CapitalConstraintCache:
    """Lazy per-epic TTL cache for Capital market constraints.

    Loop-state owned (G1FocusCache / G6CallCache pattern). Failure ladder:
    fresh hit → serve (no I/O); expired + fetch fail → STALE-serve (rules are
    quasi-static) + cooldown; never-held + fetch fail → ``None`` + per-epic
    120 s negative cooldown; size/step venue reject → ``evict`` (cooldown
    bypassed) so the next attempt force-refetches.
    """

    _entries: dict[str, tuple[CapitalMarketConstraint, float]] = field(
        default_factory=dict
    )
    _neg_until: dict[str, float] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def peek(self, epic: str) -> CapitalMarketConstraint | None:
        """Fresh-OR-stale constraint without any I/O (close-path factor)."""
        entry = self._entries.get(epic)
        return entry[0] if entry is not None else None

    def evict(self, epic: str) -> None:
        """Drop the epic + its cooldown → the next ``get`` force-refetches."""
        self._entries.pop(epic, None)
        self._neg_until.pop(epic, None)

    async def get(self, session: Any, epic: str) -> CapitalMarketConstraint | None:
        entry = self._entries.get(epic)
        now = time.monotonic()
        if entry is not None and now - entry[1] < CONSTRAINT_TTL_SEC:
            return entry[0]
        if now < self._neg_until.get(epic, 0.0):
            return entry[0] if entry is not None else None
        lock = self._locks.setdefault(epic, asyncio.Lock())
        async with lock:
            # Single-flight: a concurrent caller may have refreshed meanwhile.
            entry = self._entries.get(epic)
            now = time.monotonic()
            if entry is not None and now - entry[1] < CONSTRAINT_TTL_SEC:
                return entry[0]
            if now < self._neg_until.get(epic, 0.0):
                return entry[0] if entry is not None else None
            return await self._fetch_locked(session, epic, stale=entry)

    async def _fetch_locked(
        self,
        session: Any,
        epic: str,
        stale: tuple[CapitalMarketConstraint, float] | None,
    ) -> CapitalMarketConstraint | None:
        try:
            resp = await asyncio.wait_for(
                session.request("GET", f"{CAPITAL_MARKET_DETAIL_PATH}/{epic}"),
                timeout=FETCH_TIMEOUT_SEC,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"market detail status={resp.status_code}")
            body = resp.json()
            if not isinstance(body, dict):
                raise RuntimeError("market detail non-dict payload")
            constraint = payload_to_constraint(epic, body)
        except Exception as exc:  # noqa: BLE001 — venue I/O must not escape
            self._neg_until[epic] = time.monotonic() + NEGATIVE_COOLDOWN_SEC
            if stale is not None:
                logger.warning(
                    "[capital-constraint] %s refresh failed %r — serving STALE "
                    "(quasi-static rules; retry in %.0fs)",
                    epic, exc, NEGATIVE_COOLDOWN_SEC,
                )
                return stale[0]
            logger.warning(
                "[capital-constraint] %s fetch failed %r — None + %.0fs cooldown "
                "(caller keeps the legacy 1-lot path; the entry still flows)",
                epic, exc, NEGATIVE_COOLDOWN_SEC,
            )
            return None
        self._entries[epic] = (constraint, time.monotonic())
        self._neg_until.pop(epic, None)
        return constraint


def _bars_quote_usd_rate(
    conn: sqlite3.Connection, quote_ccy: str,
    md_conn: sqlite3.Connection | None = None,
) -> float | None:
    """Latest 1m bar close of the conversion pair → quote-ccy→USD multiplier.

    storage-split (round 3 MED fix): ``bars`` is marketdata-domain, written
    ONLY to the marketdata conn post-split. ``md_conn=None`` (legacy/test
    callers, single-DB mode) falls back to ``conn``, byte-identical.
    """
    pair = _QUOTE_RATE_EPICS.get(quote_ccy)
    if pair is None:
        return None
    epic, invert = pair
    _md = md_conn if md_conn is not None else conn
    ts_upper = int(time.time()) + BAR_TS_CLOCK_SKEW_SLACK_SEC
    try:
        row = _md.execute(
            "SELECT close FROM bars WHERE instrument_id = ? AND "
            "bar_interval = '1m' AND ts <= ? ORDER BY ts DESC LIMIT 1",
            (f"capital:{epic}", ts_upper),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    px = float(row[0])
    if px <= 0.0:
        return None
    return (1.0 / px) if invert else px


def _snapshot_rate(
    constraint: CapitalMarketConstraint | None, invert: bool
) -> float | None:
    if constraint is None or constraint.snapshot_mid <= 0.0:
        return None
    return (1.0 / constraint.snapshot_mid) if invert else constraint.snapshot_mid


def _peek_quote_usd_rate(
    cache: CapitalConstraintCache, conn: sqlite3.Connection, quote_ccy: str,
    md_conn: sqlite3.Connection | None = None,
) -> float | None:
    """quote ccy → USD multiplier WITHOUT network (bars, then cached snapshot)."""
    q = (quote_ccy or "").upper()
    if q in _USD_EQUIVALENTS:
        return 1.0
    rate = _bars_quote_usd_rate(conn, q, md_conn=md_conn)
    if rate is not None:
        return rate
    pair = _QUOTE_RATE_EPICS.get(q)
    if pair is None:
        return None
    return _snapshot_rate(cache.peek(pair[0]), pair[1])


async def _resolve_quote_usd_rate(
    cache: CapitalConstraintCache,
    conn: sqlite3.Connection,
    session: Any,
    quote_ccy: str,
    md_conn: sqlite3.Connection | None = None,
) -> float | None:
    """quote ccy → USD; bars first, the venue snapshot (via the cache) second."""
    rate = _peek_quote_usd_rate(cache, conn, quote_ccy, md_conn=md_conn)
    if rate is not None:
        return rate
    pair = _QUOTE_RATE_EPICS.get((quote_ccy or "").upper())
    if pair is None:
        return None
    return _snapshot_rate(await cache.get(session, pair[0]), pair[1])


async def translate_capital_order(
    *,
    state: ProdLoopState,
    conn: sqlite3.Connection,
    capital_session: Any,
    epic: str,
    notional_usd: float,
    last_price: float,
) -> CapitalOrderPlan | None:
    """T4 notional → venue lots. ``None`` = degraded → caller keeps legacy path.

    Total (never raises): ANY failure degrades to the CURRENT live behaviour
    (1.0 lot + legacy fill maths) with the ``capital_constraint_fallbacks``
    counter + WARN — the entry always flows (flow_not_block).
    """
    try:
        if capital_session is None or notional_usd <= 0.0 or last_price <= 0.0:
            return None
        cache = state.capital_constraints
        constraint = await cache.get(capital_session, epic)
        if constraint is None:
            state.capital_constraint_fallbacks += 1
            logger.warning(
                "[capital-sizing] %s constraint unavailable — legacy 1-lot "
                "fallback (fallbacks=%d)", epic, state.capital_constraint_fallbacks,
            )
            return None
        rate = await _resolve_quote_usd_rate(
            cache, conn, capital_session, constraint.quote_ccy,
            md_conn=state.md_conn,
        )
        if rate is None or rate <= 0.0:
            state.capital_constraint_fallbacks += 1
            logger.warning(
                "[capital-sizing] %s quote ccy %r → USD rate unavailable — legacy "
                "1-lot fallback (a wrong rate would mis-record exposure)",
                epic, constraint.quote_ccy,
            )
            return None
        lots = size_usd_to_lots(
            constraint=constraint, notional_usd=notional_usd,
            last_price=last_price, quote_usd_rate=rate,
        )
        unit = unit_notional_usd(
            constraint=constraint, last_price=last_price, quote_usd_rate=rate
        )
        if lots <= 0.0 or unit <= 0.0:
            state.capital_constraint_fallbacks += 1
            return None
        effective = lots * unit
        factor = (constraint.lot_size if constraint.lot_size > 0.0 else 1.0) * rate
        if effective > notional_usd * (1.0 + 1e-9):
            # Venue min-deal raised the submit above the T4 ask. ACCEPTED:
            # venue floor EXPRESSION, not a sizing multiplier (9-stack intact).
            # The fence ledger records the submitted notional and the corrected
            # position_risk_state re-binds the caps on the NEXT entry's sizing.
            ratio = effective / notional_usd
            log = logger.warning if ratio > _MIN_BUMP_WARN_RATIO else logger.info
            log(
                "[capital-sizing] %s venue min-deal raised the submit above the "
                "T4 ask: $%.2f → $%.2f (%.2fx, lots=%s ≥ min %s) — observability "
                "only, no block", epic, notional_usd, effective, ratio, lots,
                constraint.min_deal_size,
            )
        return CapitalOrderPlan(
            lots=lots, unit_notional_usd=unit, effective_notional_usd=effective,
            contract_factor_usd=factor,
        )
    except Exception as exc:  # noqa: BLE001 — sizing wiring must not escape
        state.capital_constraint_fallbacks += 1
        logger.warning(
            "[capital-sizing] %s translate failed %r — legacy fallback", epic, exc
        )
        return None


def capital_close_contract_factor(
    *, state: ProdLoopState, conn: sqlite3.Connection, epic: str
) -> float | None:
    """Peek-only close-fill factor (lotSize × quote→USD); ``None`` = legacy.

    The cache is usually warmed by the entry; a cache miss (e.g. a position
    hydrated after a restart) keeps the legacy close ``size_usd`` — visible via
    the fallbacks counter, PnL unaffected (it keys off the ENTRY ``size_usd``).
    """
    try:
        constraint = state.capital_constraints.peek(epic)
        if constraint is None:
            state.capital_constraint_fallbacks += 1
            return None
        rate = _peek_quote_usd_rate(
            state.capital_constraints, conn, constraint.quote_ccy,
            md_conn=state.md_conn,
        )
        if rate is None or rate <= 0.0:
            state.capital_constraint_fallbacks += 1
            return None
        return (constraint.lot_size if constraint.lot_size > 0.0 else 1.0) * rate
    except Exception:  # noqa: BLE001 — close path must not break on factor maths
        return None


def maybe_evict_on_reject(
    state: ProdLoopState,
    *,
    epic: str,
    reject_code: str | None,
    reject_msg: str | None,
) -> bool:
    """Evict a possibly-stale constraint on a size/step-shaped venue reject.

    Review blocker fix: a venue-side minDealSize/step change inside the TTL
    would otherwise reject every order for up to 1 h (and stale-serve could
    extend it). Eviction bypasses the negative cooldown so the NEXT attempt
    force-refetches. Flow preserved — nothing is blocked here.
    """
    blob = f"{reject_code or ''} {reject_msg or ''}".upper()
    if not any(marker in blob for marker in _SIZE_REJECT_MARKERS):
        return False
    state.capital_constraints.evict(epic)
    logger.warning(
        "[capital-constraint] %s evicted on size/step venue reject (%s) — "
        "forced re-fetch on the next attempt (flow preserved)", epic, reject_code,
    )
    return True
