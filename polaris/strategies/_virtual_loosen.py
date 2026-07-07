"""VIRTUAL-mode entry loosening — shared param-resolution helper (Jin 2026-07-07).

DEMO/PAPER only. Jin mandate: the pipeline is proven fully connected (every EARN
strategy dispatches/evaluates) but too CONSERVATIVE to fire meaningfully in
VIRTUAL paper (e.g. a 55-bar Donchian breakout rarely trips) — "그만 묶어"
(stop over-gating). VIRTUAL has no real capital and no real fees, so entry
params may loosen freely; REAL must stay byte-identical to the research-survivor
thresholds (aggressive_always_profit / no_defensive_param_dampen: this loosens,
it never dampens; flow_not_block).

Env read DIRECTLY (``os.environ``), NOT via ``polaris.scripts._virtual_account.
virtual_account_enabled`` — ``polaris/strategies/`` sits below ``polaris/scripts``
in the layering (same tier as ``polaris/core``), and ``core/sizing/
probe_notional.py`` already sets this exact precedent (reads
``POLARIS_VIRTUAL_ACCOUNT`` directly "to keep core→scripts layering clean").
This module mirrors that choice for the strategies package.

Usage (module-level, evaluated once at import — mirrors the existing
``uk100_breakout_1h``/``_okx_liquid_universe`` env-branch pattern):

    from polaris.strategies._virtual_loosen import virtual_loosen
    DONCHIAN_WINDOW: Final[int] = virtual_loosen(20, 55)  # (virtual, real)

Default OFF (``POLARIS_VIRTUAL_ACCOUNT`` unset) -> the ``real`` value always
wins, so every strategy's REAL-mode constants are byte-identical to today.
"""

from __future__ import annotations

import os

__all__ = ["virtual_loosen", "virtual_mode_enabled"]


def virtual_mode_enabled() -> bool:
    """True iff ``POLARIS_VIRTUAL_ACCOUNT=1`` (default off, mirrors _virtual_account.py)."""
    return os.environ.get("POLARIS_VIRTUAL_ACCOUNT", "0") == "1"


def virtual_loosen[T](virtual: T, real: T) -> T:
    """Return ``virtual`` iff VIRTUAL mode is on, else ``real`` (byte-identical default).

    Evaluated once at module-import time by each call site (a plain env read,
    process-lifetime-stable) — never re-checked mid-run, so a single strategy
    module's constants are internally consistent for the whole process.
    """
    return virtual if virtual_mode_enabled() else real
