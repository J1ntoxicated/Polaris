"""ADR-012 — ExitEngine.compose: the SOLE actor (parameter overlay on #26 FSM).

DEMO/PAPER. The engine fuses probe readings into ONE confidence-weighted
``composite_lean``, quantizes it to a single ``action`` (HOLD / WIDEN / TIGHTEN
/ HARVEST), and — from Slice 2 — fills ONLY existing knobs (``trail_mult``
loosen-only, ``mfe_protect`` ratchet-only, ``profit_target_r``,
``widen_atr_mult`` via the Q9 floor-rail).

Slice 1 = ``mode="observe"``: it STILL computes the composite + the action it
WOULD take (for the tuning log) BUT returns ALL knobs ``None`` and
``applied=False``, so NOTHING can thread into ``run_precise_exit`` — the live
exit path is provably byte-identical. ``trail_only`` / ``full`` are reserved for
Slice 2 (wired behind ``POLARIS_PROBE_ENGINE`` after a shadow-compare +
/debate). The hard rails BYPASS this engine entirely.
"""

from __future__ import annotations

from polaris.core.probes import (
    EngineAction,
    EngineDecision,
    EngineMode,
    ProbeReading,
    _clamp,
)

__all__ = ["ExitEngine"]

# Quantization thresholds on the composite lean (calibratable via /debate, never
# a silent hardcode — see ADR-012 §/debate flags). Adverse lean → TIGHTEN then
# HARVEST as it deepens; favorable lean → WIDEN; the dead band around 0 = HOLD.
_HARVEST_LEAN: float = -0.55  # strong adverse → harvest EARLIER (worst loss-side)
_TIGHTEN_LEAN: float = -0.20  # mild adverse → tighten the trail
_WIDEN_LEAN: float = 0.35  # favorable → let the winner run wider


def _quantize(composite_lean: float) -> EngineAction:
    """Map the composite lean to a single overlay action (HOLD by default)."""
    if composite_lean <= _HARVEST_LEAN:
        return "HARVEST"
    if composite_lean <= _TIGHTEN_LEAN:
        return "TIGHTEN"
    if composite_lean >= _WIDEN_LEAN:
        return "WIDEN"
    return "HOLD"


class ExitEngine:
    """Composes probe readings → one EngineDecision (parameter overlay)."""

    __slots__ = ()

    def compose(
        self, readings: list[ProbeReading], *, mode: EngineMode
    ) -> EngineDecision:
        """Confidence-weighted composite lean → quantized action.

        ``mode="observe"`` returns ALL knobs ``None`` + ``applied=False`` (the
        byte-identical guarantee); ``trail_only`` / ``full`` are Slice 2 and
        currently behave like ``observe`` (still no knobs) so an accidental flag
        flip cannot change a live exit before Slice 2 wires the knobs.
        """
        composite_lean = _composite(readings)
        action = _quantize(composite_lean)
        contributing = [r.probe_id for r in readings]
        # Slice 1: observe (and the not-yet-wired trail_only / full) thread
        # ZERO knobs and apply nothing — nothing reaches run_precise_exit.
        _ = mode  # reserved for Slice 2 knob wiring
        return EngineDecision(
            action=action,
            composite_lean=composite_lean,
            trail_mult=None,
            mfe_protect=None,
            profit_target_r=None,
            widen_atr_mult=None,
            contributing=contributing,
            applied=False,
        )


def _composite(readings: list[ProbeReading]) -> float:
    """Confidence-weighted mean lean in ``[-1, +1]`` (0.0 on no readings)."""
    weight_sum = sum(r.confidence for r in readings)
    if weight_sum <= 0.0:
        return 0.0
    weighted = sum(r.lean * r.confidence for r in readings)
    return _clamp(weighted / weight_sum, -1.0, 1.0)
