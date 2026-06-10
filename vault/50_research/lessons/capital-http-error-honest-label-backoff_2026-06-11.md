---
type: lesson
status: active
date_created: 2026-06-11
tags: [capital, adapter, reject-classification, circuit-breaker, backoff, flow_not_block]
related: [[MOC-A1-design-dev]], [[layer-7-strategy-isolation]]
---

# Capital HTTP 에러 가짜-PENDING 라벨 → 무고 SOFT_HALT 55회: 정직 라벨 + 분류 + 백오프 수리

DEMO/PAPER. 빌더 wave (D-1/D-2/D-3 + 리뷰 B1/B2 반영). 진입/사이징/청산 결정·9-stack 무접촉.

## 무엇이 깨졌나
- **D-1**: `_parse_deal_response` 가 비-200 HTTP 에러에 기본값 `"PENDING"` 을 status 로 오라벨
  (HTTP status/에러바디 어디에도 미로깅) → reject_code="PENDING" 전파.
- **D-2**: "PENDING" 이 external 셋 어디에도 없어 FAULT_REJECT 로 기록 → 가짜 fault 159건
  (fx_breakout_basket 29 / fx_range_fade 2 / micro_reversion 109 / session_breakout 19),
  SOFT_HALT 55회. venue/transport 사건이 전략 회로를 끊음 = 무결성-only 서킷 철학 위반.
- **B1 (리뷰 적발)**: open-leg confirm 폴(`_smoke_roundtrip_capital`)은 try 미보호 — 429 폭풍/
  타임아웃이 FAULT_EXCEPTION(3/300s) → **HARD_HALT** 로 빠짐 (라이브 exception fault 40건, 39건 exc="").
- **B2 (리뷰 적발)**: confirm 6폴 내내 PENDING 인 venue 스톨도 같은 "PENDING" 라벨로 합류.

## 수리 (전부 flow 보존 방향)
1. **정직 라벨** `adapter.py`: 비-200 → 무조건 `HTTP_<code>` (에러바디의 status류 키는 라벨로
   신뢰 금지 — raw 보존만), errorCode/errorMessage → reason, WARNING 로그. `http_status` 필드
   추가(0 = HTTP 응답 자체 없음). 200 분기는 byte-identical (422 fills 의 200+PENDING→confirm 무수정).
2. **분류 교정** `_production_reject.py`: `venue.lower()=="capital" and (HTTP_* or
   CONFIRM_STALL_PENDING)` → external (fault 0, `venue_rejects_by_code` 카운트 — HTTP_429
   코드별 카운터로 D-3 주문경로 429 라이브 확정 가능). bare "PENDING" 은 의도적으로 계속 fault
   (재출현 = 코드버그 백스톱). OKX(sCode)/Alpaca(semantic) 무접촉. SOFT_HALT 임계 무수정.
3. **백오프** (open/close 한정, 총 3회·추가지연 ≤1.5s·Retry-After 캡 1.0s·asyncio.sleep 비블로킹):
   open 은 429+connect-phase 만 재시도(POST 멱등키 없음 — 모호실패 blind 재시도 = 중복주문 위험,
   raise 대신 합성 `HTTP_TIMEOUT`/`HTTP_TRANSPORT` 반환), close 는 5xx/timeout 도 재시도(DELETE
   멱등 — absent→CloseOrphan 화해). 비-httpx 예외는 그대로 propagate (FAULT_EXCEPTION 백스톱 보존).
4. **confirm 가드** (B1/B2): open-leg confirm 폴 httpx 예외 → budget 내 재폴, 전폴 실패 →
   `HTTP_CONFIRM`, budget 소진 PENDING → `CONFIRM_STALL_PENDING` (둘 다 external). 고스트
   포지션 가능성은 기존 reconcile (position drift) 커버.
5. **보정 스크립트** `correct_fake_pending_faults.py` (dry-run 기본 = ro URI): reject →
   `reject_invalidated` 재라벨 + detail 마커 (삭제 없음 — A3 감사추적), halt 는 600s 윈도
   재계산 가드(잔존 진짜 reject ≥3 → STILL_VALID 보존). 라이브 dry-run 실측 159/55 전건
   INVALIDATED·open 1건(29e3d2f3)·reverse-mixed 0 — `--apply` 는 봇 정지+백업 후 별도 실행.

## 교훈
- **에러는 도착한 형태 그대로 라벨하라**: 파서 기본값이 도메인 상태("PENDING")로 fallback 하면
  전혀 다른 사건(HTTP 429)이 정상 어휘로 위장해 분류 체계 전체가 오염된다.
- **외부사건 분류는 라벨 생산자와 소비자를 같이 고쳐야 한다**: D-1 만 고치면 HTTP_429 가
  여전히 anomalous-fault 로 떨어졌다 (생산 라벨 ↔ 소비 게이트 동시 패치 + e2e 테스트).
- **재시도 매트릭스는 멱등성이 1순위**: "몇 번 재시도" 보다 "이 실패는 미처리가 증명되는가"
  (429/connect-phase=증명됨, ReadTimeout/5xx=모호) 가 먼저다.
- 리뷰 B1 류 (같은 외부사건의 **둘째 탈출 경로**가 더 센 halt 로 빠지는 갭) 는 단일 경로 수리
  후 반드시 "같은 사건이 다른 코드패스로도 새는가" 를 묻는 적대 질문으로만 잡혔다.

검증: 신규 테스트 23+5건 + 전체 2036 green (tick_engine 시각 플레이크 2건 기존), ruff/mypy --strict clean.
