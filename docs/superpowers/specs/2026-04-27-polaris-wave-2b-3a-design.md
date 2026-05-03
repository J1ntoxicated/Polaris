# Polaris Wave 2B + 3A — Combined Design Spec

**Date**: 2026-04-27 23:30
**Skill**: superpowers:brainstorming → writing-plans
**Vault refs**: [[INSIGHT-018]] [[INSIGHT-006]] [[INSIGHT-024]] [[2026-04-27-polaris-structural-overhaul-design]]

---

## 1. Context

Wave 2A 완료 후 후속 처리 (Jin "자꾸 미루지 말기" mandate). 3 batches risk-controlled:
- **Wave 2B core**: composer.py `_remap_trend_score` 추가 (INSIGHT-024 의 structural 답)
- **Wave 3A-1**: INSIGHT-018 myfxbook spam 정리 (root cause: credentials empty)
- **Wave 3A-2**: INSIGHT-006 path_replay sqlite race 해소

Wave 3B (INSIGHT-015 Phase 2 + INSIGHT-008 audit) = 별도 spec 즉시 후속 (architectural multi-table risk 큼, isolated dispatch 정합).

---

## 2. Batch 1 — INSIGHT-018 myfxbook silent skip

**Root cause** (진단 log 결과 `2026-04-27 22:26-23:38`):
- `.env` 의 `MYFXBOOK_EMAIL=` / `MYFXBOOK_PASSWORD=` empty
- `myfxbook.py:_has_credentials()` False → `login()` empty → `fetch_sentiment()` 0 pairs
- 매 30s tick 마다 `data_collector.py:159` 가 "myfxbook_sentiment latest empty" loud log → **spam (Jin credential 미입력 시 계속 noise)**

**Fix** (block 아님, observability 정합):
- `data/collectors/myfxbook.py`: `_has_credentials()` False 면 한 번만 INFO log + skip (1h interval throttle)
- `ticks/data_collector.py:159`: collector 0 pairs 일 때 silent skip (loud log 제거 — root cause 이미 식별됨)
- 단 collector credentials OK 인데 0 pairs return = log "warn" (실제 fetch 실패)

**File**: `invasion/data/collectors/myfxbook.py` + `invasion/ticks/data_collector.py`

**Commit**: `fix(insight-018 myfxbook): credentials-empty silent skip + spam quench`

---

## 3. Batch 2 — INSIGHT-006 path_replay sqlite race

**Root cause**:
- `invasion/strategy/path_replay.py:84-111` + `:149-158`: `record_bar()` 가 `conn.commit()` 직접 호출
- multi-thread concurrent commit → SQLite WAL race → "error return without exception set" 24h+ 9-13건 sustained

**Fix** (architectural — concurrent write 패턴 정합):
- `record_bar()`: `conn.commit()` 직접 호출 제거
- `store._enqueue(...)` 패턴 사용 (write queue, single-threaded commit)
- 또는 `store._lock` acquire 후 commit (lock-based serialization)

**File**: `invasion/strategy/path_replay.py`

**Commit**: `fix(insight-006 sqlite-race): path_replay record_bar enqueue pattern`

---

## 4. Batch 3 — Wave 2B Signal Layer Trend Redesign

**Goal**: INSIGHT-024 root structural 답 — commodity (trend market) signal 정합.

**Vault evidence**:
- [[INSIGHT-024]] CAP commodity fitness deficit (commit `c5e09d15` spec 참조)
- composer.py `_remap_contrarian_score` line 215 — sweet_spot boost / overheat damp / extreme damp
- 현재 모든 group 이 contrarian remap 통과 (commodity 도 포함, mismatch root)

**Approach** (amplify-only 정합):
- `composer.py` 에 `_remap_trend_score` 신규 함수 추가
  - Logic: trend signal magnitude × trend_boost (default 1.20, range 1.0~2.0 amplify-only)
  - sign (direction) 유지
  - sweet_spot/overheat/extreme 분기 trend-aware (trend market 은 momentum amplification)
- 호출 dispatch: composer.py 내부 또는 caller 에서 group 컨텍스트로 contrarian vs trend 선택
  - **Option A (선호)**: composer 내부 dispatcher — `_remap_score(raw_score, group, regime, config)` 가 group="commodity" 면 trend, else contrarian
  - Option B: engine.py caller 에서 분기 — composer 변경 적음, 단 caller 마다 분기 코드 중복

**Preg keys 신규**:
- `signal_trend_boost_default` (default 1.20, range 1.0~2.0)
- `signal_trend_groups` (default `"commodity"`, csv) — trend remap 적용 group 화이트리스트
- `signal_trend_remap_enabled` (default 1, 0/1)

**File**: `invasion/signals/composer.py` + `invasion/signals/engine.py` (caller dispatch) + `invasion/config/_params_signal.py`

**Commit**: `feat(wave-2b trend-signal): composer _remap_trend_score commodity trend market path (insight-024)`

---

## 5. Verification Plan

### Per-batch
- AST + import smoke
- log emission verify (Batch 1: spam 종식, Batch 3: trend remap 첫 fire)

### Runtime (1-2 cycle / 30m-1h)
- INSIGHT-018: 30s 마다 noise → 1h 1회 단순 INFO 로 변경
- INSIGHT-006: sqlite race 24h count 9 → 0 target
- Wave 2B trend score: commodity entry 시 trend remap log emit (`signal_trend_boost`)

---

## 6. North Star alignment

- ✅ Block 0건 (모든 batch 가 fix / 추가 / observability)
- ✅ Amplify-only (Wave 2B trend boost lower bound 1.0)
- ✅ Aggressive contrarian 유지 (commodity 외 영역)
- ✅ `feedback_no_block_filter_architecture` (block 추가 X)
- ✅ `feedback_audit_fstring_prefix_scan` (Batch 1 spam quench)

---

## 7. Wave 3B (별도 spec 후속)

- INSIGHT-015 Phase 2: events.jsonl 194MB → sqlite events table SSOT (multi-table migration risk)
- INSIGHT-008 silent module audit: 8 modules body grep verify (vault-only, no code)
- INSIGHT-002 monitoring observability layer redesign

→ Wave 2B+3A dispatch verify 후 (1 cycle observe), Wave 3B 별도 spec + dispatch.

---

## 8. References

- Vault: [[INSIGHT-018]] [[INSIGHT-006]] [[INSIGHT-024]] [[INSIGHT-015]] [[INSIGHT-008]] [[INSIGHT-002]]
- Memory: [[feedback_no_quick_patch_ever]] [[feedback_no_block_filter_architecture]] [[feedback_overhaul_over_incremental]] [[feedback_sequential_superpowers_vault_organic]]
- Code: `invasion/data/collectors/myfxbook.py`, `invasion/ticks/data_collector.py`, `invasion/strategy/path_replay.py`, `invasion/signals/composer.py`, `invasion/signals/engine.py`
