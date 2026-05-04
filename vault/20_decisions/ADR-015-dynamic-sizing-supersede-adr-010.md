---
entity_type: adr
entity_id: ADR-015
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[ADR-010]]", "[[ADR-007]]", "[[INSIGHT-032]]", "[[dynamic_sizing]]"]
mode: dev
reviewed_by: codex
ack_by: pending
ack_at: pending
maturity: provisional
tags: [type/adr, status/provisional, scope/risk, priority/p1, polaris]
---

# ADR-015 — Dynamic Sizing MAX_FRACTION = 0.20 (ADR-010 단일 포지션 조항 supersede)

## Status

- proposed: 2026-05-04 (Phase 2j AI Dynamic Sizing 구현, Codex Round 17)
- provisional: 2026-05-04 (Jin ack 대기)

## Context

ADR-010 Risk Management 조항:
> "단일 포지션 ≤ 2% balance"

ADR-010 작성 시점 (2026-05-03) 컨텍스트:
- Fixed size 운영 ($200 기본)
- $5,000~$10,000 baseline → $200 = 2~4%
- 동적 sizing 미존재 → fraction-based 개념 없음

Phase 2j (2026-05-04) 변경:
- `src/risk/dynamic_sizing.py` 도입 — Kelly + confidence² + regime + drawdown 동적 pipeline
- `MAX_FRACTION = 0.20` hard cap 설정 (현재 $5,000 기준 $1,000)
- ADR-010 2% 조항과 수치 충돌 발생

## Decision

**ADR-010 "단일 포지션 ≤ 2% balance" 조항을 다음으로 supersede**:

> 단일 포지션 ≤ MAX_FRACTION (= 0.20, 20%) — dynamic_sizing.compute_size() hard cap.
> 실효 fraction은 Kelly × confidence² × regime_mult × dd_mult 다중 damping으로 0.20 미만.

### 근거

1. **ADR-010 고정 2% = 시대 부적합**: fixed $200 시대 근사치. fraction-based로 paradigm shift 후 무의미.

2. **다중 damping으로 실효 fraction ≪ 0.20**:
   - Kelly half-cap (0.5 max)
   - confidence² (0.7→0.49, 0.5→0.25)
   - regime_mult (flat=0.7, downtrend=0.3)
   - dd_mult (dd=10%→0.8x, dd=25%→0.5x, dd=40%+→0.2x floor)
   - 예: flat + conf 0.7 + kelly 0.3 → 0.3 × 0.49 × 0.7 × 1.0 = 0.103 (10.3%)

3. **0.20 도달 조건 = 극단 조합만**:
   - crisis (1.5x) + high confidence (0.9² = 0.81) + uptrend (1.0) + 0 drawdown
   - Kelly >= 0.164 필요 (반반 성과 기준 자연 달성 어려움)
   - 평상시 effective fraction = 5~12% 예상

4. **모태 ADR-007 정합**: paper freedom $1,000 ≈ 20% × $5,000 — 동일 magnitude.

5. **ADR-010 daily 5% 손실 한도 유지**: 해당 조항은 별도 layer로 충돌 없음.

### 변경 없는 ADR-010 조항

- 백테스트 + 페이퍼 병행 워크플로 → 유지
- 일일 손실 한도 ≤ 5% balance → 유지 (drawdown_pct layer와 독립)
- weekly review with Jin → 유지

## Consequences

### 긍정

- dynamic_sizing.py MAX_FRACTION = 0.20 코드-문서 정합 회복
- 북극성 "crisis = max bet" 실행 가능 (2% cap에서는 crisis 효과 표현 불가)
- Kelly 자연 damping으로 실효 risk = 이전 2% cap과 실제 유사

### 부정

- 이론적 max size $1,000 (20% × $5,000) — 기존 $200보다 5x
- 극단 param 조합 시 과대 sizing 위험

### Mitigations

- MIN_SIZE_USD = $50 (하한) + MAX_FRACTION = 0.20 (상한) 이중 guard
- paper 환경에서만 운영 중 → live 도입 시 재평가 필수 (Jin approval required)
- drawdown_pct > 0.40 → dd_mult = 0.20 floor → effective max = 0.04 (4%) 자동 차단

## Paper Safety

paper 운영 중 관측 기댓값:
- 평균 effective fraction: 5~12%
- $5,000 기준 평균 size: $250~$600
- crisis spike: $1,000 (20%) — 단기, drawdown_pct 상승으로 자동 감소

## Live 도입 조건 (미래)

- paper 60일 측정 후 → 별도 ADR (Jin ack required)
- MAX_FRACTION 재평가 (0.20 → live volatility 기반 재보정)
- position_pct 0.04 (realtime_runner 개별 HYPO max) 와의 정합 재검토

## Codex Review

- Round 17 — drawdown_pct docstring + _KELLY_COLD_START 명확화 맥락에서 제기
- ADR-015 provisional — Jin ack 후 applied 전환

## Related

- [[ADR-010]] — superseded (일부 조항만, 백테스트/paper 워크플로 / daily 5% 조항 유지)
- [[ADR-007]] — paper sizing freedom ($1,000 = 20% × $5,000 정합)
- [[INSIGHT-032]] — Phase 2j AI sizing 부활 근거
- [[dynamic_sizing]] — 구현 파일 (40_components)
