"""Static gates folded into the suite: ruff clean + mypy --strict on tools/ops."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_ruff_clean_on_ops() -> None:
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff unavailable")
    proc = subprocess.run(
        [ruff, "check", "tools/ops", "tests/ops"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_mypy_strict_on_ops() -> None:
    mypy = shutil.which("mypy")
    if mypy is None:
        pytest.skip("mypy unavailable")
    proc = subprocess.run(
        [mypy, "--strict", "tools/ops"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
