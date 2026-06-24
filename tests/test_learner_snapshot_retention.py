"""Retention guard for on-disk learner snapshots (2026-06 disk-full incident).

The operator-facing ``<ts>.db`` hot backups (one full ~250 MB DB copy each)
accumulated UNBOUNDED to 20 GB / 722 files because nothing pruned old ones.
``_prune_disk_snapshots`` keeps only the newest N generations; the in-DB
``learner_snapshot`` row remains the rollback SSOT, so dropping old disk
generations is safe. These tests pin that behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

from polaris.core.learners._primitives import LEARNER_SNAPSHOT_KEEP
from polaris.core.learners.base import _prune_disk_snapshots


def _make_generation(base: Path, ts: int, *, mtime: int) -> None:
    """Create one snapshot generation: a ``<ts>.db`` + sibling manifests."""
    for name in (f"{ts}.db", f"{ts}.max_hold.json", f"{ts}.regime_mult.json"):
        p = base / name
        p.write_bytes(b"x")
        os.utime(p, (mtime, mtime))


def test_prune_keeps_newest_and_drops_older(tmp_path: Path) -> None:
    timestamps = [100, 200, 300, 400, 500, 600, 700, 800]
    for i, ts in enumerate(timestamps):
        _make_generation(tmp_path, ts, mtime=1_000_000 + i)  # ts=800 newest

    _prune_disk_snapshots(tmp_path, keep=3)

    assert sorted(p.stem for p in tmp_path.glob("*.db")) == ["600", "700", "800"]
    # Sibling manifests of pruned generations go with the .db; kept ones stay.
    assert not (tmp_path / "100.max_hold.json").exists()
    assert not (tmp_path / "100.regime_mult.json").exists()
    assert (tmp_path / "800.max_hold.json").exists()
    assert (tmp_path / "800.regime_mult.json").exists()


def test_prune_noop_when_under_keep(tmp_path: Path) -> None:
    for i, ts in enumerate([100, 200]):
        _make_generation(tmp_path, ts, mtime=1_000_000 + i)

    _prune_disk_snapshots(tmp_path, keep=5)

    assert len(list(tmp_path.glob("*.db"))) == 2


def test_prune_breaks_mtime_ties_by_timestamp(tmp_path: Path) -> None:
    # Two backups with the SAME mtime — the newer timestamp must win the keep
    # slot deterministically (ts secondary sort key), not filesystem glob order.
    _make_generation(tmp_path, 100, mtime=1_000_000)
    _make_generation(tmp_path, 200, mtime=1_000_000)

    _prune_disk_snapshots(tmp_path, keep=1)

    assert [p.stem for p in tmp_path.glob("*.db")] == ["200"]


def test_prune_empty_dir_is_safe(tmp_path: Path) -> None:
    _prune_disk_snapshots(tmp_path, keep=LEARNER_SNAPSHOT_KEEP)  # no raise


def test_keep_default_is_a_small_handful() -> None:
    # Each .db is a full ~250 MB DB copy — the default must stay bounded so the
    # operator backups can never re-grow to the 20 GB incident size.
    assert 1 <= LEARNER_SNAPSHOT_KEEP <= 10
