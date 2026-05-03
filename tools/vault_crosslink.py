"""vault crosslink — backlink injection.

Scans all vault/*.md for [[entity_id]] references and adds 'Mentioned in:'
section to each entity page. Closes the satellite gap — every node sees
which other nodes reference it.

Run: python -m tools.vault_crosslink
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
VAULT = PROJECT_ROOT / "vault"

# [[entity_id]] or [[entity_id|alias]] — extract entity_id only
WIKILINK = re.compile(r"\[\[([^\[\]|#]+?)(?:\|[^\[\]]+)?(?:#[^\[\]]+)?\]\]")


def scan_wikilinks() -> dict[str, list[str]]:
    """Scan vault for [[X]] mentions → return {target: [list of source files]}.

    Includes BOTH non-symlink markdown AND symlinked source files (전수조사).
    Symlinks resolved via Path.resolve() — Jin requested comprehensive scan.
    """
    backlinks: dict[str, list[str]] = defaultdict(list)
    for md in VAULT.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in WIKILINK.finditer(text):
            target = match.group(1).strip()
            target_clean = target.rsplit("/", 1)[-1].strip()
            source = str(md.relative_to(VAULT))
            if source not in backlinks[target_clean]:
                backlinks[target_clean].append(source)
    return backlinks


def scan_plaintext_mentions(entity_names: set[str]) -> dict[str, list[str]]:
    """Scan ALL vault md (including symlink targets) for plain-text entity names.

    Jin 전수조사 mandate: lessons.md / harness_items.md / agent docs 등 symlink
    source 들이 [[]] 안 쓰고 plain text 로 BZ 언급해도 detect.

    Returns {entity_name: [source files]}.
    Excludes: own entity page, _index.md (frontmatter pollution).
    """
    mentions: dict[str, list[str]] = defaultdict(list)
    if not entity_names:
        return mentions

    # Word boundary regex per entity — case-sensitive for tickers/strategies
    # (BZ, BTC etc are case-sensitive symbols)
    patterns = {
        name: re.compile(rf'\b{re.escape(name)}\b')
        for name in entity_names
    }

    for md in VAULT.rglob("*.md"):
        # Skip _index.md and auto-generated entity pages (own backlinks not useful)
        if md.name.startswith("_index") or md.stem in entity_names:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue

        # Strip [[wikilink]] content — those are already captured by scan_wikilinks
        text_no_wl = WIKILINK.sub(" ", text)
        # Strip frontmatter — frontmatter mentions are metadata not real references
        if text_no_wl.startswith("---\n"):
            try:
                fm_end = text_no_wl.index("\n---\n", 4)
                text_no_wl = text_no_wl[fm_end + 5:]
            except ValueError:
                pass

        source = str(md.relative_to(VAULT))
        for name, pat in patterns.items():
            if pat.search(text_no_wl):
                if source not in mentions[name]:
                    mentions[name].append(source)
    return mentions


def find_entity_paths() -> dict[str, Path]:
    """Map entity_id → vault path. ALL vault md treated as potential entities.

    Jin mandate (2026-04-26 final): "모든 게 다 연결" — all symlinks (north_star,
    feedback_*, architecture, agents, skills, plans) included so they get backlinks.
    """
    paths: dict[str, Path] = {}
    # Process in priority order — auto-generated entities win over symlinks
    canonical_dirs = [
        # Auto-generated canonical (first priority)
        "02_live/strategies", "02_live/tickers", "04_signals",
        "30_code", "40_preg",
        "60_exit_patterns", "70_regimes", "80_decisions",
        "90_harness/insights", "90_harness/digests", "90_harness/self_inspection",
        "90_harness/audit",
        # Symlinks (lower priority — included so they get backlinks too)
        "00_north_star", "10_lessons", "20_architecture",
        "_meta",
    ]
    for d in canonical_dirs:
        target = VAULT / d
        if not target.exists():
            continue
        for md in target.rglob("*.md"):
            stem = md.stem
            if stem.startswith("_"):
                continue
            if stem not in paths:
                paths[stem] = md
    return paths


# Marker for auto-injected backlink section
MARKER_START = "<!-- AUTO_BACKLINKS_START -->"
MARKER_END = "<!-- AUTO_BACKLINKS_END -->"


def inject_backlinks(path: Path, sources: list[str]) -> bool:
    """Append/update '## Mentioned in' section in entity md.

    Idempotent — replaces existing AUTO_BACKLINKS block.
    Returns True if file was modified.
    """
    text = path.read_text(encoding="utf-8")

    # Build new backlinks block
    block = f"\n{MARKER_START}\n## Mentioned in (vault backlinks)\n\n"
    if not sources:
        block += "_(no current mentions)_\n"
    else:
        # Group by folder for readability
        by_folder: dict[str, list[str]] = defaultdict(list)
        for s in sorted(sources):
            folder = s.rsplit("/", 1)[0] if "/" in s else "(root)"
            by_folder[folder].append(s)
        for folder, items in sorted(by_folder.items()):
            block += f"**{folder}**:\n"
            for item in items:
                stem = Path(item).stem
                block += f"- `[[{item.replace('.md', '')}|{stem}]]`\n"
            block += "\n"
    block += f"{MARKER_END}\n"

    # Replace existing block or append
    pattern = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(text):
        new_text = pattern.sub(block.strip("\n") + "\n", text)
    else:
        new_text = text.rstrip() + "\n" + block

    if new_text == text:
        return False

    # Atomic write
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return True


def inject_combined_backlinks(path: Path, wl_sources: list[str], pt_sources: list[str]) -> bool:
    """Combined: [[wikilinks]] + plaintext mentions.

    Both grouped separately for clarity.
    """
    text = path.read_text(encoding="utf-8")

    block = f"\n{MARKER_START}\n## Mentioned in (vault backlinks)\n\n"

    if wl_sources:
        block += "### Wikilinks (`[[]]` 직접 참조)\n\n"
        by_folder: dict[str, list[str]] = defaultdict(list)
        for s in sorted(wl_sources):
            folder = s.rsplit("/", 1)[0] if "/" in s else "(root)"
            by_folder[folder].append(s)
        for folder, items in sorted(by_folder.items()):
            block += f"**{folder}**:\n"
            for item in items:
                stem = Path(item).stem
                block += f"- `[[{item.replace('.md', '')}|{stem}]]`\n"
            block += "\n"

    if pt_sources:
        block += "### Plain-text mentions (전수조사)\n\n"
        by_folder2: dict[str, list[str]] = defaultdict(list)
        for s in sorted(pt_sources):
            if s in wl_sources:
                continue  # already in wikilinks section
            folder = s.rsplit("/", 1)[0] if "/" in s else "(root)"
            by_folder2[folder].append(s)
        if not by_folder2:
            block += "_(all mentions are wikilinks above)_\n"
        else:
            for folder, items in sorted(by_folder2.items()):
                block += f"**{folder}**:\n"
                for item in items:
                    stem = Path(item).stem
                    block += f"- `[[{item.replace('.md', '')}|{stem}]]`\n"
                block += "\n"

    if not wl_sources and not pt_sources:
        block += "_(no current mentions)_\n"

    block += f"{MARKER_END}\n"

    pattern = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(text):
        new_text = pattern.sub(block.strip("\n") + "\n", text)
    else:
        new_text = text.rstrip() + "\n" + block

    if new_text == text:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return True


def main() -> dict:
    backlinks_wl = scan_wikilinks()
    entity_paths = find_entity_paths()
    entity_names = set(entity_paths.keys())

    # Plain-text 전수조사 (Jin mandate)
    backlinks_pt = scan_plaintext_mentions(entity_names)

    updated = 0
    skipped = 0
    pt_extra = 0  # plain-text mentions found

    for entity_id, path in entity_paths.items():
        wl_sources = backlinks_wl.get(entity_id, [])
        pt_sources = backlinks_pt.get(entity_id, [])
        # Filter self-reference
        self_rel = str(path.relative_to(VAULT))
        wl_sources = [s for s in wl_sources if s != self_rel]
        pt_sources = [s for s in pt_sources if s != self_rel]
        # Plain-text 가 wikilink set 에 없는 것만 extra
        pt_only = [s for s in pt_sources if s not in wl_sources]
        pt_extra += len(pt_only)

        if inject_combined_backlinks(path, wl_sources, pt_sources):
            updated += 1
        else:
            skipped += 1

    no_match = sum(1 for t in backlinks_wl if t not in entity_paths)

    return {
        "entities_total": len(entity_paths),
        "updated": updated,
        "skipped": skipped,
        "satellites": no_match,
        "wikilinks_total": sum(len(s) for s in backlinks_wl.values()),
        "plaintext_extra": pt_extra,  # plain-text only (no wikilink)
    }


if __name__ == "__main__":
    import time
    start = time.time()
    result = main()
    elapsed = time.time() - start
    print(f"[crosslink] done @ {elapsed:.2f}s")
    print(f"  Entities: {result['entities_total']}")
    print(f"  Updated: {result['updated']}")
    print(f"  Skipped (no change): {result['skipped']}")
    print(f"  Satellite mentions (no entity): {result['satellites']}")
    print(f"  Wikilink backlinks: {result['wikilinks_total']}")
    print(f"  Plain-text extra (전수조사): {result['plaintext_extra']}")
