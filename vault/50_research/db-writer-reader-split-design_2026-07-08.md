# DB Writer/Reader Split — Design (2026-07-08)

> DEMO/PAPER only · aggressive/flow_not_block preserved (writes degrade, never throttle/block) · 근본 구조 수정 ([[feedback_db_lock_is_architecture_signal]]), 핫패스 패치 아님 · 이 워크플로우는 **배포 안 함** (설계만).
> 실측: boot+3.5h `database is locked` 323회(~90/h), 실패 3곳, `-wal` 392M · PASSIVE 3%만 통과.

## 0. 근본 원인 (진단 확정)
`connect()`([[polaris/storage/schema.py]]:306)는 `isolation_level=None`(autocommit) + WAL + `busy_timeout=5000`. WAL 불변식 = **동시 writer 1개**. 그러나 봇 프로세스가 여는 **독립 RW 커넥션이 6+개**가 각자 `BEGIN..COMMIT`으로 그 단일 write-lock을 경쟁:
- 루프소유 `conn`(L717) — tick body(trades/positions) + **loop-thread producer 2개가 공유**: `_altdata_producer(conn)` / `refresh_ticker_ground(conn)`.
- `focus_conn`(L779), `QuoteTickWriter._conn`(1Hz), `TechnicalStoreWriter._conn`(1Hz), `_persist_blocking` per-bar(ingest), 15s throwaway checkpoint conn(L805), retention 핸들.

#74 오프로드는 **루프 STALL만** 없앴다(write를 executor 스레드로 옮겨 event loop 비블로킹). 그러나 각 writer가 **여전히 자기 conn으로 WAL lock을 경쟁** → `quote_writer`는 이걸 "EXPECTED WAL backpressure"로 명시하고 drop 계상까지 한다(L68-74, L405-419). 지배적 경쟁자 = **`refresh_ticker_ground`의 1882-row 단일 txn**(180s마다 write-lock을 수백 ms 점유) → 그 창에서 1Hz×2 flush + bar ingest가 쌓여 `busy_timeout=5s` 초과 → `database is locked`.

WAL creep 원인: 봇/대시보드의 **근접-상시 리더가 스냅샷을 고정** → PASSIVE는 "no live snapshot" 프레임만 flush → 3%만 통과, `-wal` 무한 증가(재기동에만 의존해 리셋).

## 1. 목표 아키텍처 — 단일 직렬화 Writer (#74의 일반화)
**#74 = 관심사별 전용 오프로드 스레드. 일반화 = 관심사 무관 단일 오프로드 스레드/큐 하나.** 프로세스 내 **RW 커넥션을 정확히 1개**로 만든다 → 봇 자기 write끼리 WAL lock 경쟁 = 구조적으로 0 → `database is locked` 소멸. 리더는 별도 `mode=ro` 커넥션. 체크포인트는 그 유일 writer가 소유.

```
loop thread / producers ──submit(job)──▶ [MPSC queue] ──▶ DBWriter thread (THE only RW conn)
  (in-mem coalesce, no DB)                                   BEGIN; batch; COMMIT; periodic TRUNCATE ckpt
readers (dashboard, bot reads) ──▶ connect_ro() (mode=ro, busy_timeout, walk→close)
```
job = degrade-tolerant writer는 **fire-and-forget**(현행과 동일하게 실패=drop, flow_not_block), 내구성 필요 시 `Future` ack. 단일 writer라 명시적 lock 불필요(큐가 직렬화). 커밋 배치 = 처리량.

## 2. Failing writer 3곳 라우팅
| Writer | 현재 | 신규 경로 |
|---|---|---|
| **W1 ticker-ground** `_static_ground.py:431` | 루프 `conn`, 1882-row 단일 txn | fuse는 순수 in-mem 유지(현행 no-await 루프) → 행을 `POLARIS_GROUND_CHUNK_ROWS`씩 **DBWriter에 chunk submit**. 거대 txn 소멸 + 1Hz flush와 인터리브. `read_active_universe`는 reader conn. |
| **W2 altdata** `production_paper_loop.py:485` | 루프 `conn`, autocommit 단문 | `persist_altdata_snapshot`를 DBWriter job으로 submit(fire-and-forget, audit-only). |
| **W3 ingest offload** `ingest.py:496` | 전용 conn per-call `_persist_blocking` | DBWriter에 `persist_bars`/baseline write job submit(전용 conn 폐기 → 경쟁 writer 제거). |
| (편입) quote_writer / tech_store | 각자 전용 conn | in-mem coalesce 유지, `_flush_blocking`이 자기 conn 대신 **동일 DBWriter에 batch submit** → 2 conn→0. |
| (편입) focus_conn write | 전용 conn | offloaded write를 DBWriter로 submit(read는 ro 유지). |

## 3. Reader 정책 (스냅샷 고정 해소)
- `schema.py`에 `connect_ro(db_path)` 추가 = 대시보드 L345 패턴 일반화: `file:...?mode=ro`(uri) + `busy_timeout`(`POLARIS_DB_BUSY_TIMEOUT_MS`) + `PRAGMA query_only=ON`.
- **walk→close 규율**: 리더는 read-txn을 sleep 너머로 열어두지 않는다. 대시보드는 이미 walk 후 `close()`(L540) — 유지. autocommit SELECT는 문장마다 read-mark 해제(python sqlite3 default는 SELECT에 auto-BEGIN 안 함) → 고정 최소화.
- 봇 내부 read: W1을 루프 `conn`에서 빼면 루프 `conn`은 read-mostly+autocommit → 장수 read-txn 핀 없음. 남는 상시 리더 = 대시보드(별 프로세스, ro) → §4 writer-thread TRUNCATE가 리더 갭에서 회수.
- 장수 리더 보험: `POLARIS_DB_RO_RECYCLE_SEC` 마다 ro conn close/reopen(스냅샷 강제 해제) — 대시보드/봇 리더 공통 헬퍼.

## 4. WAL 정책 (재기동 비의존)
단일 writer가 유일 writer이므로 **TRUNCATE 체크포인트가 안전**해진다(과거 TRUNCATE 거부 사유 = "봇 자기 write가 exclusive lock에 블록"인데, 이제 봇 write는 전부 이 스레드의 job이라 서로 블록할 대상이 없음; TRUNCATE가 대기하는 건 오직 ro 리더 드레인, WAL에서 리더는 체크포인트에 블록되지 않음).
- writer conn PRAGMA = `connect()` 그대로(WAL/NORMAL/FK) + `busy_timeout=POLARIS_DB_BUSY_TIMEOUT_MS`(기본 현행 5000) + `wal_autocheckpoint=POLARIS_DB_WAL_AUTOCKPT_PAGES`(기본 현행 1000, 백스톱 PASSIVE, 0 금지).
- writer 스레드가 job 배치 사이에 **`PRAGMA wal_checkpoint(TRUNCATE)`**를 (a) `POLARIS_DBWRITER_CKPT_SEC`마다, 또는 (b) `-wal` 페이지 > `POLARIS_DBWRITER_CKPT_WAL_PAGES`일 때 실행. TRUNCATE가 리더 갭을 못 잡으면 부분 flush 후 다음 사이클 재시도 → 언젠가 sub-초 갭에서 파일 회수 → **creep 근절, 재기동 무관**.
- 기존 `_checkpoint_wal_blocking`/`_wal_checkpoint_producer`(L804-832) + 15s throwaway conn **제거** → 체크포인트 소유자 1개로 통일(누락 PRAGMA 문제(L805)도 동시 해소).

## 5. Env knobs (하드코딩 magic number 금지)
`os.environ.get`+기본값, 로직에 리터럴 금지. `POLARIS_DBWRITER_ENABLED`(기본 1) = **킬스위치**: 0이면 라우팅된 writer가 각자 현행 direct-conn 거동으로 폴백(코드 revert 없이 env로 즉시 롤백). 그 외: `_BATCH_MAX`, `_DRAIN_MS`, `_QUEUE_MAX`(초과 시 fire-and-forget drop = flow_not_block), `_CKPT_SEC`, `_CKPT_WAL_PAGES`, `POLARIS_GROUND_CHUNK_ROWS`, `POLARIS_DB_BUSY_TIMEOUT_MS`, `POLARIS_DB_WAL_AUTOCKPT_PAGES`, `POLARIS_DB_RO_RECYCLE_SEC`.

## 6. 파일별 변경 · 위험도 · 롤백
| 파일 | 변경 | 위험 | 롤백 |
|---|---|---|---|
| **신규** `polaris/storage/db_writer.py` | `DBWriter`(MPSC queue+전용 스레드+유일 RW conn, batch commit, TRUNCATE ckpt, graceful stop=final drain+ckpt+close). ~200 LOC ≤500. | 신규 격리 → 없음 | 파일 미배선 시 no-op |
| `polaris/storage/schema.py` | `connect_ro()` 추가(additive), busy_timeout/autockpt env화(기본=현값). | 낮음 | 기본값=현행 |
| `polaris/core/data/quote_writer.py` | `_flush_blocking`→DBWriter submit, `_conn` 제거(ENABLED=0 시 폴백 유지). in-mem 경로 무변경. | 중(핫패스지만 write는 이미 오프로드+degrade, 행 동일) | ENABLED=0 |
| `polaris/core/data/technical_store_writer.py` | 동상(submit). | 중 | ENABLED=0 |
| `polaris/core/data/ingest.py` | `_persist_blocking`/`_ingest_blocking`이 전용 conn 대신 DBWriter submit. | 중 | ENABLED=0 |
| `polaris/scripts/_static_ground.py` | `refresh_ticker_ground`: 거대 txn 제거, fuse는 in-mem, 행 chunk submit; read는 reader conn. | 중 | ENABLED=0 |
| `polaris/scripts/production_paper_loop.py` | boot에 `DBWriter` 1개 생성→`state.db_writer`, teardown drain+ckpt+close; altdata/ground/quote/tech/ingest/focus write를 writer로 배선; `_checkpoint_wal_blocking`+`_wal_checkpoint_producer` 제거. | 중-상(오케스트레이션) | ENABLED=0 시 producer 폴백 + 체크포인트 producer는 flag로 잔존 가능 |
| (Stage 2, **별도 빌드**) tick-body trades/positions write | 루프 `conn`→DBWriter | 상(핫패스) | 본 빌드 범위 외 |

**Stage 1**(위 표) 만으로 지배적 경쟁(1Hz×2 + 1882-row txn + ingest + focus)이 단일 conn으로 합류 → 봇 자기-BUSY 소멸. 남는 2nd writer = tick-body(희소·고속 commit) → `busy_timeout`가 흡수 → lock ~0. **Stage 2**는 완전 단일 writer(선택, 후속).

## 7. 테스트 전략 (temp DB만 · 라이브 무접촉)
1. **동시 write 부하 → lock 0**: `tmp_path` DB에 DBWriter 1개 + N 스레드/코루틴이 quote(1Hz)·tech(1Hz)·ground(1882-row chunk)·altdata·ingest write를 동시 60s 주입 → `sqlite3.OperationalError(locked/busy)` 발생 0 assert. 대조군(ENABLED=0, 다중 conn)은 lock>0 재현(회귀 가드).
2. **reader 무영향**: 부하 중 `connect_ro` 리더가 1Hz walk 지속 → 모든 walk 성공·정합 스냅샷(부분 txn 미노출) assert, 리더가 writer 지연 유발 안 함.
3. **checkpoint 동작**: 대량 write로 `-wal` 팽창 → TRUNCATE 사이클 후 `PRAGMA wal_checkpoint` 반환/파일크기로 회수 확인; 상시 리더 존재 하에서도 재시도 끝에 truncate 성공 assert(creep 상한).
4. **graceful stop**: stop_evt→final drain(무손실)+ckpt+close, 재기동 없이 `-wal` 유계 assert.
5. property(`hypothesis`): 임의 job 인터리브에서 커밋 원자성(부분 배치 미노출)·큐 full 시 drop이 flow만 낮추고 crash 없음.
회귀: `pytest tests/ -q` 전체(핫패스 행 동일성).

## 8. 라이브 배포 절차 (본 워크플로우는 미실행)
빌드+§7 그린 후: main 체크포인트 커밋(push는 Jin 승인). 배포 = **다음 스케줄 재기동 픽업**(`./scripts/start_bot.sh`, 일일 07:30 케이던스) — 라이브 봇(PID 50310) 무중단, 라이브 DB write 없음. 픽업 후 `POLARIS_DBWRITER_ENABLED=1`로 관측(3.5h): `database is locked` 카운트·`-wal` 크기·drop 계상. 이상 시 env `POLARIS_DBWRITER_ENABLED=0` → 다음 재기동에 현행 거동 복귀(코드 revert 불요). graceful 대안: MANUAL_STOP sentinel 후 재기동.

## 관련
[[feedback_db_lock_is_architecture_signal]] · [[feedback_flow_not_block]] · [[feedback_single_heavy_workflow_cpu_freeze]] (WAL 체크포인트 위생) · #74 quote_writer 오프로드 선례 · [[ADR-003]] L3/L6
