"""VIRTUAL ACCOUNT mode — env switch (Jin 2026-07-07).

DEMO/PAPER only. Jin mandate: defer real-venue reconciliation entirely —
build a fully self-contained VIRTUAL paper account so the whole pipeline
(strategies/gates/sizing/R-ruler/learning) validates virtually with ZERO
dependency on the real demo-venue state (OKX dust holdings, insufficient_
balance rejects, 51020 orphan-sell storms).

``POLARIS_VIRTUAL_ACCOUNT=1`` forces ``real_roundtrip=False`` regardless of
the ``--real-roundtrip`` CLI flag / caller-passed value. Every venue touch
point already downstream (real order submit, venue reconcile/orphans/drift,
OKX venue-resting stop, the signed OKX balance health-check, the live
``fetch_alpaca_buying_power``/``fetch_okx_available_usdt`` balance reads) is
already gated behind ``real_roundtrip`` — see ``_production_pipeline.py``,
``production_paper_loop.py``, ``_production_recalc_exit.py``. Forcing
``real_roundtrip`` off at this single seam is therefore sufficient to route
every fill through the existing ``simulate_open_fill``/``simulate_close`` path
and skip every real-venue read/write, with no other call site to touch.

Default OFF — behavior is byte-identical to today unless the env is set.
"""

from __future__ import annotations

import os

__all__ = ["resolve_real_roundtrip", "virtual_account_enabled"]


def virtual_account_enabled() -> bool:
    """True iff ``POLARIS_VIRTUAL_ACCOUNT=1`` (default off)."""
    return os.environ.get("POLARIS_VIRTUAL_ACCOUNT", "0") == "1"


def resolve_real_roundtrip(requested: bool) -> bool:
    """Apply the virtual-account override to a caller-requested ``real_roundtrip``.

    Virtual mode always wins: ``True`` requested + virtual mode ON -> ``False``.
    Virtual mode OFF -> ``requested`` unchanged (byte-identical default path).
    """
    if virtual_account_enabled():
        return False
    return requested
