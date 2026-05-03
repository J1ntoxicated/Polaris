# MODULE_REVIEW: trade_analyzer Split Plan (F-N17)

**Target**: `invasion/ai/analysis/trade_analyzer.py`
**Current size**: 934 lines
**Threshold**: 601-800 split 검토 / > 1000L = P0. 934L 는 분할 검토 존. SSOT sweep (35c61e7) 에서 `_RoConn` 3-class shim 이 추가로 ~75L 차지.
**Discoverer**: F-N17 batch (14 files >= 800L)
**Cross-review**: ai_advisor owns. ml_advisor 는 `get_tuning_suggestions` multiplier clamp [0.5, 1.5] 재검토 이슈 (북극성 `feedback_no_defensive_param_dampen`) 제기 중 — **이 배치는 파일 분할만, 로직 변경 금지**.

## Why split
- SSOT sweep 이후 RO shim (3 class + 1 factory) 가 파일 상단에 75L 차지 — 분석 로직과 전혀 결합 없음.
- 5개의 독립 analyze_* 메서드 (signal_quality / exit_efficiency / entry_timing / ticker_performance / provider_attribution) 가 서로 상태 공유 없음 — 각 메서드는 `_db_connect()` → ro_query → 집계 딕트 반환의 동일 패턴.
- `get_tuning_suggestions` 는 캐시된 `self._analysis` 읽기만 수행 — ml_advisor 가 검토하려는 핵심 로직이 600L 안에 묻혀 있음.
- Mechanical separation 으로 ai_advisor (signal/exit 튜닝 힌트) ↔ ml_advisor (weight multiplier clamp) 독립 리뷰 경로 확보.

## Logical block map

| Block | Lines | Responsibility | Extractable? | Risk |
|-------|-------|----------------|--------------|------|
| **A** | 1-25 | Module docstring + imports + `DB_PATH` / `STATE_FILE` / `MIN_TRADES_FOR_ANALYSIS` constants | stays | N/A |
| **B** | 28-50 | `_normalize_exit_type()` — 문자열 → 카테고리 분류 (TRAIL/STOP/TIME/BEP/AI_KILL/DPM/OTHER) | YES — pure util | LOW — 순수 함수, 외부 참조 없음 |
| **C** | 53-127 | `_RoCursor` / `_RoRow` / `_RoConn` F-N15 SSOT shim + `_db_connect()` factory | YES — self-contained shim | LOW — 파일 내부 전용, 외부 import 없음 (grep 확인) |
| **D** | 130-140 | `TradeAnalyzer.__init__` + `_last_analysis_ts` + `_analysis` 상태 + `_load` | stays | LOW |
| **E** | 144-290 | `analyze_signal_quality` — score↔pnl 상관 + score_buckets + provider_combos + direction_stats | stays (orchestration) | MED — signal_quality SSOT, ai_advisor 담당 |
| **F** | 292-375 | `analyze_exit_efficiency` — exit_type 정규화 집계 + capture_ratio + best/worst | stays | MED — exit 튜닝 feed |
| **G** | 377-491 | `analyze_entry_timing` — score_ranges + factor_count_stats + optimal_score_range | stays | MED |
| **H** | 493-629 | `get_tuning_suggestions` — signal_weight_hints (1.0 + avg clamp 없음) / min_score_hint / exit_hints / direction_bias / confidence | stays — **ml_advisor 재검토 pending** | HIGH — 북극성 위반 지적, 이 배치 NOT TOUCHED |
| **I** | 631-724 | `analyze_ticker_performance` — per-ticker WR + blacklist/whitelist 후보 + tier_stats | stays | MED |
| **J** | 726-760 | `analyze_provider_attribution` — providers split + wr/avg_pnl/total_pnl | stays | MED |
| **K** | 764-792 | `run_full_analysis` — 전체 사이클 오케스트레이션 + 캐시 + `_save` | stays (orchestration root) | HIGH — DO NOT TOUCH |
| **L** | 796-870 | `link_unlinked_signals` — F-N2 + F-N15 통합된 retroactive signal→trade linker (SSOT 준수) | stays | HIGH — SSOT boundary critical |
| **M** | 874-917 | `get_state` / `_save` / `_load` — 대시보드 요약 + JSON 영속화 | stays | LOW |
| **N** | 922-934 | `_pearson` — 상관계수 pure util | YES — pure math | LOW — 순수 함수, `analyze_signal_quality` 에서만 호출 |

## Extraction order (low-to-high risk)

1. **This batch** — Block C (F-N15 RO shim 3-class + `_db_connect`) → `invasion/ai/analysis/_ro_conn.py`. Re-export via `from ._ro_conn import _db_connect` (외부 sink 없으므로 back-compat shim 불필요, 내부 호출 6 site 유지).
2. Block B + N → `invasion/ai/analysis/_analysis_utils.py` (pure utils: `_normalize_exit_type` + `_pearson`). Re-export 불필요 (underscore-prefix private).
3. Block J (`analyze_provider_attribution`) → `invasion/ai/analysis/provider_attribution.py` as module-level function taking `(db_path)` → `dict`. `TradeAnalyzer` 내 thin wrapper 유지. MED 위험 (외부 dashboard/ops 호출 확인 필요).
4. Block H (`get_tuning_suggestions`) — **ml_advisor 재검토 완료 후**에만 분할. multiplier clamp `[0.5, 1.5]` 적용 시 동일 커밋에 분할 가능. 북극성 위반 로직이 고립된 채로 분할되면 안 됨.
5. Block I (`analyze_ticker_performance`) → `invasion/ai/analysis/ticker_performance.py`. Ops blacklist/whitelist 소비처 확인 후.
6. Blocks E + F + G 는 TradeAnalyzer 오케스트레이션 본체로 유지. 분리 시 `run_full_analysis` 가 건너뛰는 인자 전달 boilerplate 가 폭발.

## Risk controls
- 각 추출은 **mechanical refactor**: 동일 symbol, 동일 호출 시그너처.
- 추출 후: `wc -l`, `python3 -m py_compile`, `grep -n "sqlite3.connect"` 0 유지, `python3 -c "import invasion.main"`.
- SSOT 보존 검증: `grep -n "_db_connect\|ro_query" invasion/ai/analysis/` 로 F-N15 경로가 깨지지 않음 확인.
- 커밋 per extraction — 롤백 = single `git revert`.
- 커밋 스코프: `git add <path>` + `git commit -- <path>` (feedback_harness_commit_scope).

## This batch (#1) — Block C extraction

**Scope**: `_RoCursor` / `_RoRow` / `_RoConn` 3-class + `_db_connect()` factory (lines 53-127, 75L) 이 통째로 `invasion/ai/analysis/_ro_conn.py` 로 이동. `trade_analyzer.py` 는 `from ._ro_conn import _db_connect` 로 import.

**Why lowest risk**:
- RO shim 은 F-N15 (35c61e7) 에서 추가된 **완전히 self-contained 한 shim** — `DataStore().ro_query` 만 wrap.
- 외부 모듈 import 없음 (grep `_RoConn|_RoCursor|_RoRow|_db_connect` → trade_analyzer.py 단일 파일).
- `_db_connect()` 는 6 call-site 모두 `trade_analyzer.py` 내부 — import 경로만 바꾸면 됨.
- 로직 변경 zero (ml_advisor 가 지적한 multiplier clamp 는 Block H, 이 배치 밖).

**Post-extraction baseline**:
- `trade_analyzer.py`: 934 → ~860L (block C ~75L 감소).
- `_ro_conn.py`: ~80L (블록 C + 단일 docstring).
- `sqlite3.connect` raw call: 0 (F-N15 SSOT 유지).
- `import invasion.main` → OK.

**Test plan**:
```bash
wc -l invasion/ai/analysis/trade_analyzer*.py invasion/ai/analysis/_ro_conn.py
python3 -m py_compile invasion/ai/analysis/trade_analyzer.py invasion/ai/analysis/_ro_conn.py
grep -n "sqlite3.connect" invasion/ai/analysis/trade_analyzer.py  # 0
python3 -c "from invasion.ai.analysis.trade_analyzer import TradeAnalyzer; t=TradeAnalyzer()"
python3 -c "import invasion.main"
```

## Non-goals (이 배치)
- 로직 변경 없음. `get_tuning_suggestions` multiplier 식 `1.0 + avg` 그대로 유지 (clamp 는 별도 PR).
- `analyze_*` 메서드 body 이동 없음.
- `link_unlinked_signals` / DataStore write path 손대지 않음.
- Public API (TradeAnalyzer 클래스 메서드 시그너처) 불변.
