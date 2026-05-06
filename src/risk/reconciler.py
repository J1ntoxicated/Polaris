"""Reconciler — Polaris portfolio vs broker (OKX demo / live) drift audit.

User mandate: "리콘사일 안해? 데모 계정이랑?" — Polaris must reconcile with
the actual exchange account, not just trust internal ledger.

Per cycle (default 5 min):
1. broker.get_balance() → exchange cash (USDT)
2. broker.get_positions() → exchange base ccy balances
3. Compare to PortfolioManager.cash + AggregatedPosition.total_base_qty
4. Report drift in 4 categories:
   - cash_drift_usd: Polaris.cash - exchange USDT
   - position_count_mismatch: tickers in Polaris not on exchange (or vice versa)
   - qty_drift: per-ticker base_qty difference
   - sync_status: OK / DRIFT / NO_BROKER

Pure-ish: pure compare logic, broker call is shell I/O.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriftEntry:
    """Single ticker / cash drift."""
    ccy: str                 # USDT for cash, BTC/ETH/etc for positions
    polaris_qty: float
    broker_qty: float
    drift_qty: float         # broker - polaris
    drift_pct: float         # drift / polaris (or 100% if polaris=0)


@dataclass(frozen=True)
class ReconcileResult:
    """One reconciliation cycle outcome."""
    ts_ms: int
    sync_status: str          # OK | DRIFT | NO_BROKER | ERROR
    cash_drift_usd: float
    position_count_polaris: int
    position_count_broker: int
    drift_entries: tuple[DriftEntry, ...]
    summary: str


def _ticker_to_base_ccy(ticker: str) -> str:
    """BTC-USDT → BTC. ETH-USDT → ETH."""
    if "-" in ticker:
        return ticker.split("-", 1)[0]
    return ticker[:3]  # fallback


def reconcile(
    portfolio,
    broker,
    drift_threshold_pct: float = 0.05,   # 5% drift = WARN
    ts_ms: Optional[int] = None,
) -> ReconcileResult:
    """Compare Polaris portfolio to broker exchange state.

    Returns ReconcileResult with drift entries. Does NOT mutate state.
    Caller logs / alerts on DRIFT.
    """
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)

    if not broker.is_live:
        # Paper broker — no exchange to reconcile against
        return ReconcileResult(
            ts_ms=ts_ms, sync_status="NO_BROKER",
            cash_drift_usd=0.0,
            position_count_polaris=portfolio.n_open_contributions,
            position_count_broker=0,
            drift_entries=(),
            summary="paper_broker_no_reconcile",
        )

    try:
        broker_cash = broker.get_balance()
        broker_positions = broker.get_positions()
    except Exception as e:
        logger.warning(f"[RECONCILE] broker query failed: {e!r}")
        return ReconcileResult(
            ts_ms=ts_ms, sync_status="ERROR",
            cash_drift_usd=0.0,
            position_count_polaris=portfolio.n_open_contributions,
            position_count_broker=0,
            drift_entries=(),
            summary=f"broker_error: {e!r}",
        )

    # Cash reconcile (USDT primary)
    broker_usdt = broker_cash.get("USDT", 0.0)
    polaris_cash = portfolio.cash
    cash_drift = broker_usdt - polaris_cash

    # Position reconcile per ccy
    drift_entries: list[DriftEntry] = []

    # Polaris-side aggregated qty per ccy
    polaris_qty: dict[str, float] = {}
    for ticker, pos in portfolio.positions.items():
        ccy = _ticker_to_base_ccy(ticker)
        polaris_qty[ccy] = polaris_qty.get(ccy, 0) + pos.total_base_qty

    # Broker-side qty per ccy
    broker_qty = {p["ccy"]: p["bal"] for p in broker_positions if p["ccy"] != "USDT"}

    # All ccys mentioned in either side
    all_ccys = set(polaris_qty.keys()) | set(broker_qty.keys())
    has_drift = abs(cash_drift) > drift_threshold_pct * polaris_cash if polaris_cash > 0 else abs(cash_drift) > 1.0
    for ccy in sorted(all_ccys):
        p_qty = polaris_qty.get(ccy, 0.0)
        b_qty = broker_qty.get(ccy, 0.0)
        diff = b_qty - p_qty
        denom = p_qty if p_qty > 0 else (b_qty if b_qty > 0 else 1.0)
        diff_pct = diff / denom if denom > 0 else 0.0
        if abs(diff_pct) > drift_threshold_pct or (p_qty == 0) != (b_qty == 0):
            has_drift = True
        drift_entries.append(DriftEntry(
            ccy=ccy, polaris_qty=p_qty, broker_qty=b_qty,
            drift_qty=diff, drift_pct=diff_pct,
        ))

    # Cash entry always added
    drift_entries.insert(0, DriftEntry(
        ccy="USDT", polaris_qty=polaris_cash, broker_qty=broker_usdt,
        drift_qty=cash_drift,
        drift_pct=(cash_drift / polaris_cash) if polaris_cash > 0 else 0.0,
    ))

    sync_status = "OK" if not has_drift else "DRIFT"

    # Summary string
    n_drift = sum(1 for e in drift_entries if abs(e.drift_pct) > drift_threshold_pct)
    summary = (
        f"{sync_status} cash={polaris_cash:.2f}vs{broker_usdt:.2f} "
        f"(drift=${cash_drift:+.2f}) "
        f"polaris_pos={len(polaris_qty)} broker_pos={len(broker_qty)} "
        f"n_drift_ccys={n_drift}"
    )

    return ReconcileResult(
        ts_ms=ts_ms, sync_status=sync_status,
        cash_drift_usd=cash_drift,
        position_count_polaris=len(polaris_qty),
        position_count_broker=len(broker_qty),
        drift_entries=tuple(drift_entries),
        summary=summary,
    )
