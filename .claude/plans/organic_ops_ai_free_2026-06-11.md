# Organic Ops — AI-프리 코어 + 트리거 AI 레이어 (2026-06-11, Jin 지시)

DEMO/PAPER 전용. Aggressive bias 보존(throttle/block 금지, flow_not_block). 거부 키워드 0건.
**Jin 방향 지시**: 봇 = 완전 AI-프리(인루프 LLM 비용 0). AI 개입은 운영·개발 레이어에서
트리거 기반으로. 문제 시 Claude가 Jin 폰으로 푸시 → Jin 판단. 전략 설계는 vault(A1)와
DB(A3) 팩터 인벤토리를 묶어 유기적으로. (이 문서 = 설계 SSOT; ADR 승격은 Jin sign-off 후)

## 0. 원칙
- **핫패스(틱~분) = 결정론 Python만.** LLM 호출 0 → 비용 0·레이턴시 0·외부 가용성 의존 0.
- **AI = 비동기 레이어**: 진단·캘리브·전략설계·에스컬레이션. 봇은 절대 AI 응답을 기다리지
  않고 last-known-good 파라미터로 계속 돈다 (halt 아님 — flow_not_block).
- **모든 AI 개입은 DB에 기록** (요청·결정·근거·적용 시각) → vault A3 축과 자연 연동.

## 1. 컴포넌트

### W1. Sentinel — 결정론 라이브 감사 (최우선)
사이드카 프로세스(봇 무접촉, ro DB + 로그 tail). 30~60s 주기, `sentinel_findings` 테이블 + 대시보드 패널.
불변식/품질 체크:
  S1 가격 신선도: venue별 최신 WS 틱 나이 < 임계(트레이딩 세션 중일 때만 판정)
  S2 엑싯 판정→체결 패리티: [L6/exit] close 판정 후 N초 내 venue fill 존재 (P0 인시던트 상시판)
  S3 진입 패리티 + reject 이상: intent→fill→positions 정합, FAULT_REJECT 율/반복 (예: DEP-USDT 51020)
  S4 stop ratchet 단조성: positions.stop_price 시계열 단조 (18b851a/8fc9aa1 불변식 라이브 모니터)
  S5 reconcile drift: 내부 vs venue 포지션/잔고
  S6 **피처 가용성**: OFI 분포(수 시간 |ofi|max=0 → flow 데이터 사망 = 오늘 실측), 윈도 dry율, WS 연결 상태(alpaca 무연결 감지)
severity: info/warn/critical. critical → AI Worker 트리거(아래) + 푸시 에스컬레이션.

### W2. AI Decision Bus — 트리거 기반 AI 개입
- `ai_requests`(id, type, payload_json, urgency, status, created_ts) ← 봇/Sentinel/스케줄러가 적재.
  type 예: fault_diag(센티넬 critical) / target_calib(전략별 타겟 — Jin 예시) / param_review /
  strategy_design / data_gap.
- `ai_decisions`(request_id, decision_json, rationale, ttl, applied_ts) → 봇이 다음 사이클에
  **검증 게이트**(스키마+범위 클램프+거부 키워드 sweep+9-stack 가드) 거쳐 적용.
- **AI Worker 라우팅(하이브리드)**: 정형 판단(타겟 캘리브, 임계 튜닝) = OpenAI API 마이크로워커(저렴);
  구조적 진단·코드 수정 = Claude Code 세션(cron/RemoteTrigger); 중대/모호 = Jin 푸시(PushNotification,
  폰 상시). 라우팅 키 = type×urgency.

### W3. 인루프 GPT 철수 (G3/G4/G7 → 오프라인)
이미 결정론 섀도 + gate-outcome counterfactual 코호트 계측(병렬 세션, 06-11) 존재.
측정 게이트: 섀도↔GPT 합치율 + KILL 코호트 전방수익 비교 → 합치/우위 확인 시 섀도 승격
(인루프 LLM 0, env flag 단계 적용). GPT는 **오프라인 캘리브레이터**로 이동: 주기적으로 코호트
데이터를 읽고 게이트 임계 튜닝을 Decision Bus에 제안. G7도 동일 패턴(섀도 계측 이미 06-11 착수).

### W4. 전략 공장 — vault×DB 유기 루프
strategy_design/data_gap 트리거 → AI가 (a) vault A1(교훈·디베이트·ADR) + (b) DB A3 **팩터
인벤토리**(어떤 피처가 어느 venue에 있고 품질이 어떤지 — 예: Capital 스트림 뎁스 없음→OFI 사망,
COT 주간, FRED 일간)를 읽고 → 팩터 가용성 매트릭스 vault note + 신규 전략 spec + 부족 데이터
수집기 목록 → 빌드 큐(TDD+적대리뷰 사이클). "전략 짤 때 팩터가 DB 기준으로 어떻게 판단되고
뭐가 더 필요한지"의 제도화.

## 2. 진단 결과 (2026-06-11 실측, p5_live25 + polaris_live.sqlite ro)
- **트루 실시간 아직 아님(부분)**: Capital 틱엔진 500ms는 진짜 실시간이나 **8,957 평가 중
  8,937 dry + |ofi|max=0.000 수 시간 지속** = flow 피처 데이터 결핍(Capital 스트림에 호가
  사이즈 부재 의심 — 검증 1순위). OKX/Alpaca 진입 = 1m bar+5s 루프(≈60~120s 지연). 엑싯은
  WS fresh 마크 시 라이브(P4) + 틱 패스 500ms.
- **거래 적은 이유(계층)**: ① 신호 발화 희소가 병목(24h G2 PASS 9, 게이트 킬 1뿐 — 게이트가
  죽이는 게 아님) ② 틱엔진 flow 결핍 dry ③ OKX US blocklist(변동성 알트 44 불가, majors 잔잔)
  ④ Alpaca 장외 + L0 fetch timeout. → 대응: OFI 데이터 수리(최우선 데이터 갭), majors 튜닝(#4),
  전략별 타겟/팩터 공장(W4).

## 3. 롤아웃 (각 단계 TDD + 적대 리뷰 + env flag)
W1 Sentinel+패널 → W2 Bus+푸시 → W3 섀도 승격 측정 → W4 공장 1회전.
W1 내 첫 작업으로 S6(OFI/dry) 검증 — 오늘 발견의 근본 확인이 W4 데이터 갭 목록의 1번.

## 4. W1 상세 스펙 (빌드 SSOT — Jin 승인 2026-06-11)
**관측 전용(observe-only)**: Sentinel은 봇 거동에 어떤 영향도 주지 않는다 — halt/throttle/
block 시맨틱 일절 없음. 발견을 기록·노출할 뿐.
- **프로세스**: `polaris/scripts/sentinel.py` 사이드카(봇 무접촉). 라이브 DB는
  `file:...?mode=ro` URI로만 접근(락 0). 발견은 **별도** `data/sentinel.sqlite`에 기록
  (봇 writer와 경합 0). `--interval`(기본 45s) 루프 + `--once`(테스트/수동). PID
  `data/paper/sentinel.pid`, SIGTERM graceful. 로그 `data/paper/sentinel.log`.
- **스키마(sentinel.sqlite)**: `sentinel_findings(finding_id PK, check_id, severity
  info|warn|critical, subject(venue/symbol/position_id), summary, detail_json,
  first_ts, last_ts, status active|resolved, resolved_ts)` — (check_id,subject) 단위
  dedup: 재발은 last_ts 갱신, 해소되면 status=resolved(스팸 0). + `sentinel_runs(ts,
  checks_run, findings_active, duration_ms)` 하트비트. + `sentinel_state(key, value_json)`
  (S4 마지막 stop 스냅샷 등 체크 내부 상태).
- **체크 v1** (각각 독립 함수, 실패는 다른 체크에 비전파):
  S1 가격 신선도 — venue별 quote_ticks MAX(ts) 나이. 세션 캘린더(resolve_stream
     .session_calendar 재사용) 휴장 중엔 판정 skip. warn>30s, critical>120s (env 오버라이드).
  S2 엑싯 판정→체결 패리티 — 닫힘 판정 흔적(positions.status='closed' 전이/close intents)과
     is_close=1 fill 정합; 닫혔는데 close fill 부재 or close intent 후 N분 무체결 = critical.
     (정확한 소스 테이블은 탐사 결과로 확정)
  S3 진입 패리티+reject 이상 — open fill 있는데 positions 행 부재(또는 역), 동일 symbol
     반복 reject(1h ≥3회) = warn, FAULT 급증 = critical.
  S4 stop ratchet 단조성 — open 포지션 (position_id,side,stop_price) 스냅샷을 sentinel_state에
     보관, 패스 간 비교: long stop 감소/short stop 증가 = critical (불변식 위반 라이브 검출).
  S6 피처 가용성 — quote_ticks 기반 venue별 틱 유입율, (탐사로 확인되면) bid/ask size 분포
     |ofi| 프록시, 세션 중 WS 무유입 = warn. + 봇 로그 미접근이므로 DB로 판단 가능한 것만 v1.
  (S5 reconcile drift: 기존 reconcile 산출 테이블이 있으면 v1 포함, 없으면 v1.1로 명시 연기)
- **대시보드**: tools/visualizer/server.py에 `/api/sentinel` 추가(sentinel.sqlite ro 읽기,
  active findings+최근 run). 패널은 **별도 `tools/visualizer/sentinel.html`**(index.html
  무접촉 — 병렬 세션 충돌 회피), :8770/sentinel.html.
- **테스트**: tmp sqlite 픽스처로 각 체크 RED→GREEN(위반 시나리오 주입), dedup/resolve 전이,
  ro-모드 가드(라이브 DB에 쓰기 시도 0), `--once` E2E. mypy --strict + ruff. 파일 ≤500 LOC.
- **금지**: 봇 코드 수정 0 (server.py 추가 엔드포인트만 예외, additive). 거부 키워드 0.
  Sentinel이 봇/포지션/사이징에 개입하는 어떤 경로도 금지.
