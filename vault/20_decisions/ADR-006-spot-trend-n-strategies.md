---
entity_type: adr
entity_id: ADR-006
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[ADR-001]]", "[[INSIGHT-005]]", "[[60_alpha/_README]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: provisional
tags: [type/adr, status/provisional, scope/spot, polaris]
---

# ADR-006 — SPOT Trend N-Strategies Architecture (모태 ADR-007 인수)

## Status
- proposed: 2026-05-03 (모태 ADR-007 인수)
- provisional: 2026-05-03 (codex-debate 합의 시 → applied)

## Context (모태 ADR-007 핵심)

모태 ADR-007 결정: SPOT 봇 architectural overhaul — 6 핵심 변경 (universe scan / tier-based cell / N strategies + tournament / ATR-normalized exit / phased multi-broker / AI 점진).

Polaris 적응 시 핵심 4개만 인수 (멀티-broker / AI 모듈은 후속):

## Decision (Polaris 적응)

### 1. Universe — Dynamic Scan
- 모태: OKX SPOT 200+ ticker dynamic scan + liquidity tier filter
- Polaris: 동일 채택 (Phase 2b 컴포넌트 작성 시)
- 단 Phase 2 시작은 5-10 ticker fixed (단순화), Phase 3 이후 dynamic 확장

### 2. Cell Key — Tier-based 6-dim
- 모태: tier × group × session × regime × strategy × direction
- Polaris: 동일 (단 SPOT-only이라 direction 기본 long. PERP 도입은 [[ADR-009]] 별도 결정)

### 3. Strategy — N Independent + Tournament
- 모태: 1 strategy 5-AND-gate → N independent + tournament
- Polaris: 동일 (Phase 2b부터 N=2-3 시작, 점진 확장)
- 단 evolver 자동 진화 폐기 — 60_alpha 워크플로 명시 진화만

### 4. Execution — ATR-normalized
- 모태: fixed % thresholds → ATR-normalized dynamic per-ticker
- Polaris: 동일 채택
- 단 [[ADR-008]] (vol_factor PROPORTIONAL) 사전 적용 — 모태 ADR-010 학습 반영

### 폐기/연기
- Phased multi-broker: Polaris는 OKX-only 시작 (Alpaca 후속 ADR)
- AI 점진 (M1/M2/M3/M4): Polaris는 codex-debate-partner 단일 (단순화)

## Consequences

### 긍정
- 모태 검증된 architecture 인수 (Phase α 적용 결과)
- N strategies + tournament로 alpha 다양화

### 부정
- N strategy 늘면 메타 작업 증가 (각 strategy ADR + 60_alpha 가설)
- universe scan 200+ 시 tick rate 부담

### Mitigations
- Phase 2 시작은 N=2 (메타 한도)
- universe scan은 Phase 3 이후 (점진 확장)

## Verification
- [ ] Phase 2b 첫 컴포넌트가 universe scanner 또는 ATR 함수 구현
- [ ] Phase 3에서 N=2 strategy tournament 작동 확인

## Rollback Path
- ATR-normalized이 데이터 부족 시 → fixed % fallback (단 ADR 변경 필요)
- N strategies 운영 부담 시 → N=1 단순화 (별도 ADR)

## Related
- ADR-001 (SPOT-first fresh start)
- ADR-007 (Paper sizing freedom)
- ADR-008 (vol_factor proportional fix)
- ADR-009 (SPOT vs PERP paradigm 검토)
- INSIGHT-005 (regime presets)
- 60_alpha/_README
