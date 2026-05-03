"""vault_lint v4 — Polaris vault 정합성 검사.

Constitution-driven lint:
- P1 Authority 분리: vault md 안 machine state write 흔적 차단
- P2 Lifecycle: ADR/INSIGHT/HYPOTHESIS expires 필수, proposed 7일 초과 warn
- P4 Validation Boundary: 40_components reviewed_by codex 미명시 fail
- P6 Pure Core: 40_components pure 필드 권장
- M3 유기적 연결: 30_knowledge 노트 백링크 ≥ 2

Karpathy 3-type:
- orphan / stale / contradictions

Usage:
    python3 tools/vault_lint.py --karpathy           # 3-type lint only
    python3 tools/vault_lint.py --polaris            # Polaris contract checks only
    python3 tools/vault_lint.py                      # all checks
    python3 tools/vault_lint.py --report             # write 50_runtime/vault_lint_report-YYYY-MM-DD.md
    python3 tools/vault_lint.py --dry-run            # exit 0 regardless

Exit codes:
    0 = pass (or warnings only)
    1 = fail (one or more violations)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
VAULT = PROJECT_ROOT / "vault"

REQUIRED_EXPIRES_TYPES = {"adr", "insight", "hypothesis"}
PROPOSED_MAX_DAYS = 7
MIN_BACKLINKS_KNOWLEDGE = 2

# P1 — machine state write 흔적 패턴 (코드 블록 + inline backtick 밖에서만 검출).
# SQL 패턴은 table identifier 뒤따라야 매치 (false positive 차단 — 단순 인용 SQL 키워드는 통과).
MACHINE_STATE_LEAK_PATTERNS = [
    # File write APIs
    r"\bopen\([^)]*\.(json|jsonl|sqlite|db|csv|parquet)[^)]*[,\s]+(mode\s*=\s*)?['\"][wax]b?\b",
    r"\.write_text\(",
    r"\.write_bytes\(",
    # JSON / pickle / shelve high-level write
    r"\bjson\.dump\(",
    r"\bpickle\.dump\(",
    r"\bshelve\.open\(",
    # SQL writes — table identifier 필수 (단순 키워드 인용 차단)
    r"\bINSERT\s+INTO\s+[\w`\"\.]+",
    r"\bUPDATE\s+[\w`\"\.]+\s+SET\b",
    r"\bDELETE\s+FROM\s+[\w`\"\.]+",
    r"\.execute(?:many)?\(\s*['\"]?(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)",
    # ORM patterns
    r"\bsession\.add\(|\bsession\.commit\(|\bdb\.commit\(",
]


def _in_inline_code(line: str, col: int) -> bool:
    """주어진 라인의 col 위치가 inline code (`...`) 안인지 검사.

    fence (```)는 별도 처리 — 이 함수는 한 라인 inline backtick만.
    """
    backtick_count = line[:col].count("`")
    # 라인 내 backtick이 홀수면 inline code 안
    return backtick_count % 2 == 1


def _iter_md_pages():
    """Vault 안 모든 .md 파일 (templates/generated 제외)."""
    for md in VAULT.rglob("*.md"):
        rel = md.relative_to(VAULT)
        if rel.parts[0] in (".templates", "generated"):
            continue
        yield md


def _read_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _parse_frontmatter(text: str) -> dict:
    """YAML frontmatter parser. inline + multi-line list (`- item`) 지원.

    한계: nested dict / multi-line scalar (`|`) 미지원 (필요 시 PyYAML 도입).
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    result = {}
    current_list_key = None
    current_list_items: list[str] = []

    def _coerce_value(val: str):
        val = val.strip()
        # null / none → empty
        if val.lower() in {"null", "none", "~"}:
            return ""
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            return [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]
        if val.startswith('"') and val.endswith('"'):
            return val[1:-1]
        if val.startswith("'") and val.endswith("'"):
            return val[1:-1]
        return val

    for raw_line in fm_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # multi-line list item ("  - value")
        stripped = line.lstrip()
        if stripped.startswith("- ") and current_list_key is not None:
            item = stripped[2:].strip().strip('"').strip("'")
            if item:
                current_list_items.append(item)
            continue
        # 새 key 만나면 직전 list 마감
        if current_list_key is not None:
            result[current_list_key] = current_list_items
            current_list_key = None
            current_list_items = []
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val_stripped = val.strip()
        if not val_stripped:
            # multi-line list 시작
            current_list_key = key
            current_list_items = []
        else:
            result[key] = _coerce_value(val_stripped)
    # 마지막 list 마감
    if current_list_key is not None:
        result[current_list_key] = current_list_items
    return result


def _body(text: str) -> str:
    """Frontmatter 제거 후 본문."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:]


def _extract_backlinks(body: str) -> set[str]:
    """`[[entity]]` 형태 추출."""
    return set(re.findall(r"\[\[([^\]\|#]+?)(?:\|[^\]]+)?\]\]", body))


# ─────────────────────────── Karpathy 3-type ───────────────────────────


def lint_orphan() -> list[str]:
    """30_knowledge 노트 백링크 ≥ 2 (M3 차단). 다른 영역도 ≥ 1."""
    violations = []
    for md in _iter_md_pages():
        rel = md.relative_to(VAULT)
        stem = md.stem
        # 인덱스 파일 제외
        if stem.startswith("_") or stem in {"INDEX", "log"} or stem.startswith("."):
            continue
        text = _read_safe(md)
        body = _body(text)
        body_links = _extract_backlinks(body)
        fm = _parse_frontmatter(text)
        fm_links_raw = fm.get("back_links", [])
        fm_links = set()
        if isinstance(fm_links_raw, list):
            for ln in fm_links_raw:
                m = re.match(r"\[\[([^\]\|#]+?)\]\]", str(ln).strip())
                if m:
                    fm_links.add(m.group(1))
        all_links = body_links | fm_links
        min_required = MIN_BACKLINKS_KNOWLEDGE if rel.parts[0] == "30_knowledge" else 1
        if len(all_links) < min_required:
            violations.append(
                f"FAIL orphan: {rel} has {len(all_links)} backlink(s), need ≥ {min_required}"
            )
    return violations


def lint_stale() -> list[str]:
    """expires 만료 + status active 노트 검출."""
    violations = []
    today = _dt.date.today()
    for md in _iter_md_pages():
        text = _read_safe(md)
        fm = _parse_frontmatter(text)
        expires = str(fm.get("expires", "")).strip()
        if not expires or expires.lower() == "never":
            continue
        try:
            exp_date = _dt.date.fromisoformat(expires)
        except ValueError:
            continue
        status = str(fm.get("status", "")).lower()
        tags = fm.get("tags", []) if isinstance(fm.get("tags"), list) else []
        is_inactive = status in {"expired", "archived"} or any(
            "status/expired" in str(t) or "status/archived" in str(t) for t in tags
        )
        if exp_date < today and not is_inactive:
            rel = md.relative_to(VAULT)
            violations.append(
                f"FAIL stale: {rel} expired {expires} but status={status}"
            )
    return violations


def lint_contradictions() -> list[str]:
    """같은 entity_id 가 여러 파일에 중복."""
    violations = []
    by_id: dict[str, list[Path]] = {}
    for md in _iter_md_pages():
        text = _read_safe(md)
        fm = _parse_frontmatter(text)
        eid = str(fm.get("entity_id", "")).strip()
        if not eid:
            continue
        by_id.setdefault(eid, []).append(md)
    for eid, paths in by_id.items():
        if len(paths) > 1:
            files = ", ".join(str(p.relative_to(VAULT)) for p in paths)
            violations.append(f"FAIL contradiction: entity_id={eid} 중복 ({files})")
    return violations


# ─────────────────────────── Polaris contracts ───────────────────────────


def _code_block_ranges(body: str) -> list[tuple[int, int]]:
    """본문 안 코드 블록 (```) 범위 (start, end) 리스트 — fence 라인 단위 정확 매칭."""
    ranges = []
    in_block = False
    block_start = 0
    pos = 0
    for line in body.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if in_block:
                ranges.append((block_start, pos + len(line)))
                in_block = False
            else:
                block_start = pos
                in_block = True
        pos += len(line)
    if in_block:
        ranges.append((block_start, pos))
    return ranges


def _in_code_block(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def lint_machine_state_leak() -> list[str]:
    """P1 — vault md 안 machine state write 흔적 검출 (fence + inline code 밖). 다중 위반 모두 보고."""
    violations = []
    for md in _iter_md_pages():
        text = _read_safe(md)
        body = _body(text)
        ranges = _code_block_ranges(body)
        rel = md.relative_to(VAULT)
        seen_patterns: set[str] = set()
        # 라인별 offset 매핑 (inline backtick 검사용)
        line_offsets = []
        offset = 0
        for line in body.splitlines(keepends=True):
            line_offsets.append((offset, line))
            offset += len(line)
        for pattern in MACHINE_STATE_LEAK_PATTERNS:
            for m in re.finditer(pattern, body):
                if _in_code_block(m.start(), ranges):
                    continue
                # inline backtick 검사 — 같은 라인 내
                line_start = 0
                line_text = ""
                for ls, lt in line_offsets:
                    if ls <= m.start() < ls + len(lt):
                        line_start = ls
                        line_text = lt
                        break
                col = m.start() - line_start
                if _in_inline_code(line_text, col):
                    continue
                snippet = m.group(0)[:60]
                key = f"{rel}::{snippet}"
                if key in seen_patterns:
                    continue
                seen_patterns.add(key)
                violations.append(
                    f"FAIL P1 machine-state-leak: {rel} → '{snippet}'"
                )
    return violations


def lint_expires_required() -> list[str]:
    """P2 — ADR/INSIGHT/HYPOTHESIS는 expires 필수."""
    violations = []
    for md in _iter_md_pages():
        text = _read_safe(md)
        fm = _parse_frontmatter(text)
        et = str(fm.get("entity_type", "")).lower()
        if et not in REQUIRED_EXPIRES_TYPES:
            continue
        expires = str(fm.get("expires", "")).strip()
        if not expires:
            rel = md.relative_to(VAULT)
            violations.append(f"FAIL P2 expires-required: {rel} (type={et})")
    return violations


def lint_proposed_age() -> list[str]:
    """P2 — ADR proposed/provisional 7일 초과 warn."""
    warnings = []
    today = _dt.date.today()
    for md in _iter_md_pages():
        text = _read_safe(md)
        fm = _parse_frontmatter(text)
        et = str(fm.get("entity_type", "")).lower()
        if et != "adr":
            continue
        status = str(fm.get("status", "")).lower()
        tags = fm.get("tags", []) if isinstance(fm.get("tags"), list) else []
        is_proposed = status in {"proposed", "provisional"} or any(
            "status/proposed" in str(t) or "status/provisional" in str(t) for t in tags
        )
        if not is_proposed:
            continue
        last_modified = str(fm.get("last_modified", "")).strip()
        try:
            lm_date = _dt.date.fromisoformat(last_modified)
        except ValueError:
            continue
        age_days = (today - lm_date).days
        if age_days > PROPOSED_MAX_DAYS:
            rel = md.relative_to(VAULT)
            warnings.append(
                f"WARN P2 proposed-age: {rel} {age_days}d > {PROPOSED_MAX_DAYS}d"
            )
    return warnings


def lint_reviewed_by_codex() -> list[str]:
    """ADR-004 + P4 — 40_components 노트 reviewed_by: codex 필수."""
    violations = []
    components_dir = VAULT / "40_components"
    if not components_dir.exists():
        return violations
    for md in components_dir.rglob("*.md"):
        if md.stem.startswith("_"):
            continue
        text = _read_safe(md)
        fm = _parse_frontmatter(text)
        rb = str(fm.get("reviewed_by", "")).lower()
        if "codex" not in rb:
            rel = md.relative_to(VAULT)
            violations.append(
                f"FAIL ADR-004 reviewed-by: {rel} reviewed_by='{rb}' (codex 필수)"
            )
    return violations


def lint_pure_field() -> list[str]:
    """P6 — 40_components pure 필드 권장."""
    warnings = []
    components_dir = VAULT / "40_components"
    if not components_dir.exists():
        return warnings
    for md in components_dir.rglob("*.md"):
        if md.stem.startswith("_"):
            continue
        text = _read_safe(md)
        fm = _parse_frontmatter(text)
        if "pure" not in fm:
            rel = md.relative_to(VAULT)
            warnings.append(f"WARN P6 pure-field: {rel} pure 필드 누락")
    return warnings


def lint_authoritative_basis() -> list[str]:
    """Governance — AUTHORITATIVE maturity 노트 authoritative_basis 필수."""
    violations = []
    for md in _iter_md_pages():
        text = _read_safe(md)
        fm = _parse_frontmatter(text)
        maturity = str(fm.get("maturity", "")).lower()
        if maturity == "authoritative" and not fm.get("authoritative_basis"):
            rel = md.relative_to(VAULT)
            violations.append(
                f"FAIL governance authoritative-basis: {rel}"
            )
    return violations


def lint_tag_taxonomy() -> list[str]:
    """`.tag_taxonomy.md` 정의 외 태그 사용 시 warn."""
    warnings = []
    tax_path = VAULT / ".tag_taxonomy.md"
    if not tax_path.exists():
        return [f"WARN tag-taxonomy: .tag_taxonomy.md 부재"]
    tax_text = _read_safe(tax_path)
    defined = set()
    for m in re.finditer(r"`#([\w/]+)`", tax_text):
        defined.add(m.group(1))
    if not defined:
        return [f"WARN tag-taxonomy: .tag_taxonomy.md 에서 태그 정의 추출 실패"]
    for md in _iter_md_pages():
        text = _read_safe(md)
        fm = _parse_frontmatter(text)
        tags = fm.get("tags", []) if isinstance(fm.get("tags"), list) else []
        for tag in tags:
            tag = str(tag).strip().lstrip("#")
            if "/" not in tag:
                continue  # bare tag (polaris 등)
            if tag not in defined:
                rel = md.relative_to(VAULT)
                warnings.append(f"WARN tag-taxonomy: {rel} 미정의 태그 '#{tag}'")
                break
    return warnings


# ─────────────────────────── Runner ───────────────────────────


CHECKS = {
    "orphan": ("Karpathy", lint_orphan),
    "stale": ("Karpathy", lint_stale),
    "contradictions": ("Karpathy", lint_contradictions),
    "machine_state_leak": ("Polaris P1", lint_machine_state_leak),
    "expires_required": ("Polaris P2", lint_expires_required),
    "proposed_age": ("Polaris P2", lint_proposed_age),
    "reviewed_by_codex": ("Polaris ADR-004", lint_reviewed_by_codex),
    "pure_field": ("Polaris P6", lint_pure_field),
    "authoritative_basis": ("Polaris Governance", lint_authoritative_basis),
    "tag_taxonomy": ("Polaris Standard", lint_tag_taxonomy),
}


def run_checks(selected: list[str]) -> dict[str, list[str]]:
    return {name: CHECKS[name][1]() for name in selected if name in CHECKS}


def write_report(results: dict[str, list[str]]) -> Path:
    today = _dt.date.today().isoformat()
    report_dir = VAULT / "50_runtime"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"vault_lint_report-{today}.md"
    total_fail = sum(1 for items in results.values() for x in items if x.startswith("FAIL"))
    total_warn = sum(1 for items in results.values() for x in items if x.startswith("WARN"))
    lines = [
        "---",
        "entity_type: lint_report",
        f"entity_id: lint-{today}",
        "auto: true",
        f"last_modified: {today}",
        "expires: never",
        "editable: false",
        'back_links: ["[[_NOW]]"]',
        "mode: meta",
        "reviewed_by: none",
        "tags: [meta, lint, polaris, mode/meta]",
        "---",
        "",
        f"# Vault Lint Report — {today}",
        "",
        f"**Total: FAIL {total_fail} / WARN {total_warn}**",
        "",
    ]
    for name, items in results.items():
        category, _ = CHECKS[name]
        fails = [x for x in items if x.startswith("FAIL")]
        warns = [x for x in items if x.startswith("WARN")]
        lines.append(f"## {name} ({category})")
        lines.append(f"- FAIL: {len(fails)} / WARN: {len(warns)}")
        for x in items:
            lines.append(f"  - {x}")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--karpathy", action="store_true", help="Karpathy 3-type only")
    ap.add_argument("--polaris", action="store_true", help="Polaris contract checks only")
    ap.add_argument("--orphan", action="store_true")
    ap.add_argument("--stale", action="store_true")
    ap.add_argument("--contradictions", action="store_true")
    ap.add_argument("--report", action="store_true", help="write lint report")
    ap.add_argument("--dry-run", action="store_true", help="exit 0 regardless")
    args = ap.parse_args()

    if not VAULT.exists():
        print(f"ERROR: vault directory not found: {VAULT}", file=sys.stderr)
        sys.exit(2)

    karpathy_checks = ["orphan", "stale", "contradictions"]
    polaris_checks = [
        "machine_state_leak",
        "expires_required",
        "proposed_age",
        "reviewed_by_codex",
        "pure_field",
        "authoritative_basis",
        "tag_taxonomy",
    ]

    if args.orphan:
        selected = ["orphan"]
    elif args.stale:
        selected = ["stale"]
    elif args.contradictions:
        selected = ["contradictions"]
    elif args.karpathy:
        selected = karpathy_checks
    elif args.polaris:
        selected = polaris_checks
    else:
        selected = karpathy_checks + polaris_checks

    results = run_checks(selected)

    total_fail = 0
    total_warn = 0
    for name, items in results.items():
        category, _ = CHECKS[name]
        fails = [x for x in items if x.startswith("FAIL")]
        warns = [x for x in items if x.startswith("WARN")]
        total_fail += len(fails)
        total_warn += len(warns)
        status = "PASS" if not fails else "FAIL"
        print(f"[{status}] {name} ({category}): {len(fails)} FAIL / {len(warns)} WARN")
        for x in items:
            print(f"  {x}")

    print(f"\n=== Total: {total_fail} FAIL / {total_warn} WARN ===")

    if args.report:
        path = write_report(results)
        print(f"Report written: {path}")

    if args.dry_run:
        sys.exit(0)
    sys.exit(1 if total_fail > 0 else 0)


if __name__ == "__main__":
    main()
