"""ADR-012 — ProbeRole SSOT (structural hardening #8, 2026-06-23).

DEMO/PAPER. ONE source of truth mapping ``probe_id -> ProbeRole``. The role is
WHERE in the gate pipeline a probe is allowed to attach; ``ProbeKind`` stays the
ORTHOGONAL observation axis (technical / volume / session / loss / profit).

  * **Eligibility** (G1) — universe membership / tradeability.
  * **Signal** (G2) — raw-signal conviction.
  * **Validate** (G3/G4) — pre-entry confirmation.
  * **Position** (G6) — in-flight position health.
  * **Exit** (G6/G7) — exit-timing precision.

Pure typing/schema — ZERO runtime behavior change. Every probe shipped today
attaches ONLY at G6 (Position-Monitor) and is Position- or Exit-role; the other
roles are reserved seats for the future entry twin (hardening #14 keeps the
probe layer documented EXIT-ONLY until that seam exists). A probe whose
``probe_id`` is absent from the registry raises ``KeyError`` at lookup so a
role-missing probe fails import/test rather than silently defaulting.
"""

from __future__ import annotations

from typing import Literal

__all__ = ["PROBE_ROLE_REGISTRY", "ProbeRole", "role_for_probe"]

# The five pipeline roles a probe may occupy. Orthogonal to ProbeKind.
ProbeRole = Literal["Eligibility", "Signal", "Validate", "Position", "Exit"]

# SSOT: probe_id -> role. The 4 Slice-1 catalog probes all ride the G6 exit
# attach, so each is Position or Exit. ProfitTaking / LossDefense / Session are
# pure exit-timing levers (Exit); Technical describes live position health used
# for both widen-let-run and exhaustion (Position). DEFER adding new-role
# (Eligibility/Signal/Validate) entries until the entry seam is built.
PROBE_ROLE_REGISTRY: dict[str, ProbeRole] = {
    "profit_taking": "Exit",
    "loss_defense": "Exit",
    "session_hours": "Exit",
    "technical": "Position",
}


def role_for_probe(probe_id: str) -> ProbeRole:
    """Return the SSOT role for ``probe_id`` (raises ``KeyError`` if missing).

    A role-missing probe MUST fail loudly — the registry is the single source
    of truth, never a silent default.
    """
    return PROBE_ROLE_REGISTRY[probe_id]
