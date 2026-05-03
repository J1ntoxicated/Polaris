# AI Refine+Reset Architecture — Implementation Plan (단계 2-5)

> Spec: `docs/superpowers/specs/2026-05-01-ai-architecture-redesign.md`
> ADR: `vault/03_knowledge/decisions/ADR-006-ai-refine-reset-architecture-2026-05-01.md`
> Vault: `[[INSIGHT-031]]`, `[[ADR-006]]`
> 단계 1 적용 완료 (commit `a47d3e4e`).

**Goal**: ADR-006 의 4-Layer Refine+Reset architecture 완성 (단계 2-5).

**Architecture**:
```
Layer 0  Data ingestion (existing)
Layer 1  Signal Fusion (NEW — conviction score)
Layer 2  봇별 Execution (existing + diet)
Layer 3  Cell Matrix learning (existing + extended dim)
Layer 4  AI 4-mode meta-coach (NEW)
   M1 Daily Pattern Synthesis
   M2 Weekly Strategy Curator
   M3 Hourly Northstar Audit
   M4 Event Crisis Coach
Reset L1-L4 (NEW autonomous triggers)
```

**Tech Stack**: Python 3, SQLite, existing ParamRegistry / cell_matrix / tournament infrastructure.

---

## 단계 2 — Outcome Trace + M3 Prototype (1-3일)

### Task 2.1: ai_calls schema extend

**Files:**
- Modify: `invasion/data/store_core.py` (or schema migration)
- Test: `tests/ai/test_ai_calls_schema.py`

**Step 1: Migration SQL**

```sql
ALTER TABLE ai_calls ADD COLUMN decision_taken INTEGER DEFAULT 0;
ALTER TABLE ai_calls ADD COLUMN action TEXT;
ALTER TABLE ai_calls ADD COLUMN outcome_window_sec INTEGER;
ALTER TABLE ai_calls ADD COLUMN outcome_pnl_change REAL;
ALTER TABLE ai_calls ADD COLUMN outcome_kpi_change TEXT;
ALTER TABLE ai_calls ADD COLUMN mode TEXT DEFAULT NULL;
```

**Step 2: Outcome trace wrapper** in `invasion/ai/orchestrator.py`:

```python
def record_outcome(self, call_id: int, *, action: str | None = None,
                   decision_taken: bool = False,
                   outcome_window_sec: int = 3600) -> None:
    """기록된 AI 호출에 대한 outcome 측정 트리거.
    background task 가 outcome_window_sec 후 KPI 측정 + DB update."""
    # ...
```

**Step 3: Outcome measurement task**:
- Background scheduler 가 ai_calls 테이블 query
- ts + outcome_window_sec 도달한 row → trade outcome / KPI 측정 → update

**Commit**: `feat(ai): outcome trace schema + measurement wrapper`

### Task 2.2: M3 Northstar Audit prototype

**Files:**
- Create: `invasion/ai/m3_northstar_audit.py`
- Test: `tests/ai/test_m3_audit.py`

**Spec**:
- 매시간 1회 호출
- 입력: 시스템 KPI snapshot (NET, WR, exit_dist, regime, cell_sparse, drawdown)
- 출력: 위반 항목 + 자동 수정 (bounded)
- Cooldown: same param 60min, change ≤20%

**Implementation**:

```python
# invasion/ai/m3_northstar_audit.py
class M3NorthstarAudit:
    def __init__(self, orch, store):
        self.orch = orch
        self.store = store
        self._last_fix = {}  # param_name → ts

    def audit_and_fix(self) -> list[dict]:
        kpis = self._collect_kpis()
        violations = self._detect_violations(kpis)
        fixes = []
        for v in violations:
            if not self._can_fix(v):
                continue  # cooldown 또는 bound 초과
            fix = self._apply_bounded_fix(v)
            if fix:
                fixes.append(fix)
        return fixes

    def _collect_kpis(self) -> dict:
        # NET, WR, exit_dist, regime, cell_sparse, drawdown
        ...

    def _detect_violations(self, kpis) -> list:
        # `feedback_no_defensive_param_dampen` 위반 등
        ...
```

**Commit**: `feat(ai): M3 Northstar audit prototype`

### Task 2.3: M3 wire to scheduler

**Files:**
- Modify: `invasion/ticks/hourly_stats.py` (or scheduler)

```python
# Hourly trigger
m3 = M3NorthstarAudit(orch, store)
fixes = m3.audit_and_fix()
log_event("M3", f"audit: {len(fixes)} fixes applied", "info")
```

**Commit**: `feat(ai): M3 hourly wiring`

---

## 단계 3 — Signal Fusion (3-7일)

### Task 3.1: conviction_score 계산기

**Files:**
- Create: `invasion/signal/fusion.py`
- Test: `tests/signal/test_fusion.py`

**Spec**:
```python
def conviction_score(signals: dict, weights: dict) -> float:
    """
    signals: {bb: float, rsi: float, atr: float, microstructure: float, ...}
    weights: {bb: 0.3, rsi: 0.2, ...}
    Returns: weighted sum normalized to [0, 1]
    """
```

### Task 3.2: 봇별 weights config

**Files:**
- Modify: `data/frozen_params.json`

```json
"signal_weights_by_bot": {
    "spot_crypto": {"bb": 0.3, "rsi": 0.2, "microstructure": 0.3, "funding": 0.2},
    "spot_stock": {"bb": 0.4, "rsi": 0.3, "macd": 0.2, "vol": 0.1},
    "cfd_main": {...}
}
```

### Task 3.3: cell_matrix dim 추가 — `signal_mix`

**Files:**
- Modify: `invasion/strategy/cell_matrix.py`
- Migration script

**Spec**: cell_matrix 9-dim (기존 8 + signal_mix). signal_mix = encoded weight combination.

### Task 3.4: SPOT crypto 봇에 적용 (1봇 prototype)

**Files:**
- Modify: `invasion/spot/signal_scalp.py`

기존 `evaluate()` → 신규 `evaluate_with_fusion()` (conviction score 사용).

**Commit**: `feat(signal): conviction score + bot weights`

---

## 단계 4 — Strategy Diet (1-2주)

### Task 4.1: 봇별 alpha source ADR

**Files:**
- Create: `vault/03_knowledge/decisions/ADR-007-bot-alpha-source-2026-05-XX.md`

각 봇의 핵심 알파 source 명시 (spec 의 표 참고).

### Task 4.2: 살아남는 strategies 선정

**Process**:
- Tournament elo 상위 5-10 / 봇
- 봇별 alpha source 정합성 매칭
- 폐기 strategies → tournament 비활성

### Task 4.3: 신규 strategy 5-10 / 봇 정의

**Files:**
- `invasion/strategy/family_seeds.py` 정리
- 봇별 strategy 모듈 (예: `invasion/spot/strategies/crypto/`)

### Task 4.4: 1주 검증

- Cell matrix 빠른 채움 확인
- Outcome 측정 (각 봇 baseline)

**Commit**: `feat(strategy): bot diet + alpha source`

---

## 단계 5 — AI 4-mode 완성 (2-4주)

### Task 5.1: M1 Daily Pattern Synthesis

**Files:**
- Create: `invasion/ai/m1_daily_pattern.py`

**Spec**:
- 매일 1회 (UTC midnight 또는 Asia close)
- 입력: 24h trades (groupby strategy/regime/ticker)
- 출력: pattern + signal weight 권고
- Cell matrix 가 자동 적용 (M1 직접 수정 X)

### Task 5.2: M2 Weekly Strategy Curator

**Files:**
- Create: `invasion/ai/m2_weekly_curator.py`

**Spec**:
- 매주 1회 (Sunday UTC)
- 입력: 모든 strategy elo + outcome
- 출력: 신규 strategy 후보 + 죽일 명단
- Tournament paper 1주 → 자동 promotion

### Task 5.3: M4 Event Crisis Coach

**Files:**
- Create: `invasion/ai/m4_crisis.py`

**Spec**:
- Trigger: drawdown >5%, regime crisis_high, 1h NET <-$500
- Hard rule 우선 (예: -5% → 즉시 size cut 50%)
- AI 는 hard rule 안에서 % 조정
- 자율 적용 (alert 가 아님)

### Task 5.4: Reset L1-L4 trigger

**Files:**
- Create: `invasion/ai/reset_engine.py`

**Spec**:

```python
class ResetEngine:
    def check_l1(self, strategy_id) -> bool:
        # 7d expectancy <0 + n>30 → True
        ...
    def check_l2(self, cell_key) -> bool:
        # 7d ROI <0 + sparse <30% → True
        ...
    def check_l3(self, bot_name) -> bool:
        # 7d -15% paper loss → True
        ...
    def check_l4(self) -> bool:
        # 28d 5+ resets → True (escalate)
        ...
```

### Task 5.5: Auto-revert all layers

- M1 weight → 24h 비교 → 더 나쁘면 revert
- M2 strategy promotion → 1주 elo <threshold → demote
- L2 cell reset → 24h 비교 → 더 나쁘면 backup 복원

### Task 5.6: 기존 advisor 코드 archive

**Files**:
- Move `invasion/ai/live_exit.py` → `invasion/ai/_archive/`
- Move 기존 `invasion/ai/live.py` 의 advisor 부분
- 새 modules 에서 only 필요한 부분 import

**Commit**: `feat(ai): M1/M2/M4 + reset engine + advisor archive`

---

## 단계 5 종료 시 deliverable

- ADR-006 fully implemented
- AI cost $2-5/일 (95% ↓)
- 모든 호출 outcome trace
- 자율 시스템 24/7 운영
- Jin 개입: 0% (월 1회 sanity 선택)

## Self-Review

### Spec coverage
| Spec section | Plan task |
|---|---|
| Outcome trace | 2.1 |
| M3 Audit | 2.2-2.3 |
| Signal Fusion | 3.1-3.4 |
| Strategy diet | 4.1-4.4 |
| M1 Daily | 5.1 |
| M2 Weekly | 5.2 |
| M4 Crisis | 5.3 |
| Reset L1-L4 | 5.4 |
| Auto-revert | 5.5 |

### Risk mitigation per task
- Task 2.2 M3: cooldown 60min + change ≤20% (FROZEN)
- Task 3.4 fusion: 1봇 prototype 후 점진
- Task 5.2 mutation: paper 7d + sandbox size $50
- Task 5.4 reset: API outage detector pause

### Estimated 작업량

| 단계 | line | 시간 |
|---|---|---|
| 단계 2 | ~600 | 1-3일 |
| 단계 3 | ~500 | 3-7일 |
| 단계 4 | ~300 (정리) | 1-2주 |
| 단계 5 | ~1500 | 2-4주 |
| **Total** | **~2900** | **~1달** |

---

## Approval

- [x] Jin mandate (오늘 자동 진행 권한 부여)
- [x] ADR-006 작성 완료
- [x] 단계 1 적용 (commit `a47d3e4e`)
- [ ] 단계 2-5 점진 적용 (subagent driven 또는 inline)
