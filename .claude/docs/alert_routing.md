# Alert Routing Table — Category → Handler

> `alert_squad.md` 참조. Detector 가 쓴 category 별 담당 핸들러.

## Routing Table

| Category | Severity | Handler | 호출 방식 | Auto/Manual |
|---|---|---|---|---|
| `dd_1h` | 🔴 HIGH | `ops_sql + codex-rescue` | inline Agent (subagent_type=codex-rescue) | AUTO |
| `loss_streak` | 🔴 HIGH | `codex-rescue` | inline Agent | AUTO |
| `silent` | 🔴 HIGH | `health_ping` | bash tail log + feed state grep | AUTO |
| `fsm_autorevert` | 🔴 HIGH | `slice_audit` | Ops SQL + Dev spec (pset 수동 실행) | AUTO analyze / MANUAL pset |
| `northstar_violation` (신규) | 🔴 HIGH | `auto_spec` | codex-rescue + dev-coder dispatch | AUTO (Jin 04-19 00:40 위임) |
| `wr_1h` | 🟡 MED | `strategy_drift` | evolver_state 읽기 + topk_bandit 분석 | AUTO (튜닝 spec) |
| `regime_thrash` | 🟡 MED | `regime_audit` | RegimeService hysteresis grep | AUTO |
| `exit_other` | 🟢 LOW | `schema_audit` | Dev — exit_code mapping | BATCH (3+ 쌓이면) |

## 북극성 제약

- 어느 handler 든 공격량 삭감(weight/score/block) spec 금지 (`feedback_no_defensive_param_dampen`)
- 허용 spec: 표적 교체 / exit 구조 / amplify / 임계 완화

## Handler 계약

각 handler 는 다음 5항 JSON 반환:
```json
{"analysis_summary": "...", "action_type": "SPEC|FP|DEFER",
 "spec_target": "dev|ops|null", "spec_body": "...",
 "confidence": "HIGH|MED|LOW"}
```

Router 가 이를 받아:
- action_type=SPEC → 해당 target 에 MSG push + item SPEC'D
- action_type=FP → item CLOSED
- action_type=DEFER → item OPEN 유지, cooldown 후 재평가

## 변경 / 추가 절차

- 신규 category 추가 → (1) detector 코드 Dev task (2) 본 표에 행 추가 (3) handler 정의
- Severity 변경 → `harness_alerter.py` `_SEVERITY` dict + 본 표 동기 수정 (단일 SSOT 충돌 주의)

## 상호 참조

- Squad 조직: `alert_squad.md`
- Lifecycle: `alert_lifecycle.md`
- Verification: `alert_verification.md`
