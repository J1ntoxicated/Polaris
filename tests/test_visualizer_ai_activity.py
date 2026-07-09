"""AI-tab 3-section rebuild backend tests (DEMO/PAPER, display-only, RO).

Covers the ``/api/ai_activity`` builders behind the new bot_ai / harness_ai /
cowork_intel sections (Jin 2026-07-10 — the previous single "AI" tab mixed the
bot's runtime OpenAI GPT usage, the Claude dev-harness usage, and the cowork
watchlist-intel feed into one undifferentiated tab). Every builder is pure
read-only over a synthetic SQLite DB / synthetic files; nothing here touches
sizing/risk/orders. Missing file/table/log is exercised explicitly — each
section must fail SAFE (zero/empty), never raise.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

import tools.visualizer.server as srv
from polaris.storage.schema import ALL_DDL


@pytest.fixture
def live_db(tmp_path: Path) -> Path:
    path = tmp_path / "live.sqlite"
    conn = sqlite3.connect(path)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    return path


def _rw(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None)


# ── gate_events model_used ──────────────────────────────────────────────────


def test_gate_events_model_used_missing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv, "_DB_PATH", tmp_path / "absent.sqlite")
    out = srv._gate_events_model_used(86400)
    assert out == {"total": 0, "model_used": {}}


def test_gate_events_model_used_counts_and_window(
    live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_DB_PATH", live_db)
    conn = _rw(live_db)
    now = int(time.time())
    rows = [
        ("e1", "run", None, None, 3, "success", "PASS", "python", 0, 0, 0, "{}", None, now - 10),
        ("e2", "run", None, None, 4, "success", "PASS", "python", 0, 0, 0, "{}", None, now - 20),
        ("e3", "run", None, None, 7, "success", "HOLD", "gpt", 400, 50, 20, "{}", None, now - 30),
        # outside the 24h window — must be excluded.
        ("e4", "run", None, None, 3, "success", "PASS", "python", 0, 0, 0, "{}", None, now - 90000),
    ]
    conn.executemany(
        "INSERT INTO gate_events (event_id, run_id, signal_id, position_id, "
        "gate_id, phase, decision, model_used, latency_ms, input_tokens, "
        "output_tokens, payload_json, error_text, created_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    out = srv._gate_events_model_used(86400)
    assert out == {"total": 3, "model_used": {"python": 2, "gpt": 1}}


# ── entry_admission_shadow ──────────────────────────────────────────────────


def test_entry_admission_shadow_summary_missing_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_DB_PATH", tmp_path / "absent.sqlite")
    out = srv._entry_admission_shadow_summary(86400)
    assert out == {"total": 0, "admit": 0, "would_suppress": 0, "recent": []}


def test_entry_admission_shadow_summary_counts_and_recent(
    live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_DB_PATH", live_db)
    conn = _rw(live_db)
    now = int(time.time())
    rows = [
        ("a1", "run", None, "okx", "BTC-USDT", "s1", "trend", 10.0, 0.1, 0.5,
         0.05, 1, 0, "ok", "real", now - 5),
        ("a2", "run", None, "okx", "ETH-USDT", "s1", "chop", 8.0, -0.1, 0.2,
         0.05, 0, 1, "cost_exceeds_edge", "real", now - 10),
        ("a3", "run", None, "capital", "US500", "s2", "trend", 12.0, 0.2, 0.6,
         0.05, 1, 0, "ok", "real", now - 15),
    ]
    conn.executemany(
        "INSERT INTO entry_admission_shadow (event_id, run_id, signal_id, "
        "venue, symbol, strategy_id, regime, cell_n_eff, cell_avg_pnl_r, "
        "expected_move_r, real_round_trip_cost_r, admit, would_suppress, "
        "reason, basis, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    out = srv._entry_admission_shadow_summary(86400, recent_n=2)
    assert out["total"] == 3
    assert out["admit"] == 2
    assert out["would_suppress"] == 1
    assert len(out["recent"]) == 2
    # newest first
    assert out["recent"][0]["symbol"] == "BTC-USDT"
    assert out["recent"][0]["admit"] is True
    assert out["recent"][1]["symbol"] == "ETH-USDT"
    assert out["recent"][1]["would_suppress"] is True


# ── #32 judge log parsing ───────────────────────────────────────────────────


def _log_line(ts: str, body: str) -> str:
    return f"{ts} [INFO] polaris.core.pipeline.agents.ai_judge:554 [ai-judge] {body}"


def test_judge_call_stats_missing_log(tmp_path: Path) -> None:
    out = srv._judge_call_stats(86400, log_path=tmp_path / "absent.log")
    assert out["calls"] == 0
    assert out["verdicts"] == {}
    assert out["escalations"] == {}
    assert out["salvage_count"] == 0
    assert out["avg_latency_ms"] is None
    assert out["log_available"] is False


def test_judge_call_stats_parses_verdict_escalation_latency(tmp_path: Path) -> None:
    now = time.gmtime(time.time())
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", now)
    log = tmp_path / "polaris_runtime.log"
    log.write_text(
        "\n".join([
            _log_line(ts, "role text verdict=EXTEND (det=HOLD) escalation=gpt_ok latency=2600ms"),
            _log_line(ts, "role text verdict=PROCEED (det=PASS) escalation=gpt_ok_salvaged latency=1100ms (truncated)"),
            _log_line(ts, "role text → fallback (det=PASS) escalation=gpt_timeout wall_clock_ms=9001"),
            _log_line(ts, "role text → fallback (det=PASS) escalation=gpt_error err=boom"),
        ]) + "\n",
        encoding="utf-8",
    )
    out = srv._judge_call_stats(86400, log_path=log)
    assert out["calls"] == 4
    assert out["verdicts"] == {"EXTEND": 1, "PROCEED": 1}
    assert out["escalations"] == {
        "gpt_ok": 1, "gpt_ok_salvaged": 1, "gpt_timeout": 1, "gpt_error": 1,
    }
    assert out["salvage_count"] == 1
    # latencies: 2600, 1100, 9001 (wall_clock counts too) → avg = 4233.67
    assert out["latency_sample_n"] == 3
    assert out["avg_latency_ms"] == pytest.approx((2600 + 1100 + 9001) / 3, abs=0.5)
    assert out["log_available"] is True


def test_judge_call_stats_excludes_lines_outside_window(tmp_path: Path) -> None:
    old_ts = time.strftime(
        "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 200000)
    )
    fresh_ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time()))
    log = tmp_path / "polaris_runtime.log"
    log.write_text(
        "\n".join([
            _log_line(old_ts, "verdict=EXTEND (det=HOLD) escalation=gpt_ok latency=1000ms"),
            _log_line(fresh_ts, "verdict=PROTECT (det=HOLD) escalation=gpt_ok latency=2000ms"),
        ]) + "\n",
        encoding="utf-8",
    )
    out = srv._judge_call_stats(86400, log_path=log)
    assert out["calls"] == 1
    assert out["verdicts"] == {"PROTECT": 1}


def test_judge_call_stats_reads_rotated_sibling_when_current_too_short(
    tmp_path: Path,
) -> None:
    """A fresh-since-restart current log alone under-covers a 24h window — the
    newest rotated sibling (``<name>.<suffix>``) must be pulled in too."""
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time()))
    rotated_ts = time.strftime(
        "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3600)
    )
    current = tmp_path / "polaris_runtime.log"
    current.write_text(
        _log_line(now_ts, "verdict=EXTEND (det=HOLD) escalation=gpt_ok latency=1500ms") + "\n",
        encoding="utf-8",
    )
    rotated = tmp_path / "polaris_runtime.log.20260101"
    rotated.write_text(
        _log_line(rotated_ts, "verdict=PROTECT (det=HOLD) escalation=gpt_ok latency=2500ms") + "\n",
        encoding="utf-8",
    )
    out = srv._judge_call_stats(86400, log_path=current)
    assert out["calls"] == 2
    assert out["verdicts"] == {"EXTEND": 1, "PROTECT": 1}


# ── Claude harness usage ledger ─────────────────────────────────────────────


def test_harness_model_usage_top_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_MODEL_LEDGER_PATH", tmp_path / "absent.json")
    assert srv._harness_model_usage_top() == []


def test_harness_model_usage_top_corrupt_json_fail_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "ledger.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(srv, "_MODEL_LEDGER_PATH", p)
    assert srv._harness_model_usage_top() == []


def test_harness_model_usage_top_aggregates_and_ranks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = {
        "sess-a": {
            "claude-opus-4-8": {
                "2026-07-01": {"turns": 10, "out_tok": 100, "side_turns": 5},
                "2026-07-02": {"turns": 20, "out_tok": 200, "side_turns": 0},
            },
        },
        "sess-b": {
            "claude-opus-4-8": {
                "2026-07-03": {"turns": 5, "out_tok": 50, "side_turns": 0},
            },
            "claude-haiku-4-5": {
                "2026-06-30": {"turns": 1000, "out_tok": 10, "side_turns": 0},
            },
        },
    }
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps(ledger), encoding="utf-8")
    monkeypatch.setattr(srv, "_MODEL_LEDGER_PATH", p)
    out = srv._harness_model_usage_top(4)
    assert out[0]["model"] == "claude-haiku-4-5"
    assert out[0]["turns"] == 1000
    opus = next(r for r in out if r["model"] == "claude-opus-4-8")
    assert opus["turns"] == 10 + 5 + 20 + 5  # main+side across all dates
    assert opus["out_tok"] == 350
    assert opus["last_seen"] == "2026-07-03"


def test_harness_recent_waves_filters_wave_lines_only(tmp_path: Path) -> None:
    p = tmp_path / "log.md"
    p.write_text(
        "\n".join([
            "2026-07-09 02:12 [ignite_p1: bootstrap]",
            "- 2026-07-09 fix(a): first wave",
            "2026-07-09 daily-auto [closes=1]",
            "- 2026-07-09 fix(b): second wave",
            "- 2026-07-10 fix(c): third wave",
        ]),
        encoding="utf-8",
    )
    out = srv._harness_recent_waves(3, log_path=p)
    assert out == [
        "- 2026-07-09 fix(a): first wave",
        "- 2026-07-09 fix(b): second wave",
        "- 2026-07-10 fix(c): third wave",
    ]


def test_harness_recent_waves_missing_file(tmp_path: Path) -> None:
    assert srv._harness_recent_waves(3, log_path=tmp_path / "absent.md") == []


# ── Cowork intel feed ────────────────────────────────────────────────────────


def test_cowork_intel_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_ALPACA_SEED_PATH", tmp_path / "absent.json")
    out = srv._build_cowork_intel()
    assert out == {"available": False, "status": "NO_FEED"}


def test_cowork_intel_corrupt_json_fail_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "seed.json"
    p.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(srv, "_ALPACA_SEED_PATH", p)
    out = srv._build_cowork_intel()
    assert out == {"available": False, "status": "NO_FEED"}


def test_cowork_intel_expired_feed_and_top8_by_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_db: Path
) -> None:
    seed = {
        "generated_at": "2026-07-04T13:32:24Z",
        "expiry_ts": "2026-07-06T20:00:00Z",  # in the past → EXPIRED
        "candidates": [
            {"symbol": f"SYM{i}", "venue": "alpaca", "thesis_tag": "t", "score": i / 10.0}
            for i in range(10)
        ],
    }
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(srv, "_ALPACA_SEED_PATH", p)
    monkeypatch.setattr(srv, "_MARKET_CONTEXT_PATH", tmp_path / "absent.md")
    monkeypatch.setattr(srv, "_DB_PATH", live_db)
    out = srv._build_cowork_intel()
    assert out["available"] is True
    assert out["status"] == "EXPIRED"
    assert len(out["candidates"]) == 8
    assert out["candidates"][0]["symbol"] == "SYM9"  # highest score first
    assert out["cohort_fired"] == 0
    assert out["market_context_head"] == []


def test_cowork_intel_active_feed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_db: Path
) -> None:
    far_future = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 999999)
    )
    seed = {"generated_at": "now", "expiry_ts": far_future, "candidates": []}
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(srv, "_ALPACA_SEED_PATH", p)
    monkeypatch.setattr(srv, "_MARKET_CONTEXT_PATH", tmp_path / "absent.md")
    monkeypatch.setattr(srv, "_DB_PATH", live_db)
    out = srv._build_cowork_intel()
    assert out["status"] == "ACTIVE"


def test_cowork_intel_market_context_head_and_cohort_fired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_db: Path
) -> None:
    seed = {"generated_at": "now", "expiry_ts": "", "candidates": []}
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(srv, "_ALPACA_SEED_PATH", p)
    mkt = tmp_path / "market_context.md"
    mkt.write_text("# Market Context\nGenerated now\n\n## Section\n- bullet\n", encoding="utf-8")
    monkeypatch.setattr(srv, "_MARKET_CONTEXT_PATH", mkt)
    monkeypatch.setattr(srv, "_DB_PATH", live_db)

    conn = _rw(live_db)
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        "product_class, stream_id, signal_id, strategy_id, entry_strategy_id, "
        "active_strategy_id, side, qty, status, opened_ts, closed_ts, "
        "swap_count, stop_price, peak_price, trough_price, mfe_r, mae_r, "
        "exit_state, deal_id, entry_atr_pct, entry_atr_timeframe, pnl_r, "
        "risk_usd, entry_regime, exit_cadence, stop_atr_mult, seed_tag) "
        "VALUES ('p1','okx','BTC-USDT','g1','spot','s1','sig1','strat','strat','strat',"
        "'long',1.0,'open',0,NULL,0,0,0,0,0,0,'open',NULL,0,'1h',0,0,'trend','normal',0,'etf_flow')"
    )
    conn.commit()
    conn.close()

    out = srv._build_cowork_intel()
    assert out["status"] == "UNKNOWN"
    assert out["market_context_head"] == [
        "# Market Context", "Generated now", "## Section",
    ]
    assert out["cohort_fired"] == 1


# ── full 3-section assembly ──────────────────────────────────────────────────


def test_build_ai_activity_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_db: Path
) -> None:
    monkeypatch.setattr(srv, "_DB_PATH", live_db)
    monkeypatch.setattr(srv, "_PROBE_DB_PATH", tmp_path / "absent_probe.sqlite")
    monkeypatch.setattr(srv, "_MODEL_LEDGER_PATH", tmp_path / "absent_ledger.json")
    monkeypatch.setattr(srv, "_ALPACA_SEED_PATH", tmp_path / "absent_seed.json")
    monkeypatch.setattr(srv, "_MARKET_CONTEXT_PATH", tmp_path / "absent_mkt.md")

    out = srv._build_ai_activity()
    assert set(out.keys()) == {"bot_ai", "harness_ai", "cowork_intel", "ts"}

    bot_ai = out["bot_ai"]
    assert bot_ai["window_h"] == 24
    assert bot_ai["in_loop_ai_calls"] == 0
    assert bot_ai["gate_events"] == {"total": 0, "model_used": {}}
    assert bot_ai["judge"]["calls"] == 0
    assert bot_ai["entry_admission_shadow"]["total"] == 0
    # debates read the REAL vault/50_research/debates/*.md (not monkeypatched,
    # same as the pre-existing _read_debates behavior) — just check the shape.
    assert isinstance(bot_ai["debates"], list)
    assert bot_ai["probe_escalations"] == []  # _PROBE_DB_PATH monkeypatched absent

    harness = out["harness_ai"]
    assert harness["label"] == "Claude harness (dev) — not the bot's LLM"
    assert harness["models"] == []

    cowork = out["cowork_intel"]
    assert cowork == {"available": False, "status": "NO_FEED"}
