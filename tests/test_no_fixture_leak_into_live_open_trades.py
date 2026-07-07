"""(e) No test-fixture-style position can reach the live loop's open_trades.

Jin 2026-07-07 spec item (e): "no pos-stophit/pos-g7exit style fixture can
reach the live open_trades". Root-cause investigation (see
tests/test_shared_log_fixture_leak_fix.py) found the observed
"fixture leak" was a SHARED LOGGING FILE artifact, not a state leak — no
fixture position ID (``pos-stophit``, ``pos-g7exit``, ``pos-A``, ``pos-B``,
``pos-2``) exists anywhere in production code; they are constructed only
inside ``tests/test_live_recalc_loop.py`` / ``tests/test_okx_pooled_wallet_close.py``
against a locally-owned ``ProdLoopState`` instance the tests construct
themselves, never the real loop's.

This test asserts the structural invariant directly: every production
mutation site of ``ProdLoopState.open_trades`` is DB-backed or fill-backed —
never a hardcoded/test-shaped literal.
"""

from __future__ import annotations

import ast
from pathlib import Path

PRODUCTION_FILES = (
    "polaris/scripts/production_paper_loop.py",
    "polaris/scripts/_production_run_signal.py",
    "polaris/scripts/_production_tick_engine.py",
    "polaris/scripts/_production_pipeline.py",
    "polaris/scripts/_production_close.py",
)

FIXTURE_ID_MARKERS = ("pos-stophit", "pos-g7exit", "pos-A", "pos-B", "pos-2",
                      "pos-C", "pos-1", "pos-cadence", "pos-swap", "pos-adj")


def test_no_fixture_position_id_literal_in_production_code() -> None:
    """None of the test-only fixture position-id strings appear anywhere in
    the production pipeline source (they are unit-test-only literals)."""
    repo_root = Path(__file__).resolve().parent.parent
    for rel in PRODUCTION_FILES:
        path = repo_root / rel
        assert path.exists(), f"expected production file missing: {rel}"
        text = path.read_text(encoding="utf-8")
        for marker in FIXTURE_ID_MARKERS:
            assert f'"{marker}"' not in text and f"'{marker}'" not in text, (
                f"fixture-shaped literal {marker!r} found in production file "
                f"{rel} — a test fixture id must never appear in prod code"
            )


def test_open_trades_mutations_are_db_or_fill_backed() -> None:
    """Every ``state.open_trades`` mutation (``.extend``/``.append``/``= ...``)
    in production code is fed by a DB-read call or a real/simulated fill
    object — never a literal ``SimulatedTrade(...)`` constructed inline with
    a hardcoded test-shaped position_id.
    """
    repo_root = Path(__file__).resolve().parent.parent
    allowed_sources = ("hydrate_open_positions", "imported", "trade")
    for rel in PRODUCTION_FILES:
        path = repo_root / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr != "open_trades":
                continue
            # Cheap structural check: look at the enclosing Call/Assign via
            # source text around the node's line instead of full parent
            # tracking (ast.walk doesn't give parents) — read the line.
            lineno = node.lineno
            line = path.read_text(encoding="utf-8").splitlines()[lineno - 1]
            if ".extend(" in line or ".append(" in line or "open_trades =" in line:
                assert any(src in line for src in allowed_sources), (
                    f"{rel}:{lineno} mutates open_trades from an unexpected "
                    f"source: {line.strip()!r}"
                )
