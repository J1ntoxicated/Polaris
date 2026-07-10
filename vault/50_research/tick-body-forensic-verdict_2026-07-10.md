---
type: verdict
status: active
date_created: 2026-07-10
tags: [forensic, tick-latency, db-writer, performance]
---

# 틱 바디 포렌식 verdict (wf_1f586d0a, 4축+Opus 합성)

**증상**: tick_sec 설정과 무관하게 틱 바디 실측 median 45.2s / p90 75.5s / max 270s.
틱-abort 100건 전량 `database is locked`.

## 핫 랭킹 (실측)
1. **baseline persist→recalc 침묵 16.7s** — 틱이 DBWriter의 row-by-row
   ingest+baseline COMMIT을 `wrap_future`로 동기 대기. ingest.py:106-217
   전량 per-row execute (8400봉=8.4K INSERT+25.2K append_sample 단일
   SAVEPOINT) → 배치 max 46.9s.
2. **ingest fetch 직렬 ~14.2s** — (tf,venue) 버킷 gather 없이 순차 await
   + 케이던스 전부 5s 배수(위상 0)라 컨버전스 틱에 몰림.
3. **큐 드레인 5.5s** — asset_class별 순차 await로 commit 직렬 노출.
4. **틱 전체 abort** — loop-conn 잔존 직접쓰기(BEGIN IMMEDIATE ~14사이트)가
   DBWriter 장기 트랜잭션과 WAL 쓰기락 경합 → busy_timeout 5s 소진.
5. GPT judge 무죄(~0.9s/tick). 429 미미(~16s/h).

## 근본 원인
- **이중-writer 데이터모델 미완** ([[feedback_db_lock_is_architecture_signal]] 적중):
  DBWriter 스레드 conn + 메인루프 conn이 동일 WAL에 각자 write.
- DBWriter 잡 row-by-row (executemany 미사용) — 쓰기락 과보유의 실행부.
- ingest가 틱바디에 동기 결합 + 순차 실행.
- 장수 reader가 WAL pin (checkpoint partial 1895건, WAL 53-70MB) — 대시 폴러 포함.
- 관측성 공백: blanket except가 원인 statement 소거; scheduler가 일시 lock에
  learner 영구 비활성(session_mult/regime_mult 세션 내내 정지!).

## 수정안 (스로틀 0 — 구조 개선만)
- **W1 (즉시, LOW risk)**: ①ingest executemany화(M — median 45→~25s 최대 레버)
  ②케이던스 위상 스태거(S — p90 꼬리 평탄화, 빈도 불변) ③틱 예외범위 축소+
  learner 재활성(S — 100 전량-abort 부분회수+튜닝 정지 회귀 차단).
- **W2**: 단일-writer 마이그레이션 완료(L — loop-conn 직접쓰기 14사이트 →
  DBWriter 큐, lock-abort 클래스 소멸) → 이후 ingest 병렬화(M).
- **W3 (ops)**: reader short-lived txn화 (WAL pin 해소, 틱 무관).

**달성 추정**: W1 후 median ~25s → W2 후 ~20s+꼬리 소멸 → 병렬화 후 ~8-10s
→ 전체 랜딩 시 초 단위. 상세 = task output wudhnu99d.
