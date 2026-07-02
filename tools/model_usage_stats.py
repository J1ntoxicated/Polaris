#!/usr/bin/env python3
"""model_usage_stats.py — Polaris harness model-usage watermark + vault stats.

Scans Claude Code transcript JSONL files (main session + subagent sidechain),
aggregates per-session/model/day turn counts, token usage, and subagent-spawn
matrix into a persistent ledger (data/runtime/model_usage_ledger.json), then
renders a <=60-line markdown summary to vault/40_ops/model-usage-stats.md.

DEMO/PAPER harness tooling only — no trading-logic contact. stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRANSCRIPT_DIR = Path.home() / ".claude" / "projects" / "-Users-jinyoon-Projects-Polaris"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "runtime" / "model_usage_ledger.json"
DEFAULT_VAULT_PATH = PROJECT_ROOT / "vault" / "40_ops" / "model-usage-stats.md"
EXCLUDED_DIR_NAMES = {"memory", "tool-results"}
SYDNEY_TZ = ZoneInfo("Australia/Sydney")

DayBucket = dict[str, Any]  # {"turns", "side_turns", "out_tok", "in_tok", "spawns": {..}}
ModelLedger = dict[str, DayBucket]  # date -> bucket
SessionLedger = dict[str, ModelLedger]  # model -> ModelLedger
Ledger = dict[str, SessionLedger]  # session_id -> SessionLedger


def _new_bucket() -> DayBucket:
    return {"turns": 0, "side_turns": 0, "out_tok": 0, "in_tok": 0, "spawns": {}}


def _is_excluded(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    return any(part in EXCLUDED_DIR_NAMES for part in rel_parts[:-1])


def _iter_transcript_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.jsonl") if p.is_file() and not _is_excluded(p, root)]


def _local_date(timestamp: str) -> str | None:
    try:
        ts = timestamp.replace("Z", "+00:00")
        moment = dt.datetime.fromisoformat(ts)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.UTC)
        return moment.astimezone(SYDNEY_TZ).date().isoformat()
    except (ValueError, TypeError):
        return None


def scan_transcripts(root: Path) -> Ledger:
    """Scan all transcript JSONL files under root and build a fresh ledger."""
    ledger: Ledger = defaultdict(lambda: defaultdict(lambda: defaultdict(_new_bucket)))
    for path in _iter_transcript_files(root):
        is_subdir_sidechain = path.parent != root
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    _process_event(event, ledger, is_subdir_sidechain)
        except OSError:
            continue
    return {sid: {m: dict(days) for m, days in models.items()} for sid, models in ledger.items()}


def _process_event(event: dict[str, Any], ledger: Ledger, is_subdir_sidechain: bool) -> None:
    if event.get("type") != "assistant":
        return
    message = event.get("message")
    if not isinstance(message, dict):
        return
    model = message.get("model")
    if not model or model == "<synthetic>":
        return

    session_id = event.get("sessionId")
    if not session_id:
        return

    timestamp = event.get("timestamp")
    date_key = _local_date(timestamp) if isinstance(timestamp, str) else None
    if date_key is None:
        date_key = "unknown"

    is_sidechain = bool(event.get("isSidechain")) or is_subdir_sidechain

    bucket = ledger[session_id][model][date_key]
    if is_sidechain:
        bucket["side_turns"] += 1
    else:
        bucket["turns"] += 1

    usage = message.get("usage")
    if isinstance(usage, dict):
        bucket["out_tok"] += _to_int(usage.get("output_tokens"))
        bucket["in_tok"] += (
            _to_int(usage.get("input_tokens"))
            + _to_int(usage.get("cache_read_input_tokens"))
            + _to_int(usage.get("cache_creation_input_tokens"))
        )

    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") not in ("Agent", "Task"):
            continue
        tool_input = block.get("input") or {}
        subagent_type = tool_input.get("subagent_type") or "fork"
        spawn_model = tool_input.get("model") or "inherit"
        key = f"{subagent_type}|{spawn_model}"
        bucket["spawns"][key] = bucket["spawns"].get(key, 0) + 1


def _to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def update_ledger(
    old_ledger: dict[str, Any], new_scan: Ledger, seen_session_ids: set[str]
) -> Ledger:
    """Merge new_scan into old_ledger: idempotent overwrite for scanned
    sessions, preserve sessions absent from disk (transcript 30-day cleanup)."""
    merged: Ledger = {}
    for session_id, session_data in old_ledger.items():
        if session_id not in seen_session_ids:
            merged[session_id] = session_data
    for session_id, session_data in new_scan.items():
        merged[session_id] = session_data
    return merged


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_ledger(path: Path, ledger: Ledger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _aggregate_by_model(ledger: Ledger) -> dict[str, dict[str, Any]]:
    """model -> {turns, side_turns, out_tok, in_tok, last_seen}"""
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"turns": 0, "side_turns": 0, "out_tok": 0, "in_tok": 0, "last_seen": ""}
    )
    for models in ledger.values():
        for model, days in models.items():
            entry = agg[model]
            for date_key, bucket in days.items():
                entry["turns"] += bucket.get("turns", 0)
                entry["side_turns"] += bucket.get("side_turns", 0)
                entry["out_tok"] += bucket.get("out_tok", 0)
                entry["in_tok"] += bucket.get("in_tok", 0)
                if date_key != "unknown" and date_key > entry["last_seen"]:
                    entry["last_seen"] = date_key
    return dict(agg)


def _aggregate_by_day(ledger: Ledger) -> dict[str, dict[str, int]]:
    """date -> model -> turns (main+side)"""
    agg: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for models in ledger.values():
        for model, days in models.items():
            for date_key, bucket in days.items():
                agg[date_key][model] += bucket.get("turns", 0) + bucket.get("side_turns", 0)
    return {d: dict(m) for d, m in agg.items()}


def _aggregate_spawns(ledger: Ledger) -> dict[str, int]:
    """"subagent_type|model" -> count"""
    agg: dict[str, int] = defaultdict(int)
    for models in ledger.values():
        for days in models.values():
            for bucket in days.values():
                for key, count in bucket.get("spawns", {}).items():
                    agg[key] += count
    return dict(agg)


TIER_NOTE = (
    "Tier map (Jin 2026-07-03): Opus=review/advisor · Fable=orchestrator/research-verify/"
    "Jin-dialogue · Sonnet=coding · Haiku=realtime."
)


def render_markdown(ledger: Ledger, days: int = 14) -> str:
    generated = dt.datetime.now(SYDNEY_TZ).strftime("%Y-%m-%d %H:%M AEST")

    lines: list[str] = [
        "---",
        "type: runtime",
        "tags: [model-stats, harness]",
        "---",
        "",
        "# Model Usage Stats",
        "",
        f"AUTO-GENERATED — do not hand-edit. Generated {generated}.",
        "",
        TIER_NOTE,
        "",
    ]

    by_model = _aggregate_by_model(ledger)
    lines.append("## By model (cumulative)")
    lines.append("")
    lines.append("| model | turns (main+side) | out_tok | in_tok | last_seen |")
    lines.append("|---|---|---|---|---|")
    for model in sorted(by_model, key=lambda m: -by_model[m]["turns"]):
        e = by_model[model]
        turns_str = f"{e['turns']}+{e['side_turns']}"
        lines.append(
            f"| {model} | {turns_str} | {_fmt_tok(e['out_tok'])} | "
            f"{_fmt_tok(e['in_tok'])} | {e['last_seen'] or 'n/a'} |"
        )
    lines.append("")

    by_day = _aggregate_by_day(ledger)
    recent_dates = sorted((d for d in by_day if d != "unknown"), reverse=True)[:days]
    recent_dates.reverse()
    models_in_range: list[str] = []
    for d in recent_dates:
        for m in by_day[d]:
            if m not in models_in_range:
                models_in_range.append(m)

    lines.append(f"## Daily iteration frequency (last {days}d)")
    lines.append("")
    if recent_dates and models_in_range:
        header = "| date | " + " | ".join(models_in_range) + " |"
        sep = "|---|" + "|".join(["---"] * len(models_in_range)) + "|"
        lines.append(header)
        lines.append(sep)
        for d in recent_dates:
            row = [str(by_day[d].get(m, 0)) for m in models_in_range]
            lines.append(f"| {d} | " + " | ".join(row) + " |")
    else:
        lines.append("(no data)")
    lines.append("")

    spawns = _aggregate_spawns(ledger)
    lines.append("## Spawn matrix")
    lines.append("")
    lines.append("| subagent_type | model | count |")
    lines.append("|---|---|---|")
    ranked = sorted(spawns.items(), key=lambda kv: -kv[1])
    shown, rest = ranked[:12], ranked[12:]
    for key, count in shown:
        subagent_type, spawn_model = key.split("|", 1)
        lines.append(f"| {subagent_type} | {spawn_model} | {count} |")
    if rest:
        lines.append(f"| ... | +{len(rest)} more | |")
    if not ranked:
        lines.append("| (none) | | |")

    return "\n".join(lines) + "\n"


def _run(
    scan_dir: Path, ledger_path: Path, vault_path: Path, days: int, write: bool
) -> str:
    new_scan = scan_transcripts(scan_dir)
    seen_session_ids = set(new_scan.keys())

    if write:
        old_ledger = _load_ledger(ledger_path)
        merged = update_ledger(old_ledger, new_scan, seen_session_ids)
        _save_ledger(ledger_path, merged)
        ledger_for_render = merged
    else:
        ledger_for_render = new_scan

    markdown = render_markdown(ledger_for_render, days=days)

    if write:
        vault_path.parent.mkdir(parents=True, exist_ok=True)
        vault_path.write_text(markdown, encoding="utf-8")

    return markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Polaris model usage stats")
    parser.add_argument("--dir", type=Path, default=DEFAULT_TRANSCRIPT_DIR)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--vault-out", type=Path, default=DEFAULT_VAULT_PATH)
    parser.add_argument("--ledger-out", type=Path, default=DEFAULT_LEDGER_PATH)
    args = parser.parse_args()

    try:
        markdown = _run(args.dir, args.ledger_out, args.vault_out, args.days, args.write)
    except Exception as exc:  # noqa: BLE001 — CLI boundary, report and exit non-zero
        print(f"model_usage_stats: error: {exc}", file=sys.stderr)
        return 1

    if not args.write:
        print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
