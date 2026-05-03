# T13 단계 2 설계 확정 (D3/D3.5/D4/D5 통합)

> Plan v2.2 V. 단계 2 해당. Harness 자율 확정 (Jin 승인 gate 대체, auto mode 지속).

## D3 — 신/구 매핑 grep 검증

**Pillar ↔ 기존 자산 매핑**:
| Pillar | 기존 | 신규/확장 | 검증 |
|---|---|---|---|
| 1. Input Taxonomy | 없음 | `docs/metric_taxonomy.yaml` + `_metric_contract.py` (D16a) | ✅ D0 완료 `ecbadba1` |
| 2. Multi-Matrix Cell | `strategy/cell_matrix.py` 6-dim | +ticker +liquidity_tier 8-dim (D-E 동시) | 확정, 구현 D16a~c |
| 3. 3-Tier 프로세스 | 단일 프로세스 | `trade_events` + `signal_queue` + 11 테이블 (D10) | ✅ D10 schema 완료 `2fe92e29`, 프로세스 분리 D17 |
| 4. PHS + Real-time Exit | `exit_cycle.py:475-483` TIME→TRAIL prototype | `phs.py` 6-factor (D19) | prototype 유지, D19 확장 |
| 5. Flow Amplifier | `_pipeline_sizing.py` amplify chain (_ramp/_conviction/_atr_exp) | +flow_signal/liq/slip (D18.5) | 기존 작동, D18.5 확장 |

**H.# ↔ 기존 매핑**:
- H.1 (trade_id write) ✅ D8 `c0d29970`
- H.2 (reconcile + duplicate open) ✅ D9 `ebede747`
- H.4 (Canary + KPI Guard) — D12 확장
- H.5 (Kill Switch) ✅ D6 `321aea19`
- H.10 (Backup) ✅ D7+D7.5 `7eac1ca1`

## D3.5 — canonical_files.md sync

신규 12 row 추가 + 2 row 업데이트. `.claude/docs/canonical_files.md` update 완료 (본 commit).

## D4 — plan_t13_integrated_final.md (Harness 확정)

Jin "다 하라" 지시 하 승인 gate 대체. `tasks/plan_t13_integrated_final.md` 생성 — 현 시점 (D6~D10 완료) 스냅샷 + 잔여 D# 배정.

## D5 — Agent 5 이름/trigger (Phase 4 commit `818562d0` 에 이미 완결, 확인만)

| agent | trigger | 현재 상태 |
|---|---|---|
| `dev-unit-contract-validator` | preg 신규/수정 | 스텁 존재, D16a 에서 활성 |
| `dev-trace-linker` | signals.trade_id write site 변경 | D8 완료 후 활성 가능 |
| `ops-cell-lifecycle` | cell_matrix 쓰기 | D16b 이후 활성 |
| `ops-quarantine-reviewer` | signal_queue / quarantined 주간 | D14 signal hygiene 이후 활성 |
| `dev-session-axis-auditor` | cell_matrix session 축 일관성 | D16a.5 이후 활성 |

모든 stub `.claude/agents/` 존재 확인. 실제 dispatch 는 각 D# 구현 시점에 시작.

---

**상태**: 단계 2 확정 완료. 단계 3 (MVP) 은 D6/D7/D7.5/D8/D9/D10 이미 완료. 남은 D11 Cell API wrap / D11.5~9 Forensic `e128f96b` 완료. 다음 단계 4 (D12~D19 확장).
