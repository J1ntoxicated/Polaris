# Research — 트레이딩 전략 딥 리서치

전략·시장 구조·학술 연구를 3-AI 병렬로 심층 조사. `/octo:research` 기반.
출력 포맷 + 주제 뱅크: [research_output.md](research_output.md).

## 사용법
```
/research "크립토 펀딩 레이트와 가격 방향 상관관계"
/research "소규모 계좌에서 먹히는 contrarian 전략"
/research "meme coin 변동성 패턴과 최적 trailing stop"
```

## 워크플로우

### 1. 질문 분해
3-5개 서브 질문: 핵심 개념 / 기존 연구 / 봇 적용 가능성 / 리스크·한계

### 2. 3-AI 병렬 리서치
- **GPT**: 최신 트레이딩 전략 + 퀀트 논문 + 실전 사례
- **Gemini**: 시장 데이터 + Google Scholar + 최신 트렌드
- **Claude**: 깊은 분석 + 코드 구현 가능성 + 아키텍처 적합성

### 3. 교차 검증 + 합성
3개 AI 비교 → 합의점·상충점 → 신뢰도 (소스·데이터 기반 여부)

### 4. 봇 적용 분석
- `config.py` 영향 파라미터
- 신규 모듈 vs 기존 모듈 확장
- 예상 WR/RR 개선폭 (데이터 기반 추정)
- 난이도 (Low/Med/High)

### 5. 출력
포맷: [research_output.md §1](research_output.md)

### 6. 후속 액션
- 전략 실험 → `/strategy-lab`
- 파라미터 변경 → `/debate` → `/debate-apply`
- 코드 구현 → (Dev 직접 구현)

## 트리거
- Jin 전략 리서치 요청 시
- `/brainstorm` 심층 조사 필요 아이디어
- `/edge-finder` 설명 불가 패턴 발견 시

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
