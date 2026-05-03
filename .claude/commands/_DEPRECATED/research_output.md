# Research — 출력 포맷 & 주제 뱅크

[research.md](research.md)의 출력 예시 + 향후 탐색 주제.

## 출력 포맷 예시
```
=== RESEARCH: 펀딩 레이트 × 가격 방향 ===

핵심 발견:
- 펀딩 < -0.01%: 72% 확률로 4시간 내 반등 (GPT+Gemini 합의)
- 효과 크기: major tier에서 가장 강함, micro에서 약함
- 최적 진입: 펀딩 역전 시점 + OKX taker buy surge

적용 방안:
- crypto_cmh_filters.funding_threshold: -0.015 → -0.010 (완화)
- 진입 조건에 funding reversal 감지 추가 (코드 필요)
- 예상 효과: crypto WR +5-8% (데이터 기반 추정)

신뢰도: 중-상 (학술 데이터 있음, 우리 데이터 검증 필요)
소스: [논문/블로그 링크]
```

## 리서치 주제 뱅크 (향후 탐색)
- 크립토 세션별 변동성 패턴 (아시아 vs 미국)
- OKX OI (Open Interest) 급변과 가격 방향
- 센티먼트 극단값 지속 시간 × 반전 확률
- CFD 스프레드 비용 최적화 (진입 시간대)
- 소규모 계좌 복리 전략
- Crisis 레짐 진입 타이밍 (F&G 극단값 + VIX 급등 복합)

## 후속 자동 연계
- 파라미터 변경 제안 → `/debate` → `/debate-apply`
- 코드 변경 필요 → (Dev 직접 구현) (스펙 → 코드)
- A/B 테스트 필요 → `/strategy-lab`
- 추가 데이터 필요 → `/edge-finder`

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
