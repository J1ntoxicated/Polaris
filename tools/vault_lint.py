#!/usr/bin/env python3
"""
vault_lint.py — Polaris vault 2-tier lint (Karpathy spec)

Light (pre-commit, fast):
  - missing frontmatter
  - frontmatter required fields (type, status, date_created, tags)
  - broken wiki-links ([[X]] where X.md not in vault)
  - no outbound links (except _NOW.md, log.md, INDEX.md, charter editable=false)
  - no inbound links in mature dirs (10_decisions, 20_strategies, 30_components)

Heavy (weekly cron):
  - contradiction detection (same key in two ADRs with different stance)
  - stale-note (>90d untouched + status:active)
  - orphan recommendations (no in/out links + not root/charter)

Usage:
  python3 tools/vault_lint.py --light                # default
  python3 tools/vault_lint.py --heavy
  python3 tools/vault_lint.py --karpathy --report    # both + structured report
  python3 tools/vault_lint.py --fix                  # auto-fix where safe (frontmatter scaffolding)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VAULT = PROJECT_ROOT / "vault"

ROOT_EXEMPT = {"_NOW.md", "INDEX.md", "log.md", ".tag_taxonomy.md"}
MATURE_DIRS = {"10_decisions", "20_strategies", "30_components"}
CHARTER_DIR = "00_charter"

WIKI_LINK_RE = re.compile(r"\[\[([^\]\|]+?)(?:\|[^\]]+)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
REQUIRED_FRONTMATTER_FIELDS = ("type", "status", "date_created", "tags")
STALE_DAYS = 90

# Banned keywords (aggressive bias preservation — should never appear in vault)
BANNED_KEYWORDS = (
    "12주", "60d", "90d demo gate",
    "regulatory cap", "professional risk", "capital protection",
    "monthly review", "30일 lock-in",
    "표본 부족 risk", "real-money safety",
    "macro guard", "news blackout",
    "auto-disable", "auto drawdown stop", "regime auto-throttle",
    "posture defensive", "posture standard",
)


@dataclass
class Issue:
    severity: str          # error / warn / info
    category: str          # frontmatter / wikilink / orphan / stale / contradiction / banned
    path: str
    detail: str


@dataclass
class LintReport:
    light_issues: list[Issue] = field(default_factory=list)
    heavy_issues: list[Issue] = field(default_factory=list)
    files_scanned: int = 0
    files_with_frontmatter: int = 0

    def by_severity(self) -> dict[str, int]:
        counter: dict[str, int] = defaultdict(int)
        for i in self.light_issues + self.heavy_issues:
            counter[i.severity] += 1
        return dict(counter)


# ---------- helpers ----------

def iter_md_files() -> Iterable[Path]:
    for p in VAULT.rglob("*.md"):
        if any(part.startswith(".") and part not in {".templates", ".tag_taxonomy.md"}
               for part in p.parts):
            continue
        yield p


def parse_frontmatter(text: str) -> dict | None:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    fields: dict = {}
    for line in body.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def strip_fenced_code(text: str) -> str:
    """Remove ```...``` fenced blocks so wiki-link / banned scans skip code examples."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out.append(line)
    return "\n".join(out)


_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def strip_inline_code(text: str) -> str:
    """Remove `...` inline-code spans so banned/wiki scans skip them."""
    return _INLINE_CODE_RE.sub("", text)


def extract_wikilinks(text: str) -> set[str]:
    cleaned = strip_inline_code(strip_fenced_code(text))
    return {m.group(1).strip() for m in WIKI_LINK_RE.finditer(cleaned)}


def relative_to_vault(path: Path) -> str:
    try:
        return str(path.relative_to(VAULT))
    except ValueError:
        return str(path)


def build_filename_index() -> dict[str, Path]:
    """Map basename (without .md) → Path. Wiki-links resolve by basename."""
    index: dict[str, Path] = {}
    for p in iter_md_files():
        stem = p.stem
        index.setdefault(stem, p)
        # Also index ADR alias: ADR-001-vault-structure -> ADR-001
        m = re.match(r"^(ADR-\d{3})", stem)
        if m:
            index.setdefault(m.group(1), p)
    return index


# ---------- light lint ----------

def lint_light(report: LintReport) -> None:
    filename_index = build_filename_index()
    inbound: dict[str, set[str]] = defaultdict(set)

    for path in iter_md_files():
        report.files_scanned += 1
        rel = relative_to_vault(path)
        text = path.read_text(encoding="utf-8")

        fm = parse_frontmatter(text)
        is_root_exempt = path.name in ROOT_EXEMPT and path.parent == VAULT

        if fm is None:
            if not is_root_exempt:
                report.light_issues.append(Issue(
                    "error", "frontmatter", rel,
                    "missing frontmatter (--- ... --- block)"))
        else:
            report.files_with_frontmatter += 1
            for f in REQUIRED_FRONTMATTER_FIELDS:
                if f not in fm:
                    report.light_issues.append(Issue(
                        "warn", "frontmatter", rel,
                        f"missing required field: {f}"))

        # Banned keyword scan (section-aware: ## heading containing 거부/폐기/anti-pattern
        # or "Anti-pattern" enables all lines under it until next heading)
        section_ok_tokens = (
            "거부", "폐기", "anti-pattern", "Anti-pattern", "거부된", "폐기된",
            "Reject", "reject", "Aggressive bias", "aggressive bias",
            "keyword sweep", "Keyword sweep", "keyword scan", "Keyword scan",
        )
        line_ok_tokens = section_ok_tokens + (
            "NO ", "→ 0건", "0건", "ban", "rejected", "rejected:",
            "없음", "없다", "무효", "금지", "deprecated", "거부된", "거부 키워드",
            "anti-stealth", "보수 위장", "보수 논거",
            "0 hits", "0 hit", "no hits", "No hits",
            "Rejected-keyword", "rejected-keyword", "Rejected keyword",
            "no defensive", "no auto-disable", "no auto-stop",
            "[x] No", "- [x] No", "keywords introduced",
        )
        # Strip inline-code spans so checklist/example references in `backticks` are ignored.
        scan_text = strip_inline_code(text)
        lines = scan_text.splitlines()
        # Compute "section ok" mask: heading line activates ok scope until next heading.
        section_ok = [False] * len(lines)
        currently_ok = False
        for idx, ln in enumerate(lines):
            stripped = ln.lstrip()
            if stripped.startswith("#"):
                currently_ok = any(tok in ln for tok in section_ok_tokens)
            section_ok[idx] = currently_ok

        for banned in BANNED_KEYWORDS:
            tlow = banned.lower()
            bad_lines: list[str] = []
            for idx, ln in enumerate(lines):
                if tlow not in ln.lower():
                    continue
                if section_ok[idx]:
                    continue
                if any(tok in ln for tok in line_ok_tokens):
                    continue
                bad_lines.append(ln.strip())
            if bad_lines:
                report.light_issues.append(Issue(
                    "error", "banned", rel,
                    f"banned keyword '{banned}' in non-rejection context: {bad_lines[0][:80]}"))

        outlinks = extract_wikilinks(text)
        if not outlinks and not is_root_exempt and CHARTER_DIR not in path.parts:
            report.light_issues.append(Issue(
                "warn", "wikilink", rel,
                "no outbound wiki-links"))

        for link in outlinks:
            target = filename_index.get(link)
            if target is None:
                # Allow link to root files like _NOW / INDEX
                stem_alt = link.replace("[[", "").replace("]]", "")
                if stem_alt in {"_NOW", "INDEX", "log", ".tag_taxonomy"}:
                    continue
                report.light_issues.append(Issue(
                    "warn", "wikilink", rel,
                    f"broken wiki-link: [[{link}]]"))
            else:
                inbound[relative_to_vault(target)].add(rel)

    # No inbound in mature dirs
    for path in iter_md_files():
        rel = relative_to_vault(path)
        parts = path.relative_to(VAULT).parts
        if not parts:
            continue
        top = parts[0]
        if top in MATURE_DIRS and not inbound.get(rel):
            report.light_issues.append(Issue(
                "warn", "wikilink", rel,
                f"no inbound wiki-links in mature dir {top}/"))


# ---------- heavy lint ----------

def lint_heavy(report: LintReport) -> None:
    today = dt.date.today()

    for path in iter_md_files():
        rel = relative_to_vault(path)
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}

        # Stale note
        date_str = fm.get("date_updated") or fm.get("date_created")
        status = fm.get("status", "")
        if date_str and status == "active":
            try:
                d = dt.date.fromisoformat(date_str.strip().strip('"'))
                age = (today - d).days
                if age > STALE_DAYS:
                    report.heavy_issues.append(Issue(
                        "warn", "stale", rel,
                        f"status:active but {age}d untouched (>{STALE_DAYS}d threshold)"))
            except ValueError:
                pass

    # Contradiction detection (very rough heuristic, P0):
    # In ADRs, if same param key has different value across two active ADRs.
    contradiction_keys = ("Kelly k", "single-trade default", "single-trade absolute ceiling",
                          "Track A gross cap", "Track B gross cap", "Total daily risk")
    seen: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path in (VAULT / "10_decisions").glob("ADR-*.md"):
        rel = relative_to_vault(path)
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}
        if fm.get("status", "") != "active":
            continue
        for key in contradiction_keys:
            for line in text.splitlines():
                if key in line and "|" in line:
                    val = line.split("|")[-2].strip() if line.count("|") >= 2 else line.strip()
                    seen[key].append((rel, val))
    for key, occurrences in seen.items():
        if len(occurrences) <= 1:
            continue
        values = {v for _, v in occurrences}
        if len(values) > 1:
            report.heavy_issues.append(Issue(
                "error", "contradiction", "10_decisions/",
                f"key '{key}' has divergent values: {occurrences}"))

    # Orphan recommendations (no in OR out links + not root/charter)
    filename_index = build_filename_index()
    inbound: dict[str, set[str]] = defaultdict(set)
    outbound: dict[str, set[str]] = defaultdict(set)
    for path in iter_md_files():
        rel = relative_to_vault(path)
        text = path.read_text(encoding="utf-8")
        for link in extract_wikilinks(text):
            target = filename_index.get(link)
            if target is None:
                continue
            outbound[rel].add(link)
            inbound[relative_to_vault(target)].add(rel)
    for path in iter_md_files():
        rel = relative_to_vault(path)
        if path.name in ROOT_EXEMPT:
            continue
        if CHARTER_DIR in path.parts:
            continue
        if not inbound.get(rel) and not outbound.get(rel):
            report.heavy_issues.append(Issue(
                "info", "orphan", rel,
                "orphan: no in OR out links — consider linking or moving to attachments"))


# ---------- output ----------

def print_report(report: LintReport, *, json_mode: bool) -> int:
    if json_mode:
        payload = {
            "files_scanned": report.files_scanned,
            "files_with_frontmatter": report.files_with_frontmatter,
            "light_issues": [i.__dict__ for i in report.light_issues],
            "heavy_issues": [i.__dict__ for i in report.heavy_issues],
            "by_severity": report.by_severity(),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"vault_lint — files scanned: {report.files_scanned}")
        print(f"  with frontmatter: {report.files_with_frontmatter}")
        print(f"  light issues: {len(report.light_issues)}")
        print(f"  heavy issues: {len(report.heavy_issues)}")
        for i in report.light_issues + report.heavy_issues:
            print(f"  [{i.severity}] {i.category} :: {i.path} :: {i.detail}")

    has_error = any(i.severity == "error" for i in report.light_issues + report.heavy_issues)
    return 1 if has_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--light", action="store_true", help="run light pre-commit lint (default)")
    parser.add_argument("--heavy", action="store_true", help="run heavy weekly lint")
    parser.add_argument("--karpathy", action="store_true", help="run light + heavy (full)")
    parser.add_argument("--report", action="store_true", help="emit json report")
    parser.add_argument("--fix", action="store_true", help="(P1) auto-fix safe issues")
    args = parser.parse_args()

    report = LintReport()

    run_light = args.light or args.karpathy or not (args.heavy or args.karpathy)
    run_heavy = args.heavy or args.karpathy

    if run_light:
        lint_light(report)
    if run_heavy:
        lint_heavy(report)

    if args.fix:
        n = autofix_missing_frontmatter()
        print(f"[--fix] scaffolded frontmatter on {n} files", file=sys.stderr)

    return print_report(report, json_mode=args.report)


# ---------- autofix ----------

def _infer_lesson_frontmatter(stem: str, text: str) -> str:
    today = dt.date.today().isoformat()
    lesson_id = stem.split("_")[0] if "_" in stem else stem
    # Try to extract date from filename suffix YYYY-MM-DD
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})$", stem)
    date_created = date_match.group(1) if date_match else today
    # Pull strategy / regime / type from inline list if present
    def grab(field: str) -> str:
        m = re.search(rf"-\s*{field}:\s*([^\n]+)", text)
        return m.group(1).strip() if m else "unknown"
    strategy = grab("strategy")
    type_ = grab("type")
    return (
        "---\n"
        "type: research\n"
        "status: active\n"
        f"date_created: {date_created}\n"
        f"tags: [lesson, p0, strategy/{strategy}, type/{type_}]\n"
        f"related: [[ADR-007]], [[ADR-008]]\n"
        f"lesson_id: {lesson_id}\n"
        "---\n\n"
    )


def autofix_missing_frontmatter() -> int:
    """Scaffold frontmatter on lesson files that are missing it.

    Conservative: only touches `50_research/lessons/*.md` with no `---` header.
    Adds 2 outbound wiki-links ([[ADR-007]] + [[ADR-008]]) so notes leave orphan state.
    """
    fixed = 0
    target_dir = VAULT / "50_research" / "lessons"
    if not target_dir.exists():
        return 0
    for path in sorted(target_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.lstrip().startswith("---"):
            continue
        fm = _infer_lesson_frontmatter(path.stem, text)
        path.write_text(fm + text, encoding="utf-8")
        fixed += 1
    return fixed


if __name__ == "__main__":
    sys.exit(main())
