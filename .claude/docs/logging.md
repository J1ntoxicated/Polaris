# 로깅 원칙 + Ops 로그 관리

## 로깅 원칙

**로그 없으면 판단 근거 없다.** 모든 데이터 흐름에 로그 필수:

- 시그널 (provider별 입출력)
- 게이트 (통과/거부 + 이유)
- 사이징 (multiplier별)
- 진입/청산 (판정 근거 + 결과)
- AI (호출/응답/비용)
- 레짐/전략 (입력 → 판정)

**누락 발견 → 즉시 추가.**

## 적정성 피드백 루프

### 1. Code-side (dev-audit-advisor)
- 트리거: `invasion/* 5 파일 이상 수정`
- 체크: 새 코드 `log_event()` 누락
- 조치: dev-coder inline dispatch 로 추가

### 2. Analytical-side (ops-log-advisor 소비자 관점) ⭐
**Harness 는 로그 소비자 — 누락 판단 주체**:
- 분석 중 "근거 로그 없다" 감지 → dev-coder inline dispatch 로 log_event 추가
- 봇 재시작 (bash start.sh)

**예시**:
- "STALE_STOP 판정 시 limit/current 값 둘 다 로그에 없음"
- "DPM_KILL 점수 산출 과정 로그 없음"
- "provider별 기여도 표시 안 됨"

### Harness 체크포인트
매 주기 짧게: "지금 분석에 필요한 데이터가 로그에 있나?" 자문.
없으면 즉시 dev-coder dispatch. 조사 전 추가가 조사 후 후회보다 저렴.

## 로그 관리 (Harness + agent)

| 항목 | 주체 | 내용 |
|------|------|------|
| 실시간 모니터링 | ops-log-advisor | 에러 급증/패턴 이상 — 매 주기 |
| 적정성 판단 | ops-log-quality-auditor | 조사 시 누락 감지 → dev-coder dispatch |
| 거래 분석 소비 | ops-trade-forensic | 주요 데이터 소스 |
| 로그 rotation 감시 | Harness | `invasion.log` 10MB → auto `.1` rotation (RotatingFileHandler) |
| 이상 패턴 에스컬 | ops-log-advisor | 반복 에러/무한 루프 → Harness 판단 |
| 로그 레벨 조정 | dev-coder | 스팸 info → debug 강등 |

### 권한 경계
- 파일 삭제/수동 rotation 금지 (auto handler 맡김)
- 로그 레벨/포맷 코드 변경 → dev-coder dispatch

## 참조
- [audit_framework.md](audit_framework.md) — 감사 카탈로그
- [loop.md](../loop.md)
