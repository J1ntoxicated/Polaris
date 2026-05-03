# Wave 10 Phase 2d — Cell-aware Partial Close Activation

**Date**: 2026-04-28 16:30
**Status**: Draft (Jin review)
**Skill**: superpowers:brainstorming → writing-plans (next)
**Vault refs**: [[INSIGHT-016]] [[INSIGHT-026]] [[ADR-005]] [[2026-04-28-wave-10-profit-management-exit-logic-design]] (parent spec)

---

## 1. Context — Discovery vs Original Assumption

Parent Wave 10 spec (Phase 2 Step D) 의 가정:
> "TP Ladder (partial close) 50% close at first target / 50% trail-up. **새 architectural — exit_cycle.py partial close mechanism 추가**"

코드 audit 결과 가정 **틀림** — partial close 메커니즘은 이미 fully wired:
- `_exit_classic_path.py:371-374` — `pnl >= partial_threshold` 시 "TRAIL PARTIAL" emit
- `close_handler.py:194-207` — 50% size close + 남은 50% 의 trail × 1.5, hard_stop × 1.5 widening
- `_exit_classic_path.py:284-285` — `partial_closed=True` 시 trail distance 2x ("TRAIL SWING")
- DB schema: `position.partial_closed` boolean, partial trade 별도 row insert (LASERKILL-PR1 + WIRE5 patch 적용된 fully-instrumented partial trade economics)

진짜 gap = **30d 실측 PARTIAL fire = 0건**. Threshold 가 winner peak 보다 훨씬 커서 영구 dormant.

## 2. Empirical Data (30d closed winners)

| Group | partial_close threshold (group default) | winner avg_peak | 도달 비율 | n trades |
|---|---|---|---|---|
| crypto    | 1.5% | 0.41% | 27% | 3683 |
| stock     | 2.5% | 0.76% | 30% |  148 |
| commodity | 2.0% | 0.47% | 23% |   50 |
| forex     | 0.8% | 0.28% | 35% |   33 |
| etf       | 1.5% | 0.30% | 20% |   33 |
| indices   | 1.2% | 0.10% |  8% |    4 |

평균 winner peak / threshold = ~24% → winner 가 거의 도달 못 하는 ceiling. PARTIAL/SWING fire 0건 = mechanism 영구 sleep.

Group max_peak 도 보면 — crypto 99% (rare moonshot), stock 7%, 다른 그룹 모두 < 7%. p50 (median) 이 더 정확한 representative.

## 3. Decision Matrix

### Option A — Group threshold 낮춤 (즉시 deploy)

| Group | new threshold (peak avg × 0.5) |
|---|---|
| crypto    | 0.20% (was 1.5%) |
| stock     | 0.38% (was 2.5%) |
| commodity | 0.24% (was 2.0%) |
| forex     | 0.14% (was 0.8%) |

Trade-offs:
- ✅ 즉시 deploy, 코드 변경 1 line per group
- ❌ Group 별 hardcoded — cell 별 차이 무시 (winning strategy 와 losing strategy 둘 다 동일)
- ❌ 자연 도태 정합성 약함 (operator decision)

### Option B — Cell-aware partial_close threshold (Phase 2e 패턴 재사용) ⭐ 권고

`cell_exit_learner` 확장 — `optimal_partial_pct` 컬럼 추가, winner `max_profit_pct` p50 학습. Wave 10 Phase 2e (cell-aware TP) + Wave 11 Phase 1 (UPSERT persistence) 패턴 그대로 재사용.

새 column: `optimal_partial_pct REAL` (similar pattern to `optimal_tp_pct`).
Reader site: `_exit_classic_path.py:371` partial_threshold override.

```python
# Wave 10 Phase 2d (proposed):
partial_threshold = ep.get("partial_close") or preg("partial_close")
try:
    _learned_pp = _cel_get(_ck, "partial", float(partial_threshold))
    # amplify-only direction = LOWER threshold = winner 빨리 절반 lock = protect
    if _learned_pp > 0 and _learned_pp < partial_threshold:
        _log_event("EXIT", f"cell-aware PARTIAL {ticker} "
                   f"{partial_threshold:.2f}% → {_learned_pp:.2f}% (lock)", "debug")
        partial_threshold = _learned_pp
except Exception:
    pass  # silent fallback
```

Trade-offs:
- ✅ 자연 도태 — 각 cell 자체 winner peak 분포 학습 → strategy/exchange/group/direction 별 자동 tune
- ✅ Phase 2e + Wave 11 Phase 1 코드 패턴 그대로 → low risk
- ✅ amplify-only mandate (lower threshold = winner protect 강화 = block paradigm 0)
- ✅ Cell 자체 winner-poor 면 학습 X → 기존 group default fallback (no harm)
- ⚠️ 학습 컬럼 한 번 더 추가 (n=6 → n=7) — Wave 11 Phase 1 UPSERT 가 보존하므로 안전

### Option C — 자동 group threshold tune (Option A + 학습)

Group avg_peak 의 50% 를 hourly 자동 update — Option B 의 단순 버전. cell 단위 미달 데이터.

❌ 기각 — Option B 가 더 fine-grained + 같은 패턴 재사용.

## 4. Recommended Approach: Option B

**Phase 2d = Cell-aware partial_close threshold** (Option B).

### Architecture

기존 학습 컬럼 6개 → 7개 확장:
```
optimal_trail_activate
optimal_bep_activate
optimal_max_hold_sec
optimal_hard_stop_pct
optimal_tp_pct           ← Phase 2e (이번 세션)
optimal_partial_pct      ← Phase 2d (본 spec)
exit_optim_n_samples
```

### Learning Logic

`learn_cell_exit_thresholds` 에 추가:
```python
# 6) partial: winner max_profit_pct p50 (median).
# p50 = "보통 winner 가 도달하는 peak" → 절반 lock 안전 지점.
# p75 (TP) 보다 낮은 leverage 점 → partial close 후 swing 가능.
optimal_partial = None
if winner_peaks and len(winner_peaks) >= 4:
    wp_sorted = sorted(winner_peaks)
    p50_idx = len(wp_sorted) // 2
    optimal_partial = wp_sorted[p50_idx]
```

### Reader (amplify-only)

`_exit_classic_path.py:371`:
```python
partial_threshold = ep.get("partial_close") or preg("partial_close")
# Wave 10 Phase 2d: cell-aware partial threshold (amplify-only).
# learned < base = LOWER threshold = winner 빨리 lock = protect.
# learned >= base = winner 늦게 lock = winner cycle 단축 위험 = REJECT.
try:
    _ck_pp = _cel_key2(position)  # reuse existing _ck2 if try-block scope
    _ck_pp["regime"] = regime or "neutral"
    _learned_pp = _cel_get2(_ck_pp, "partial", float(partial_threshold))
    if _learned_pp > 0 and _learned_pp < partial_threshold:
        _log_event(
            "EXIT",
            f"cell-aware PARTIAL {position.ticker} "
            f"{partial_threshold:.2f}% → {_learned_pp:.2f}% (lock)",
            "debug",
        )
        partial_threshold = _learned_pp
except Exception as _cel_pp_e:
    _log_event("EXIT", f"cell PARTIAL override err: {_cel_pp_e}", "debug")

if partial_threshold > 0 and pnl >= partial_threshold:
    if not position.partial_closed:
        return _exit(f"TRAIL PARTIAL {pnl:+.2f}% (threshold {partial_threshold}%)")
```

### Schema Migration

`store_core.py` ALTER ADD list 에 `"optimal_partial_pct REAL"` 추가 (Wave 11 Phase 1 UPSERT 가 보존).

### `_THRESHOLD_COL` 등록

```python
_THRESHOLD_COL = {
    ...,
    "tp": "optimal_tp_pct",
    "partial": "optimal_partial_pct",  # ← new
}
```

## 5. North Star Alignment

- ✅ Block paradigm 0 (학습값 < base 일 때만 적용 = 자연 protect, > base reject = silent)
- ✅ Amplify-only — partial close 자체가 winner protect (절반 lock)
- ✅ `feedback_no_block_filter_architecture` (cell 자체 학습, 차단/skip 없음)
- ✅ `feedback_loss_profit_asymmetry` 정합 (winner 잠금 강화)
- ✅ `feedback_overhaul_over_incremental` 정합 (group hardcoded → cell-adaptive learning architecture)
- ✅ Wave 11 Phase 1 UPSERT 와 통합 (학습 컬럼 영구 보존)

## 6. Risk Analysis

### High
1. **Partial close 빈도 폭발** — cell 학습값이 매우 낮아지면 (peak avg 0.05% 같은 약체 cell) trade 마다 partial fire. 약체 cell 은 amplify-only 정합 — winner 50% lock 빨리 = drawdown 보호. 그러나 fee 압박 있음 (partial close = 추가 fee).
   - **Mitigation**: lower bound clamp (`optimal_partial_pct >= max(learned, fee_floor + 0.1%)` 등). Fee floor 기준.

### Medium
2. **Partial close 후 trail 1.5x widening** — 기존 `close_handler.py:204` 가 hardcoded. partial fire 빈도 증가 시 1.5x 도 누적 영향 검토 필요. 본 spec 범위 밖 — 추후 측정.
3. **stock RTH timing** — Alpaca stock 의 partial close 가 long-hold 환경 (RTH 6.5h) 에서 어떻게 작동? 기존 0.76% peak avg → 학습값 ~0.4% → close 빈도 low (정상 RTH). 위험 낮음.

### Low
4. **Schema migration race** — Wave 11 Phase 1 UPSERT + cell_exit_learner UPDATE 가 같은 row 에 동시 write. SQLite WAL serialize 가 보호. 안전.

## 7. Verification Plan

### Per-batch (immediate)
- AST + import smoke
- preg + schema migration verify (`optimal_partial_pct` column 존재)
- learn_cell_exit_thresholds dict shape 확인 (`partial` key 추가)
- `_THRESHOLD_COL["partial"]` lookup 정상

### 24h post-deploy
- PARTIAL/SWING fire 빈도: **0 → ?** (target: 의미있는 fire — 100+ partial trades/24h)
- partial close trades 의 economics: avg_pp / total / hold_sec
- Drawdown reduction: 같은 winner cell 의 final PnL 분포 (full TRAIL vs PARTIAL+SWING)

### 1-7 days
- cell.optimal_partial_pct 채워짐 비율 (cell_exit_learner hourly batch 후 cells 중 NOT NULL %)
- Partial → SWING transition 후 final exit 분포 (TP/TRAIL/STOP/SIGNAL)
- 24h NET swing 추세 (winner protect 효과 측정)

## 8. Why "Activation" Not "Mechanism"

기존 30d 실측 = PARTIAL 0건 = mechanism 영구 sleep. 진짜 root cause = **threshold 가 group 별 hardcoded 너무 큼**.

Phase 2c (TRAIL 가속 mechanism) + Phase 2e (cell-aware TP) + Phase 2d (cell-aware partial) 3-rope 구조:
- TP (p75) — 강한 winner 정점 lock
- Partial (p50) — 보통 winner 절반 lock + swing
- TRAIL (giveback_mult) — winner cycle 가속 (operator tune)

3-tier winner protect 시스템 자연 도태 정합 — 각 cell 자체 학습값으로 자동 tune.

## 9. References

- Vault: [[INSIGHT-016]] (idea pool 10 exit redesign), [[INSIGHT-026]] (STOP drag), [[INSIGHT-027]] (UPSERT persistence), [[ADR-005]] (Wave 11 Phase 1)
- Memory: [[feedback_no_block_filter_architecture]], [[feedback_loss_profit_asymmetry]], [[feedback_overhaul_over_incremental]]
- Code:
  - `invasion/strategy/cell_exit_learner.py` (학습 logic)
  - `invasion/trade/_exit_classic_path.py:371-374` (partial close trigger)
  - `invasion/trade/close_handler.py:194-207` (50% close mechanism)
  - `invasion/data/store_core.py` (schema migration)

## 10. Decision

**Recommend Option B (Cell-aware partial_close)** — Phase 2e + Wave 11 Phase 1 패턴 그대로 재사용, 자연 도태 정합, low risk.

다음 단계: writing-plans skill 로 implementation plan 작성 → batch dispatch.
