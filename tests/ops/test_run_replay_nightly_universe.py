"""``scripts/run_replay_nightly.sh`` must not hardcode a ticker default.

Root cause this pins: the wrapper used to fall back to a literal instrument
list (``okx:BTC-USDT okx:ETH-USDT okx:ADA-USDT``) whenever
``POLARIS_REPLAY_INSTRUMENTS`` was unset — the exact static-universe
anti-pattern Layer 0's dynamic discovery exists to prevent. It now defers to
``run_replay --top-n-active`` (DB-driven, ``polaris.scripts.run_replay.
resolve_top_n_active_instruments``) whenever no explicit override is given.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "run_replay_nightly.sh"

# venue:SYMBOL-USDT style literal (the shape of the old hardcoded default).
_HARDCODED_INSTRUMENT_RE = re.compile(r"\bokx:[A-Z]{2,10}-USDT\b")


def test_script_exists_and_syntax_ok() -> None:
    assert SCRIPT.exists()
    proc = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def test_no_hardcoded_instrument_default() -> None:
    """No literal venue:SYMBOL instrument id anywhere in the script — the only
    acceptable ticker mentions are inside comments documenting the OLD
    behaviour being removed, so this asserts the CODE (non-comment lines)
    is clean."""
    code_lines = [
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    ]
    code_text = "\n".join(code_lines)
    assert not _HARDCODED_INSTRUMENT_RE.search(code_text), (
        "found a hardcoded venue:symbol instrument literal in "
        "run_replay_nightly.sh — the nightly universe must come from the DB "
        "(--top-n-active), never a pinned symbol"
    )


def test_falls_back_to_top_n_active_not_a_pinned_default() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--top-n-active" in text
    # The old unconditional bash-parameter-expansion default is gone.
    assert 'INSTRUMENTS="${POLARIS_REPLAY_INSTRUMENTS:-}"' in text
    assert "POLARIS_REPLAY_TOP_N" in text
