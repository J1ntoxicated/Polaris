# Wave 5 — Evolver Pruning Deprecation (Block Paradigm 일관성)

**Date**: 2026-04-28 02:15
**Status**: Draft (Jin overnight review)
**Vault refs**: [[INSIGHT-025-evolver-tournament-block-paradigm-2026-04-28]] [[feedback_no_block_filter_architecture]] [[feedback_overhaul_over_incremental]]

---

## 1. Context

DEMOTE 폐기 (commit `6791c3d0`) + Wave 2A B5 sample protection (commit `b19c9480`/`25981a3b`) 후 evolver/Tournament block paradigm 일관성 검토.

INSIGHT-025 결과: **3 disable mechanism 중 DEMOTE 만 폐기, Tournament+evolver pruning 잔존 = inconsistent**. `feedback_no_block_filter_architecture` mandate 50% 만 적용.

---

## 2. Decision Matrix

| Option | Scope | 북극성 | Risk | Phase |
|---|---|---|---|---|
| A. 전체 폐기 | Tournament + evolver pruning + cell.mult ramp-down | ✅✅ 완전 일관 | high (178+ scale, ramp-down 신규) | Phase 3 (Wave 6) |
| **B. Pruning only 폐기** | evolver line 320/348/376 폐기 + mutation rate 조절 | ✅ 부분 일관 (Tournament 유지) | medium | **이번 spec** |
| C. Sample protection (deploy) | Pruning + Tournament sample 보호 | △ 부분 (block 유지) | low | Phase 1 (이미 deploy) |

**이번 spec = Option B (Phase 2)**.

---

## 3. Implementation

### Step 1 — evolver pruning 3 sites 폐기

**File**: `invasion/strategy/evolver.py`

#### Line 320 (group overflow):
```python
# OLD
strats.sort(key=lambda s: s.get("fitness", 0) or 0, reverse=True)
for s in strats[MAX_STRATEGIES_PER_GROUP:]:
    # ... protection check
    store.disable(s["name"])  # ← BLOCK PARADIGM

# NEW (Wave 5):
# DEPRECATED 2026-04-28: pruning 메커니즘 폐기 — DEMOTE 폐기 (commit 6791c3d0)
# 일관성. Cell.mult amplify-only sizing 으로 자연 도태 의존.
# Mutation rate 자체 감소로 strategy 누적 제어 (ITEM-MUTATION-RATE).
# strategies pruning 0건 — log info 만 emit.
if len(strats) > MAX_STRATEGIES_PER_GROUP:
    log_event("EVOLVER",
        f"Group {g} has {len(strats)} strategies (cap {MAX_STRATEGIES_PER_GROUP}) "
        f"— pruning DEPRECATED, mutation rate adjustment 후속",
        "info")
```

#### Line 348 (family cap):
같은 패턴 — pruning 폐기, log info 만.

#### Line 376 (asset_class cap):
같은 패턴.

### Step 2 — Mutation rate 조절 mechanism

**Goal**: Strategies 무한 누적 방지 (pruning 폐기 후).

**File**: `invasion/strategy/evolver.py` mutation generation 영역

**Logic**:
- Mutation rate `_get_mutation_rate()` 가 strategies 총 수에 비례 감소
- 178 strategies < 250 → mutation rate 1.0 (full)
- 250-400 → 0.5 (half)
- 400+ → 0.1 (minimal)

또는 더 sophisticated:
- Group/family/asset_class 별 strategies count 측정
- 해당 그룹의 mutation rate 만 감소 (다른 group 영향 0)

**Preg 신규**:
```python
_reg("evolver_mutation_rate_max_strategies", 250, (50, 500), "evolve",
     "strategy/evolver.py:_get_mutation_rate (Wave 5)",
     "Strategies count threshold — 이상이면 mutation rate 감소")
_reg("evolver_mutation_rate_decay", 0.5, (0.1, 1.0), "evolve",
     "strategy/evolver.py:_get_mutation_rate (Wave 5)",
     "Mutation rate decay factor when above threshold")
```

### Step 3 — Constants/config 정리

```python
# OLD
MAX_STRATEGIES_PER_GROUP = 30  # group cap
FAMILY_VARIANT_CAP = 5         # family cap
ASSET_CLASS_CAP = 50           # asset_class cap

# NEW
# DEPRECATED constants — pruning 폐기로 무효 (Wave 5)
# Mutation rate 조절으로 대체
```

또는 keep constants, just unused. Cleaner: deprecate with comment.

---

## 4. North Star alignment

- ✅ Block 누적 0 (pruning 폐기)
- ✅ DEMOTE 폐기 일관성 (`feedback_no_block_filter_architecture`)
- ✅ Amplify-only sizing 의존 (이미 cell.mult 1.0~2.0)
- ✅ `feedback_overhaul_over_incremental` (단계적, but 분명한 architectural change)

⚠️ **Concern**: cell.mult 가 amplify-only (1.0~2.0) 라 자연 도태 메커니즘 부재. 
- 모든 strategies trade_count 학습 → amplify (winner) 또는 base 1.0 (loser)
- Loser cell 이 base 1.0 = "no amplify" 그대로 trade 흐름
- 진짜 도태 = 학습 후에도 winner 못 됨 = 손실 누적 (paper account 정합)

→ Wave 6 (Option A) 에서 cell.mult ramp-down 또는 amplify-only 그대로 자연 누적 결정.

---

## 5. Verification Plan

### Per-batch
- AST + import smoke
- 178 strategies 수 추적 (mutation rate 효과)
- evolver round 마다 pruning event 0건 expected

### 1-7 days observe
- Strategies count 추세 (250 도달까지 시간)
- Group/family/asset_class 분포 변화
- Trade economic 지표 (24h NET, win-rate)
- Perf 영향 측정 (signal evaluation latency)

---

## 6. Risk

1. **Strategies 무한 누적** — mutation rate 조절 효과 부족 시 문제. Wave 6 에서 hard limit 추가 가능.
2. **Perf 영향** — 178 → 250+ strategies = signal evaluation cost 증가. Latency profiling 필요.
3. **Loser strategy traffic** — pruning 폐기 후 loser strategies trade 흐름 = paper account 손실 증가 (Jin mandate 정합 — empirical 노출).
4. **Tournament 충돌** — Tournament line 318 status='disabled' 잔존 = 부분 block 잔존. Wave 6 후속.

---

## 7. Wave 6 후속 (별도 spec)

- Tournament Elo floor + status='disabled' 폐기 (Option A 완성)
- cell.mult ramp-down 학습 mechanism (자연 도태)
- Strategies hard limit (catastrophic scale 방지)
- Signal evaluation perf optimization

---

## 8. References

- Vault: [[INSIGHT-025]] [[INSIGHT-019]] (DEMOTE 폐기 commit 6791c3d0)
- Memory: [[feedback_no_block_filter_architecture]] [[feedback_overhaul_over_incremental]] [[feedback_no_defensive_param_dampen]] [[feedback_aggressive_always_profit]]
- Code: `invasion/strategy/evolver.py` (line 320/348/376)
- Specs: [[2026-04-28-evolver-pruning-protection-design]] (Phase 1)

---

## 9. Jin overnight 결정 영역

이 spec 은 draft. Jin 깨어나서 결정:
- B 진행 (Pruning 폐기 + mutation rate)?
- B 보류 (Phase 1 protection 만 충분)?
- A 직접 진행 (Tournament + cell.mult ramp-down 통합)?
- 다른 방향?

자율 dispatch X — Jin 결정 후 진행 (architectural 큰 변경, 충분한 검토 mandate).
