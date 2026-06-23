"""ADR-012 — ProbeBus: run registered probes with PER-PROBE fault isolation.

DEMO/PAPER. ``observe(ctx)`` runs every registered probe; a probe that raises
is LOGGED and SKIPPED (never breaks the tick — mirrors the per-position fault
isolation in ``recalc_active_positions``). ABSTENTIONS (``None``) are dropped.
Pure / sync: no DB, no network, no await.
"""

from __future__ import annotations

import logging

from polaris.core.probes import Probe, ProbeContext, ProbeReading

logger = logging.getLogger(__name__)

__all__ = ["ProbeBus"]


class ProbeBus:
    """Runs registered probes with per-probe fault isolation."""

    __slots__ = ("_probes",)

    def __init__(self, probes: list[Probe]) -> None:
        self._probes = list(probes)

    def observe(self, ctx: ProbeContext) -> list[ProbeReading]:
        """Run every probe; collect non-None readings. One probe raising is
        logged + skipped — the tick continues, the exit pass is unaffected.
        """
        readings: list[ProbeReading] = []
        for probe in self._probes:
            try:
                reading = probe.evaluate(ctx)
            except Exception as exc:  # noqa: BLE001 — fault-isolate per probe
                logger.warning(
                    "[probe] %s raised for %s — skipped: %r",
                    getattr(probe, "probe_id", probe.__class__.__name__),
                    ctx.position_id, exc,
                )
                continue
            if reading is not None:
                readings.append(reading)
        return readings
