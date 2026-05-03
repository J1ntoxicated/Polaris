"""preg export — 657 _reg() calls → vault/40_preg/<category>/<name>.md per key.

Reads 7 _params_*.py files, extracts _reg() calls via AST, writes one md per key
with frontmatter (default, bound, category, source, defined_in:lineno).

Bidirectional [[]] links: read-by analysis (which modules pget the key).

Run: python -m tools.preg_export
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from tools._md_writer import (
    build_frontmatter,
    now_utc,
    safe_filename,
    write_atomic,
)

PROJECT_ROOT = Path(__file__).parent.parent
PARAMS_DIR = PROJECT_ROOT / "invasion" / "config"
INVASION_ROOT = PROJECT_ROOT / "invasion"
VAULT_PREG = PROJECT_ROOT / "vault" / "40_preg"


def parse_reg_calls(py_path: Path) -> list[dict]:
    """Find every _reg(name, default, (lo, hi), category, source, doc?) call."""
    src = py_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"WARN: parse fail {py_path.name}: {e}")
        return []

    out = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_reg"
            and len(node.args) >= 4
        ):
            continue

        try:
            name = ast.literal_eval(node.args[0])
            default = ast.literal_eval(node.args[1])
            bound_node = node.args[2]
            bound = ast.literal_eval(bound_node) if not (
                isinstance(bound_node, ast.Constant) and bound_node.value is None
            ) else None
            category = ast.literal_eval(node.args[3])
            source = ast.literal_eval(node.args[4]) if len(node.args) > 4 else ""
            doc = ast.literal_eval(node.args[5]) if len(node.args) > 5 else ""
        except (ValueError, TypeError) as e:
            # ast.literal_eval can fail for f-strings, complex expressions
            print(f"WARN: literal_eval fail {py_path.name}:{node.lineno}: {e}")
            continue

        out.append({
            "name": name,
            "default": default,
            "bound": bound,
            "category": category,
            "source": source,
            "doc": doc,
            "defined_in": str(py_path.relative_to(PROJECT_ROOT)),
            "lineno": node.lineno,
        })
    return out


def find_pget_callers(key: str) -> list[str]:
    """Grep for `pget("<key>")` or `preg.get("<key>")` across invasion/.

    Returns list of "file:lineno" references. Uses re for speed (vs full AST walk).
    """
    callers = []
    pattern = re.compile(
        rf'(?:pget|preg\.get|_preg)\(\s*["\']{re.escape(key)}["\']'
    )
    for py in INVASION_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if py.parent == PARAMS_DIR:
            continue  # skip _params_*.py (definition site)
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                callers.append(f"{py.relative_to(PROJECT_ROOT)}:{i}")
    return callers


def write_preg_md(entry: dict, callers: list[str]) -> None:
    """Write `vault/40_preg/<category>/<name>.md`."""
    name = entry["name"]
    category = entry["category"] or "uncategorized"
    out_path = VAULT_PREG / category / f"{safe_filename(name)}.md"

    # Format default + bound for display
    default = entry["default"]
    bound = entry["bound"]
    bound_str = f"({bound[0]}, {bound[1]})" if isinstance(bound, (list, tuple)) and len(bound) == 2 else "None"

    fm = {
        "entity_type": "preg",
        "entity_id": name,
        "auto": True,
        "do_not_edit": True,
        "name": name,
        "default": default,
        "bound": bound,
        "category": category,
        "source": entry["source"],
        "defined_in": entry["defined_in"],
        "lineno": entry["lineno"],
        "read_by_count": len(callers),
        "last_synced": now_utc(),
        "tags": [f"preg/{category}"],
    }
    md = build_frontmatter(fm)
    md += f"\n# preg: `{name}`\n\n"

    if entry["doc"]:
        md += f"> {entry['doc']}\n\n"

    md += f"**Default**: `{default}`  |  **Bound**: `{bound_str}`  |  **Category**: `{category}`\n\n"
    md += f"**Defined in**: `{entry['defined_in']}:{entry['lineno']}`  \n"
    md += f"**Source ref**: `{entry['source']}`\n\n"

    md += "## Read by\n\n"
    if callers:
        for c in callers[:20]:
            md += f"- `{c}`\n"
        if len(callers) > 20:
            md += f"- ... +{len(callers)-20} more\n"
    else:
        md += "_(no caller found via grep — may be read indirectly via `_preg(name_var)` dynamic key)_\n"

    md += "\n## Related\n\n"
    md += f"- Category index: `[[40_preg/{category}/_index]]`\n"
    md += "- See `[[_meta/sqlite_mcp_query_cookbook]]#Q5` for runtime override history\n"

    write_atomic(out_path, md)


def write_category_index(category: str, entries: list[dict]) -> None:
    """`vault/40_preg/<category>/_index.md` — Dataview rollup."""
    out_path = VAULT_PREG / category / "_index.md"
    fm = {
        "entity_type": "index",
        "entity_id": f"preg_{category}_index",
        "auto": True,
        "do_not_edit": True,
        "category": category,
        "key_count": len(entries),
        "last_synced": now_utc(),
        "tags": [f"preg/{category}", "index"],
    }
    md = build_frontmatter(fm)
    md += f"\n# preg category: `{category}`\n\n"
    md += f"> {len(entries)} keys in this category.\n\n"
    md += "## Keys\n\n"
    md += "```dataview\n"
    md += "TABLE default, bound, source, read_by_count\n"
    md += f'FROM "40_preg/{category}"\n'
    md += "WHERE entity_type = \"preg\"\n"
    md += "SORT name ASC\n"
    md += "```\n\n"
    md += "## Manual list (Dataview unavailable fallback)\n\n"
    for e in sorted(entries, key=lambda x: x["name"]):
        md += f"- `{e['name']}` — default `{e['default']}`, bound `{e['bound']}`\n"
    write_atomic(out_path, md)


def write_root_index(all_entries: list[dict]) -> None:
    """`vault/40_preg/_index.md` — top-level rollup."""
    by_cat: dict[str, list] = {}
    for e in all_entries:
        cat = e["category"] or "uncategorized"
        by_cat.setdefault(cat, []).append(e)

    fm = {
        "entity_type": "index",
        "entity_id": "preg_root_index",
        "auto": True,
        "do_not_edit": True,
        "total_keys": len(all_entries),
        "categories": list(by_cat.keys()),
        "last_synced": now_utc(),
        "tags": ["preg", "index", "root"],
    }
    md = build_frontmatter(fm)
    md += f"\n# preg Root Index\n\n"
    md += f"> {len(all_entries)} total preg keys across {len(by_cat)} categories.\n\n"
    md += "## Categories\n\n"
    for cat in sorted(by_cat.keys()):
        md += f"- `[[40_preg/{cat}/_index|{cat}]]` — {len(by_cat[cat])} keys\n"
    md += "\n## Live Queries\n\n"
    md += "- Recent overrides (24h): see `[[_meta/sqlite_mcp_query_cookbook#Q5]]`\n"
    md += "- 북극성 invariant REJECT (amplify-only protection): see `[[_meta/sqlite_mcp_query_cookbook#Q10]]`\n"

    write_atomic(VAULT_PREG / "_index.md", md)


def export_all() -> dict:
    """Main entry."""
    all_entries: list[dict] = []
    for params_file in sorted(PARAMS_DIR.glob("_params_*.py")):
        entries = parse_reg_calls(params_file)
        all_entries.extend(entries)

    # Group by category
    by_cat: dict[str, list] = {}
    for e in all_entries:
        cat = e["category"] or "uncategorized"
        by_cat.setdefault(cat, []).append(e)

    # Write per-key md (with caller analysis)
    for entry in all_entries:
        callers = find_pget_callers(entry["name"])
        write_preg_md(entry, callers)

    # Per-category index
    for cat, entries in by_cat.items():
        write_category_index(cat, entries)

    # Root index
    write_root_index(all_entries)

    return {
        "total_keys": len(all_entries),
        "categories": {cat: len(items) for cat, items in by_cat.items()},
    }


if __name__ == "__main__":
    import time
    start = time.time()
    result = export_all()
    elapsed = time.time() - start
    print(f"[preg_export] done @ {now_utc()}")
    print(f"  Total keys: {result['total_keys']}")
    print(f"  Categories: {result['categories']}")
    print(f"  Elapsed: {elapsed:.2f}s")
