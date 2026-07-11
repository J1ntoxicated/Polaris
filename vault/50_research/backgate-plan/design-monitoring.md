---
type: research
status: active
date_created: 2026-07-11
tags: [backgate, monitoring, watchdog, digest, promotion-tracker, fingerprint]
---

# Design — Monitoring A~E: 감시 그물 (설계 전용, 코드 0)

DEMO/PAPER 가상계정. W1 = 전체 시퀀스 최선행 (전제 없음, 즉시 착수 가능).
원칙: 숫자만·해석 0 · 신규 write 표면 0 · DB read는 기존 1h 오픈 지점만
(**watchdog 5분 확장 금지**).

## W1 — A(채널 건강) + C(피드 신선도) + E(다이제스트)
- `monitor_tick.sh` **섹션 ⑦**: gate_shadow_events 신선도 / 행수 / 게이트 커버리지.
- log_scan `fred_stale` / `news_stale` 마커 (#5 DFII10 · #7 뉴스 피드).
- daily_digest **100건 gross 롤업** — 숫자만, 해석 0.
- **분포 가드** [R1-B5]: 채널별 분포 요약 — 평균/표준편차/top-symbol 집중도/dedup율
  (숫자만). "쌓이고는 있는데 이상한 데이터"를 행수 카운트가 못 잡는 구멍 봉쇄.
- **input fingerprint** [R1-B5]: gate_shadow_events에 입력 스냅샷 해시 병기 + 실경로
  동일 해시 스탬프 = 섀도우-실경로 분기 감지 마커.
- 검증: 합성 로그라인으로 마커 발화 확인 + 섹션 ⑦ 출력 = 직접 SELECT 일치 +
  digest 롤업 = DB 재계산 일치.

## W3 — B(promotion_tracker)
- ro-URI · 카운트만 · **판정은 사람 + /debate** (트래커는 수치 대시보드).
- 직접 SELECT 대조 = 1회성 아닌 **주기 재검** [R1-B5].
- 상주 항목 [R1-B2]: news_scalar↔judge_conviction 상관 (post-flip 비선형 SIZE_UP
  경로 감시) — flip 이후에도 상시 유지.
- 임계값 SSOT (충돌 조정 ⑦): 승격 숫자는 vault 문서([[experiment-roadmap]] ·
  [[regime_factory_2026-07-10]])가 SSOT — 트래커 코드는 backlink 주석만 (drift 차단).

## 판정 주체 고정 (충돌 조정 ⑧)
표면이 A~E로 넓어져도 Haiku 틱 = **고정 쿼리 실행 + 판정만** — 자유 쿼리·write 권한
불허 (오보 4회 전례). 신규 쿼리는 Q() 고정쿼리 목록에 설계 시점 등록.

## W5 — 이벤트-드리븐 알림 (조건부)
폴링→이벤트 전환은 전용 /debate 후에만 — 현행 케이던스 우선.

## 소비자-선행 원칙 (리스크 톱3-③ 완화의 집행 지점)
모든 신규 섀도우 표면은 설계 시점에 오프라인 리더(캘리브레이터/트래커/OOF 공장)를
명명 — W2↔W3 짝 강제. 리더 없는 컬럼/로그 추가 = 설계 리뷰 REJECT 사유.

실코드 근거: `tools/ops/{monitor_tick.sh,watchdog.py,daily_digest.py}` ·
[[ops-automation]].
관련: [[master-sequence]] · [[design-sizer]] · [[design-regime-v2-rollout]]
