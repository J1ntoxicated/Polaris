# Wave 10 — Profit Management Exit Logic Redesign

**Date**: 2026-04-28 14:10
**Status**: Draft (Jin review)
**Skill**: superpowers:brainstorming → writing-plans (next)
**Vault refs**: [[INSIGHT-016]] [[INSIGHT-021]] [[INSIGHT-026]] [[ADR-003]] [[ADR-004]] [[Wave-5C cell-exit-learner]]

---

## 1. Context

Jin mandate (2026-04-28 14:00):
> "익절 손절 로직을 잘 짜야 프로핏 매니징이 될꺼 같은데? 브레인 스톰 좀 해봐. 우리 구조로."

**24h NET 분해** (drag root):
- TP +$1915 (100% WR) + TRAIL +$754 (96% WR) = **+$2669** winner-pull (excellent)
- TIME -$1436 (26.8% WR) drag
- **STOP -$2722** (0% WR) ← **24h NET -$1485 의 가장 큰 출혈** (winner 다 토함)

Profit managing 핵심 = **STOP 줄이기** + **winner protect 강화**.

---

## 2. 현재 Exit Wire 매트릭스 (vault grounded)

| Exit | Trigger | Wire 상태 | Cell-aware? |
|---|---|---|---|
| **TP** | profit threshold | hard-coded | ❌ |
| **TRAIL** | `max_pnl >= trail_activate`, lock-in giveback | preg + cell.optimal_trail_activate ✅ | ✅ |
| **TIME** | `age >= max_hold_sec` | preg + cell.optimal_max_hold_sec ✅ | ✅ |
| **TIME_PNL_AWARE** | TIME 진입 시 pnl_sign 분기 (ADR-004) | preg | partial |
| **TIME hold-aware** | INSIGHT-021 OKX 1h+ -0.10% | preg | partial |
| **STOP** | `pnl < hard_stop_pct (-2%)` | preg + cell.optimal_hard_stop_pct **MISSING WIRE** ❌ | ❌ critical |
| **BEP** | `bep_activate <= max_pnl < trail_activate` 영역에서 bep_floor | preg + cell.optimal_bep_activate ✅ | ✅ |
| **SIGNAL** | DPM signal_reversed | runtime | N/A |

### 🔴 Critical Wire Gap 발견

**`cell.optimal_hard_stop_pct` 학습값 적용 path 누락** (`_exit_classic_path.py:119, 218`):

```python
# 현재 (cell-aware 미적용):
hard_stop = ep.get("hard_stop_pct") or preg("hard_stop_pct")

# 그러나 trail/bep 는 cell-aware override:
trail_activate = _cel_get2(_ck2, "trail", float(trail_activate))
bep_activate = _cel_get2(_ck2, "bep", float(bep_activate))
# hard_stop 만 누락!
```

→ Wave 5C cell-exit-learner 가 `optimal_hard_stop_pct` 학습 (147 cells) 했지만 **runtime 적용 site 없음**. 학습값 lost.

이게 STOP -$2722 drag root cause 의 큰 부분 — strategy 별 hard_stop tuning 적용 안 됨.

---

## 3. Decision Matrix

### Phase 1 — Critical Wire Fix (즉시, 작은 변경)

| Item | Mechanism | 북극성 | ROI/day |
|---|---|---|---|
| **A. Cell-aware STOP wire 추가** | `_exit_classic_path.py` 의 hard_stop 도 `_cel_get2(_ck2, "hard_stop", ...)` override | 학습 자연 도태 | **+$1500** (STOP -$2722 → -$1000~) |
| **B. BEP Auto Activate** (low threshold) | preg `bep_activate` 낮춰 winner 진입 후 빠른 activation | winner protect | +$500 (drawdown 차단) |

### Phase 2 — Winner Optimization (architectural)

| Item | Mechanism | 북극성 | ROI/day |
|---|---|---|---|
| **C. TRAIL 가속** (INSIGHT-016 #10) | trail giveback threshold 좁힘 | amplify (winner 빠른 lock) | +$200 |
| **D. TP Ladder** (partial close) | 50% close at first target / 50% trail-up | amplify (분할 lock) | +$300 |
| **E. Cell-aware TP threshold** | cell.optimal_tp_pct 학습 (Wave 5C 확장) | 학습 자연 적용 | +$200 |

### Phase 3 — Proactive (검증 후)

| Item | Mechanism | 북극성 | ROI/day |
|---|---|---|---|
| **F. Signal divergence cut** | entry score vs current score gradient → close suggest | observability | +$300 |

### ❌ Reject

- **G. Drawdown limit (hold 무관 -X% cut)** — block paradigm 의 한 형태, `feedback_no_block_filter_architecture` 위반. 대신 cell-adaptive STOP 학습 자연 도태 정합.

---

## 4. Phase 1 Implementation (즉시 dispatch)

### Step 1 — `_exit_classic_path.py` cell-aware STOP 추가

**File**: `invasion/trade/_exit_classic_path.py` (line 218 영역)

기존:
```python
try:
    from ..strategy.cell_exit_learner import (
        cell_key_from_position as _cel_key2,
        get_cell_exit_threshold as _cel_get2,
    )
    _ck2 = _cel_key2(position)
    _ck2["regime"] = regime or "neutral"
    if not ep.get("trail_activate"):
        trail_activate = _cel_get2(_ck2, "trail", float(trail_activate))
    if not ep.get("bep_activate"):
        bep_activate = _cel_get2(_ck2, "bep", float(bep_activate))
except Exception as _cel_e2:
    _log_event("EXIT", f"cell trail/bep override err: {_cel_e2}", "debug")
```

추가:
```python
    # WAVE-10 PHASE-1: hard_stop 도 cell-aware override (학습값 적용)
    if not ep.get("hard_stop_pct"):
        hard_stop = _cel_get2(_ck2, "hard_stop", float(hard_stop))
```

또한 `_exit_fsm_path.py` 의 동일 site 도 verify + 적용.

### Step 2 — BEP Auto Activate (preg tune)

**Goal**: winner 진입 후 빠른 BEP activation (drawdown 차단).

현재 default 검증 후 tune:
- `bep_activate` (group/exchange aware) — 현재 값 너무 큰 가능성
- 적정 값: ~0.5% (winner 진입 후 빠른 lock)

ParamRegistry 검증 후 default 조정.

### Step 3 — Verify (post-deploy)

- `_cel_get2(_ck2, "hard_stop", ...)` 가 cell.optimal_hard_stop_pct 반환
- log_event "EXIT" 에 cell-aware STOP override fire 추적
- 24h STOP NET 변화 측정

**Commit**: `feat(wave-10 phase-1): cell-aware STOP wire + BEP auto (profit management critical)`

---

## 5. Phase 2 Implementation (Phase 1 효과 측정 후)

### TRAIL 가속 + TP Ladder

**TRAIL giveback threshold 좁힘**:
- 현재: max_pnl 후 giveback X% 시 close
- 변경: giveback threshold 30% → 20% (winner 빠른 lock)
- preg-driven

**TP Ladder**:
- 50% partial close at first profit target
- 50% trail-up
- 새 architectural — exit_cycle.py partial close mechanism 추가

### Cell-aware TP threshold

- Wave 5C extension: `optimal_tp_pct` 컬럼 추가
- Winner trade `pnl_pct at exit` p75 학습
- exit_cycle.py TP 분기에 적용

---

## 6. North Star Alignment

- ✅ Block paradigm 0 유지 (block 추가 없음)
- ✅ Amplify-only mandate (BEP/TRAIL = winner protect, STOP cell-aware 학습)
- ✅ Cell-aware learning 자연 도태 정합
- ✅ DEMOTE 폐기 + cell.mult 학습 일관성
- ✅ `feedback_no_block_filter_architecture` (drawdown limit reject — block)
- ✅ `feedback_loss_profit_asymmetry` 정합 (winner 강화 + loser 학습 cut)
- ✅ INSIGHT-026 STOP drag root fix (cell-aware hard_stop wire)

---

## 7. Risk Analysis

### Phase 1
1. **Cell-aware STOP 적용 후 STOP rate 변화** — 학습값 < base (-2%) 일 때 더 tight = 더 자주 STOP. amplify-only mandate 정합 (학습값 < base 시 base 유지) 검증 필요.
2. **BEP auto threshold tune** — 너무 낮으면 winner 노이즈 cut. 너무 높으면 효과 0.
3. **`hard_stop` ep override** — entry_params.exit_params 에 hard_stop_pct 있는 strategy 영향. ep.get() 우선 정합.

### Phase 2
1. **TP Ladder partial close** — exit_cycle.py 의 architectural 변경 (size partial). DB schema (trades) 영향 가능.
2. **TRAIL 가속** — winner 빠른 lock = max_profit 못 잡을 가능성. 측정 후 tune.

### Phase 3
1. **Signal divergence cut** — DPM signal_reversed 와 overlap. dual-path 검증 필요.

---

## 8. Verification Plan

### Per-batch
- AST + import smoke
- preg 등록 verify
- `_cel_get2` 호출 site 직접 test

### 24h post-deploy
- STOP NET 변화 (-$2722 → ?)
- Open positions drawdown 분포 (HYPE-급 -$24 → -$5 cap?)
- Winner cycle preservation (TP+TRAIL +$2669 영향 0?)
- Cell.optimal_hard_stop_pct fire 빈도

### 1-7 days
- 24h NET 추세 swing
- Loser strategies 자연 도태 (cell.mult 학습 + hard_stop tightening)

---

## 9. Why "잘 짜야"

Jin mandate 의 진짜 의미 = **profit management = winner protect + loser fast cut + 일관성**.

현재 wire 검증 결과:
- Winner protect: TRAIL ✅ / BEP ✅ / TIME_PNL_AWARE ✅
- Loser fast cut: STOP ❌ (cell-aware 학습 미적용 — wire gap)
- 일관성: cell-aware learning 4 컬럼 / 단 1 컬럼 (hard_stop) 만 적용 site 누락

**진짜 root** = `_cel_get2(..., "hard_stop", ...)` 호출 누락 = 학습값 학습됐지만 사용 안 됨.

Phase 1 = 이 wire gap 한 줄 fix.

---

## 10. References

- Vault: [[INSIGHT-016]] (idea pool 10), [[INSIGHT-021]] (1-2h death zone deployed), [[INSIGHT-026]] (STOP drag), [[ADR-003]] (amplify-only clamp), [[ADR-004]] (TIME_PNL_AWARE)
- Memory: [[feedback_no_block_filter_architecture]], [[feedback_loss_profit_asymmetry]], [[feedback_no_defensive_param_dampen]], [[feedback_overhaul_over_incremental]]
- Code:
  - `invasion/trade/_exit_classic_path.py` (line 119, 218)
  - `invasion/trade/_exit_fsm_path.py`
  - `invasion/strategy/cell_exit_learner.py` (학습 logic)
  - `invasion/trade/exit_cycle.py` (TIME_PNL_AWARE)

---

## 11. Decision

**Phase 1 = 즉시 dispatch 정합** (작은 wire gap, 학습된 데이터 활용, ROI ~$1500/day).

**Phase 2 = Phase 1 효과 측정 후** (1-3 days).

**Phase 3 = Phase 2 후속** (architectural 신중).

Jin review 후 Phase 1 진행 여부 결정.
