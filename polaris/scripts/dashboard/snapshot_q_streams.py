"""Polaris dashboard v1 — per-stream (venue lane) summary queries.

Recent-closed-by-venue feed, the live Alpaca paper-account equity probe, and the
per-stream rollup that reconciles each lane to the global headline. Split out of
``snapshot_queries.py`` to keep each module ≤500 LOC (move-only; no logic
change). Display-only — never a trading path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from collections.abc import Mapping
from typing import Any, Final, NamedTuple

from polaris.core.economics.fees import real_fee_usd
from polaris.core.metrics.risk_unit import R_USD_PROXY, realised_r_stream
from polaris.core.sessions.equity_session_gate import us_equity_session_state
from polaris.core.sizing.constants import (
    demo_starting_equity_alpaca_display,
    demo_starting_equity_capital,
    demo_starting_equity_okx,
)
from polaris.core.sizing.session import resolve_venue_session
from polaris.core.streams.config import STREAMS
from polaris.scripts.dashboard.snapshot_models import (
    ClosedTrade,
    PositionRow,
    StreamSummary,
)
from polaris.scripts.dashboard.snapshot_q_common import (
    SLIPPAGE_BPS_DIVISOR,
    _exit_reason_by_position,
    _model_price,
    _safe_query,
    _symbol_from_inst,
    _today_start_ms,
)
from polaris.scripts.dashboard.snapshot_q_positions import (
    _cell_mult_lookup,
    _entry_price_lookup,
    _last_prices,
    _read_positions,
)
from polaris.storage.virtual_account_equity import virtual_equity_now
from polaris.storage.weekly_equity_trace import all_current_week_rows

logger = logging.getLogger(__name__)

# Display label + web color per stream, keyed on the SSOT ``stream_id``
# (``polaris.core.streams.config.STREAMS``). The venue→stream_id mapping itself
# is NOT duplicated here — it comes from ``VENUE_TO_STREAM`` (the SSOT). This map
# only carries the two purely-presentational attributes the streams config does
# not own (a human label + a CSS hex color the web board renders). Hex colors so
# the JSON snapshot is directly consumable by the frontend (board.js).
_STREAM_DISPLAY: Final[Mapping[str, tuple[str, str]]] = {
    "A_okx_crypto": ("OKX SPOT", "#5fafff"),       # blue lane
    "B_capital_cfd": ("CAPITAL CFD", "#ffd75f"),   # gold lane
    "C_alpaca_equity": ("ALPACA EQUITY", "#87d75f"),  # green lane
}
_STREAM_DISPLAY_DEFAULT: Final[tuple[str, str]] = ("?", "#9e9e9e")


RECENT_CLOSED_PER_STREAM: Final[int] = 6  # cap of recent-closed rows per lane

# VIRTUAL ACCOUNT seed (Jin 2026-07-07) — $100k per exchange, the fresh
# measurement anchor. Distinct from the legacy real-venue starting-equity
# constants (``demo_starting_equity_okx`` etc, the old $79k/$51k-style venue
# split) — this is the NEW uniform per-exchange virtual seed.
VIRTUAL_SEED_USD: Final[float] = 100_000.0


def _recent_closed_by_venue(
    conn: sqlite3.Connection, *, per_venue: int = RECENT_CLOSED_PER_STREAM
) -> dict[str, list[ClosedTrade]]:
    """Most-recent closed trades per venue (newest first, capped per venue).

    Lightweight read for the per-stream OPEN/CLOSED split: each close fill is a
    closed trade, grouped by venue. Entry price/held are reconstructed from the
    close fill's own pnl (display-only; the global ``recent_trades`` panel keeps
    the exact entry-pairing logic). Empty venues yield no key → an empty list at
    the call site (graceful zero). Pure read-only.
    """
    # P0-4 ①: real exit_reason via the SAME positions⋈segments lineage SSOT the
    # global recent_trades panel uses (no separate pnl-sign TP/SL/FLAT guess).
    exit_reason_by_id = _exit_reason_by_position(conn)
    # Step N: each close row's R is the STREAM-COMMON realised R derived from its
    # OWN venue + ``fills.pnl_usd`` (``pnl_usd / R_budget(venue)``) — the same
    # ruler every other panel uses, comparable across venues, no stored-pnl_r
    # read (so OLD venue-skewed rows display on the new ruler too). An unknown
    # venue yields R_budget 0 → the shared R_USD_PROXY fallback (one number).
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, side, fill_price,
                  pnl_usd, ts_ms, base_qty, contribution_id, size_usd
           FROM fills
           WHERE is_close = 1
           ORDER BY ts_ms DESC""",
    )
    out: dict[str, list[ClosedTrade]] = {}
    for r in rows:
        venue = str(r[0] or "").lower()
        bucket = out.setdefault(venue, [])
        if len(bucket) >= per_venue:
            continue
        inst = str(r[1] or "")
        side = str(r[3] or "")
        fill_price = float(r[4] or 0.0)
        pnl = float(r[5] or 0.0)
        ts_ms = int(r[6] or 0)
        qty = float(r[7] or 0.0)
        contrib = r[8]
        contrib_str = str(contrib) if contrib else None
        size_usd = float(r[9] or 0.0)
        if qty > 0 and fill_price > 0:
            sign = 1.0 if side.lower() == "sell" else -1.0
            entry_px = fill_price - (pnl / qty) * sign
        else:
            entry_px = fill_price
        reason = exit_reason_by_id.get(contrib_str or "", "exit")
        # P0-4 ②: real (not demo) close-leg fee, so this chip's fee/net agree
        # with the TRADES tab's same trade (single real-fee schedule, one net).
        real_fee = real_fee_usd(venue, size_usd) if size_usd > 0 else 0.0
        net_usd = pnl - real_fee
        # Stream-common R from this venue's R_budget; unknown venue → proxy.
        r_units = realised_r_stream(pnl_usd=pnl, venue=venue)
        if r_units == 0.0 and pnl != 0.0:
            r_units = pnl / R_USD_PROXY
        bucket.append(
            ClosedTrade(
                ts_close=ts_ms // 1000,
                venue=venue,
                symbol=_symbol_from_inst(inst),
                strategy_id=str(r[2] or ""),
                side_close=side,
                entry_price=entry_px,
                exit_price=fill_price,
                pnl_usd=pnl,
                r_units=r_units,
                held_sec=0.0,
                exit_reason=reason,
                real_fee_usd=real_fee,
                net_usd=net_usd,
            )
        )
    return out


# Alpaca paper account equity probe (display-only). Unlike OKX/Capital, Alpaca
# has NO static starting-equity constant — the paper account is funded directly
# at the venue, so the only source of truth for its baseline is the live
# ``GET /v2/account`` call. We probe it once per TTL window and cache the result;
# the dashboard then shows the real account value instead of a $0 placeholder.
# Read-only (account query, never an order). Graceful on every failure path:
# missing keys / network error / non-200 → ``None`` → caller falls back to 0.0.
ALPACA_EQUITY_PROBE_TTL_SEC: Final[float] = 60.0


class _AlpacaEquity(NamedTuple):
    """Probed Alpaca paper-account values (USD). ``starting`` is the session
    baseline (``last_equity`` — equity at the prior market close) so the
    ``equity = starting + net_pnl + upnl`` identity reconciles with DB-tracked
    session activity exactly like the OKX/Capital lanes."""

    equity: float
    starting: float


# (monotonic_deadline, _AlpacaEquity | None) — None caches a failed/absent probe
# for the TTL window too, so a creds-less dashboard does not retry every refresh.
_alpaca_equity_cache: tuple[float, _AlpacaEquity | None] | None = None


async def _fetch_alpaca_account(api_key: str, secret: str) -> dict[str, Any]:
    """Probe ``GET /v2/account`` via the paper adapter (read-only, no order)."""
    # Lazy import so the dashboard module has no hard dependency on the venue
    # adapter (and tests that never probe never import httpx via this path).
    from polaris.venues.alpaca.adapter import AlpacaAdapter

    async with AlpacaAdapter(api_key=api_key, secret=secret) as adapter:
        return await adapter.fetch_account()


def _alpaca_account_equity() -> _AlpacaEquity | None:
    """Live Alpaca paper-account equity (USD), TTL-cached. ``None`` if unavailable.

    Reads credentials from ``os.environ`` ONLY (it does not load ``.env`` itself
    — the dashboard server loads it at startup, and the test suite never sets the
    keys, so tests stay fully offline → this returns ``None`` → the alpaca lane
    keeps its 0.0 baseline, unchanged behavior). Secrets are never logged. Any
    error (no keys / transport / non-200 / parse) is swallowed and cached as
    ``None`` for the TTL window. Display-only; never feeds sizing/gating/orders.
    """
    global _alpaca_equity_cache
    now = time.monotonic()
    if _alpaca_equity_cache is not None and now < _alpaca_equity_cache[0]:
        return _alpaca_equity_cache[1]

    result: _AlpacaEquity | None = None
    # Resolve creds from the environment only (no .env auto-load here). Mirrors
    # the adapter's PAPER-first / ARCHIVE-fallback order without importing it
    # when the keys are absent.
    key = os.environ.get("ALPACA_PAPER_API_KEY") or os.environ.get(
        "ARCHIVE_ALPACA_PAPER_API_KEY", ""
    )
    secret = os.environ.get("ALPACA_PAPER_SECRET") or os.environ.get(
        "ARCHIVE_ALPACA_PAPER_SECRET", ""
    )
    if key and secret:
        try:
            account = asyncio.run(_fetch_alpaca_account(key, secret))
            equity = float(account.get("equity") or account.get("portfolio_value") or 0.0)
            # ``last_equity`` = equity at the prior market close → session-start
            # baseline. Fall back to current equity when absent (first session).
            starting = float(account.get("last_equity") or equity)
            if equity > 0.0:
                result = _AlpacaEquity(equity=equity, starting=starting)
        except Exception as exc:  # noqa: BLE001 — display-only, never crash a refresh
            # Log the class only — never the keys or full response.
            logger.warning("[dashboard] alpaca equity probe failed: %s", type(exc).__name__)
            result = None

    _alpaca_equity_cache = (now + ALPACA_EQUITY_PROBE_TTL_SEC, result)
    return result


def _marks_age_sec(
    conn: sqlite3.Connection, *, venue: str, now_s: int,
    md_conn: sqlite3.Connection | None = None,
) -> int:
    """Age (seconds) of the freshest ``<venue>:*`` bar close, for a closed-venue
    stale-mark label. 0 when no bars exist yet for that venue (nothing to be
    stale). Generalized (Jin 2026-07-08 dashboard-live-net fix) from the
    Alpaca-only original — Capital CFD (FX/indices/gold) also closes on
    weekends, so this now serves both lanes. Storage-split (2026-07-14):
    ``bars`` is marketdata-domain — reads ``md_conn`` when supplied, falling
    back to ``conn`` (byte-identical for every existing single-conn caller)."""
    rows = _safe_query(
        md_conn if md_conn is not None else conn,
        "SELECT MAX(ts) FROM bars WHERE instrument_id LIKE ?",
        (f"{venue}:%",),
    )
    latest_ts = int(rows[0][0] or 0) if rows and rows[0][0] is not None else 0
    return max(0, now_s - latest_ts) if latest_ts > 0 else 0


def _per_stream_summary(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    positions: list[PositionRow] | None = None,
    md_conn: sqlite3.Connection | None = None,
) -> list[StreamSummary]:
    """Per-stream (venue lane) rollup — one row per registered stream.

    Emits a row for **every** stream in the SSOT (``STREAMS``) even when a venue
    has zero activity, so the dashboard always renders all lanes. Reconciliation
    invariant (the dashboard never lies):

    - ``Σ net_pnl_usd`` == global ``daily_pnl_usd`` (``_daily_realised_pnl``):
      both use the identical session lookback + ``Σ(close pnl) − Σ(all fees)``
      formula, grouped by venue here.
    - ``Σ daily_trades`` == global closed-FILL count (``_daily_realised_pnl``'s
      own count leg; a partial-close trade emits >1 close fill, so this can
      exceed the closed-POSITION count — kept for the tooltip, P0-4 ③).
    - ``Σ closed_n`` == global closed-POSITION count over the same window — the
      TRADES counter the board displays (one row per trade, not per fill).
    - ``Σ open_positions_n`` / ``upnl_usd`` / ``exposed_usd`` decompose the same
      ``positions`` list the global snapshot aggregates, so they sum exactly.

    ``positions`` is the already-built list from ``_read_positions`` (passed by
    ``collect_snapshot`` for exact reconciliation); when omitted it is read here.
    Pure read-only; no trading behavior touched.
    """
    if positions is None:
        last_prices = _last_prices(conn)
        entry_lookup = _entry_price_lookup(conn)
        cell_mult = _cell_mult_lookup(conn)
        # regime_lookup is only used to refine cell_mult; an empty map is fine
        # for the rollup (mult does not affect pnl/upnl/exposed aggregation).
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=last_prices,
            entry_lookup=entry_lookup,
            cell_mult=cell_mult,
            regime_lookup={},
        )

    # --- fills side: net realised pnl + closed-trade count, GROUP BY venue.
    # Same formula + 'Today' lookback as ``_daily_realised_pnl`` (P0-2, Jin
    # 2026-07-02 — floored at max(session_start, AEST midnight)) so the
    # per-venue sum reconciles to the global 'Today' headline exactly.
    lookback_ms = _today_start_ms(conn, now_s=now_s)
    # ``net_pnl`` already nets fees (Σ close pnl − Σ all fees) so it reconciles
    # to the global ``_daily_realised_pnl``. ``fee_total`` + ``slip_total`` are
    # display-only cost breakdowns surfaced alongside it. slippage_usd is derived
    # from ``slippage_bps`` (no explicit slippage_usd column in fills):
    # slippage_bps / 10000 × size_usd, summed per venue.
    # Hardening #1 (2026-06-23): exclude RECONCILED (tracking-failure) fills (both
    # legs) so the per-venue net/count match the global daily headline EXACTLY —
    # the reconciliation invariant (Σ streams == daily) is preserved because both
    # apply the identical ``status != 'reconciled'`` LEFT JOIN. Orphan fills KEPT.
    fill_rows = _safe_query(
        conn,
        """SELECT f.venue,
                  COALESCE(SUM(CASE WHEN f.is_close = 1 THEN f.pnl_usd ELSE 0.0 END), 0.0)
                  - COALESCE(SUM(f.fee_usd), 0.0) AS net_pnl,
                  COALESCE(SUM(f.is_close), 0) AS closed_n,
                  COALESCE(SUM(f.fee_usd), 0.0) AS fee_total,
                  COALESCE(SUM(f.slippage_bps / ? * f.size_usd), 0.0) AS slip_total
           FROM fills f
           LEFT JOIN positions p ON p.position_id = f.contribution_id
           WHERE f.ts_ms >= ?
             AND (p.status IS NULL OR p.status != 'reconciled')
           GROUP BY f.venue""",
        (SLIPPAGE_BPS_DIVISOR, lookback_ms),
    )
    pnl_by_venue: dict[str, float] = {}
    trades_by_venue: dict[str, int] = {}
    fee_by_venue: dict[str, float] = {}
    slip_by_venue: dict[str, float] = {}
    for r in fill_rows:
        venue = str(r[0] or "").lower()
        pnl_by_venue[venue] = float(r[1] or 0.0)
        trades_by_venue[venue] = int(r[2] or 0)
        fee_by_venue[venue] = float(r[3] or 0.0)
        slip_by_venue[venue] = float(r[4] or 0.0)

    # --- AI cost side: gate_events has NO venue column, so attribute each event
    # to a venue via the position_id → positions.venue join (the SSOT venue
    # source). NULL-position_id gate_events are UNATTRIBUTABLE and intentionally
    # excluded; their tokens are not assigned to any stream (documented gap,
    # display-only). NOTE (gate→outcome instrumentation): a run that ACTUALLY
    # OPENED now backfills its G1-G5 rows with the position_id, so opened
    # entries' pre-position tokens DO attribute here; only killed / never-opened
    # runs remain unattributed (per-venue AI cost is higher + more complete).
    # Cost = Σ (input+output tokens)/1000 × MODEL_PRICE_PER_1K[model_used].
    ai_cost_by_venue: dict[str, float] = {}
    ai_rows = _safe_query(
        conn,
        """SELECT p.venue, g.model_used,
                  COALESCE(SUM(g.input_tokens + g.output_tokens), 0)
           FROM gate_events g
           JOIN positions p ON g.position_id = p.position_id
           WHERE g.created_ts >= ?
             AND g.position_id IS NOT NULL AND g.position_id != ''
           GROUP BY p.venue, g.model_used""",
        (lookback_ms // 1000,),
    )
    for r in ai_rows:
        venue = str(r[0] or "").lower()
        tokens = int(r[2] or 0)
        cost = tokens / 1000.0 * _model_price(r[1])
        ai_cost_by_venue[venue] = ai_cost_by_venue.get(venue, 0.0) + cost

    # --- positions side: open_n / exposed / upnl, grouped by venue from the
    # already-aggregated PositionRow list (exact decomposition of the globals).
    open_by_venue: dict[str, int] = {}
    exposed_by_venue: dict[str, float] = {}
    upnl_by_venue: dict[str, float] = {}
    for p in positions:
        v = p.venue.lower()
        open_by_venue[v] = open_by_venue.get(v, 0) + 1
        exposed_by_venue[v] = exposed_by_venue.get(v, 0.0) + p.size_usd
        upnl_by_venue[v] = upnl_by_venue.get(v, 0.0) + p.upnl_usd

    # P0-4 ③: TRADES counter = ONE closed POSITION, not one closed FILL (a
    # partial-close position emits >1 close fill, so the fills count over-counts
    # trades). Same session lookback + RECONCILED exclusion as the fills query
    # above; ``positions.closed_ts`` is in SECONDS (fills.ts_ms is ms).
    closed_pos_rows = _safe_query(
        conn,
        """SELECT venue, COUNT(*) FROM positions
           WHERE status = 'closed' AND closed_ts >= ?
           GROUP BY venue""",
        (lookback_ms // 1000,),
    )
    closed_positions_by_venue: dict[str, int] = {
        str(r[0] or "").lower(): int(r[1] or 0) for r in closed_pos_rows
    }

    # Per-venue starting capital. OKX/Capital use the static demo-equity SSOT;
    # Alpaca prefers the live ``/v2/account`` probe baseline (the venue-funded
    # truth) and falls back to the P0-2 display-baseline constant
    # (``demo_starting_equity_alpaca_display()``) when the probe is unavailable
    # (no keys / error) — this keeps the per-stream lane reconciled with the
    # header ``starting_capital`` (== okx + capital + alpaca display leg) instead
    # of silently zeroing the lane. ``equity = starting + net_pnl + upnl`` then
    # reconciles with DB session activity for every lane.
    alpaca_equity = _alpaca_account_equity()
    starting_by_venue: dict[str, float] = {
        "okx": demo_starting_equity_okx(),
        "capital": demo_starting_equity_capital(),
        "alpaca": (
            alpaca_equity.starting
            if alpaca_equity is not None
            else demo_starting_equity_alpaca_display()
        ),
    }

    # OPEN vs CLOSED split — per-venue recent-closed trades (newest first).
    recent_closed_by_venue = _recent_closed_by_venue(conn)

    # Alpaca RTH session state (weekday+holiday-aware SSOT) drives the
    # marks label below: "rth" → the live venue equity probe is trustworthy;
    # anything else (closed/pre_market/after_hours) → uPnL is computed from
    # the last internal mark (stale bars/ticks), so label it explicitly.
    alpaca_session = us_equity_session_state(now_s)
    alpaca_marks_age = (
        _marks_age_sec(conn, venue="alpaca", now_s=now_s, md_conn=md_conn)
        if alpaca_session != "rth" else 0
    )
    # Capital CFD (FX/indices/gold) weekend closure (Jin 2026-07-08 fix —
    # dashboard-live-net hunt) — the bot's own venue-native session SSOT
    # (``polaris.core.sizing.session.resolve_venue_session``) already models
    # this (``_capital_session``: closed Fri 22:00 UTC → Sun 22:00 UTC), but the
    # dashboard's staleness check previously only looked at Alpaca, on the false
    # premise OKX/Capital are "always 24/7" — Capital is NOT. Same pattern as
    # Alpaca above: "fx_open" → live; anything else → last internal mark, labeled.
    capital_session = resolve_venue_session("capital", now_s)
    capital_marks_age = (
        _marks_age_sec(conn, venue="capital", now_s=now_s, md_conn=md_conn)
        if capital_session != "fx_open" else 0
    )

    # Weekly per-exchange trace (Jin 2026-07-07) — Monday-anchored, NON-
    # DESTRUCTIVE (this is "this week so far" telemetry; the equity/DD above
    # is the continuously-compounding account, untouched by the weekly row).
    weekly_by_exchange = {r.exchange: r for r in all_current_week_rows(conn, now_ts=now_s)}

    # VIRTUAL ACCOUNT per exchange (Jin 2026-07-07) — the fresh $100k seed
    # measurement (``virtual_account_equity.virtual_equity_now``), SEPARATE
    # from the legacy real-venue ``starting`` / ``equity`` above. Zero venue
    # calls (internal fills ledger only). THE profit readout for the board.
    virtual_by_venue = {
        venue: virtual_equity_now(conn, exchange=venue, seed_equity=VIRTUAL_SEED_USD)
        for venue in ("okx", "capital", "alpaca")
    }

    out: list[StreamSummary] = []
    # Stable lane order = SSOT registration order (A, B, C).
    for stream_id, cfg in STREAMS.items():
        venue = cfg.venue
        label, color = _STREAM_DISPLAY.get(stream_id, _STREAM_DISPLAY_DEFAULT)
        net_pnl = pnl_by_venue.get(venue, 0.0)
        upnl = upnl_by_venue.get(venue, 0.0)
        starting = starting_by_venue.get(venue, 0.0)
        equity = starting + net_pnl + upnl
        # Naive per-stream DD: shortfall of current equity vs starting (peak
        # proxy). Best-effort display only — the global curve owns the true DD.
        drawdown_pct = (
            max(0.0, (starting - equity) / starting * 100.0) if starting > 0 else 0.0
        )
        fee = fee_by_venue.get(venue, 0.0)
        slippage = slip_by_venue.get(venue, 0.0)
        ai_cost = ai_cost_by_venue.get(venue, 0.0)
        # Display-only "evidence-based profit". ``net_pnl`` is ALREADY net of
        # fees AND slippage — ``fills.pnl_usd`` is derived from the actual fill
        # price, so any slippage vs. the expected price is already baked into
        # it; ``slippage_usd`` here is a separate, unsigned (model) estimate
        # from ``slippage_bps`` and would double-count if subtracted again.
        # Only ai_cost (a real extra deduction not reflected in fills.pnl_usd)
        # is subtracted. Never feeds sizing/gating.
        net_after_cost = net_pnl - ai_cost
        # Label uPnL as a stale internal mark whenever the venue's OWN native
        # session (SSOT: resolve_venue_session) is closed — Alpaca (RTH) and
        # Capital (FX/indices weekend) both have real closed windows; OKX
        # (crypto, "always_on") never does, so it always stays "" (no label).
        marks_label = ""
        marks_age_sec = 0
        if stream_id == "C_alpaca_equity" and alpaca_session != "rth":
            marks_label = "internal marks (venue closed)"
            marks_age_sec = alpaca_marks_age
        elif stream_id == "B_capital_cfd" and capital_session != "fx_open":
            marks_label = "internal marks (venue closed)"
            marks_age_sec = capital_marks_age
        out.append(
            StreamSummary(
                stream_id=stream_id,
                venue=venue,
                label=label,
                product_class=cfg.product_class,
                color=color,
                starting_capital=starting,
                equity_usd=equity,
                net_pnl_usd=net_pnl,
                upnl_usd=upnl,
                exposed_usd=exposed_by_venue.get(venue, 0.0),
                open_positions_n=open_by_venue.get(venue, 0),
                daily_trades=trades_by_venue.get(venue, 0),
                drawdown_pct=drawdown_pct,
                fee_usd=fee,
                slippage_usd=slippage,
                ai_cost_usd=ai_cost,
                net_after_cost_usd=net_after_cost,
                # P0-4 ③: closed_n = closed POSITIONS (one row per trade), the
                # TRADES counter the board displays. ``daily_trades`` stays the
                # fills.is_close count (a partial-close trade emits >1 close
                # fill, so it can exceed closed_n) — kept for the tooltip and
                # for its OWN reconciliation invariant (Σ daily_trades == the
                # global fills-based daily_n), unchanged. recent_closed is the
                # lane's most-recent closed trades (empty list when none).
                closed_n=closed_positions_by_venue.get(venue, 0),
                recent_closed=recent_closed_by_venue.get(venue, []),
                marks_label=marks_label,
                marks_age_sec=marks_age_sec,
                weekly_start_equity=(
                    weekly_by_exchange[venue].start_equity
                    if venue in weekly_by_exchange else 0.0
                ),
                weekly_realized_pnl_usd=(
                    weekly_by_exchange[venue].realized_pnl_usd
                    if venue in weekly_by_exchange else 0.0
                ),
                weekly_unrealized_pnl_usd=(
                    weekly_by_exchange[venue].unrealized_pnl_usd
                    if venue in weekly_by_exchange else 0.0
                ),
                weekly_trades=(
                    weekly_by_exchange[venue].trades if venue in weekly_by_exchange else 0
                ),
                # VIRTUAL ACCOUNT (Jin 2026-07-07) — the fresh $100k-seed
                # measurement, zero venue calls. THE profit readout.
                virtual_seed_usd=(
                    virtual_by_venue[venue].seed_anchor
                    if venue in virtual_by_venue else VIRTUAL_SEED_USD
                ),
                virtual_equity_usd=(
                    virtual_by_venue[venue].equity
                    if venue in virtual_by_venue else VIRTUAL_SEED_USD
                ),
                virtual_weekly_curve=(
                    [
                        weekly_by_exchange[venue].start_equity,
                        weekly_by_exchange[venue].current_equity,
                    ]
                    if venue in weekly_by_exchange else []
                ),
            )
        )
    return out
