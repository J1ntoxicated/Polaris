# Audit T13 — Hardcoded Delta (Plan v2.2 D1)

> **Base**: `docs/HARDCODE_AUDIT.md` (04-17 snapshot, 299 preg / Tier 분류 / Top 20 후보 완결)
> **Scope**: T13 추가 발견 + D0.5 migrate 효과 + 점진 이관 후보.

## 1. D0.5 실행 효과 (commit `6ef41ffd`)

mult preg 11건 bounds low → 1.0 migrate. default ≥ 1.0 조건으로 **live value 무변화**. learner drift 재발 방지.

- `session_breakout_mult` / `exit_vol_mult_{crypto,forex,indices}` / `exit_hold_mult_crypto` / `exit_trail_mult_{crypto,forex,commodity,indices}` / `position_size_mult_europe` / `fsm_trail_loose_mult`

## 2. 잔재 — default<1.0 (Jin debate 대기, 2건)

| preg | default | bounds | 이유 | 제안 |
|---|---|---|---|---|
| `slippage_size_adjust_mult` | 0.5 | (0.1, 1.0) | 슬리피지 큰 상황 size 감소 (risk mgmt) | amplify-only 적용 시 slippage 무시 → 북극성과 실무 trade-off |
| `position_size_mult_asia` | 0.9 | (0.3, 2.0) | Asia 세션 낮은 유동성 | 1.0 으로 올려 동등 비중 / 또는 session axis cell learner 에 위임 |

## 3. Provider weight 17건 — 별개 scope

`provider_mult_*` bounds (0.0, 2.0) 은 weight composite. 0.0 = 완전 제외 = block-filter 와 등가 효과. Plan v2.3 별도 debate (원안 #10 + D-B quarantine 과 연결).

## 4. 신규 하드코딩 발견 (04-17 이후)

### exit_cycle.py:444-483 fallback 값

| line | 변수 | fallback | preg default | 정합? |
|---|---|---|---|---|
| 446 | `_loss_cap` | -2.0 | -2.0 | ✅ |
| 449 | `_profit_ext` | 0.5 | 0.5 | ✅ |
| 462 | `_time_to_trail_min` | 0.3 | 0.1 | ⚠️ 불일치 (preg default 가 더 aggressive) |

→ minor code smell. preg 조회 실패 path 는 사실상 dead code (preg 등록됨). 손대지 않음 (`feedback_no_feature_bloat`).

### position.py:337 fallback

`"crypto"` → `"unknown"` 교정됨 (D-D `d8844704`). 기존 crypto 로 저장된 레거시 DB row 는 Plan O forensic cleanup 별도.

## 5. 점진 이관 후보 (Plan v2.3 D# 배정)

| 우선도 | 영역 | 설명 |
|---|---|---|
| 🔴 | Provider weight 17건 | 별도 debate (원안 #10 + D-B) |
| 🟡 | `position_size_mult_asia` / `slippage_size_adjust_mult` | Jin debate 후 결정 |
| 🟢 | 기존 `HARDCODE_AUDIT.md` Top 20 잔여 | adaptive_tuner 확장 시 순차 |

## 6. 참조

- Base snapshot: `docs/HARDCODE_AUDIT.md` (04-17)
- Taxonomy: `docs/metric_taxonomy.yaml` (D0)
- D0.5 commit: `6ef41ffd` (mult bounds 11건 migrate)

---

**상태**: T13 MVP 완료. 2건 Jin debate + 17건 별도 scope 제외 전부 migrate 또는 적정.
