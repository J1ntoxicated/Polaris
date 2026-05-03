# Wave 6 — Tournament Elo Floor + Cell.Mult Ramp-Down (Block Paradigm 완전 일관성)

**Date**: 2026-04-28 08:55
**Status**: Draft (architectural 큰 redesign — 충분한 검토 mandate)
**Vault refs**: [[INSIGHT-025-evolver-tournament-block-paradigm-2026-04-28]] [[ADR-003-northstar-clamp-extended-2026-04-26]] [[feedback_no_block_filter_architecture]] [[feedback_no_defensive_param_dampen]]

---

## 1. Context

INSIGHT-025 phase 3 후속. DEMOTE 폐기 (Wave 1) + Wave 5 evolver pruning 폐기 후, **Tournament Elo floor + status='disabled' set 만 잔존**. 마지막 block paradigm 일관성 영역.

### 현재 잔존 block mechanism

`invasion/strategy/tournament.py:317-318`:
```python
role = "ELIMINATED"
s["status"] = "disabled"  # ← 잔존 block paradigm
```

조건: `is_below_floor(sid)` (Elo < 1000) AND not `is_protected`.

### 동시 architectural decision

cell.mult 범위 — 현재 `1.0 ~ 2.0` (amplify-only mandate, ADR-003).
- Loser cell mult=1.0 (base size, 손실 누적)
- Winner cell mult>1.0 (amplify)
- 자연 도태 메커니즘 부재 = loser cells 가 영원 base size trade

→ Tournament 폐기 시 strategies 무한 누적 + loser 영원 base size = catastrophic scale.

---

## 2. Decision Matrix (architectural tradeoffs)

| Option | Tournament status | cell.mult | 북극성 | Risk | Pros / Cons |
|---|---|---|---|---|---|
| **A. Tournament 완전 폐기 + cell.mult amplify-only** | 폐기 (Elo + ELIMINATED 둘 다) | 1.0~2.0 그대로 | 완전 일관 ✅ | very high | Pro: block 0 / Con: loser 영원 base, scale 폭발 |
| **B. Tournament Elo 학습 유지 + status set 폐기** | Elo+fitness 학습 유지, status='disabled' set 만 폐기 | 1.0~2.0 그대로 | 부분 일관 ✅ | medium | Pro: empirical 검증 유지 / Con: scale 영향 |
| **C. cell.mult ramp-down 도입** (0.1~2.0) | Tournament 유지 | 0.1~2.0 (loser dampen) | ❌ ADR-003 위반 | low | ADR-003 "amplify-only mandate" 직접 충돌 |
| **D. Tournament 폐기 + cell.mult amplify-only + Strategy hard limit** | 폐기 | 1.0~2.0 | 완전 일관 ✅ | medium | Pro: block 0 + scale 제어 / Con: hard limit 자체가 새 mechanism |
| **E. Tournament 폐기 + Per-strategy lifecycle states** | 폐기 (status 영역 redesign) | 1.0~2.0 | 완전 일관 ✅ | high | Pro: explicit lifecycle / Con: 큰 redesign |

---

## 3. 분석 — 각 옵션 vault grounded

### Option A — 가장 정합 (북극성 mandate) but scale risk
- DEMOTE + pruning 폐기 일관성
- 178 → 250+ → 500+ strategies 누적 가능성
- Perf cost (signal evaluation latency)
- Loser strategies 영원 base size trade (paper account empirical 정합)

### Option B — 부분 일관 (Tournament Elo 학습 유지)
- Tournament 의 정당한 학습 메커니즘 (Elo head-to-head) 유지
- `status='disabled'` set 만 폐기 = ELIMINATED log 만 emit, strategy 그대로 active
- Wave 5 pruning 폐기와 같은 패턴 (CAP overflow log 만, disable X)
- Pragmatic + amplify-only ✅

### Option C — REJECT (ADR-003 위반)
- cell.mult range 0.1~2.0 = loser 0.1× = dampen
- ADR-003 amplify-only clamp mandate 직접 위반
- `feedback_no_defensive_param_dampen` 위반

### Option D — Hard limit + Tournament 폐기
- Tournament 폐기 일관성 + scale 안전 (hard limit)
- Hard limit 자체가 새 mechanism (block paradigm 의 한 형태?)
- 단 hard limit 은 mutation rate 와 결합 (Wave 5 mutation rate decay 와 일관)
- Pragmatic + 안전

### Option E — Per-strategy lifecycle states
- `state: candidate / learning / mature / retired`
- Tournament 가 state 전환만 (status='disabled' set X)
- Explicit lifecycle, observability ↑
- 큰 redesign — 다른 영역 영향

---

## 4. 권장 — Option B (Phase 3) + Option D (Phase 4) 단계적

### Phase 3 — Tournament status='disabled' set 폐기 (Option B)

**File**: `invasion/strategy/tournament.py:317-318`

OLD:
```python
role = "ELIMINATED"
s["status"] = "disabled"  # ← BLOCK PARADIGM
s["disabled_at"] = time.time()
report["actions"].append(f"{sid}: ELIMINATED (Elo {self.elo.get(sid):.0f} < {ELO_FLOOR})")
```

NEW:
```python
role = "ELIMINATED_LOG"
# WAVE-6 PHASE-3 DEPRECATED 2026-04-28: status='disabled' set 폐기 (block paradigm
# 일관성, INSIGHT-025 phase 3). Tournament Elo 학습은 유지 — empirical 검증
# 데이터로만 활용. cell.mult amplify-only sizing 으로 자연 도태 의존.
report["actions"].append(
    f"{sid}: ELIMINATED_LOG (Elo {self.elo.get(sid):.0f} < {ELO_FLOOR}, "
    f"status set DEPRECATED)")
```

### Phase 4 — Strategy scale 제어 (Option D)

**Goal**: Tournament + pruning 모두 폐기 후 strategies 무한 누적 방지.

**Mechanism**:
- Hard limit `evolver_max_total_strategies` (default 500, range 200-1000)
- 도달 시 mutation rate decay 0 (생성 정지)
- 또는 Wave 5 mutation rate decay 자동 도달 (250→400→500 자연 한계)

**Preg 신규**:
```python
_reg("evolver_max_total_strategies", 500, (200, 1000), "evolve",
     "strategy/evolver.py:_check_scale_limit (Wave 6 Phase 4)",
     "Strategies 총 hard limit — 도달 시 mutation 정지")
```

---

## 5. North Star alignment

- ✅ Block 0 (status='disabled' set 폐기, Tournament Elo 학습은 유지 = empirical 검증)
- ✅ Amplify-only mandate (cell.mult 1.0~2.0 그대로, ADR-003 정합)
- ✅ DEMOTE/pruning 폐기 일관성
- ✅ Aggressive contrarian (모든 strategies amplify-only base size trade)
- ⚠️ Loser strategies trade flow = 손실 노출 (paper account empirical 정합)

---

## 6. Risk

1. **Strategies 무한 누적** — Phase 4 hard limit 으로 제어, mutation rate decay 와 결합
2. **Perf 영향** — 178 → 500 strategies = signal evaluation cost 증가, 측정 필요
3. **Loser strategy traffic** — 영원 base size trade = paper 손실 증가 expected
4. **Tournament 의 정당성** — Elo + ELIMINATED 학습 데이터는 유지 (관찰만, 차단 X)
5. **`engine.py:347` disable() method** — 다른 path (legacy callers) 가 호출 가능, grep 확인 필요

---

## 7. Implementation Plan

### Phase 3 (이 spec)
- Tournament line 317-318 status='disabled' 폐기
- ELIMINATED_LOG 만 emit
- Tournament Elo 학습 유지 (empirical 검증 데이터)
- AST + import smoke
- Bot restart + 1-7 days observe (strategies count 추세)

### Phase 4 (별도 spec, Phase 3 효과 측정 후)
- evolver_max_total_strategies hard limit
- mutation rate decay 와 결합
- Strategies count 안정화 검증

---

## 8. References

- Vault: [[INSIGHT-025]] [[INSIGHT-019]] [[INSIGHT-024]] [[ADR-003]]
- Memory: [[feedback_no_block_filter_architecture]] [[feedback_no_defensive_param_dampen]] [[feedback_overhaul_over_incremental]] [[feedback_aggressive_always_profit]]
- Code: `invasion/strategy/tournament.py:317-318`, `invasion/strategy/evolver.py`, `invasion/strategy/engine.py:347`

---

## 9. Jin 결정 영역 (architectural 큰 redesign)

이 spec 은 draft. Jin 결정:
- Phase 3 진행 (Tournament status set 폐기) — 가장 일관 정합
- Phase 4 함께 / 후속
- Option D (hard limit 추가) 선택?
- Option E (per-strategy lifecycle) 더 architectural?

자율 dispatch X — Jin 결정 후 진행 (Tournament 자체 변경, 큰 영향).
