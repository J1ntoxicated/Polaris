# MODULE_REVIEW — `invasion/exchange/okx/paper.py` 1042L Split Plan (F-N17)

> exchange_advisor 담당. OKX Paper Broker — open / close / check_exits / MTM /
> postmortem write / slip. `>1000L = P0 분할` (`code_size_limits.md`).
> 단일 `OKXPaperTrader` class + `PaperPosition` dataclass + module-level
> `_EXIT_CODE_MAP` / `classify_exit_reason`. Behavior-preserving mechanical
> split 순서 제안.

---

## 1. 1042L Block Map

| Block | 라인 | 역할 | 추출 난이도 | Risk |
|-------|------|------|------------|------|
| B0 Imports + Constants | 1-18 | `time/json/os/tempfile/threading`, `deque/Path`, fee 상수 | — (재사용) | — |
| B1 `_EXIT_CODE_MAP` + `classify_exit_reason` | 23-123 | 54 prefix → standard code 매핑 + O(n) classifier | 저 (pure fn, module-level) | Low |
| B2 Fee Constants | 127-129 | `MAKER_FEE / TAKER_FEE / FUNDING_INTERVAL` | 저 | — |
| B3 `PaperPosition.__init__` + slip | 132-161 | direction normalize + entry slip (±3bps) + fee / funding init | 저 (mechanical) | Low |
| **B4 PaperPosition.update (MTM)** | **172-195** | **price/funding accumulate + max/min pnl track + price_path trim** | **저 (self-contained)** | **Low** |
| B5 PaperPosition properties | 164-233 | `pnl_pct / pnl_dollar / age_seconds / to_dict` + backward-compat alias | 저 | Low |
| B6 OKXPaperTrader init + balance read-through | 237-314 | portfolio SSOT + local fallback + `_lock / _blacklist / _ticker_cooldown` | 중 (class wiring) | Low |
| B7 `open_position` | 317-431 | blown-up reset + blacklist/max gate + mom_gate + tier mult + sizing + signal snapshot + `entry_params` | 높 (entry path SSOT) | Med |
| B8 `check_exits` | 433-606 | prices batch fetch → 4-layer exit ladder (STOP/TRAIL/DEAD/TIME/DECAY) + min_hold gate | **높 (FSM canary 36f83e2 공존)** | **High** |
| **B9 `_close_position` body** | **608-787** | **dedup + maker/taker fee + exit slip + worst-price cap + realized_slippage_bps + trade dict 조립 + ai_selector record** | **높 (5608f37 SIGNAL slip + 6e1f61d strategy_id 공존)** | **High** |
| **B10 Postmortem JSONL write** | **788-813** | **`pos.strategy_id` → `ai_postmortem.jsonl` rotate-write (외곽 I/O)** | **저 (pure I/O, lock 밖)** | **Low** |
| B11 Streak helpers | 818-861 | `_count_streak` + `get_streak_multiplier` (live_config arrays) | 저 | Low |
| B12 `get_stats` | 863-903 | 500-trade rolling + 방향별 WR + session delta | 저 | Low |
| B13 `_log_trade` | 905-911 | JSONL append | 저 | Low |
| B14 `_save_state` | 913-943 | temp-file atomic write + `.bak` rotate (portfolio 모드 no-op) | 저 | Low |
| B15 `_load_state` | 945-1007 | state JSON read + legacy position restore + trade history tail 500 | 중 (PaperPosition 재구성) | Low |
| B16 `_archive_session` | 1009-1042 | session 종료 시 `data/paper_sessions/session_*.json` dump | 저 (pure I/O) | Low |

**총 17 blocks. Low 11개 / Med 3개 / High 3개 (B7 open / B8 check_exits / B9 close).**

---

## 2. Extraction Order (Risk-Ascending)

본 PR 에서는 **B10 Postmortem JSONL write 만 분리**. Jin 제약:

- FSM canary (36f83e2) = B8 check_exits 의 `HARVEST_*/PROTECTED_*/TOUCHED_*/TIME_LOSER` prefix → 건드리지 않음
- Postmortem strategy_id (6e1f61d) = B9 내부 `_strat_id = getattr(pos, "strategy_id", "") or _es.get(...)` → 유지, 호출 시그니처로 보존
- SIGNAL slip (5608f37) = B9 내부 `pos.realized_slippage_bps = _exit_slip_bps` + `exit_slip_cap_policy` → 건드리지 않음

### 향후 순서 권고 (별도 PR)

1. **B1 classify_exit_reason** → `paper_exit_codes.py` (pure fn, 54 prefix map도 동반)
2. **B16 _archive_session** → `paper_session_archive.py` (`_TRADES_FILE` 참조만 공유)
3. **B14 _save_state / B15 _load_state** → `paper_state_io.py` (`_STATE_FILE` + PaperPosition 재구성)
4. **B12 get_stats + B11 streak helpers** → `paper_stats.py` (read-only on `trade_history`)
5. **B4 PaperPosition.update (MTM)** → 필요 시 별도 헬퍼, 현 규모면 class 유지 충분

B7/B8/B9 = 트레이딩 core path. 단일 PR 분할 금지. 분할 시 FSM/slip/postmortem 3중
canary 교차 검증 필수 (Codex review 경유).

---

## 3. 본 PR 추출: B10 Postmortem JSONL write

**대상**: `_close_position` L788-L813 (try/except 통째 블록).

**신규 모듈**: `invasion/exchange/okx/paper_postmortem.py`
- `write_postmortem(pos, trade, reason, asset_group)` — pure I/O helper
- `data/ai_postmortem.jsonl` rotate-write 동일 로직
- `pos.strategy_id` + `_es.strategy_id` fallback 순서 **원본 그대로** (6e1f61d 보존)

**호출 교체**: `_close_position` 에서 `from .paper_postmortem import write_postmortem`
import + 해당 try/except 자리 1-line 호출.

**Behavior 불변**:
- `ai_postmortem.jsonl` 컬럼/쓰기 순서 동일
- exception 삼키기 (`except Exception` → `log_event debug`) 동일
- `rotate_jsonl_if_needed` 호출 순서 동일
- lock 외부 실행 (현 구조 그대로 — lock 안으로 옮기지 않음)

---

## 4. 검증

```bash
wc -l invasion/exchange/okx/paper.py invasion/exchange/okx/paper_postmortem.py
python3 -m py_compile invasion/exchange/okx/paper.py invasion/exchange/okx/paper_postmortem.py
python3 -c "import invasion.main"
```

postmortem JSONL 형식 검증: 실제 close 1회 후 `tail -1 data/ai_postmortem.jsonl |
python3 -m json.tool` — 12-key dict (ticker/direction/strategy_id/pnl_pct/
entry_price/exit_price/entry_sentiment/hold_seconds/reason/exit_type/correct/
tier/asset_group/time) 동일.
