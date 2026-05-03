# /alert-triage — Alert 수동/자동 Triage

봇 HarnessAlerter 가 emit 한 alert 파일을 수신, 분류, 핸들러 dispatch, item 화.

## 참조 (부팅 시 read)

- `.claude/docs/alert_squad.md` — 조직도
- `.claude/docs/alert_routing.md` — category → handler 매핑
- `.claude/docs/alert_lifecycle.md` — 로그 schema
- `.claude/docs/alert_verification.md` — squad 헬스 체크
- `.claude/docs/alert_triage_detail.md` — 실행 모드/출력/로그 상세

## 실행 모드 요약

### 1) Auto mode (Monitor notification 수신 시)
Monitor "ALERT harness_alerts ..." → 최신 alert 파일 read → frontmatter parse → route jsonl append → item append → handler inline invoke → state 전이 → SPEC'D 면 dev-coder / ops-executor dispatch. 상세 흐름은 detail.md.

### 2) Manual mode (Jin 요청)
- `/alert-triage` — 최근 10개 alert triage 상태 표
- `/alert-triage <item_id>` — 특정 item 재분석, handler 재실행
- `/alert-triage health` — verification.md 체크리스트 전체 실행

## 북극성 정합

- Handler 가 dampen/block spec 리턴 시 Router 가 **거부** → `CLOSED_NORTHSTAR_VIOLATION`
- 대신 대체 spec (표적 교체/exit 구조/amplify) 요청 재실행

## 로그 (전 Stage jsonl, alert_lifecycle.md schema)

- EMIT: 봇 `data/alert_emit.jsonl`
- ROUTE: Harness `data/alert_route.jsonl`
- HANDLE: Harness `data/alert_handler.jsonl`
- CLOSE: Harness `data/alert_close.jsonl`

## Idempotency

같은 alert 파일 중복 trigger 방지:
- ROUTE 전 `alert_route.jsonl` 에서 `alert_file` key grep — 있으면 SKIP_DUPLICATE
- 수동 재실행 (`/alert-triage <item_id>`) 은 force flag 예외

## Model discipline

- 모든 핸들러 = Opus 4.7 단일 (Jin 2026-04-20)
- Router (category lookup + parse) → effort=low
- Handler 분석 (codex-rescue / general-purpose) → effort=medium
- 복잡 root-cause / architecture → effort=high/xhigh

## 참조 → [alert_triage_detail.md](../docs/alert_triage_detail.md)

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
