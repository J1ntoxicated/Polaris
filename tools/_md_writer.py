"""Atomic markdown writer + frontmatter helpers — MVP writer contract.

All vault file writes go through write_atomic() to prevent Obsidian seeing partial writes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_frontmatter(meta: dict[str, Any]) -> str:
    """yaml frontmatter — entity schema 표준.

    list/dict values are JSON-serialized for portability.
    """
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, (list, dict)):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


def write_atomic(path: Path, content: str) -> None:
    """POSIX atomic rename — Obsidian 은 partial write 못 봄.

    1. parent dir 자동 생성
    2. .tmp 파일에 write
    3. atomic rename (POSIX guarantees)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def now_utc() -> str:
    """Canonical timestamp — Australia/Sydney 환경이지만 stored 는 ISO8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


def markdown_table(rows: list, cols: list[str]) -> str:
    """sqlite Row list → markdown table.

    rows: list of sqlite3.Row or dict-like.
    cols: explicit column order.
    """
    if not rows:
        return "_(no data)_\n"
    out = "| " + " | ".join(cols) + " |\n"
    out += "|" + "|".join("---" for _ in cols) + "|\n"
    for r in rows:
        cells = []
        for c in cols:
            v = r[c] if hasattr(r, "__getitem__") else getattr(r, c, None)
            if v is None:
                cells.append("")
            elif isinstance(v, float):
                cells.append(f"{v:.4f}".rstrip("0").rstrip("."))
            else:
                cells.append(str(v))
        out += "| " + " | ".join(cells) + " |\n"
    return out


def safe_filename(name: str) -> str:
    """Make a string safe for use as a filename.

    Replaces / \\ : * ? " < > | with underscore.
    """
    bad_chars = '/\\:*?"<>|'
    for c in bad_chars:
        name = name.replace(c, "_")
    return name.strip()
