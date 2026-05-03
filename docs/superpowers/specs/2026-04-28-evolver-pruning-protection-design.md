# Evolver Pruning Protection — Wave 2A B5 Restoration

**Date**: 2026-04-28 02:00
**Vault refs**: [[INSIGHT-024]] [[2026-04-27-polaris-structural-overhaul-design]] [[2026-04-28-wave-2a-retroactive-fix-design]] [[feedback_no_block_filter_architecture]]

---

## 1. Context

Forensic 결과 Wave 2A B5 (6 trend strategies re-enable) 가 evolver pruning 으로 매 round disabled revert.

### Root cause (vault grounding)

**evolver.py 5 disable callers**:
- Line 181 (`fitness < DISABLE_FITNESS AND n_trades >= 20`) ✅ sample protected
- Line 213 (STOP-WR gate, `n >= STOP_WR_MIN_TRADES`) ✅ sample protected
- **Line 320/348/376 (group/family/asset_class CAP overflow pruning)** ❌ **sample protection 부재** ← root cause

**Mechanism**:
- 매 round evolver 새 mutations + fitness sort descending → top N keep, 나머지 disable
- trade_count=0 strategies = fitness 학습 못 함 = 항상 prune 1순위
- Wave 2A B5 manual re-enable 후 evolver 가 즉시 다시 prune → 무한 loop

### Vault evidence

- Tournament Line 284 `is_protected = n_trades < WARM_UP_TRADES` — Tournament 는 sample protection 있음 ✅
- evolver pruning 은 sample protection 부재 ❌ — Tournament 와 일관성 없음
- `feedback_no_block_filter_architecture` Jin mandate: "임시 차단 누적 금지" — pruning 은 임시 차단의 한 형태
- `MSG-P0-4-G11-KILL` 패턴: Jin manual retirement 가 정당 (status='retired', explicit)

---

## 2. Decision Matrix

| Option | 내용 | 북극성 | Risk | 결정 |
|---|---|---|---|---|
| **A. Sample protection** | line 320/348/376 prune 후보 filter `trade_count >= MIN_LIFETIME (20)` | amplify-only ✅ | low | ✅ Wave 2A 후속 |
| **B. jin_review_flag protect** | `jin_review_flag=1` strategies prune 면제 + 6 strategies set | manual mandate 정합 ✅ | low | ✅ Wave 2A 후속 |
| C. Pruning 자체 폐기 | DEMOTE 폐기 패턴. CAP 메커니즘 자체 reconsider | 가장 정합 ✅✅ | high (perf, scale) | ⏳ Wave 5 별도 spec |

**A + B 결합 채택** — pragmatic + 영속 원칙 정합. C 는 별도 long-term spec.

---

## 3. Implementation

### Batch 1 — evolver.py pruning sample + jin_review_flag protection

**File**: `invasion/strategy/evolver.py`

#### Line 320 (group overflow):
```python
strats.sort(key=lambda s: s.get("fitness", 0) or 0, reverse=True)
for s in strats[MAX_STRATEGIES_PER_GROUP:]:
    # WAVE-5-PROTECT: sample 미충족 또는 jin manual mandate 면제
    if (s.get("trade_count", 0) or 0) < PRUNE_MIN_LIFETIME_TRADES:
        continue
    if s.get("jin_review_flag", 0) == 1:
        continue
    if s in new_strategies:
        new_strategies.remove(s)
    else:
        store.disable(s["name"])
    report["actions"].append(f"Pruned {s['name']} (group {g} overflow)")
```

#### Line 348 (family cap):
같은 protection 추가.

#### Line 376 (asset_class cap):
같은 protection 추가.

#### Constants:
```python
# Top-of-file 또는 적절한 위치
PRUNE_MIN_LIFETIME_TRADES = 20  # sample protection (preg "cell_score_sample_threshold" 와 일관)
```

또는 preg 활용:
```python
def _prune_min_trades():
    try:
        return int(preg("evolver_prune_min_lifetime_trades") or 20)
    except (TypeError, ValueError):
        return 20
```

**Preg 신규** (`_params_signal.py` 또는 evolver param area):
```python
_reg("evolver_prune_min_lifetime_trades", 20, (5, 100), "signal",
     "strategy/evolver.py:_prune_overflow",
     "Minimum lifetime trades 학습 시간 보장 — pruning 면제 threshold")
```

### Batch 2 — Wave 2A B5 6 strategies jin_review_flag=1 + active sync

**SQL UPDATE**:
```sql
UPDATE strategies SET jin_review_flag=1, status='active'
WHERE name IN (
  'crypto_momentum_reversal_g1_gauss',
  'contrarian_commodity_g55_gauss',
  'contrarian_commodity_g54_ai',
  'contrarian_commodity_g1_bayes',
  'contrarian_commodity_g53_ai',
  'contrarian_commodity_g57_bayes'
);
```

**JSON sync** (6 files):
```python
import json
for s in [...]:
    p = f"data/strategies/{s}.json"
    d = json.load(open(p))
    d['status'] = 'active'
    d['jin_review_flag'] = 1  # Wave 2A B5 manual mandate
    json.dump(d, open(p, 'w'), indent=2)
```

**Bot restart 시 in-memory state 가 JSON 에서 load = active + jin_review_flag=1 → evolver protect.**

**Commit**: `feat(evolver pruning protection): sample + jin_review_flag protect for trade_count=0 strategies`

---

## 4. North Star alignment

- ✅ Block 누적 0 — protection 추가만 (block 제거)
- ✅ Amplify-only mandate — 학습 시간 보장 (sample 도달 후 정당한 evaluate)
- ✅ `feedback_no_block_filter_architecture` — pragmatic restoration, Wave 5 (구조 폐기) 후속
- ✅ `feedback_no_quick_patch_ever` — Wave 2A B5 architectural 충돌의 정직한 retroactive fix

---

## 5. Verification Plan

### Per-batch
- AST + import smoke
- 6 strategies status=active + jin_review_flag=1 sync (JSON+DB)

### Bot restart 후 30m observe
- evolver round 마다 6 strategies sustain active
- TOURNAMENT log 에 6 strategies "Pruned" event 0건 expected
- 6 strategies trade_count > 0 첫 fire (commodity entry 시 trend remap → mutation strategies fitness 학습 시작)

---

## 6. Wave 5 후속 (별도 spec)

- evolver pruning 메커니즘 자체 reconsider (DEMOTE 폐기 패턴)
- Tournament Elo floor → status disabled set 자체 reconsider
- amplify-only sizing 으로 자연 도태 ↔ 명시 retirement (Jin mandate) 분리
- 178+ strategies scale 평가

→ 별도 brainstorming + spec, 충분한 검토 후 진행.

---

## 7. References

- Vault: [[INSIGHT-024]] [[2026-04-27-polaris-structural-overhaul-design]] [[2026-04-28-wave-2a-retroactive-fix-design]]
- Memory: [[feedback_no_block_filter_architecture]] [[feedback_no_defensive_param_dampen]] [[feedback_no_quick_patch_ever]] [[feedback_sequential_superpowers_vault_organic]]
- Code: `invasion/strategy/evolver.py` (line 320/348/376), `invasion/strategy/tournament.py`, `data/strategies/*.json`
