"""#32 axes A+B — the judge CALL gate (anti-flooding + evidence-robustness).

Two pure predicates, AND-composed, decide whether a deterministic gate decision
should ESCALATE to the GPT judge — they NEVER decide the trade. ``escalate=False``
means the DETERMINISTIC decision flows UNCHANGED (flow_not_block intact); the gate
only chooses whether to ASK the AI.

- **A — selective call (핀셋 escalation, anti-flooding).** A signal is escalated
  only when the deterministic decision is uncertain / on a boundary (entry) or at a
  decision-moment (exit). A clean, decisive, warm signal flows deterministically with
  NO GPT call and NO shadow row — exactly the audit-#62 flooding fix (G7 222 calls =
  per-position-per-recalc → low tens).
- **B — evidence-robustness gate (탄탄할 때만).** Even when A fires, the judge is asked
  only when the bot's OWN evidence (completeness / cell warmth / freshness /
  consistency) is robust enough to produce an actionable verdict. A sparse-evidence
  boundary case SKIPS the GPT call entirely (saves the call, not just discards the
  verdict). Weak evidence is NOT a block — it is "AI 개입 보류": the deterministic
  signal flows unchanged. A cold cell never blocks an entry — it only withholds AI
  escalation.

All thresholds are env-FLAGGED (no_hardcode_in_plans) and read fresh per call so a
supervised run can tune them without a code edit. All trigger signals are ALREADY
computed pre-call (zero new fetch / zero added latency).

9-stack / -1.0R: this module sizes nothing and touches no stop. It only returns a
bool + a reason string.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from polaris.core.pipeline.agents._shadow_rules import CELL_WARM_MIN_N_EFF

__all__ = [
    "EvidenceRobustness",
    "ROBUST_MIN_DEFAULT",
    "evidence_robustness",
    "robust_min",
    "should_escalate_entry",
    "should_escalate_exit",
    "stop_near_atr",
    "drift_min",
    "volume_z_min",
    "judge_exit_cooldown_sec",
    "g3_scalar_band",
    "size_up_boost",
    "extend_atr",
    "tighten_atr",
    "refine_ttl_sec",
]

# ---------------------------------------------------------------------------
# Env-flagged thresholds (no_hardcode_in_plans). Read fresh per call.
# ---------------------------------------------------------------------------

ROBUST_MIN_DEFAULT: Final[float] = 0.50

# B-component weights (sum ~1.0). Tunable without code edits.
_W_COMPLETENESS: Final[str] = "POLARIS_JUDGE_W_COMPLETENESS"
_W_WARMTH: Final[str] = "POLARIS_JUDGE_W_WARMTH"
_W_FRESHNESS: Final[str] = "POLARIS_JUDGE_W_FRESHNESS"
_W_CONSISTENCY: Final[str] = "POLARIS_JUDGE_W_CONSISTENCY"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def robust_min() -> float:
    """B threshold — escalate only when evidence_robustness >= this (env)."""
    return _env_float("POLARIS_JUDGE_ROBUST_MIN", ROBUST_MIN_DEFAULT)


def g3_scalar_band() -> tuple[float, float]:
    """G3 boundary band [lo, hi] — a scalar inside is ambiguous → escalate."""
    return (
        _env_float("POLARIS_JUDGE_G3_SCALAR_LO", 0.85),
        _env_float("POLARIS_JUDGE_G3_SCALAR_HI", 1.15),
    )


def stop_near_atr() -> float:
    """G7 stop-near band in ATR units — |last-stop| <= this*atr → decision moment."""
    return _env_float("POLARIS_JUDGE_STOP_NEAR_ATR", 0.5)


def drift_min() -> float:
    """Big-move drift floor (abs momentum_drift over this = market actually moved)."""
    return _env_float("POLARIS_JUDGE_DRIFT_MIN", 0.3)


def volume_z_min() -> float:
    """Big-move volume-z floor (abs volume_z over this = market actually moved)."""
    return _env_float("POLARIS_JUDGE_Z_MIN", 2.0)


def judge_exit_cooldown_sec() -> float:
    """G7 per-position cooldown — re-escalate the SAME rung only after this (env)."""
    return _env_float("POLARIS_JUDGE_EXIT_COOLDOWN_SEC", 180.0)


def size_up_boost() -> float:
    """C SIZE_UP — judge_conviction value folded into the ONE continuous scalar."""
    return _env_float("POLARIS_JUDGE_SIZE_UP_BOOST", 1.15)


def extend_atr() -> float:
    """C EXTEND — extra ATR the widen REQUEST pushes the stop (rail still binds)."""
    return _env_float("POLARIS_JUDGE_EXTEND_ATR", 1.0)


def tighten_atr() -> float:
    """C TIGHTEN — ATR-step the tighten REQUEST pulls the stop closer (rail binds)."""
    return _env_float("POLARIS_JUDGE_TIGHTEN_ATR", 0.5)


def refine_ttl_sec() -> float:
    """C REFINE_TIMING — one-shot time-box before the entry proceeds at market."""
    return _env_float("POLARIS_JUDGE_REFINE_TTL_SEC", 5.0)


# ---------------------------------------------------------------------------
# B — evidence robustness (pure; reads the SAME projection the judge renders).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceRobustness:
    """Robustness score + its four weighted parts (telemetry-friendly)."""

    score: float
    completeness: float
    warmth: float
    freshness: float
    consistency: float


def _completeness(payload: dict[str, Any]) -> float:
    """Fraction of present own-data sources (0..1).

    Counts: evidence.label, news_n>0, >=1 raw alt-data signal, baseline,
    ticker_ground.has_sentiment. ``has_event`` is EXCLUDED until task #54 lands
    (news_headline key is never emitted — counting it would over-credit).
    """
    evidence = payload.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    baseline = payload.get("baseline")
    ground = payload.get("ticker_ground")
    ground = ground if isinstance(ground, dict) else {}
    raw_keys = ("crypto_fg", "avg_funding", "vix", "hy_spread", "cot_net_spec_pctile")
    checks = (
        evidence.get("label") is not None,
        _as_float(evidence.get("news_n"), 0.0) > 0.0,
        any(k in evidence for k in raw_keys),
        isinstance(baseline, dict) and bool(baseline),
        bool(ground.get("has_sentiment")),
    )
    return sum(1.0 for c in checks if c) / float(len(checks))


def _warmth(payload: dict[str, Any]) -> float:
    """Cell sample warmth (0..1). n_eff>=CELL_WARM_MIN_N_EFF → 1.0; linear; cold ≤0.3."""
    cell = payload.get("cell_routing")
    cell = cell if isinstance(cell, dict) else {}
    n_eff = _as_float(cell.get("n_eff"), 0.0)
    if n_eff >= CELL_WARM_MIN_N_EFF:
        return 1.0
    if n_eff <= 0.0:
        return 0.0
    # Linear 0..1 over [0, CELL_WARM_MIN_N_EFF); a cold cell (<warm) caps at 0.3.
    return min(0.3, n_eff / CELL_WARM_MIN_N_EFF)


def _ramp(staleness: float, fresh: float, stale: float) -> float:
    """1.0 up to ``fresh``, linear down to 0.0 at ``stale`` (clamped)."""
    if staleness < 0.0:
        staleness = 0.0
    if staleness <= fresh:
        return 1.0
    if staleness >= stale or stale <= fresh:
        return 0.0
    return 1.0 - (staleness - fresh) / (stale - fresh)


def _observation_freshness(payload: dict[str, Any], *, now_ts: int) -> float:
    """Per-SOURCE observation freshness (0..1) — the currency fix.

    The ground ``updated_ts`` is always 'now' (the fuse time), so it can never see
    that the UNDERLYING evidence (a weekend FRED print / a weekly COT report / a
    thin-ticker headline) is days-to-weeks old. This reads the per-source observed
    ages the fuser now surfaces (``macro_age_days`` / ``cot_report_date`` /
    ``news_max_age_h``) and ramps on the OLDEST one. Thresholds are GENEROUS so a
    normally-cadenced source (weekend macro, weekly COT) is NOT over-discounted
    (과도디스카운트 X); only a genuinely stale observation decays.

    No age keys present → 1.0 (non-regression: pre-fix payloads are unaffected).
    flow_not_block: this only shapes the freshness scalar, it never blocks.
    """
    evidence = payload.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    ages_sec: list[float] = []
    macro_days = evidence.get("macro_age_days")
    if isinstance(macro_days, (int, float)):
        ages_sec.append(float(macro_days) * 86400.0)
    news_h = evidence.get("news_max_age_h")
    if isinstance(news_h, (int, float)):
        ages_sec.append(float(news_h) * 3600.0)
    cot_age = _cot_report_age_sec(evidence.get("cot_report_date"), now_ts)
    if cot_age is not None:
        ages_sec.append(cot_age)
    if not ages_sec:
        return 1.0
    fresh = _env_float("POLARIS_JUDGE_OBS_FRESH_SEC", 604800.0)  # 7d
    stale = _env_float("POLARIS_JUDGE_OBS_STALE_SEC", 2592000.0)  # 30d
    return _ramp(max(ages_sec), fresh, stale)


def _cot_report_age_sec(report_date: Any, now_ts: int) -> float | None:
    """Seconds between a COT report date (``YYYY-MM-DD``) and ``now``; None if bad."""
    if not isinstance(report_date, str) or not report_date:
        return None
    try:
        ts = (
            datetime.strptime(report_date[:10], "%Y-%m-%d")
            .replace(tzinfo=UTC)
            .timestamp()
        )
    except ValueError:
        return None
    age = float(now_ts) - ts
    return age if age >= 0.0 else 0.0


def _freshness(payload: dict[str, Any], *, now_ts: int) -> float:
    """Ground freshness ∧ per-source observation freshness (0..1).

    Ground freshness (the fuse-time stamp) is combined with the per-source
    observation freshness via ``min`` — a SINGLE continuous scalar, NOT a
    multiplicative stack (9-stack stays banned). A fresh fuse over a stale
    underlying source (the audit's stale-as-current case) now reads stale; a fresh
    fuse over fresh sources stays 1.0.
    """
    ground = payload.get("ticker_ground")
    ground = ground if isinstance(ground, dict) else {}
    updated = ground.get("updated_ts")
    if updated is None:
        return 0.0  # fail-safe: missing stamp = treat as NOT fresh
    staleness = float(now_ts) - _as_float(updated, 0.0)
    fresh = _env_float("POLARIS_JUDGE_FRESH_SEC", 3600.0)
    stale = _env_float("POLARIS_JUDGE_STALE_SEC", 21600.0)
    ground_fresh = _ramp(staleness, fresh, stale)
    return min(ground_fresh, _observation_freshness(payload, now_ts=now_ts))


def _consistency(payload: dict[str, Any]) -> float:
    """Sign-coherence of evidence label vs news sentiment (0..1).

    A confirmed contradiction is NOT penalized for the EXIT path — it is itself
    robust grounds for TIGHTEN_ON_CONFIRMED_DECAY; only the ambiguous middle (no
    label / no sentiment / mixed) is weak. We score coherence conservatively: a
    present label + same-sign sentiment → 1.0; present label + opposite-sign → 0.5
    (a clear signal either way is informative); absent → 0.0.
    """
    evidence = payload.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    label = evidence.get("label")
    if label is None:
        return 0.0
    sentiment = evidence.get("news_sentiment")
    if sentiment is None:
        return 0.5  # a label with no news tilt is still a directional read
    label_bull = "bull" in str(label).lower()
    label_bear = "bear" in str(label).lower()
    s = _as_float(sentiment, 0.0)
    if not label_bull and not label_bear:
        return 0.5
    same_sign = (label_bull and s >= 0.0) or (label_bear and s <= 0.0)
    return 1.0 if same_sign else 0.5


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def evidence_robustness(
    payload: dict[str, Any], *, now_ts: int
) -> EvidenceRobustness:
    """Weighted [0..1] robustness of the bot's OWN evidence for THIS ticker.

    Reads the SAME ``payload`` projection the judge prompt renders — no new fetch.
    Weights are env-tunable; defaults 0.35 / 0.30 / 0.20 / 0.15.
    """
    w_c = _env_float(_W_COMPLETENESS, 0.35)
    w_w = _env_float(_W_WARMTH, 0.30)
    w_f = _env_float(_W_FRESHNESS, 0.20)
    w_k = _env_float(_W_CONSISTENCY, 0.15)
    completeness = _completeness(payload)
    warmth = _warmth(payload)
    freshness = _freshness(payload, now_ts=now_ts)
    consistency = _consistency(payload)
    total_w = w_c + w_w + w_f + w_k
    if total_w <= 0.0:
        score = 0.0
    else:
        score = (
            w_c * completeness
            + w_w * warmth
            + w_f * freshness
            + w_k * consistency
        ) / total_w
    return EvidenceRobustness(
        score=score,
        completeness=completeness,
        warmth=warmth,
        freshness=freshness,
        consistency=consistency,
    )


# ---------------------------------------------------------------------------
# A — selective-call predicates (pure; all inputs pre-computed).
# ---------------------------------------------------------------------------


def should_escalate_entry(
    *,
    gate: str,
    decision: str,
    scalar: float,
    n_eff: float,
    watch_flags: tuple[str, ...] | list[str] = (),
    recent_reject_in_6h: bool = False,
) -> tuple[bool, str]:
    """A — ENTRY escalation predicate (G3 rationale / G4 timing).

    Returns ``(escalate, reason)``. Escalate only on a boundary / uncertain case;
    a clean decisive warm signal flows deterministically (no GPT). NEVER a block.

    G3 (``gate == "G3"``): escalate when ANY of —
      (a) decision == MODIFY (a conservative trim = ambiguous), or
      (b) cold cell (n_eff < CELL_WARM_MIN_N_EFF), or
      (c) scalar inside the boundary band [LO, HI].
    G4 (``gate == "G4"``): escalate only when ``watch_flags`` is non-empty
      (∈ {spread_wide, drift}); a clean non-flagged PROCEED skips.

    A same-(venue,symbol) connection-storm guard: ``recent_reject_in_6h`` → skip
    (reuse the existing payload flag; dedup the storm).
    """
    if recent_reject_in_6h:
        return False, "recent_reject_skip"
    if gate == "G4":
        flags = tuple(watch_flags or ())
        if flags:
            return True, "g4_watch_flags"
        return False, "g4_clean_proceed"
    # G3 (default for entry rationale).
    if str(decision).upper() == "MODIFY":
        return True, "g3_modify_ambiguous"
    if n_eff < CELL_WARM_MIN_N_EFF:
        return True, "g3_cold_cell"
    lo, hi = g3_scalar_band()
    if lo <= scalar <= hi:
        return True, "g3_boundary_scalar"
    return False, "g3_clean_decisive"


def should_escalate_exit(
    *,
    mode: str | None,
    exit_state_crossed: bool,
    last_price: float,
    current_stop: float,
    atr_one: float,
    momentum_drift: float,
    atr_slope: float,
    volume_z: float,
) -> tuple[bool, str]:
    """A — G7 EXIT escalation predicate (the flooding hotspot).

    Returns ``(escalate, reason)``. DECISION-MOMENT-only: escalate when ANY —
      (1) an exit-FSM rung was CROSSED this tick (``exit_state_crossed``), or
      (2) mode ∈ {REMODE, HARVEST, CUT} (HOLD / LET_RUN = no-op → skip), or
      (3) stop-near: |last - stop| <= STOP_NEAR_ATR * atr_one, or
      (4) big-move: |drift| > DRIFT_MIN OR atr_slope > 0 OR |volume_z| >= Z_MIN.
    None → SKIP (position mid-flight; the deterministic trail handles it).

    NEVER a forced cut — only decides whether to ASK the judge. The per-position
    cooldown/dedup is applied by the caller on top of this (rung-advanced OR
    cooldown-elapsed).
    """
    if exit_state_crossed:
        return True, "g7_rung_crossed"
    mode_u = str(mode).upper() if mode is not None else ""
    if mode_u in {"REMODE", "HARVEST", "CUT"}:
        return True, f"g7_mode_{mode_u.lower()}"
    if atr_one > 0.0 and abs(last_price - current_stop) <= stop_near_atr() * atr_one:
        return True, "g7_stop_near"
    if (
        abs(momentum_drift) > drift_min()
        or atr_slope > 0.0
        or abs(volume_z) >= volume_z_min()
    ):
        return True, "g7_big_move"
    return False, "g7_midflight_skip"
