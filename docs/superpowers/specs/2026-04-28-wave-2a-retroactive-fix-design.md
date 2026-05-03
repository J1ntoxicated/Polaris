# Wave 2A Retroactive Fix — JSON Sync + Cell-aware Learning

**Date**: 2026-04-28 00:50
**Vault refs**: [[INSIGHT-016]] [[INSIGHT-024]] [[ADR-004]] [[canonical_cell_matrix]] [[2026-04-27-polaris-structural-overhaul-design]]

---

## 1. Context

Forensic 결과 Wave 2A 의 2 wires 가 작동 안 함:

### Issue 1 — Strategies JSON ↔ DB sync mismatch
- Wave 2A B5 (commit `d6129946`) 6 trend strategies SQL UPDATE active 적용
- 그러나 `data/strategies/*.json` file status 는 disabled 잔존
- Bot startup `engine.reload()` 가 JSON 에서 다시 읽음 → DB disabled overwrite (revert)
- 결과: 6 strategies 영구 disabled, trade_count=0

### Issue 2 — Wave 2A B3 cell-aware max_hold 학습 logic 부재
- Commit `a02b71c4` "feat(time cell-aware max-hold)" — schema 컬럼 + preg 추가만
- `cell_matrix.py` 안에 `optimal_max_hold_sec` 학습 logic **0 hits** = 코드 미작성
- DB 727 cells / 0 samples / 0 maxhold_learned / 0 trail_learned / 0 bep_learned
- 즉 schema 컬럼은 있지만 학습 site 자체 wire 안 됨

`feedback_no_quick_patch_ever` 위반: dev-coder dispatch 가 incomplete (schema only, logic 없음).

---

## 2. Batch 1 — Issue 1 JSON Sync Fix

**Files**: `data/strategies/*.json` (6 files)

**Action**:
```bash
for s in crypto_momentum_reversal_g1_gauss contrarian_commodity_g55_gauss \
         contrarian_commodity_g54_ai contrarian_commodity_g1_bayes \
         contrarian_commodity_g53_ai contrarian_commodity_g57_bayes; do
  python3 -c "import json; p='data/strategies/${s}.json'; d=json.load(open(p)); d['status']='active'; json.dump(d, open(p,'w'), indent=2)"
done
```

**DB sync** (재적용, 안전):
```sql
UPDATE strategies SET status='active'
WHERE name IN (...) AND status='disabled';
```

**Verify**:
- JSON 6개 status="active"
- DB 6개 status="active"
- bot 다음 restart 시 revert 안 함

**Commit**: `fix(wave-2a-b5 retroactive): JSON sync 6 trend strategies (DB+JSON 동기 active)`

---

## 3. Batch 2 — Issue 2 Cell-aware Max_hold Learning Logic

**File**: `invasion/strategy/cell_matrix.py`

**Spec**: cell aggregation 시점 (cell row update 시) 에 winner trade 의 hold_seconds 분포 학습.

**Logic** (INSIGHT-016 idea #5 spec):
```python
def _learn_optimal_max_hold(conn, cell_key, base_max_hold_sec):
    """Winner trade hold_seconds 분포 학습 → optimal_max_hold_sec.
    
    cell aggregation 시점 호출. winner-only (pnl_usd > 0) trades 의
    hold_seconds 분포에서 p75 (또는 median) 사용 → winner 가 보통 얼마나
    오래 hold 했는지.
    
    북극성 amplify-only: optimal_max_hold_sec >= base 일 때만 적용.
    optimal < base 면 base 유지 (loser cell 단축 X).
    
    sample threshold = preg("cell_score_sample_threshold") (default 20)
    """
    # SELECT hold_seconds FROM trades WHERE cell_key matches AND pnl_usd > 0
    # AND status='closed' ORDER BY hold_seconds
    # p75 = sorted[int(len*0.75)]
    # if p75 > base_max_hold_sec * preg("cell_max_hold_extend_max_factor")_min_threshold:
    #     UPDATE strategy_cell_matrix SET optimal_max_hold_sec=p75, exit_optim_n_samples=N
    pass
```

**Trigger site**: `cell_matrix.py` 의 cell aggregation function (n_trades 갱신 site) 직후. 

**Caller path** (exit_cycle.py 의 사용):
- TIME exit 발생 시 cell.optimal_max_hold_sec 활용 (이미 wire 됨, Wave 2A B3 의 _exit_classic_path.py 부분)
- 학습값 NULL 이면 base 유지 (현재 동작)
- 학습값 존재 + base 초과 시 winner extend

**Preg keys** (이미 등록됨, Wave 2A B3):
- `cell_max_hold_extend_max_factor` (2.0)
- `cell_max_hold_learning_enabled` (1)

**Verify**:
- AST + import smoke
- 30m-1h observe — 727 cells 중 일부 maxhold_learned > 0 expected (sample 20 도달 cell)
- log_event "CELL_LEARN" 에 학습 fire 추적 (e.g., "optimal_max_hold_sec learned: cell=X, p75=4500s, n=22")

**Commit**: `feat(wave-2a-b3 retroactive): cell-aware max_hold winner trade p75 learning (insight-016 #5)`

---

## 4. North Star alignment

- ✅ Block 0
- ✅ Amplify-only (학습값 < base 시 base 유지, winner extend only)
- ✅ `feedback_no_quick_patch_ever` (Wave 2A B3 incomplete 의 정직한 retroactive complete)
- ✅ `feedback_overhaul_over_incremental` (단계 누락 보완)
- ✅ INSIGHT-016 idea #5 정확한 spec 적용

---

## 5. Verification Plan

### Per-batch
- AST + import smoke
- DB SQL audit: 6 strategies status / cell.optimal_max_hold_sec 학습 진행

### 1-2 cycle (30m-1h)
- Issue 1: 6 trend strategies trade_count > 0 여부 (commodity entry 시 evolver 가 trend 활용)
- Issue 2: maxhold_learned cells > 0 (winner trade sample 20+ cell)
- log_event "CELL_LEARN" 학습 fire emit

---

## 6. References

- Vault: [[INSIGHT-016]] idea #5, [[INSIGHT-024]], [[canonical_cell_matrix]] Phase 2, [[ADR-004]]
- Memory: [[feedback_no_quick_patch_ever]], [[feedback_no_block_filter_architecture]], [[feedback_overhaul_over_incremental]], [[feedback_sequential_superpowers_vault_organic]]
- Code: `data/strategies/*.json`, `invasion/strategy/cell_matrix.py`
