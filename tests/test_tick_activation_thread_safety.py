"""Thread-safety reproduction for ``_SymbolActivation`` (#74 STALL race fix).

DEMO/PAPER. The STALL fix (#74) OFFLOADS the Layer-0 focus refresh onto a worker
thread (``asyncio.to_thread`` → ``refresh_focus_watchlist`` →
``QuoteTickWriter.activation_metrics`` → ``_SymbolActivation.snapshot``) while the
live event loop keeps appending WS ticks (``on_quote`` → ``_SymbolActivation.add``)
on the loop thread. ``add`` and ``snapshot`` both touch ``self.buckets``: ``add``
writes a key, ``snapshot`` iterates ``self.buckets`` (3 folds) and ``del``s pruned
keys. Concurrent write-during-iterate raises
``RuntimeError: dictionary changed size during iteration``.

This is the EXACT regression that made the first offload attempt BLOCK in
adversarial review: the offload was correct, but it turned a previously
single-threaded structure into a shared one without making ``snapshot`` atomic.

Pin (RED before fix → GREEN after): hammer ``add`` (loop-thread modelled) and
``snapshot`` (worker-thread modelled) concurrently for thousands of iterations and
assert ZERO ``RuntimeError``. flow_not_block / 9-stack / sizing / -1R rail are not
touched — this only makes the read atomic.
"""

from __future__ import annotations

import threading

from polaris.core.data._tick_activation import (
    _ACTIVATION_BUCKET_SEC as _BUCKET,
)
from polaris.core.data._tick_activation import _SymbolActivation


def test_snapshot_is_atomic_under_concurrent_add() -> None:
    """``snapshot`` must not raise while ``add`` mutates ``buckets`` concurrently.

    Models the live split: ``add`` on the loop thread (WS append) vs ``snapshot``
    on the offloaded focus-refresh worker thread. Pre-fix ``snapshot`` iterates the
    live ``self.buckets`` (and ``del``s from it) → "dictionary changed size during
    iteration". Post-fix it folds over an atomic ``list(...)`` snapshot.
    """
    sym = _SymbolActivation()
    # Seed MANY buckets so snapshot's iteration window is wide (larger collision
    # surface), exercising the prune path (del) too once the window slides.
    base = 1_900_000_000
    for i in range(0, 3000, _BUCKET):
        sym.add(base + i, 100.0 + i)

    errors: list[BaseException] = []
    stop = threading.Event()
    reader_iters = 5_000

    def _writer() -> None:
        # Loop-thread role: each append lands in a BRAND-NEW bucket key (ts advances
        # by a full bucket every time), so ``self.buckets`` GROWS in size while the
        # reader iterates — the exact "changed size during iteration" trigger. Runs
        # until the readers finish.
        try:
            ts = base + 3000
            while not stop.is_set():
                ts += _BUCKET
                sym.add(ts, 100.0)
        except BaseException as exc:  # noqa: BLE001 — capture for the assert
            errors.append(exc)
            stop.set()

    def _reader() -> None:
        # Worker-thread role: the offloaded focus refresh folding the buckets.
        try:
            for _ in range(reader_iters):
                if stop.is_set():
                    return
                sym.snapshot()
        except BaseException as exc:  # noqa: BLE001 — capture for the assert
            errors.append(exc)
        finally:
            stop.set()

    writer = threading.Thread(target=_writer)
    readers = [threading.Thread(target=_reader) for _ in range(2)]
    writer.start()
    for t in readers:
        t.start()
    for t in readers:
        t.join()
    stop.set()
    writer.join()

    assert not errors, (
        "snapshot() raced add() across threads — the offloaded focus refresh "
        f"can crash the Layer-0 task: {errors[0]!r}"
    )


def test_snapshot_returns_consistent_shape_under_load() -> None:
    """Every concurrent ``snapshot`` still returns the full metric dict (no torn read).

    Beyond not-raising, the folded result must always carry the 6 sweep keys so the
    candidate sweep never sees a half-built dict mid-mutation.
    """
    sym = _SymbolActivation()
    base = 1_900_000_000
    sym.add(base, 100.0)

    errors: list[BaseException] = []
    bad_shape: list[dict[str, float | int]] = []
    expected = {
        "ticks_600s",
        "mid_120s_ago",
        "mid_high_600s",
        "mid_low_600s",
        "last_mid",
        "last_ts",
    }

    stop = threading.Event()

    def _writer() -> None:
        # New bucket key every append → dict grows under the reader.
        try:
            ts = base
            while not stop.is_set():
                ts += _BUCKET
                sym.add(ts, 100.0)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            stop.set()

    def _reader() -> None:
        try:
            for _ in range(10_000):
                if stop.is_set():
                    return
                m = sym.snapshot()
                if set(m) != expected:
                    bad_shape.append(m)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            stop.set()

    writer = threading.Thread(target=_writer)
    reader = threading.Thread(target=_reader)
    writer.start()
    reader.start()
    reader.join()
    stop.set()
    writer.join()

    assert not errors, f"snapshot raced add: {errors[0]!r}"
    assert not bad_shape, f"snapshot returned an incomplete dict: {bad_shape[:1]}"
