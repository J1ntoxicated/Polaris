"""Layer-direction guard — ``polaris.core`` must not import UP into scripts/venues.

DEMO/PAPER only. This is a STRUCTURE guard, not a runtime gate — it blocks no
flow and never touches sizing / the -1.0R rail / the 9-stack lock. ``core`` is the
bottom layer: the live orchestrator (``polaris.scripts``) and the venue adapters
(``polaris.venues``) depend on it, never the reverse. A ``core → scripts`` or
``core → venues`` import is a layer INVERSION (an import cycle waiting to happen,
and a replay/test seam that drags the whole live stack in).

The three inversions this seals (all resolved by MOVE-ONLY relocations into core,
with re-export shims so every existing caller stays byte-identical):
  * ``core/lifecycle/recover.py`` → ``SimulatedTrade`` (now ``core.lifecycle.trade``).
  * ``core/replay/engine.py`` → indicators + loser-timeout (now in ``core``).
  * ``core/sizing/session.py`` + ``core/pipeline/agents/_stream_guards.py`` →
    the equity session clock (now ``core.sessions.equity_session_gate``).
"""

from __future__ import annotations

import ast
from pathlib import Path

_CORE_ROOT = Path(__file__).resolve().parents[1] / "polaris" / "core"
_FORBIDDEN_PREFIXES = ("polaris.scripts", "polaris.venues")


def _imported_modules(py_file: Path) -> set[str]:
    """Every fully-qualified module name imported by ``py_file`` (static parse)."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_core_never_imports_scripts_or_venues() -> None:
    offenders: list[str] = []
    for py_file in sorted(_CORE_ROOT.rglob("*.py")):
        for mod in _imported_modules(py_file):
            if mod.startswith(_FORBIDDEN_PREFIXES):
                rel = py_file.relative_to(_CORE_ROOT.parents[1])
                offenders.append(f"{rel} imports {mod}")
    assert not offenders, (
        "polaris.core imports UP into scripts/venues (layer inversion):\n  "
        + "\n  ".join(offenders)
    )
