# Cell-Exit-Learner Latent Bug Fix — Trail/BEP/Hard_stop Learning

**Date**: 2026-04-28
**Vault refs**: [[INSIGHT-016]] [[2026-04-28-wave-2a-retroactive-fix-design]]

---

## 1. Context

Wave 2A B3 retroactive (commit `5f91016e`) 의 dev-coder 보고에서 발견된 **latent bug**:

> **Latent bug in `cell_exit_learner.py`**: same ticker-mismatch silently fails for `trail`, `bep`, `hard_stop` columns (not just `max_hold`).

**Current state** (DB SQL):
- 727 cells / 0 cells with `optimal_trail_activate`
- 727 cells / 0 cells with `optimal_bep_activate`
- 727 cells / 0 cells with `optimal_hard_stop_pct`
- 727 cells / 0 cells with `exit_optim_n_samples > 0`

= Same `trades.ticker=''` query silent-fail 패턴 (Wave 2A B3 와 동일 root cause)

---

## 2. Fix Spec

**File**: `invasion/strategy/cell_exit_learner.py`

같은 패턴 (Wave 2A B3 retroactive `_learn_optimal_max_hold_for_cell`) 으로:

### Step A — `_learn_optimal_trail_for_cell`
- Winner trade `max_profit_pct` 분포 학습 → `optimal_trail_activate`
- p25 (winner 가 보통 trail 발동까지 도달한 profit %)
- amplify-only: 학습값이 base trail < 면 update X (trail 더 빠른 lock 정합)

### Step B — `_learn_optimal_bep_for_cell`
- Winner+breakeven trade 의 max_profit 도달 분포 → `optimal_bep_activate`
- p10 (BEP 발동 conservative threshold)

### Step C — `_learn_optimal_hard_stop_for_cell`
- Loser trade `min_pnl_pct` 분포 → `optimal_hard_stop_pct`
- amplify-only: 학습값이 base stop < 면 update X (더 tight stop 정합)
- 또는 winner trade 의 max_drawdown 분포 (winner 가 견딘 drawdown)

### Step D — Trigger 통합

기존 `cell_exit_learner.py` 의 trigger function 에 4 calls (max_hold + trail + bep + hard_stop) 통합:
```python
def learn_all_optimal(conn, cell_key, base_params):
    _learn_optimal_max_hold_for_cell(conn, cell_key, base_params['max_hold_sec'])
    _learn_optimal_trail_for_cell(conn, cell_key, base_params['trail_activate'])
    _learn_optimal_bep_for_cell(conn, cell_key, base_params['bep_activate'])
    _learn_optimal_hard_stop_for_cell(conn, cell_key, base_params['hard_stop_pct'])
```

또는 Wave 2A B3 retroactive 의 `_learn_optimal_max_hold_for_rows` 와 같은 패턴 — bulk processing.

### Step E — Ticker filter 정합

기존 query 가 `trades.ticker=''` 사용 (silent fail) → 6-dim aggregate row 에서는 ticker filter 제거, 8-dim 에서만 specific ticker.

```python
if ticker == "":
    # 6-dim aggregate cell — ticker filter 없음
    sql = "SELECT ... FROM trades WHERE strategy_id=? AND direction=? ..."
else:
    # 8-dim specific cell
    sql = "SELECT ... FROM trades WHERE strategy_id=? AND direction=? AND ticker=? ..."
```

**Commit**: `feat(cell-exit-learner retroactive): trail/bep/hard_stop learning logic (latent fix)`

---

## 3. North Star alignment

- ✅ Block 0 (학습 logic 추가만)
- ✅ Amplify-only (학습값이 base 보다 strict 한 방향 일 때만 update)
- ✅ `feedback_no_quick_patch_ever` (Wave 2A B3 retroactive 와 같은 정직한 latent bug fix)
- ✅ INSIGHT-016 idea #5 spec 정합

---

## 4. Verification

- AST + import smoke
- DB SQL 1-2 cycle 후: cells with `optimal_trail_activate > 0` count
- log_event "CELL_LEARN" 학습 fire 추적

---

## 5. References

- Vault: [[INSIGHT-016]] [[2026-04-28-wave-2a-retroactive-fix-design]]
- Memory: [[feedback_no_quick_patch_ever]] [[feedback_audit_fstring_prefix_scan]]
- Code: `invasion/strategy/cell_exit_learner.py`
