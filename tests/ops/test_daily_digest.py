"""daily_digest: UTC window, golden output, ro URI, keyword sweep, idempotence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from polaris.storage.schema import ALL_DDL
from tools.ops import daily_digest
from tools.ops.ops_config import REJECTION_KEYWORDS, OpsConfig

# generation moment: 2026-06-10 00:10 UTC → digest window = 2026-06-09 UTC day
NOW = datetime(2026, 6, 10, 0, 10, tzinfo=UTC).timestamp()
DAY_START_MS = int(datetime(2026, 6, 9, tzinfo=UTC).timestamp() * 1000)
DAY_END_MS = DAY_START_MS + 86_400_000


def _seed_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


def _fill(
    conn: sqlite3.Connection,
    fill_id: str,
    *,
    venue: str = "okx",
    strategy_id: str = "tsmom",
    pnl: float = 0.0,
    fee: float = 0.0,
    ts_ms: int = DAY_START_MS + 1000,
    is_close: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side,"
        " size_usd, fill_price, fee_usd, ts_ms, order_id, pnl_usd, is_close)"
        " VALUES (?, ?, 'BTC-USDT', ?, 'buy', 100.0, 1.0, ?, ?, 'o1', ?, ?)",
        (fill_id, venue, strategy_id, fee, ts_ms, pnl, is_close),
    )


def _entry_fill(
    conn: sqlite3.Connection,
    fill_id: str,
    *,
    contribution_id: str,
    venue: str = "okx",
    strategy_id: str = "tsmom",
    fee: float = 0.0,
    ts_ms: int = DAY_START_MS + 500,
) -> None:
    """The OPEN leg (is_close=0) of a position — carries its own real fee but
    zero pnl (pnl only realizes on the close leg). Omitted from every existing
    fixture in this file, which is exactly why the close-leg-only fee bug went
    undetected."""
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side,"
        " size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id,"
        " pnl_usd, is_close)"
        " VALUES (?, ?, 'BTC-USDT', ?, 'buy', 100.0, 1.0, ?, ?, 'o1', ?, 0.0, 0)",
        (fill_id, venue, strategy_id, fee, ts_ms, contribution_id),
    )


def _fault(conn: sqlite3.Connection, event_id: str, fault_type: str, ts_s: int) -> None:
    conn.execute(
        "INSERT INTO strategy_fault_events (event_id, strategy_id, fault_type, event_ts)"
        " VALUES (?, 'tsmom', ?, ?)",
        (event_id, fault_type, ts_s),
    )


def _golden_db(cfg: OpsConfig) -> None:
    conn = _seed_db(cfg.db_path)
    _fill(conn, "f1", venue="okx", strategy_id="tsmom", pnl=12.5, fee=0.3)
    _fill(conn, "f2", venue="okx", strategy_id="tsmom", pnl=-2.5, fee=0.2)
    _fill(conn, "f3", venue="capital", strategy_id="fx_range_fade", pnl=5.0, fee=0.5)
    _fill(conn, "f4", strategy_id="volume_burst", is_close=0)
    # Entry (open) legs of f1/f2/f3 — each position pays a real fee on ENTRY too,
    # not only on close. audit2 P0-2 ①: the digest must count both legs.
    _entry_fill(conn, "f1_open", contribution_id="f1", venue="okx",
                strategy_id="tsmom", fee=0.3)
    _entry_fill(conn, "f2_open", contribution_id="f2", venue="okx",
                strategy_id="tsmom", fee=0.2)
    _entry_fill(conn, "f3_open", contribution_id="f3", venue="capital",
                strategy_id="fx_range_fade", fee=0.5)
    _fault(conn, "e1", "reject", DAY_START_MS // 1000 + 60)
    _fault(conn, "e2", "reject", DAY_START_MS // 1000 + 120)
    conn.execute(
        "INSERT INTO risk_events (risk_event_id, strategy_id, event_type, created_ts)"
        " VALUES ('r1', 'tsmom', 'cap_hit', ?)",
        (DAY_START_MS // 1000 + 90,),
    )
    for i, status in enumerate(["open", "open", "closed"]):
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id,"
            " entry_strategy_id, active_strategy_id, side, qty, status)"
            " VALUES (?, 'okx', 'BTC-USDT', 'tsmom', 'tsmom', 'tsmom', 'long', 1.0, ?)",
            (f"p{i}", status),
        )
    conn.commit()
    conn.close()
    cfg.ops_dir.mkdir(parents=True, exist_ok=True)
    cfg.restart_log.write_text(
        "2026-06-09T21:30:05Z wal_before=412.0MB wal_after=1.2MB pid=777\n",
        encoding="utf-8",
    )
    cfg.alerts_log.write_text(
        "2026-06-09T03:00:00Z [stall_burst] 3 stalls\n"
        "2026-06-09T04:00:00Z [ws_flapping] 5 errors\n"
        "2026-06-08T04:00:00Z [ws_flapping] previous day excluded\n",
        encoding="utf-8",
    )


GOLDEN = """---
type: daily-auto
status: active
date_created: 2026-06-10
generated_by: tools/ops/daily_digest.py
db: polaris_live.sqlite
window_utc: 2026-06-09 00:00..24:00
tags: [digest, daily-auto, ops]
---

# Daily auto digest — 2026-06-09 (UTC)

| metric | value |
| --- | --- |
| closes | 3 |
| pnl_usd | +13.00 |
| pnl_usd_gross | +15.00 |
| fees_usd | 2.00 |
| opens | 4 |
| faults | 2 |
| risk_events | 1 |
| open_positions_now | 2 |
| restarts | 1 |
| wal_before_mb | 412.0 |
| wal_after_mb | 1.2 |
| ops_alerts | 2 |

## Closes by venue
| venue | closes | pnl_usd_net | fees_usd |
| --- | --- | --- | --- |
| capital | 1 | +4.00 | 1.00 |
| okx | 2 | +9.00 | 1.00 |

## Closes by strategy (top 12)
| strategy | closes | pnl_usd_net |
| --- | --- | --- |
| tsmom | 2 | +9.00 |
| fx_range_fade | 1 | +4.00 |

## Faults by type
| fault_type | count |
| --- | --- |
| reject | 2 |
"""


def test_golden_output(cfg: OpsConfig) -> None:
    _golden_db(cfg)
    assert daily_digest.run(cfg, now=NOW) == 0
    out = (cfg.digest_dir / "2026-06-09.md").read_text(encoding="utf-8")
    assert out == GOLDEN
    log_line = (cfg.vault_dir / "log.md").read_text(encoding="utf-8").strip()
    assert log_line == (
        "2026-06-09 daily-auto [closes=3 pnl_usd=+13.00 opens=4 faults=2 restarts=1]"
    )


def test_weekly_virtual_pnl_section_renders_when_present(cfg: OpsConfig) -> None:
    """Weekly per-exchange VIRTUAL trace (Jin 2026-07-07) — TRACE, never RESET;
    a stamped ``weekly_equity_curve`` row for the CURRENT week (at generation
    time, ``NOW``) surfaces as a digest section. Absent when no row exists
    (the golden-output test above stays byte-identical)."""
    _golden_db(cfg)
    conn = sqlite3.connect(cfg.db_path)
    from polaris.storage.weekly_equity_trace import upsert_weekly_row

    upsert_weekly_row(
        conn, exchange="okx", now_ts=int(NOW), account_equity=100_250.0,
        realized_pnl_delta_usd=250.0, unrealized_pnl_usd=15.0, trade_delta=2,
    )
    conn.commit()
    conn.close()

    assert daily_digest.run(cfg, now=NOW) == 0
    out = (cfg.digest_dir / "2026-06-09.md").read_text(encoding="utf-8")
    assert "## Weekly virtual PnL (this week so far, per exchange)" in out
    assert "| okx | +250.00 | +15.00 | 2 |" in out


def test_net_headline_survives_entry_leg_fee_2026_06_30_case(cfg: OpsConfig) -> None:
    """audit2 P0-2 ①: 2026-06-30-style case — small +gross close pnl masked a real
    round-trip-fee loss because the entry leg's fee was never summed. Repro:
    close-leg gross +6.05, close-leg fee 57.47, entry-leg fee 57.48 (round-trip
    fee 114.95 total) → true net ≈ -108.90, not the old +6.05 "profit" look."""
    conn = _seed_db(cfg.db_path)
    _fill(conn, "c1", pnl=6.05, fee=57.47)
    _entry_fill(conn, "c1_open", contribution_id="c1", fee=57.48)
    conn.commit()
    conn.close()
    assert daily_digest.run(cfg, now=NOW) == 0
    out = (cfg.digest_dir / "2026-06-09.md").read_text(encoding="utf-8")
    assert "| pnl_usd | -108.90 |" in out
    assert "| pnl_usd_gross | +6.05 |" in out
    assert "| fees_usd | 114.95 |" in out


def test_window_boundaries_exact(cfg: OpsConfig) -> None:
    conn = _seed_db(cfg.db_path)
    _fill(conn, "in_lo", pnl=1.0, ts_ms=DAY_START_MS)          # included
    _fill(conn, "in_hi", pnl=1.0, ts_ms=DAY_END_MS - 1)        # included
    _fill(conn, "out_lo", pnl=1.0, ts_ms=DAY_START_MS - 1)     # excluded
    _fill(conn, "out_hi", pnl=1.0, ts_ms=DAY_END_MS)           # excluded
    conn.commit()
    conn.close()
    assert daily_digest.run(cfg, now=NOW) == 0
    out = (cfg.digest_dir / "2026-06-09.md").read_text(encoding="utf-8")
    assert "| closes | 2 |" in out


def test_output_capped_at_60_lines(cfg: OpsConfig) -> None:
    conn = _seed_db(cfg.db_path)
    for i in range(20):
        _fill(conn, f"s{i}", venue=f"venue{i % 6}", strategy_id=f"strat_{i:02d}", pnl=float(i))
    for i in range(9):
        _fault(conn, f"ft{i}", f"fault_kind_{i}", DAY_START_MS // 1000 + i)
    conn.commit()
    conn.close()
    assert daily_digest.run(cfg, now=NOW) == 0
    text = (cfg.digest_dir / "2026-06-09.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) <= 60
    assert sum(1 for line in lines if line.startswith("| strat_")) <= 12
    assert sum(1 for line in lines if line.startswith("| venue") and "closes" not in line) <= 4
    assert sum(1 for line in lines if line.startswith("| fault_kind_")) <= 5


def test_rejection_keywords_redacted_at_runtime(
    cfg: OpsConfig, alerts: list[tuple[str, str, float]]
) -> None:
    """DB-borne free strings (strategy_id etc.) must be swept, not just templates."""
    conn = _seed_db(cfg.db_path)
    for i, kw in enumerate(REJECTION_KEYWORDS[:3]):
        _fill(conn, f"k{i}", strategy_id=f"{kw} breakout", pnl=1.0)
    conn.commit()
    conn.close()
    assert daily_digest.run(cfg, now=NOW) == 0
    text = (cfg.digest_dir / "2026-06-09.md").read_text(encoding="utf-8")
    for kw in REJECTION_KEYWORDS:
        assert kw.lower() not in text.lower()
    assert "[redacted]" in text
    assert any(k == "digest_keyword_redacted" for k, _, _ in alerts)


def test_no_space_sample_variant_is_swept() -> None:
    """CLAUDE.md SSOT carries the no-space '표본부족' variant; the digest sweep
    must list AND redact it (audit found it omitted)."""
    assert "표본부족" in REJECTION_KEYWORDS
    text, hits = daily_digest.sweep_keywords("전략 사유: 표본부족 으로 진입")
    assert "표본부족" in hits
    assert "표본부족" not in text
    assert "[redacted]" in text


def test_template_itself_has_zero_keywords(cfg: OpsConfig) -> None:
    _golden_db(cfg)
    daily_digest.run(cfg, now=NOW)
    text = (cfg.digest_dir / "2026-06-09.md").read_text(encoding="utf-8")
    for kw in REJECTION_KEYWORDS:
        assert kw.lower() not in text.lower()


def test_opens_ro_uri_and_closes(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _golden_db(cfg)
    seen: dict[str, Any] = {}
    real_connect = sqlite3.connect

    class _Proxy:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def close(self) -> None:
            seen["closed"] = True
            self._conn.close()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._conn, name)

    def spy(dsn: str, *args: Any, **kwargs: Any) -> Any:
        seen["dsn"] = dsn
        seen["uri"] = kwargs.get("uri", False)
        return _Proxy(real_connect(dsn, *args, **kwargs))

    monkeypatch.setattr(daily_digest.sqlite3, "connect", spy)
    assert daily_digest.run(cfg, now=NOW) == 0
    assert seen["uri"] is True
    assert "mode=ro" in seen["dsn"]
    assert seen.get("closed") is True


def test_rerun_is_idempotent(cfg: OpsConfig) -> None:
    _golden_db(cfg)
    assert daily_digest.run(cfg, now=NOW) == 0
    assert daily_digest.run(cfg, now=NOW) == 0
    out_files = list(cfg.digest_dir.glob("*.md"))
    assert len(out_files) == 1
    log_lines = (cfg.vault_dir / "log.md").read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in log_lines if "daily-auto" in line) == 1


def test_db_unreadable_writes_stub_and_exits_zero(
    cfg: OpsConfig, alerts: list[tuple[str, str, float]]
) -> None:
    assert not cfg.db_path.exists()
    assert daily_digest.run(cfg, now=NOW) == 0
    stub = (cfg.digest_dir / "2026-06-09.md").read_text(encoding="utf-8")
    assert "error: db_unreadable" in stub
    assert any(k == "digest_failed" for k, _, _ in alerts)
    # no vault log line for an empty stub
    assert not (cfg.vault_dir / "log.md").exists()
