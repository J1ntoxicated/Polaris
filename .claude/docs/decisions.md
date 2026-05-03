# 의사결정 방식 + Jin 보고

## 의사결정 매트릭스

| 상황 | 방법 |
|------|------|
| 데이터로 답할 수 있는 것 | **데이터로 판단** — 백테스트, 성과 분석, 통계 |
| 데이터 없는 구조적 결정 | **`/debate`** (3-AI 교차검증) 또는 **`/research`** |
| 아키텍처 변경 | **Jin 상의** — 전문가 의견 + 백데이터 + 기대효과 + 리스크 |

**디베이트는 데이터 없을 때만.** 데이터 있으면 데이터가 진실.

## Root-cause 원칙 (필수)

모든 조사:
1. **현상** — 수치/로그 인용
2. **Root-cause** — 코드·DB 추적으로 **왜** 입증
3. **개선 방안** — 파일·라인·기대 효과
4. **게싱 금지** — "아마", "일반적으로" 금지

→ [feedback_root_cause_evidence_based](../../memory/feedback_root_cause_evidence_based.md)

## Jin 보고 형식 (구조 변경 시)

1. **현재 상태** — 데이터 기반 팩트
2. **문제점** — 구체 근거
3. **제안** — 전문가 의견, best practice 리서치
4. **기대 효과** — 백테스트 or 정량 추정
5. **리스크** — 부작용 가능성

## 에스컬 라우팅

| 발견 | 경로 |
|------|------|
| 파라미터 조정 | Harness → ops-executor inline dispatch (pr.set / live_config.json) |
| 코드 로직 버그 | Harness → dev-coder inline dispatch + git commit |
| 아키텍처 결함 | Harness → Jin 상의 (위 보고 형식) |
| 설계-코드 불일치 | Jin 즉시 상의 (예: Elo 칼럼 부재) |

## Agent dispatch 원칙

- **Done 정의** 명시 ([harness_design_principles](../../memory/feedback_harness_design_principles.md))
- 팩트·증거 기반 prompt
- 추측성 문구 금지
- 우선순위 명시 (HIGH/MEDIUM/LOW) + effort tier (low/medium/high/xhigh)

## 참조
- [loop.md](../loop.md), [north_star.md](north_star.md), [harness-mode.md](../commands/harness-mode.md)
