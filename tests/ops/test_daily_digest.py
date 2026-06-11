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
| pnl_usd | +15.00 |
| fees_usd | 1.00 |
| opens | 1 |
| faults | 2 |
| risk_events | 1 |
| open_positions_now | 2 |
| restarts | 1 |
| wal_before_mb | 412.0 |
| wal_after_mb | 1.2 |
| ops_alerts | 2 |

## Closes by venue
| venue | closes | pnl_usd | fees_usd |
| --- | --- | --- | --- |
| capital | 1 | +5.00 | 0.50 |
| okx | 2 | +10.00 | 0.50 |

## Closes by strategy (top 12)
| strategy | closes | pnl_usd |
| --- | --- | --- |
| tsmom | 2 | +10.00 |
| fx_range_fade | 1 | +5.00 |

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
        "2026-06-09 daily-auto [closes=3 pnl_usd=+15.00 opens=1 faults=2 restarts=1]"
    )


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
