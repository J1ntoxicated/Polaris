"""Regression: no test may leave the root logger pointed at a real file after
it finishes (Jin 2026-07-07 "smoke harness fixture leak" root-cause fix).

Evidence this fixes: ``data/paper/polaris_runtime.log`` (the LIVE bot's
shared runtime log — ``DEFAULT_LOG_FILE`` in ``polaris.logging_config``) was
interleaved with fixture position IDs (``pos-stophit``/``pos-g7exit``/
``pos-2``/``pos-B``) that exist ONLY inside unit-test files
(``tests/test_live_recalc_loop.py`` / ``tests/test_okx_pooled_wallet_close.py``)
which never call ``ignite_p1``/``run_production_paper_loop`` at all — those
tests operate purely on an in-memory ``memdb``. The pollution mechanism was
``ignite_p1.main()`` defaulting ``--log-file`` to ``DEFAULT_LOG_FILE`` and
calling ``setup_polaris_logging(..., force=True)``, which replaces the
process-global root-logger handlers with NO teardown — so a LATER unit
test's ``logger.info(...)`` (including fixture-seeded position IDs used only
for assertions on an in-memory DB) got appended to the live bot's shared log
file for the rest of the pytest process. Fixed by the suite-wide autouse
``_reset_root_logger_handlers`` fixture in ``tests/conftest.py``.

SAFETY: this test NEVER writes to the real ``data/paper/polaris_runtime.log``
— it ``monkeypatch.chdir``s into ``tmp_path`` first so ``ignite_p1.main()``'s
relative ``DEFAULT_LOG_FILE`` default resolves inside the tmp tree.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from polaris.scripts.ignite_p1 import main
from polaris.storage.schema import init_db


def test_ignite_main_default_log_file_leak_is_cleaned_up_after_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the exact leak mechanism inside an isolated tmp cwd (never
    the real repo), then proves the SAME test's root-logger FileHandler
    exists mid-test (the leak fires) — the suite-wide teardown invariant
    that it does NOT survive to the NEXT test is asserted by the sibling
    test below (pytest preserves file order).
    """
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "polaris.sqlite"
    init_db(db_path).close()

    # No --log-file passed: argparse fills in DEFAULT_LOG_FILE
    # ("data/paper/polaris_runtime.log"), relative to the tmp cwd above.
    rc = main(["--db", str(db_path)])
    assert rc == 0

    file_handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler)
    ]
    assert file_handlers, (
        "ignite_p1.main() without --log-file must install a FileHandler "
        "(proves the leak mechanism fires so the teardown fixture in "
        "tests/conftest.py has real work to do)"
    )
    assert (tmp_path / "data" / "paper" / "polaris_runtime.log").exists()


def test_root_logger_has_no_file_handler_after_prior_test() -> None:
    """Runs AFTER the leak-triggering test above (pytest file order) —
    proves the suite-wide autouse teardown actually cleaned up: this
    unrelated test's root logger must carry NO FileHandler left over.
    """
    file_handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler)
    ]
    assert file_handlers == [], (
        "a FileHandler survived from a prior test — the shared-log fixture "
        "leak is NOT fixed (root logger handlers must be reset per-test)"
    )
