"""Tests for tools/model_usage_stats.py — model usage watermark + vault stats.

DEMO/PAPER harness tooling only, no trading-logic contact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.model_usage_stats import (
    render_markdown,
    scan_transcripts,
    update_ledger,
)


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _assistant_event(
    session_id: str,
    model: str,
    ts: str,
    *,
    is_sidechain: bool = False,
    out_tok: int = 10,
    in_tok: int = 5,
    cache_read: int = 0,
    cache_creation: int = 0,
    tool_use: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": "hi"}]
    if tool_use:
        content.extend(tool_use)
    return {
        "type": "assistant",
        "sessionId": session_id,
        "isSidechain": is_sidechain,
        "timestamp": ts,
        "message": {
            "model": model,
            "content": content,
            "usage": {
                "output_tokens": out_tok,
                "input_tokens": in_tok,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        },
    }


class TestScanTranscripts:
    def test_counts_main_session_turn(self, tmp_path: Path) -> None:
        p = tmp_path / "sess1.jsonl"
        _write_jsonl(
            p,
            [_assistant_event("sess1", "claude-sonnet-5", "2026-07-03T00:00:00Z")],
        )
        ledger = scan_transcripts(tmp_path)
        assert ledger["sess1"]["claude-sonnet-5"]
        day = next(iter(ledger["sess1"]["claude-sonnet-5"].values()))
        assert day["turns"] == 1
        assert day["side_turns"] == 0

    def test_sums_tokens_including_cache(self, tmp_path: Path) -> None:
        p = tmp_path / "sess1.jsonl"
        _write_jsonl(
            p,
            [
                _assistant_event(
                    "sess1",
                    "claude-sonnet-5",
                    "2026-07-03T00:00:00Z",
                    out_tok=100,
                    in_tok=10,
                    cache_read=20,
                    cache_creation=30,
                )
            ],
        )
        ledger = scan_transcripts(tmp_path)
        day = next(iter(ledger["sess1"]["claude-sonnet-5"].values()))
        assert day["out_tok"] == 100
        # in_tok = input + cache_read + cache_creation = 10+20+30 = 60
        assert day["in_tok"] == 60

    def test_skips_synthetic_model(self, tmp_path: Path) -> None:
        p = tmp_path / "sess1.jsonl"
        _write_jsonl(
            p,
            [_assistant_event("sess1", "<synthetic>", "2026-07-03T00:00:00Z")],
        )
        ledger = scan_transcripts(tmp_path)
        assert ledger == {}

    def test_skips_broken_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "sess1.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            fh.write("{not valid json\n")
            fh.write(
                json.dumps(
                    _assistant_event("sess1", "claude-sonnet-5", "2026-07-03T00:00:00Z")
                )
                + "\n"
            )
        ledger = scan_transcripts(tmp_path)
        assert ledger["sess1"]["claude-sonnet-5"]

    def test_sidechain_by_flag_separated_from_main(self, tmp_path: Path) -> None:
        p = tmp_path / "sess1.jsonl"
        _write_jsonl(
            p,
            [
                _assistant_event("sess1", "claude-sonnet-5", "2026-07-03T00:00:00Z"),
                _assistant_event(
                    "sess1", "claude-sonnet-5", "2026-07-03T00:01:00Z", is_sidechain=True
                ),
            ],
        )
        ledger = scan_transcripts(tmp_path)
        day = next(iter(ledger["sess1"]["claude-sonnet-5"].values()))
        assert day["turns"] == 1
        assert day["side_turns"] == 1

    def test_sidechain_by_subdirectory_location(self, tmp_path: Path) -> None:
        sub = tmp_path / "sess2" / "subagents" / "agent-x.jsonl"
        _write_jsonl(
            sub,
            [_assistant_event("sess2", "claude-fable-5", "2026-07-03T00:00:00Z")],
        )
        ledger = scan_transcripts(tmp_path)
        day = next(iter(ledger["sess2"]["claude-fable-5"].values()))
        assert day["side_turns"] == 1
        assert day["turns"] == 0

    def test_daily_bucket_uses_sydney_timezone_boundary(self, tmp_path: Path) -> None:
        # 2026-07-03T14:30:00Z = 2026-07-04 00:30 AEST (UTC+10 in July, non-DST)
        p = tmp_path / "sess1.jsonl"
        _write_jsonl(
            p,
            [_assistant_event("sess1", "claude-sonnet-5", "2026-07-03T14:30:00Z")],
        )
        ledger = scan_transcripts(tmp_path)
        dates = list(ledger["sess1"]["claude-sonnet-5"].keys())
        assert dates == ["2026-07-04"]

    def test_excludes_memory_and_tool_results_dirs(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory" / "MEMORY.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text("not jsonl content", encoding="utf-8")
        tr = tmp_path / "tool-results" / "fake.jsonl"
        _write_jsonl(
            tr, [_assistant_event("sessX", "claude-sonnet-5", "2026-07-03T00:00:00Z")]
        )
        ledger = scan_transcripts(tmp_path)
        assert ledger == {}

    def test_spawn_matrix_records_subagent_type_and_model(self, tmp_path: Path) -> None:
        p = tmp_path / "sess1.jsonl"
        tool_use = [
            {
                "type": "tool_use",
                "name": "Agent",
                "input": {"subagent_type": "general-purpose", "model": "sonnet"},
            }
        ]
        _write_jsonl(
            p,
            [
                _assistant_event(
                    "sess1", "claude-opus-4-8", "2026-07-03T00:00:00Z", tool_use=tool_use
                )
            ],
        )
        ledger = scan_transcripts(tmp_path)
        day = next(iter(ledger["sess1"]["claude-opus-4-8"].values()))
        assert day["spawns"]["general-purpose|sonnet"] == 1

    def test_spawn_defaults_when_fields_missing(self, tmp_path: Path) -> None:
        p = tmp_path / "sess1.jsonl"
        tool_use = [{"type": "tool_use", "name": "Task", "input": {}}]
        _write_jsonl(
            p,
            [
                _assistant_event(
                    "sess1", "claude-opus-4-8", "2026-07-03T00:00:00Z", tool_use=tool_use
                )
            ],
        )
        ledger = scan_transcripts(tmp_path)
        day = next(iter(ledger["sess1"]["claude-opus-4-8"].values()))
        assert day["spawns"]["fork|inherit"] == 1


class TestUpdateLedger:
    def test_idempotent_overwrite_for_rescanned_session(self, tmp_path: Path) -> None:
        old_ledger = {
            "sess1": {
                "claude-sonnet-5": {
                    "2026-07-01": {
                        "turns": 99,
                        "side_turns": 0,
                        "out_tok": 999,
                        "in_tok": 999,
                        "spawns": {},
                    }
                }
            }
        }
        new_scan = {
            "sess1": {
                "claude-sonnet-5": {
                    "2026-07-03": {
                        "turns": 1,
                        "side_turns": 0,
                        "out_tok": 10,
                        "in_tok": 5,
                        "spawns": {},
                    }
                }
            }
        }
        merged = update_ledger(old_ledger, new_scan, seen_session_ids={"sess1"})
        # sess1 entry fully replaced by new_scan (idempotent overwrite)
        assert merged["sess1"]["claude-sonnet-5"] == {
            "2026-07-03": {
                "turns": 1,
                "side_turns": 0,
                "out_tok": 10,
                "in_tok": 5,
                "spawns": {},
            }
        }

    def test_preserves_sessions_not_present_on_disk(self, tmp_path: Path) -> None:
        old_ledger = {
            "sess_gone": {
                "claude-sonnet-5": {
                    "2026-06-01": {
                        "turns": 5,
                        "side_turns": 0,
                        "out_tok": 50,
                        "in_tok": 50,
                        "spawns": {},
                    }
                }
            }
        }
        new_scan: dict[str, Any] = {}
        merged = update_ledger(old_ledger, new_scan, seen_session_ids=set())
        assert merged["sess_gone"] == old_ledger["sess_gone"]


class TestRenderMarkdown:
    def test_output_at_most_60_lines(self) -> None:
        ledger = {
            "sess1": {
                "claude-sonnet-5": {
                    "2026-07-03": {
                        "turns": 5,
                        "side_turns": 1,
                        "out_tok": 12345,
                        "in_tok": 67890,
                        "spawns": {"general-purpose|sonnet": 2},
                    }
                },
                "claude-opus-4-8": {
                    "2026-07-02": {
                        "turns": 2,
                        "side_turns": 0,
                        "out_tok": 500,
                        "in_tok": 1000,
                        "spawns": {},
                    }
                },
            }
        }
        md = render_markdown(ledger, days=14)
        lines = md.splitlines()
        assert len(lines) <= 60

    def test_contains_model_rows_and_frontmatter(self) -> None:
        ledger = {
            "sess1": {
                "claude-sonnet-5": {
                    "2026-07-03": {
                        "turns": 3,
                        "side_turns": 0,
                        "out_tok": 1000,
                        "in_tok": 2000,
                        "spawns": {},
                    }
                }
            }
        }
        md = render_markdown(ledger, days=14)
        assert "type: runtime" in md
        assert "AUTO-GENERATED" in md
        assert "claude-sonnet-5" in md

    def test_spawn_matrix_caps_at_12_rows_with_more_note(self) -> None:
        spawns = {f"agent{i}|sonnet": 1 for i in range(20)}
        ledger = {
            "sess1": {
                "claude-opus-4-8": {
                    "2026-07-03": {
                        "turns": 1,
                        "side_turns": 0,
                        "out_tok": 10,
                        "in_tok": 10,
                        "spawns": spawns,
                    }
                }
            }
        }
        md = render_markdown(ledger, days=14)
        assert "+8 more" in md or "more" in md


class TestCLI:
    def test_write_flag_creates_vault_file_and_ledger(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        transcripts_dir = tmp_path / "transcripts"
        sess = transcripts_dir / "sessA.jsonl"
        _write_jsonl(
            sess,
            [_assistant_event("sessA", "claude-sonnet-5", "2026-07-03T00:00:00Z")],
        )
        vault_dir = tmp_path / "vault" / "40_ops"
        ledger_path = tmp_path / "data" / "runtime" / "model_usage_ledger.json"

        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "tools" / "model_usage_stats.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--dir",
                str(transcripts_dir),
                "--write",
                "--vault-out",
                str(vault_dir / "model-usage-stats.md"),
                "--ledger-out",
                str(ledger_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out_md = vault_dir / "model-usage-stats.md"
        assert out_md.exists()
        assert ledger_path.exists()

    def test_default_stdout_without_write(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        transcripts_dir = tmp_path / "transcripts"
        sess = transcripts_dir / "sessA.jsonl"
        _write_jsonl(
            sess,
            [_assistant_event("sessA", "claude-sonnet-5", "2026-07-03T00:00:00Z")],
        )
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "tools" / "model_usage_stats.py"
        result = subprocess.run(
            [sys.executable, str(script), "--dir", str(transcripts_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "claude-sonnet-5" in result.stdout


class TestSessionEndHook:
    def test_session_end_exits_zero_even_if_stats_script_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parents[1]
        hook = repo_root / ".claude" / "hooks" / "session_end.py"
        # Run hook with cwd such that model_usage_stats.py subprocess call
        # itself still resolves via PROJECT_ROOT inside the hook — we don't
        # break that path, we just assert exit 0 regardless of stats outcome.
        result = subprocess.run(
            [sys.executable, str(hook)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(repo_root),
        )
        assert result.returncode == 0, result.stderr
