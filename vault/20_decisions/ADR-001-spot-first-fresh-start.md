---
entity_type: adr
entity_id: ADR-001
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[north_star]]", "[[INSIGHT-001]]", "[[INDEX]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: authoritative
authoritative_basis: codex-debate 3 라운드 합의 + Jin ack
tags: [type/adr, status/applied, scope/spot, polaris]
---

# ADR-001 — SPOT-first Fresh Start (옵션 Y 확정)

> Polaris는 모태 invasion/spot 코드를 가져오지 않고, vault 노트 기반으로 처음부터 SPOT-first로 새로 작성한다.

## Status

- proposed: 2026-05-03
- provisional: 2026-05-03 (codex-debate 3 라운드 합의)
- applied: 2026-05-03 (Jin ack)

## Context

모태 `auto_invasion_mk1-main/invasion/spot/` 6,263 라인 인벤토리 결과 — SPOT-first 설계가 아님:
- `okx_perp_client.py` 198 라인 (perp 클라이언트가 SPOT 폴더 안에)
- `alpaca_crypto_runtime.py` 277 라인 (alpaca runtime 통째로)
- `stock/runtime_stock.py` 200 라인 (stock 모듈도)
- 115 라인이 perp/swap/futures/cfd/capital/alpaca 키워드 포함
- `runtime.py` 772 라인이 spot/perp/stock 분기 (단일 책임 위반)
- TODO/FIXME 0건 (정리 표시 vs 잔재 가득 = 더 위험)

= "SPOT-first 설계가 아니라 멀티 거래소가 SPOT 폴더에 떠밀려 들어온 결과물" → [[INSIGHT-001]]

## Decision

**옵션 Y 확정**:
- 모태 invasion/spot/ 코드는 가져오지 않음 (참조 read-only로만 모태에 둠)
- Polaris 코드는 처음부터 SPOT-first로 vault 노트 → TDD → 코드 사이클로 작성
- 검증된 노하우는 INSIGHT/lessons/JSON 19 소스로 보존:
  - 학습값 4 (edge_calibration / tournament_elo / regime_presets / frozen_params)
  - 모태 ADR 인수 4 (007/009/010/011)
  - 모태 INSIGHT 인수 4 (032/033/034/035)
  - 모태 lessons 핵심 5 (#78/#47/#46/#45/#44)
  - WS URL fix (demo → live)

## Consequences

### 긍정
- SPOT-first 깨끗한 설계 (perp/alpaca/stock 잔재 0)
- P6 Pure Core + P7 Property-based test 처음부터 적용 가능
- 컨텍스트 폴루션 매개체(누더기 코드) 차단
- Codex 발견 위험 (demo WS URL 등) 처음부터 회피

### 부정
- 새 작성 비용 2-3주 (정리 비용 1-2주 vs 폴루션 위험 매우 높음 트레이드오프 후 결정)
- 모태에서 검증된 패턴 (WS reconnect 등) 다시 작성 필요

### Mitigations
- 모태 패턴은 INSIGHT 6개로 추출 (Codex 식별)
- 모태 코드 read-only 참조 가능 (구현 reference)
- 첫 컴포넌트 (OKX SPOT WS feed)는 모태 `parse_message()` pure 패턴 vault note로 옮긴 후 새로 구현

## Alternatives Considered

- **X. 모태 가져와 정리** — 6,263 → ~3,000 라인. 정리 비용 1-2주 + **폴루션 위험 매우 높음** (잔재 추적 비용). 기각.
- **Z. 선별 함수만 인수** — pure isolated 함수만 200-400 라인 인수. "선별" 자체가 판단 비용 + 잔재 검증. 기각.

## Codex Debate Summary

- 라운드 1: 알파 미검증 1차 원인 / vault SSOT 위험 / lessons #80 인용 — 진단 통합
- 라운드 2: G1 긴급 bypass + G2 60_alpha 상태관리 + Jin only 완화 → v3
- 라운드 3: 모태 spot 직접 read → 카운트 정정 (INSIGHT 35/ADR 12/agent 20) + 빠진 소스 8개 + P6/P7 신규 → v4. 합의 95% → 보강으로 100%.

## Verification

- [x] 모태 spot 인벤토리 측정 완료 (6,263 라인 + 누더기 증거)
- [ ] Phase 2에서 첫 컴포넌트 (OKX SPOT WS feed) 새로 작성
- [ ] 모태 8 인수 소스 stub 작성 ([[_INHERIT_QUEUE]])
- [ ] Phase D writing-plans에서 인수 소스 추출 단계 명시

## Rollback Path

만약 Polaris 진행 중 새 코드가 모태 코드보다 명백히 열등 (검증 패턴 부재 등) 발견 시:
- 모태 특정 함수만 INSIGHT 추출 후 Polaris에 재작성 (코드 카피 X, 패턴만)
- 또는 모태 함수 read-only 참조 + Polaris에 inline 재작성
- ADR-001 폐기 검토는 Jin escalation 후 codex-debate

## Related

- INSIGHT-001 (모태 spot 누더기 인벤토리)
- ADR-002 (Vault-first architecture)
- _INHERIT_QUEUE (8 인수 소스)
