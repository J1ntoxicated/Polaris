"""Code AST export — invasion/**/*.py → vault/30_code/<module>.md

Each Python module → entity markdown with:
- Frontmatter: file_path, module name, sha, classes, functions, imports, log_events, preg_reads
- Body: function list, [[]] links to imports (other modules), preg_reads, log_events
- Bidirectional via crosslink: 'Mentioned in' section auto-injected later

Run: python -m tools.code_ast_export
"""
from __future__ import annotations

import ast
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from tools._md_writer import (
    build_frontmatter,
    now_utc,
    safe_filename,
    write_atomic,
)

PROJECT_ROOT = Path(__file__).parent.parent
INVASION_ROOT = PROJECT_ROOT / "invasion"
VAULT_CODE = PROJECT_ROOT / "vault" / "30_code"

# preg call patterns (for read_by detection)
PREG_PATTERNS = [
    re.compile(r'(?:pget|preg\.get|_preg)\s*\(\s*["\']([^"\']+)["\']'),
]


def module_name(py_path: Path) -> str:
    """invasion/strategy/cell_matrix.py → invasion.strategy.cell_matrix"""
    rel = py_path.relative_to(PROJECT_ROOT)
    return str(rel).replace("/", ".").replace(".py", "")


def vault_module_path(py_path: Path) -> Path:
    """invasion/strategy/cell_matrix.py → vault/30_code/strategy/cell_matrix.md"""
    rel = py_path.relative_to(INVASION_ROOT)
    return VAULT_CODE / rel.with_suffix(".md")


def extract_imports(tree: ast.AST, current_module: str) -> list[str]:
    """List of imported invasion.* modules (relative + absolute resolved).

    current_module: e.g., 'invasion.strategy.cell_matrix'
    Relative imports resolved using current module path + level.
    """
    imports: set[str] = set()
    parts = current_module.split(".")  # ['invasion', 'strategy', 'cell_matrix']

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("invasion"):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            level = node.level
            module = node.module or ""

            if level == 0 and module.startswith("invasion"):
                imports.add(module)
            elif level >= 1:
                # Relative import: from .x.y import z OR from .. import x
                # Resolve: drop `level` parts from current, append module
                base = parts[:-level] if level <= len(parts) else parts[:1]
                if module:
                    resolved = ".".join(base) + "." + module
                else:
                    resolved = ".".join(base)
                if resolved.startswith("invasion") and resolved != current_module:
                    imports.add(resolved)
                # Also add specific imported names (from .x import y → invasion...x.y)
                for alias in node.names:
                    if alias.name != "*":
                        leaf = resolved + "." + alias.name
                        if leaf.startswith("invasion"):
                            imports.add(leaf)
    return sorted(imports)


def extract_classes(tree: ast.AST) -> list[dict]:
    """Class names + line + docstring first line."""
    out = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            out.append({
                "name": node.name,
                "lineno": node.lineno,
                "doc_first_line": doc.split("\n")[0] if doc else "",
            })
    return out


def extract_functions(tree: ast.AST) -> list[dict]:
    """Top-level functions + class methods."""
    out = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            doc = ast.get_docstring(node) or ""
            out.append({
                "name": node.name,
                "lineno": node.lineno,
                "doc_first_line": doc.split("\n")[0] if doc else "",
                "is_method": False,
            })
        elif isinstance(node, ast.ClassDef):
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, ast.FunctionDef):
                    doc = ast.get_docstring(sub) or ""
                    out.append({
                        "name": f"{node.name}.{sub.name}",
                        "lineno": sub.lineno,
                        "doc_first_line": doc.split("\n")[0] if doc else "",
                        "is_method": True,
                    })
    return out


def extract_log_channels(src: str) -> list[str]:
    """log_event("CHANNEL", ...) → unique channel names."""
    channels: set[str] = set()
    pattern = re.compile(r'log_event\s*\(\s*["\']([A-Z_]+)["\']')
    for match in pattern.finditer(src):
        channels.add(match.group(1))
    return sorted(channels)


def extract_preg_reads(src: str) -> list[str]:
    """pget("key") / preg.get("key") / _preg("key") → unique key names."""
    keys: set[str] = set()
    for pat in PREG_PATTERNS:
        for match in pat.finditer(src):
            keys.add(match.group(1))
    return sorted(keys)


def extract_module_doc(tree: ast.AST) -> str:
    """Module docstring first paragraph."""
    doc = ast.get_docstring(tree) or ""
    # First paragraph (until blank line)
    paragraphs = doc.split("\n\n", 1)
    return paragraphs[0] if paragraphs else ""


def analyze_module(py_path: Path) -> dict:
    """Full analysis of a Python module."""
    src = py_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"error": str(e), "file_path": str(py_path.relative_to(PROJECT_ROOT))}

    mod_name = module_name(py_path)
    return {
        "file_path": str(py_path.relative_to(PROJECT_ROOT)),
        "module": mod_name,
        "module_doc": extract_module_doc(tree),
        "classes": extract_classes(tree),
        "functions": extract_functions(tree),
        "imports": extract_imports(tree, mod_name),
        "log_channels": extract_log_channels(src),
        "preg_reads": extract_preg_reads(src),
        "line_count": src.count("\n"),
        "sha": hashlib.sha1(src.encode()).hexdigest()[:12],
        "last_modified": datetime.fromtimestamp(
            py_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def write_module_md(info: dict) -> None:
    """Write vault/30_code/<rel_path>.md."""
    if "error" in info:
        return

    py_path = PROJECT_ROOT / info["file_path"]
    md_path = vault_module_path(py_path)
    short_name = py_path.stem  # filename w/o .py

    # Imports → wikilinks (resolve invasion.x.y → x.y short name for cleaner links)
    import_links = []
    for imp in info["imports"]:
        # invasion.strategy.cell_matrix → cell_matrix
        short = imp.split(".")[-1] if "." in imp else imp
        import_links.append(f"[[{short}]]")

    fm = {
        "entity_type": "code_module",
        "entity_id": short_name,  # short name for [[]] matching
        "auto": True,
        "do_not_edit": True,
        "module": info["module"],
        "file_path": info["file_path"],
        "sha": info["sha"],
        "line_count": info["line_count"],
        "last_modified": info["last_modified"],
        "last_synced": now_utc(),
        "class_count": len(info["classes"]),
        "function_count": len(info["functions"]),
        "import_count": len(info["imports"]),
        "log_channels": info["log_channels"],
        "preg_reads": info["preg_reads"],
        "tags": ["code", f"code/{py_path.parent.name}"],
    }

    md = build_frontmatter(fm)
    md += f"\n# `{info['module']}`\n\n"
    md += f"**File**: `{info['file_path']}` ({info['line_count']} LOC, sha {info['sha']})\n\n"

    if info["module_doc"]:
        md += f"> {info['module_doc']}\n\n"

    # Classes
    if info["classes"]:
        md += "## Classes\n\n"
        for c in info["classes"]:
            doc = f" — {c['doc_first_line']}" if c["doc_first_line"] else ""
            md += f"- `{c['name']}` (L{c['lineno']}){doc}\n"
        md += "\n"

    # Functions (top-level + methods)
    if info["functions"]:
        md += f"## Functions ({len(info['functions'])})\n\n"
        for f in info["functions"][:30]:
            prefix = "🔧 " if f["is_method"] else "ƒ "
            doc = f" — {f['doc_first_line']}" if f["doc_first_line"] else ""
            md += f"- {prefix}`{f['name']}` (L{f['lineno']}){doc}\n"
        if len(info["functions"]) > 30:
            md += f"- ... +{len(info['functions'])-30} more\n"
        md += "\n"

    # Imports → entity wikilinks
    if info["imports"]:
        md += "## Imports (invasion.*)\n\n"
        for imp, link in zip(info["imports"], import_links):
            md += f"- {link} (`{imp}`)\n"
        md += "\n"

    # Log channels emitted
    if info["log_channels"]:
        md += "## Log channels emitted\n\n"
        for ch in info["log_channels"]:
            md += f"- `[{ch}]`\n"
        md += "\n"

    # Preg keys read
    if info["preg_reads"]:
        md += f"## Preg keys read ({len(info['preg_reads'])})\n\n"
        for key in info["preg_reads"][:20]:
            md += f"- [[{key}]]\n"
        if len(info["preg_reads"]) > 20:
            md += f"- ... +{len(info['preg_reads'])-20} more\n"
        md += "\n"

    # Mermaid mini-graph (imports — visual pipeline)
    if info["imports"]:
        md += "## Pipeline Graph (imports)\n\n"
        md += "```mermaid\ngraph LR\n"
        node = short_name.replace("-", "_")
        md += f"    {node}[\"{short_name}\"]\n"
        # Show top 10 imports max for readability
        for imp in info["imports"][:10]:
            target = imp.split(".")[-1].replace("-", "_")
            if target == node:
                continue
            md += f"    {node} --> {target}\n"
        if len(info["imports"]) > 10:
            md += f"    {node} -.-> more[\"+{len(info['imports'])-10} more\"]\n"
        md += "```\n\n"

    md += "## See also\n\n"
    md += "- [[architecture_map]] — system overview\n"
    md += "- [[canonical_files]] — SSOT 매핑\n"
    md += "- [[ARCHITECTURE]] — Bot architecture\n"

    write_atomic(md_path, md)


def write_index() -> None:
    """vault/30_code/_index.md — top-level rollup."""
    fm = {
        "entity_type": "index",
        "entity_id": "code_root_index",
        "auto": True,
        "do_not_edit": True,
        "last_synced": now_utc(),
        "tags": ["code", "index", "root"],
    }
    md = build_frontmatter(fm)
    md += "\n# Code Modules Index\n\n"
    md += "> Auto-extracted from `invasion/**/*.py`. ~388 modules.\n\n"
    md += "## By Subdirectory\n\n"
    md += "```dataview\n"
    md += "TABLE entity_id, line_count, function_count, import_count\n"
    md += 'FROM "30_code"\n'
    md += "WHERE entity_type = \"code_module\"\n"
    md += "SORT line_count DESC\n"
    md += "LIMIT 50\n"
    md += "```\n\n"
    md += "## See also\n\n"
    md += "- [[architecture_map]] — full system map\n"
    md += "- [[ARCHITECTURE]] — design overview\n"
    md += "- [[canonical_files]] — SSOT\n"
    md += "- [[40_preg/_index|Preg keys index]] — 657 params\n"

    write_atomic(VAULT_CODE / "_index.md", md)


def build_imported_by_map(all_infos: list[dict]) -> dict[str, list[str]]:
    """Reverse map: module → list of modules that import it.

    Pipeline integration view — Jin mandate.
    """
    imported_by: dict[str, list[str]] = {}
    for info in all_infos:
        importer_short = Path(info["file_path"]).stem
        for imp in info["imports"]:
            # invasion.strategy.cell_matrix → cell_matrix
            target = imp.split(".")[-1]
            if target not in imported_by:
                imported_by[target] = []
            if importer_short not in imported_by[target]:
                imported_by[target].append(importer_short)
    return imported_by


def append_imported_by(info: dict, callers: list[str]) -> None:
    """Append 'Imported by' section to module md (after first pass)."""
    py_path = PROJECT_ROOT / info["file_path"]
    md_path = vault_module_path(py_path)
    if not md_path.exists():
        return
    text = md_path.read_text(encoding="utf-8")

    # Marker for idempotent injection
    MARKER = "## Imported by (Pipeline integration)"
    if MARKER in text:
        # Replace existing section
        before, _, _ = text.partition(MARKER)
        text = before.rstrip()
    else:
        text = text.rstrip()

    section = f"\n\n{MARKER}\n\n"
    if callers:
        section += f"_{len(callers)} module(s) import this:_\n\n"
        for c in sorted(callers)[:30]:
            section += f"- [[{c}]]\n"
        if len(callers) > 30:
            section += f"- ... +{len(callers) - 30} more\n"
    else:
        section += "_(no importers — top-level entry point or external API)_\n"

    text = text + section + "\n"

    # atomic write
    tmp = md_path.with_suffix(md_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(md_path)


def export_all() -> dict:
    """Walk invasion/, write all module md (2-pass for imported_by)."""
    count = 0
    errors = 0
    skipped_init = 0
    all_infos: list[dict] = []

    # Pass 1: extract + write all modules
    for py in INVASION_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if py.name == "__init__.py":
            try:
                if py.stat().st_size < 200:
                    skipped_init += 1
                    continue
            except Exception:
                pass

        info = analyze_module(py)
        if "error" in info:
            errors += 1
            continue

        write_module_md(info)
        all_infos.append(info)
        count += 1

    # Pass 2: build reverse map + inject 'Imported by' sections (pipeline view)
    imported_by = build_imported_by_map(all_infos)
    for info in all_infos:
        short = Path(info["file_path"]).stem
        callers = imported_by.get(short, [])
        append_imported_by(info, callers)

    write_index()

    return {
        "modules_written": count,
        "errors": errors,
        "skipped_empty_init": skipped_init,
        "imported_by_links": sum(len(v) for v in imported_by.values()),
    }


if __name__ == "__main__":
    import time
    start = time.time()
    result = export_all()
    elapsed = time.time() - start
    print(f"[code_ast] done @ {now_utc()}")
    print(f"  Modules written: {result['modules_written']}")
    print(f"  Imported-by links (pipeline): {result['imported_by_links']}")
    print(f"  Errors: {result['errors']}")
    print(f"  Skipped empty __init__: {result['skipped_empty_init']}")
    print(f"  Elapsed: {elapsed:.2f}s")
