"""Polaris dashboard v1 — strategy-description queries (vault notes, no DB).

File-based one-line strategy descriptions read from ``vault/20_strategies/*.md``.
Split out of ``snapshot_queries.py`` to keep each module ≤500 LOC (move-only; no
logic change). Display-only — never a trading path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

# Repo root: tools live under <repo>/polaris/scripts/dashboard/ → parents[3].
_STRATEGY_NOTES_DIR: Final[Path] = (
    Path(__file__).resolve().parents[3] / "vault" / "20_strategies"
)
_FRONTMATTER_SID_RE: Final[re.Pattern[str]] = re.compile(
    r"^strategy_id:\s*(.+)$", re.MULTILINE
)


def _one_line_description(text: str) -> str:
    """First non-empty prose line under a ``## Role`` heading, else the H1 title.

    The strategy notes put a one-line role summary under ``## Role``; fall back to
    the ``# …`` H1 (front-matter stripped) when no Role section exists. Returns ''
    when neither is present."""
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if raw.strip().lower().startswith("## role"):
            for follow in lines[i + 1 :]:
                s = follow.strip()
                if not s:
                    continue
                if s.startswith("#"):  # next heading before any prose
                    break
                return s.lstrip("-* ").strip()[:120]
            break
    for raw in lines:
        s = raw.strip()
        if s.startswith("# "):
            return s[2:].strip()[:120]
    return ""


def _strategy_descriptions(
    notes_dir: Path = _STRATEGY_NOTES_DIR,
) -> dict[str, str]:
    """{strategy_id: one-line description} from vault/20_strategies/*.md.

    File-based (no DB). Each note's ``strategy_id`` front-matter key supplies the
    map key (falls back to the file stem); the value is the ``## Role`` one-liner
    (or the H1). Graceful empty when the directory is absent and per-file
    best-effort so a single unreadable note never breaks the map. Display-only."""
    out: dict[str, str] = {}
    if not notes_dir.is_dir():
        return out
    for path in sorted(notes_dir.glob("*.md")):
        if path.name.startswith("MOC-"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _FRONTMATTER_SID_RE.search(text)
        sid = (m.group(1).strip() if m else path.stem).strip()
        desc = _one_line_description(text)
        if sid and desc:
            out[sid] = desc
    return out
