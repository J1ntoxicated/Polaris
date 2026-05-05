"""JSON paper_state → SQLite ledger backfill (Phase 9).

One-shot: scan data/paper/paper_state_*.json + INSERT positions + balances
into SQLite. Idempotent (INSERT OR REPLACE on position_id).

Usage:
    python -m src.persist.migrations data/paper data/polaris.sqlite

Reads filename pattern `paper_state_<ticker>_<strategy>.json` to extract
(ticker, strategy_name). Maps strategy_name → hypo_id via REALTIME_HYPOS.
"""
from __future__ import annotations

import glob
import json
import logging
import sys
from pathlib import Path

from src.paper.state import PaperBalance, Position, PositionStatus
from src.persist.ledger import TradeLedger

logger = logging.getLogger(__name__)


def _parse_filename(filepath: str) -> tuple[str, str] | None:
    """Extract (ticker, strategy_name) from `paper_state_<ticker>_<strategy>.json`.

    e.g. "paper_state_btc-usdt_volume_burst.json" → ("BTC-USDT", "volume_burst")
    """
    name = Path(filepath).stem
    if not name.startswith("paper_state_"):
        return None
    rest = name[len("paper_state_"):]
    # First "_" between ticker (e.g. "btc-usdt") and strategy_name
    parts = rest.split("_", 1)
    if len(parts) != 2:
        return None
    ticker_lower, strategy = parts
    return ticker_lower.upper(), strategy


def _hypo_id_for_strategy(strategy_name: str) -> str:
    """Map strategy_name → canonical hypo_id used in current REALTIME_HYPOS.

    Lazy import to avoid circular dependency with realtime_runner.
    """
    try:
        from src.paper.realtime_runner import REALTIME_HYPOS
    except Exception:
        REALTIME_HYPOS = []
    for h in REALTIME_HYPOS:
        cls = h.get("strategy_cls")
        if cls is None:
            continue
        # Strategy.name attribute
        try:
            inst = cls()
            if getattr(inst, "name", None) == strategy_name:
                return h["hypo_id"]
        except Exception:
            continue
    # Fallback: legacy / deprecated → use strategy_name as hypo_id
    return f"LEGACY-{strategy_name}"


def _restore_position(d: dict) -> Position | None:
    """Reconstruct Position from JSON dict — defensive (skip malformed)."""
    try:
        return Position(
            position_id=d["position_id"],
            ticker=d["ticker"],
            direction=int(d.get("direction", 1)),
            entry_price=float(d["entry_price"]),
            size_usd=float(d["size_usd"]),
            open_ts_ms=int(d["open_ts_ms"]),
            close_ts_ms=int(d.get("close_ts_ms", 0) or 0),
            exit_price=float(d.get("exit_price", 0) or 0),
            fee_round_trip=float(d.get("fee_round_trip", 0.002)),
            status=PositionStatus(d.get("status", "open")),
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"skip malformed position: {e} {d.get('position_id')}")
        return None


def migrate_paper_state(state_dir: str | Path, db_path: str | Path) -> dict:
    """Scan state_dir for paper_state_*.json and load into SQLite ledger.

    Returns stats dict {files, balances, opens, closes, errors}.
    """
    state_dir = Path(state_dir)
    files = sorted(glob.glob(str(state_dir / "paper_state_*.json")))
    stats = {"files": 0, "balances": 0, "opens": 0, "closes": 0, "errors": 0}

    with TradeLedger(db_path) as ledger:
        for fpath in files:
            parsed = _parse_filename(fpath)
            if parsed is None:
                continue
            ticker, strategy = parsed
            hypo_id = _hypo_id_for_strategy(strategy)
            try:
                data = json.loads(Path(fpath).read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"skip {fpath}: {e}")
                stats["errors"] += 1
                continue
            stats["files"] += 1

            # Balance
            try:
                bal = PaperBalance(
                    starting_usd=float(data.get("starting_usd", 5000.0)),
                    cash_usd=float(data.get("cash_usd", 5000.0)),
                    open_positions=tuple(
                        p for p in (_restore_position(d) for d in data.get("open_positions", []))
                        if p is not None
                    ),
                    closed_positions=tuple(
                        p for p in (_restore_position(d) for d in data.get("closed_positions", []))
                        if p is not None
                    ),
                )
                ledger.upsert_balance(hypo_id, ticker, bal)
                stats["balances"] += 1
            except (ValueError, TypeError) as e:
                logger.warning(f"skip balance {fpath}: {e}")
                stats["errors"] += 1
                continue

            # Open positions → INSERT
            for pos in bal.open_positions:
                ledger.insert_position_open(pos, hypo_id, strategy)
                stats["opens"] += 1

            # Closed positions → INSERT + UPDATE
            for pos in bal.closed_positions:
                ledger.insert_position_open(pos, hypo_id, strategy)
                if pos.exit_price > 0 and pos.close_ts_ms > 0:
                    ledger.update_position_close(
                        position_id=pos.position_id,
                        exit_price=pos.exit_price,
                        close_ts_ms=pos.close_ts_ms,
                        exit_reason="legacy_migration",
                    )
                    stats["closes"] += 1

    return stats


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: python -m src.persist.migrations <state_dir> <db_path>")
        return 1
    state_dir, db_path = argv[1], argv[2]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    stats = migrate_paper_state(state_dir, db_path)
    print(f"Migration complete: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
