"""One-off ops leaf: the cross-instrument-orphan + capped-excursion correction.

Split out of ``correct_close_pnl_stamping`` (≤500 LOC budget). Two measurement
defects the instrument-scoped slice path (the parent's ``analyze``) is blind to:

* **Cross-instrument orphan close fill** — a close fill whose ``instrument_id``
  differs from the instrument of its ``contribution_id`` position (the live
  NL25/US100 corruption: a pre-instrument-unique-id close cross-matched a
  FOREIGN instrument's entry → an exploded ``pnl_usd`` of +4794.46 + a cache-miss
  fallback ``size_usd`` of 3.1). Recomputed from the SAME-instrument SAME-order
  open fill — side-aware ``(Δpx / entry_px) × entry_size_usd × frac`` (the exact
  runtime close formula, so the EUR→USD conversion baked into the entry's
  ``size_usd`` carries through to a USD-correct pnl), with ``size_usd`` reset to
  the entry's USD-per-unit × close qty.

* **Capped mfe_r/mae_r** — a positions row whose stored excursion saturated the
  ±100R telemetry cap (the pre-floor artefact). Recomputed from the row's own
  ``entry_atr_pct`` + ``peak``/``trough`` with the CURRENT excursion math.

DEMO/PAPER only — virtual funds. DOLLARS/telemetry only; no behaviour change.
Every function is read-only except the two ``apply_*`` writers (BEGIN IMMEDIATE
+ a ``risk_events`` audit row each), called only under the parent's ``--apply``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from polaris.core.live_recalc.excursion import (
    _EXCURSION_R_CAP,
    compute_excursion_r,
)


def _true_slice_pnl(
    *, side: str, entry_px: float, entry_size_usd: float, fill_px: float,
    frac: float,
) -> float:
    """Side-aware USD slice pnl — the runtime close formula (parent twin).

    ``entry_size_usd`` carries the quote→USD conversion, so the result is the
    FX-correct USD pnl, not the raw quote-ccy Δpx×qty.
    """
    dpx = (fill_px - entry_px) if side == "long" else (entry_px - fill_px)
    return (dpx / entry_px) * entry_size_usd * frac


@dataclass(slots=True)
class OrphanFix:
    """A cross-instrument-orphan close fill (instrument_id != position
    instrument). Pre-instrument-unique-id code cross-matched a FOREIGN
    instrument's entry → an exploded pnl_usd + a cache-miss fallback size_usd."""

    fill_id: str
    position_id: str
    instrument_id: str
    strategy_id: str
    stamped_pnl: float
    corrected_pnl: float
    stamped_size_usd: float
    corrected_size_usd: float


def analyze_cross_instrument_orphans(
    conn: sqlite3.Connection, *, tol_usd: float,
) -> list[OrphanFix]:
    """Close fills whose instrument_id differs from their contribution_id
    position's instrument. The slice path is instrument-scoped and blind to
    these. Each is recomputed from the SAME-instrument SAME-order open fill
    (side-aware Δpx; size_usd carried at the entry's USD-per-unit so the close
    notional matches the entry). A fill is reported only when its open is
    recoverable and the stamp is off by > ``tol_usd``."""
    rows = conn.execute(
        "SELECT f.fill_id, f.contribution_id, f.instrument_id, f.strategy_id, "
        " f.order_id, f.fill_price, f.base_qty, f.pnl_usd, f.size_usd "
        "FROM fills f JOIN positions p ON f.contribution_id = p.position_id "
        "WHERE f.is_close = 1 "
        "AND f.instrument_id != (p.venue || ':' || p.symbol) "
        "ORDER BY f.ts_ms ASC",
    ).fetchall()
    out: list[OrphanFix] = []
    for (fid, pid, inst, sid, oid, fpx, bq, stamped, ssz) in rows:
        # The matching open: same instrument, same order, is_close=0 (the
        # order_id pairs each Capital open/close child).
        open_row = conn.execute(
            "SELECT fill_price, size_usd, base_qty, side FROM fills "
            "WHERE order_id = ? AND instrument_id = ? AND is_close = 0 "
            "ORDER BY ts_ms ASC LIMIT 1",
            (str(oid), str(inst)),
        ).fetchone()
        if open_row is None:
            continue
        entry_px = float(open_row[0] or 0.0)
        entry_size = float(open_row[1] or 0.0)
        entry_qty = float(open_row[2] or 0.0)
        # Trade side is read from the OPEN fill (buy→long, sell→short), NOT the
        # close fill's side (always the opposite) nor the FOREIGN position's
        # side. ``_true_slice_pnl`` keys on "long"/"short", so a wrong source
        # would sign-invert a long-trade orphan.
        trade_side = "long" if str(open_row[3]).lower() == "buy" else "short"
        close_qty = float(bq or 0.0)
        if entry_px <= 0.0 or entry_qty <= 0.0 or close_qty <= 0.0:
            continue
        qty_eff = min(close_qty, entry_qty)
        frac = qty_eff / entry_qty
        true_pnl = _true_slice_pnl(
            side=trade_side, entry_px=entry_px, entry_size_usd=entry_size,
            fill_px=float(fpx or 0.0), frac=frac,
        )
        # Entry USD-per-unit × the entry-clamped close qty → a close size_usd in
        # the same USD units as the entry (the cache-miss fallback dropped
        # quote→USD), clamped like the pnl frac so an over-close never over-states.
        true_size = (entry_size / entry_qty) * qty_eff
        if (abs(float(stamped or 0.0) - true_pnl) <= tol_usd
                and abs(float(ssz or 0.0) - true_size) <= max(tol_usd, 1e-6)):
            continue
        out.append(
            OrphanFix(
                fill_id=str(fid), position_id=str(pid), instrument_id=str(inst),
                strategy_id=str(sid or ""), stamped_pnl=float(stamped or 0.0),
                corrected_pnl=true_pnl, stamped_size_usd=float(ssz or 0.0),
                corrected_size_usd=true_size,
            )
        )
    return out


@dataclass(slots=True)
class ExcursionFix:
    """A positions row whose stored mfe_r/mae_r hit the ±100 telemetry cap —
    the pre-floor excursion artefact. Recomputed from the row's own
    entry_atr_pct + peak/trough with the current excursion math."""

    position_id: str
    strategy_id: str
    stamped_mfe_r: float
    stamped_mae_r: float
    corrected_mfe_r: float
    corrected_mae_r: float


def _same_instrument_entry_px(
    conn: sqlite3.Connection, position_id: str, inst: str,
) -> float:
    """The position's own same-instrument entry fill price (0.0 if absent)."""
    row = conn.execute(
        "SELECT fill_price FROM fills WHERE contribution_id = ? "
        "AND instrument_id = ? AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1",
        (position_id, inst),
    ).fetchone()
    return float(row[0]) if row is not None and row[0] is not None else 0.0


def analyze_capped_excursions(
    conn: sqlite3.Connection,
) -> list[ExcursionFix]:
    """Positions whose stored mfe_r/mae_r saturated the ±100 cap and carry the
    inputs (entry_atr_pct, peak, trough, side + a same-instrument entry fill)
    to recompute a finite, correctly-floored excursion. Rows missing any input
    are left untouched (the cap stays as the honest 'unknowable' marker)."""
    rows = conn.execute(
        "SELECT position_id, venue, symbol, side, strategy_id, peak_price, "
        " trough_price, entry_atr_pct, mfe_r, mae_r FROM positions "
        "WHERE mfe_r >= ? OR mae_r <= ?",
        (_EXCURSION_R_CAP, -_EXCURSION_R_CAP),
    ).fetchall()
    out: list[ExcursionFix] = []
    for (pid, venue, sym, side, sid, peak, trough, atr_pct, mfe, mae) in rows:
        entry_px = _same_instrument_entry_px(conn, str(pid), f"{venue}:{sym}")
        if (entry_px <= 0.0 or peak is None or trough is None
                or atr_pct is None or float(atr_pct) <= 0.0):
            continue
        atr_usd = entry_px * float(atr_pct) * 2.0
        new_mfe, new_mae = compute_excursion_r(
            entry_price=entry_px, peak_price=float(peak),
            trough_price=float(trough), side=str(side or "long"),
            atr_usd=atr_usd,
        )
        if (abs(new_mfe - float(mfe or 0.0)) <= 1e-9
                and abs(new_mae - float(mae or 0.0)) <= 1e-9):
            continue
        out.append(
            ExcursionFix(
                position_id=str(pid), strategy_id=str(sid or ""),
                stamped_mfe_r=float(mfe or 0.0), stamped_mae_r=float(mae or 0.0),
                corrected_mfe_r=new_mfe, corrected_mae_r=new_mae,
            )
        )
    return out


def apply_orphan_corrections(
    conn: sqlite3.Connection,
    orphans: list[OrphanFix],
    *,
    backup_path: str,
    now_ts: int,
) -> int:
    """Re-stamp each orphan close fill's pnl_usd + size_usd + quote_qty to the
    true same-order same-instrument slice. One BEGIN IMMEDIATE + audit row per
    fill. Returns the count corrected."""
    n = 0
    for o in orphans:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE fills SET pnl_usd = ?, size_usd = ?, quote_qty = ? "
                "WHERE fill_id = ?",
                (o.corrected_pnl, o.corrected_size_usd, o.corrected_size_usd,
                 o.fill_id),
            )
            payload = json.dumps(
                {
                    "fill_id": o.fill_id,
                    "position_id": o.position_id,
                    "instrument_id": o.instrument_id,
                    "old_pnl": o.stamped_pnl, "new_pnl": o.corrected_pnl,
                    "old_size_usd": o.stamped_size_usd,
                    "new_size_usd": o.corrected_size_usd,
                    "class": "cross_instrument_orphan",
                    "backup_path": backup_path,
                },
                separators=(",", ":"),
            )
            conn.execute(
                "INSERT INTO risk_events (risk_event_id, strategy_id, "
                "event_type, created_ts, payload_json) "
                "VALUES (?, ?, 'pnl_orphan_correction', ?, ?)",
                (uuid.uuid4().hex, o.strategy_id, now_ts, payload),
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        n += 1
    return n


def apply_excursion_corrections(
    conn: sqlite3.Connection,
    fixes: list[ExcursionFix],
    *,
    backup_path: str,
    now_ts: int,
) -> int:
    """Re-stamp each capped positions row's mfe_r/mae_r to the recomputed finite
    excursion. One BEGIN IMMEDIATE + audit row per position. Returns the count
    corrected."""
    n = 0
    for f in fixes:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE positions SET mfe_r = ?, mae_r = ? WHERE position_id = ?",
                (f.corrected_mfe_r, f.corrected_mae_r, f.position_id),
            )
            payload = json.dumps(
                {
                    "position_id": f.position_id,
                    "old_mfe_r": f.stamped_mfe_r, "new_mfe_r": f.corrected_mfe_r,
                    "old_mae_r": f.stamped_mae_r, "new_mae_r": f.corrected_mae_r,
                    "class": "excursion_cap_artefact",
                    "backup_path": backup_path,
                },
                separators=(",", ":"),
            )
            conn.execute(
                "INSERT INTO risk_events (risk_event_id, strategy_id, "
                "event_type, created_ts, payload_json) "
                "VALUES (?, ?, 'mfe_r_cap_recompute', ?, ?)",
                (uuid.uuid4().hex, f.strategy_id, now_ts, payload),
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        n += 1
    return n


def print_orphan_excursion(
    orphans: list[OrphanFix], excursions: list[ExcursionFix],
) -> None:
    """Human-readable dry-run/apply summary of both correction classes."""
    print(f"\ncross-instrument orphan close fills "
          f"(instrument_id != position instrument): {len(orphans)}")
    for o in orphans:
        print(f"  {o.fill_id} {o.instrument_id} pos={o.position_id} "
              f"pnl {o.stamped_pnl:.2f}→{o.corrected_pnl:.2f} "
              f"size_usd {o.stamped_size_usd:.2f}→{o.corrected_size_usd:.2f}")
    print(f"capped mfe_r/mae_r excursion rows (±{int(_EXCURSION_R_CAP)} "
          f"artefact): {len(excursions)}")
    for e in excursions:
        print(f"  {e.position_id} mfe_r {e.stamped_mfe_r:.2f}→"
              f"{e.corrected_mfe_r:.2f} mae_r {e.stamped_mae_r:.2f}→"
              f"{e.corrected_mae_r:.2f}")


__all__ = [
    "ExcursionFix",
    "OrphanFix",
    "analyze_capped_excursions",
    "analyze_cross_instrument_orphans",
    "apply_excursion_corrections",
    "apply_orphan_corrections",
    "print_orphan_excursion",
]
