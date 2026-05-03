---
entity_type: insight
entity_id: INSIGHT-001
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[ADR-001]]", "[[_INHERIT_QUEUE]]", "[[north_star]]"]
mode: forensic
reviewed_by: codex
maturity: authoritative
authoritative_basis: 직접 인벤토리 측정 + Codex 디베이트 3 라운드 검증
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-001 — Legacy spot is multi-asset 누더기, not SPOT-first

> 모태 `auto_invasion_mk1-main/invasion/spot/` 6,263 라인은 SPOT-first 설계가 아니라 멀티 거래소(crypto perp + alpaca + stock + forex CFD)가 SPOT 폴더에 떠밀려 들어온 결과물. → ADR-001 옵션 Y 확정 근거.

## Context

Polaris 시작 결정 단계 (2026-05-03)에서 "모태 invasion/spot 코드를 가져올지 / 새로 작성할지" 옵션 결정 위해 인벤토리 측정.

## Evidence (직접 측정)

| 항목 | 측정값 | 의미 |
|---|---|---|
| 총 라인 | 6,263 | 작은 모듈 아님 |
| `okx_perp_client.py` | 198 라인 | **perp 전용 클라이언트가 SPOT 폴더에 존재** |
| `alpaca_crypto_runtime.py` | 277 라인 | alpaca runtime 통째로 spot 폴더에 |
| `stock/runtime_stock.py` | 200 라인 | stock 모듈도 spot 폴더에 |
| `strategies/crypto/`, `stock/` 폴더 | 다수 | 자산 분리가 폴더에 없고 spot 안 강제 통합 |
| perp/swap/futures/cfd/capital/alpaca 키워드 | **115 라인** | SPOT 모듈인데 멀티 거래소 잔재 |
| `runtime.py` 라인 | 772 | spot/perp/stock 분기 처리 (단일 책임 위반) |
| TODO/FIXME/HACK | **0건** | "정리됐다" 표시인데 실제는 잔재 가득 → 더 위험 (인지 못 함) |

**Codex 디베이트 3 라운드 모태 직접 read 결과 (병렬 검증)**:
- `ws_feed_spot.py`에 demo WS URL 하드코딩 (`wss://wsuspap.okx.com:8443`) — Polaris 이식 시 live URL 교체 필수
- ADR 12개 + INSIGHT 35개 + agent 20개 (내가 처음 보고한 19/4/22 모두 부정확)

## Root Cause

모태는 처음 멀티 거래소 (OKX perp + Alpaca + Capital.com CFD + Binance)에서 출발했다가 2026-04-24 commit `f3fd23a8`로 "메인 봇 (CFD+OKX SWAP) 영구 정지 — SPOT only" 전환. 그러나 코드는 transfer가 아니라 **migration without cleanup** — 멀티 거래소 코드를 spot 폴더에 강제 이주시키고 분기 처리. 결과적으로 SPOT-first 설계가 아닌 SPOT-pivot 누더기.

## Impact

### 직접
- Polaris가 모태 spot 코드를 그대로 가져오면 6,263 라인 중 ~3,000 라인이 잔재 처리에 소요 (멀티 거래소 분기 + 폐기)
- 잔재 추적 비용 1-2주 + 폴루션 위험 매우 높음

### 간접
- 컨텍스트 폴루션 매개체 (Jin 발언 "개판" 직접 원인 중 하나)
- 새 agent/Jin이 코드 read 시 "어디까지 SPOT용인지" 판단에 추가 cognitive load

## Recommendation

- [x] **ADR-001 옵션 Y 확정 — 코드 처음부터 SPOT-first 새로 작성** (모태 코드 인수 X)
- [x] 검증 노하우는 INSIGHT/lessons/JSON 19 소스로 보존 ([[_INHERIT_QUEUE]])
- [ ] Phase 2 첫 컴포넌트 (OKX SPOT WS feed) 작성 시 demo WS URL 위험 회피
- [ ] Polaris invasion/ 또는 src/ 디렉토리는 SPOT 도메인만, 다른 자산은 명시 ADR 후 별도 폴더

## Related

- ADR-001 (옵션 Y)
- ADR-002 (Vault-first architecture)
- _INHERIT_QUEUE (8 인수 소스)
- north_star §2.2 (SPOT-first 재정의)
- principles P6 (Pure Core + Imperative Shell — 처음부터 적용)
