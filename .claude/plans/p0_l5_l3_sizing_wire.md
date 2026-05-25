---
plan: p0_l5_l3_sizing_wire
status: draft
date_created: 2026-05-20
related: [[ADR-005]], [[ADR-007]], [[layer-3-sizing-risk]], [[layer-5-learner-network]]
---

# Plan — L5 Learner → L3 Sizing Wire Fix

## Goal
Close the **single critical vision gap** found in 2026-05-20 audit: L5 learner output (`session_mult`, `regime_mult`, `ai_feedback_weight`) is **not applied** to T4 sizing. `resolve_final_size_mult` has 0 production callers — only tests.

vault spec (layer-5 Q2): `final = session × regime × cell_routing × ai_feedback`. Currently only `cell_routing` flows through.

## In scope (P0)
1. `session_mult` lookup at sizing time
2. `regime_mult` lookup at sizing time
3. `triple_block` size_mult=0.3 application
4. Individual clip [0.3, 3.0] + product clip [0.1, 5.0]
5. Fallback chain: disabled / sparse (n_eff<20) / no row → 1.0 neutral (aggressive — never block)

## Out of scope (P1)
- `ai_feedback_weight` (P0 stub, 100-trade soft mode threshold not yet reached)
- `max_hold` learner (affects exit/G7, not sizing)
- Cluster cap learner-tuning

## Design

### Session derivation (new helper)
`polaris/core/sizing/session.py` — UTC ts → `"asia"|"eu"|"us"`:
- asia: 00:00-08:00 UTC
- eu:   07:00-16:00 UTC
- us:   13:00-22:00 UTC
- Overlap: pick mid-window (deterministic). Default closed band → asia.

### SignalIntent extension
Add `session: str | None = None` to `SignalIntent`. None → derive from `now_ts`.

### compute_size additions
After step (3) cell_mult, before step (5) compose proposed:

```python
# (3.5) L5 learner mults
session_learner = SessionMultLearner(conn)
regime_learner = RegimeMultLearner(conn)
session_mult = session_learner.get_mult(
    ticker=intent.symbol, strategy_id=intent.strategy,
    regime=intent.regime, session=intent.session or derive_session(ts),
).value
regime_mult = regime_learner.get_mult(
    ticker=intent.symbol, strategy_id=intent.strategy,
    regime=intent.regime, session=intent.session or derive_session(ts),
).value

# (3.6) triple block check (size_mult=0.3 if blocked, 1.0 otherwise)
block = evaluate_triple_block(
    conn, ticker=intent.symbol, strategy_id=intent.strategy,
    regime=intent.regime, now_ts=ts,
)
triple_block_mult = block.size_mult if block else 1.0

# (5) compose: include learner mults in proposed
proposal = compute_proposed_with_learners(
    base_risk_pct=..., continuous=..., tier_amp=..., cell_mult=...,
    listing_mult=..., session_mult=session_mult, regime_mult=regime_mult,
    triple_block_mult=triple_block_mult,
)
```

### SizingProposal extension
Add fields: `session_mult`, `regime_mult`, `triple_block_mult` for audit trail.

### Anti 9-stack guarantee
All learner mults clipped individually [0.3, 3.0]. Product re-clipped [0.1, 5.0]. cell_routing + listing_watchdog still single multipliers. Hard MAX `min()` unchanged.

Total chain: `base × continuous × tier × cell × listing × session × regime × triple_block` (all single, no stacking of ≤1 dampeners). Aggressive top side preserved (3 amplifiers can reach 3.0× × 3.0× × 1.5× headroom before hard cap).

## TDD test list
1. `test_compute_size_no_learner_state` — sparse fallback → neutral 1.0, no change from current behavior
2. `test_compute_size_session_mult_promote` — session_mult WR≥55% → +0.1 applied to notional
3. `test_compute_size_regime_mult_demote` — regime_mult WR≤40% → -0.1 applied
4. `test_compute_size_triple_block_active` — block→0.3 applied
5. `test_compute_size_individual_clip` — session_mult=10.0 raw → clipped to 3.0
6. `test_compute_size_product_clip` — combined product > 5.0 → clipped
7. `test_session_derivation_utc_boundaries` — asia/eu/us boundaries
8. `test_signal_intent_session_default_derived` — None → derive from now_ts
9. `test_compute_size_disabled_learner_neutral` — `enabled=False` → 1.0
10. `test_sizing_proposal_audit_fields` — SizingProposal carries learner mults

## Codex review checklist (Jin /debate 호출)
- [ ] 9-stack collapse 재발 봉쇄 확인 (clip 1회씩 × clip 1회 product)
- [ ] Aggressive bias 보존 (sparse/disabled → 1.0 neutral, never 0)
- [ ] 차단/skip/reject 패턴 도입 0건 (triple block은 size_mult=0.3, entry 허용)
- [ ] Session derivation 결정적 (no hidden randomness)
- [ ] Production caller (`entry_sizer.compute_size`) backward compat

## Vault updates after merge
1. `layer-3-sizing-risk.md` Q1: T4 chain에 `session × regime × triple_block` 추가 명시
2. `layer-5-learner-network.md` Q2: production caller 명시 (engine.compute_size)
3. ADR-005 patch: T4 chain 갱신
4. ADR-007 status: P0 active learners → sizing wire = active (현재는 spec-only)
5. `log.md` 1-line append

## Acceptance criteria
- 10 new tests pass
- 모든 기존 sizing tests pass (regression 0건)
- ruff + mypy --strict clean
- codex review APPROVE
- production paper loop smoke 24h dry-run 신규 sizing variance 합리적 (sigma 확인)
