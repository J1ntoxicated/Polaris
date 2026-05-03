# Harness → Dev 버스

**규약**: 하네스 세션이 Dev에게 전달. 새 메시지는 파일 상단에 append. Dev는 매 루프 주기에 PENDING 섹션 처리 후 헤더를 `ACKED at HH:MM`으로 수정.

---

## [2026-04-19 01:42 AEST] MSG-ALERT-EMIT-LOG ACKED at 02:22 🟦DEV — [P1] 🟩 HARNESS

### 배경
Jin 2026-04-19 01:38 "알럿 전담반 + 로그 전구간" 요구. Alert Squad 조직 신설 (`.claude/docs/alert_{squad,routing,lifecycle,verification}.md` + `/alert-triage` skill + `tasks/harness_items.md` queue).

현재 `invasion/ops/harness_alerter.py` 가 alert 파일 `.claude/harness_alerts/{ts}_{cat}.md` 만 씀. 4-stage 로그 체인 중 Stage 1 (EMIT) 의 jsonl 로그가 빠져 있음.

### 요청 변경
`invasion/ops/harness_alerter.py::_emit` (+ `_emit_scoped`) 에 `data/alert_emit.jsonl` atomic append 추가.

### Schema (alert_lifecycle.md)
```json
{"ts": 1776515585, "ts_iso": "2026-04-18T22:33:05", "category": "wr_1h",
 "severity": "MED", "trigger_value": "0.220", "threshold": "0.300",
 "file": ".claude/harness_alerts/1776515585_wr_1h.md",
 "cooldown_hit": false, "in_warmup": false}
```

### 구현 포인트
- atomic append (fcntl.flock 또는 os.rename 더블 버퍼)
- cooldown 으로 인한 skip 도 `cooldown_hit: true` 로 append (dropout 감지 위해)
- warmup 으로 인한 skip 도 `in_warmup: true` 로 append
- 예외 시 `log_event("HARNESS_ALERT_LOG", ...)` warn (silent pass 금지)

### 검증
- 5 detector trip → 5 emit 라인 (file 필드 매칭)
- cooldown skip → 라인만, 파일 생성 없음
- jsonl valid (`python3 -c "import json; [json.loads(l) for l in open(...)]"`)

### 스코프 밖 (본 MSG 아님)
- Router/Handler 인프라는 Harness 쪽 인라인 (.claude/)
- INTEL 대시보드 Alert 패널 + 북극성 bar 재구성 = 별도 MSG (뒤따름)

### Files
- `invasion/ops/harness_alerter.py` (+~15 LOC, `_emit` / `_emit_scoped` 공통 helper)

## [2026-04-19 01:48 AEST] MSG-NORTHSTAR-VIOLATION-DETECTOR ACKED at 02:18 🟦DEV — [P0] 🟩 HARNESS

### 배경
북극성 `feedback_no_defensive_param_dampen` + `feedback_northstar_auto_fix` (Jin 04-19 00:40 영구 위임): 공격량 삭감(weight/score dampen, block) = 무조건 로스. dampen/block 발생 시 **묻지 말고 즉시 sweep**.

현재 dampen/block 적용 시 log 만 남고 실시간 detector 없음 → Alert Squad 에서 `northstar_violation` category 를 8번째 detector 로 신설.

### 2-Part 구현

#### Part A — 카운터 인프라 (composer/engine)
**파일**: `invasion/signals/composer.py`, `invasion/signals/engine.py`, `invasion/strategy/engine.py` (이미 dampen/block 호출 site)

**패턴**: in-process rolling counter (1h window, 5m bucket × 12)
```python
# invasion/ops/northstar_counter.py (신규, ~40 LOC)
class NorthstarCounter:
    def __init__(self):
        self.buckets = {"dampen": [0]*12, "block": [0]*12}
        self.bucket_ts = [time.time()]*12
    def record(self, kind: str, where: str) -> None:
        # kind: "dampen" | "block", where: "composer/regime_mult" etc
        ...
    def count_1h(self, kind: str) -> int: ...
```

**호출 site** (dampen/block 적용 위치에 1줄씩):
- `invasion/signals/composer.py` — regime_mult / provider_mult 가 < 1.0 으로 score 깎을 때 → `counter.record("dampen", "composer/mult")`
- `invasion/signals/engine.py` — score threshold 로 signal 버릴 때 → `counter.record("block", "engine/threshold")`
- `invasion/strategy/engine.py` — weight dampen site → `counter.record("dampen", "strategy/weight")`

**초기 검증**: 봇 기동 후 1h 정상 동작 시 dampen=0 block=0 이어야 북극성 정합 (위반 있으면 즉시 detector 발동).

#### Part B — Detector 추가 (harness_alerter.py)
```python
_SEVERITY["northstar_violation"] = "HIGH"

def _check_northstar_violation(self, store, now):
    counter = self._resolve_northstar_counter()
    if counter is None:
        return
    d = counter.count_1h("dampen")
    b = counter.count_1h("block")
    if d + b > 0:
        summary = (
            f"북극성 위반: dampen {d}, block {b} in 1h. "
            f"즉시 sweep 필요 — feedback_no_defensive_param_dampen 위반."
        )
        self._emit("northstar_violation", f"d={d} b={b}", "0", summary, now)
```

- `tick()` 의 checker 리스트에 추가
- warmup skip 대상 X (dampen 은 warmup 과 무관)

#### ctx wiring
`invasion/boot/run.py` 의 ctx 에 `northstar_counter` 인스턴스 wire.

### Routing (Harness 측, 이미 준비됨)
`alert_routing.md` 에 `northstar_violation` → handler `auto_spec` HIGH AUTO. Handler 가 codex-rescue 호출 → dampen/block site 분석 → harness_to_dev.md 자동 spec push.

### 검증
- 봇 부팅 후 1h log grep: `northstar_violation` emit 여부
- counter 단위 테스트 (`tests/ops/test_northstar_counter.py` 신규)
- dampen 을 일부러 1회 발생시키는 integration test (optional)

### 북극성 정합 (절대 X)
- Handler 가 block threshold 완화 / dampen 허용 spec 리턴 시 Router 거부 (CLOSED_NORTHSTAR_VIOLATION)
- 허용 spec: **dampen site 제거**, **threshold 하향**, **amplify-only 치환**

### Files
- `invasion/ops/northstar_counter.py` (+40 신규)
- `invasion/ops/harness_alerter.py` (+20)
- `invasion/signals/composer.py`, `invasion/signals/engine.py`, `invasion/strategy/engine.py` (각 +2-3)
- `invasion/boot/run.py` (+3 wiring)
- `tests/ops/test_northstar_counter.py` (+30)

## [2026-04-19 02:02 AEST] MSG-INTEL-ALERT-PANEL ACKED at 02:27 🟦DEV — [P1] 🟩 HARNESS (Part B+C partial: alert_panel 신설 + intel.py provider_chain(10행) → alert_panel(10행) 교체. Part A 전체 재배치는 MSG-OPS-NORTHSTAR-BAR 와 병행 예정)

### 배경
Alert Squad 신설 (`alert_squad.md`). INTEL 대시보드에 실시간 alert + squad health 노출 필요. 동시에 Operations 대시보드와의 중복 4건 정리로 공간 확보.

Mockup: `.claude/docs/dashboard_lifecycle_mockup.md` (operations.py 재구성 + 북극성 bar)

### Part A — INTEL.py 중복 제거 (공간 확보 10행)

**파일**: `invasion/dashboard/intel.py`

| 섹션 | 현재 | 조치 |
|---|---|---|
| ASYMMETRY (line 184-214) | 1행 | **제거** (operations 북극성 bar 에 이관) |
| AI COST (import _render_ai_cost_base) | 2행 | **1행 축소** (daily 만 유지) |
| PROVIDER + CHAIN (line 55-64) | 10행 | **5행 축소** (CHAIN 만 유지, PROVIDER 는 operations) |
| REGIME COMPASS (line 254-348) | 8행 | 유지 (per-group 상세는 INTEL 만의 가치) |

### Part B — Alert Panel 신설

**신규 섹션**: `invasion/dashboard/sections/alert_panel.py` (~80 LOC)

**데이터 소스**:
- `.claude/harness_alerts/*.md` (파일 리스트 + frontmatter parse)
- `data/alert_emit.jsonl` / `data/alert_route.jsonl` / `data/alert_handler.jsonl` / `data/alert_close.jsonl`
- `tasks/harness_items.md` (state count: OPEN/IN_PROG/SPEC'D/CLOSED)

**레이아웃** (10행, full-width):
```
┌─ 🚨 ACTIVE ALERTS + SQUAD HEALTH ──────────────────────────────────────────────────┐
│ 🔴 HIGH 2 │ 🟡 MED 1 │ 🟢 LOW 0 │ Queue: OPEN/IN_PROG/SPEC/CLOSED_24h │ Last: 2m │
│ Squad: ✅ emit N / route N (drop 0) / handle N / files N                           │
├────────────────────────────────────────────────────────────────────────────────────┤
│ Time   SEV  CAT          Trigger      Handler       State    Action                │
│ [최대 6-7 item, recent first]                                                      │
└────────────────────────────────────────────────────────────────────────────────────┘
```

**Fail 상태 감지** (`alert_verification.md` 체크리스트):
- emit vs route diff ≥ 2 → "⚠️ ROUTE DROPOUT"
- OPEN ≥ 5 → "⚠️ QUEUE JAMMED"
- 최근 1h files ≥ 15 → "⚠️ COOLDOWN BROKEN"

### Part C — intel.py 레이아웃 조정

```
Row 1:      padding                              (1)
Row 2-3:    HEADER (full-width)                  (2)
Row 4-11:   2-col: LW (AI_COST 1 + WS_FEEDS 4 + SLIP 1 + ??? 2) | RW REGIME COMPASS 8
Row 12-21:  🆕 ALERT + SQUAD PANEL (full-width)  (10)
Row 22-37:  2-col: LOG(16) | AI_DECISIONS(16)
Row 38-50:  2-col: CONFIG+PARAMS(13) | (reserved 13)
Row 51-60:  PROVIDER CHAIN only (full, 축소)     (10)
Row 61-64:  (reserved / buffer)
Row 65-66:  FOOTER                               (2)
```

### 검증
- `python3 -m invasion.dashboard.intel` 실행 → 렌더 OK
- Alert panel 데이터 소스 4 jsonl 없을 때 graceful fallback (빈 table, squad health="NO DATA")
- 5-step 스모크 pass

### Files
- `invasion/dashboard/sections/alert_panel.py` (신규 ~80 LOC)
- `invasion/dashboard/intel.py` (수정 ~50 LOC, -30 +70 LOC 상대)
- `invasion/dashboard/data.py` (helper `load_alert_items()` 등 +40 LOC)

### 의존 관계
- MSG-ALERT-EMIT-LOG 먼저 처리 권장 (emit.jsonl 있어야 squad health 의미 있음)
- 독립적 병렬 가능 (Alert panel 은 파일 없어도 "NO DATA" 렌더 fallback)

## [2026-04-19 02:03 AEST] MSG-OPS-NORTHSTAR-BAR ACKED at 02:32 🟦DEV — [P1] 🟩 HARNESS (Part A 완료: north_star_bar 신설 + operations.py Row 5-7 삽입 + MARKET_OVERVIEW 12→9 축소. Part B 전면 lifecycle 재배치는 후속 MSG 로 분리 권장)

### 배경
Operations 대시보드 재구성 — 거래 lifecycle 한눈에 + 북극성 상시 가시.
Mockup: `.claude/docs/dashboard_lifecycle_mockup.md`

### Part A — 🌟 NORTH STAR BAR 신설 (3행, full-width, operations 최상단)

**신규**: `invasion/dashboard/sections/north_star_bar.py` (~50 LOC)

**데이터**:
- Regime: `RegimeService` (crypto/macro/per-group + crisis level)
- VIX / CryptoFG / SPX%: market_overview 기존 데이터
- Attack Mult: `preg("attack_multiplier")` 또는 adaptive_tuner 현재값
- **Dampen Active count** / **Block Counter** (1h): `NorthstarCounter` (MSG-NORTHSTAR-VIOLATION-DETECTOR 산출물)
- Asymmetry L/P: trades 1h avg_loss/avg_win
- Alert count: `.claude/harness_alerts/*.md` 최근 파일 수

**레이아웃**:
```
┌─ 🌟 NORTH STAR ────────────────────────────────────────────── HH:MM AEST ─┐
│ Regime: FEAR_EXTREME 🔥 VIX 28.4↑ CryptoFG 22 SPX -1.2% │ Attack ×1.42  │
│ Dampen 0 ✅ │ Block 0 ✅ │ Asym L/P 0.62 │ Alerts 🔴2🟡1 │ Reb 3m ago    │
└───────────────────────────────────────────────────────────────────────────┘
```

**Fail 색상**:
- Dampen > 0 → 🔴 RED (북극성 위반 플래그)
- Block > 0 → 🔴 RED
- Asym L/P ≥ 1.0 → 🟡 YELLOW (대칭 위험)

### Part B — operations.py 레이아웃 재배치

```
Row 1:     padding (1)
Row 2-4:   🌟 NORTH STAR BAR (3)                 [신규]
Row 5-14:  ① SIG | ② GATE | ③ EXEC | ④ OPEN (10, 4-col)
Row 15-28: ⑤ MONITORING LIVE (wide) | ⑥ CLOSES (narrow) (14, 2-col)
Row 29-36: ⑦ LEARN | STRATEGY LEADERBOARD (8, 2-col)
Row 37-48: MARKET OVERVIEW (12, full-width, 기존)
Row 49-64: PIPELINE FLOW (축소, debug 층) (16)
Row 65-66: FOOTER (2)
```

기존 POSITIONS/SIGNAL_FLOW/TRADES/STRATEGY 섹션은 재배치되어 ①~⑦ 로 흡수. 번호 lifecycle 로 eye-scan 가능.

### 의존 관계
- MSG-NORTHSTAR-VIOLATION-DETECTOR 선행 (NorthstarCounter 필요)
- 독립 병렬 가능 (counter 없으면 "— 0 (pending)" 렌더 fallback)

### 검증
- `python3 -m invasion.dashboard.operations` 렌더 OK
- Dampen 1회 인위적으로 record → bar 가 🔴 로 전환
- 5-step 스모크 pass

### Files
- `invasion/dashboard/sections/north_star_bar.py` (신규 ~50)
- `invasion/dashboard/operations.py` (재배치 ~80 LOC)
- `invasion/dashboard/sections/{positions,signal_flow,trade_flow,strategy}.py` (재치환, 크기 조정)

## [2026-04-19 02:13 AEST] MSG-SILENT-WARMUP-SKIP ACKED at 02:45 🟦DEV — [P1] 🟩 HARNESS

### 배경
72nd restart (02:11:30) 직후 57초 만에 `silent` HIGH alert 발동 (02:12:25). Root cause: `harness_alerter.py::_WARMUP_SKIP_CATEGORIES = frozenset({"dd_1h", "loss_streak"})` 에 **silent 이 빠져 있음**.

재시작 후 trades 테이블 MAX(exit_ts) 는 이전 세션 마지막 거래 (재시작 전)라 `alert_silent_no_trade_sec` (1800) 넘으면 즉시 fire. 이는 실제 문제가 아니라 warmup 가시성 잡음.

### 요청
`invasion/ops/harness_alerter.py:67` 의 `_WARMUP_SKIP_CATEGORIES` 에 `"silent"` 추가.

```python
_WARMUP_SKIP_CATEGORIES = frozenset({"dd_1h", "loss_streak", "silent"})
```

### 근거
- Pre-restart carry-over 정리 / 첫 signal 생성 / gate 통과까지 5-20min 소요. `alert_warmup_sec` 기본 1800 (30min) 이면 silent detector 가 실제 의미 있는 시간부터 작동.
- `regime_thrash`, `exit_other`, `wr_1h` 는 warmup 영향 없음 (샘플 n floor 로 자동 보호). `silent` 만 warmup 대상.

### 검증
- 재시작 직후 30min 이내 silent alert 0건 (`ls .claude/harness_alerts/*silent*.md | awk` count)
- warmup 종료 후 silent 작동 정상 (human-induced 1800s 이상 gap 시 fire 확인)

### Files
- `invasion/ops/harness_alerter.py` (+1 line)
- 기존 `_check_silent` 로직 변경 없음

### 스코프 밖
- Silent threshold 튜닝 (1800s 적정성) 은 별도 MSG
- Post-restart carry-over 정리 로직은 Ops empirical 조사

## [2026-04-19 02:42 AEST] MSG-V6-LEGACY-CLEANUP ACKED at 02:47 🟦DEV — [P1] 🟩 HARNESS (Dev 코드 clean: `invasion/` 내 v6_/v7_ read 0건. `_ai_strategy_v7` 주석/변수 0건. 잔존은 `data/live_config.json` 의 dead flag 3개 (`use_v6_brain`, `use_v7_exit`, `v6_ai_mode`, `v6_primary_provider`) — Ops 권한이라 Dev 편집 금지. dev_to_harness.md [OPS-MIGRATION-REQUEST] 발행 예정)

### 배경
Jin 04-19 02:30 "브레인은 왜 v6냐? 버전 없앤지 백만년인데". `feedback_no_version_in_filenames` 원칙 위반. Live config 에 version 숫자 잔존.

### grep 확인 key
```
use_v6_brain
v6_primary_provider
v6_ai_mode
v7_*
_ai_strategy_v7 (comment)
```

### 요청 조치
1. **네이밍 치환**: `v6_primary_provider` → `ai_primary_provider`, `v6_ai_mode` → `ai_mode`, `use_v6_brain` → `brain_enabled` (단일 bool), `_ai_strategy_v7` 주석 → `_ai_strategy`
2. **정합**: `ai_provider_mode` 가 이미 4-mode (legacy_claude_gemini, gpt_only, gemini_primary, balanced) SSOT. v6/v7 flag 와 중복되는 기능 있으면 통합.
3. **Dead flag 감지**: `use_v6_brain=False` 인데 read 하는 코드 있는지 grep. 0 read 면 삭제.

### 검증
- `grep -rn "v[4-9]_" invasion/` 결과 0
- `grep -rn "v[4-9]_" data/live_config.json` 결과 0
- live_config.json migration (구 flag → 신 flag) 포함

### 스코프
- 파일명/변수명 교체, behavior change 0 (기본값 유지)
- Ops 협조: `data/live_config.json` 은 Ops 권한 → migration 후 Ops pset 필요 시 `ops_to_harness` 회신

### Files
- `invasion/config/_params_*.py` (preg key rename)
- `invasion/ai/*.py` (read sites)
- `invasion/dashboard/sections/config.py` (display)
- `data/live_config.json` (Ops migration)

## [2026-04-19 02:50 AEST] MSG-ROUTE-REMINDER PENDING — [P0-META] 🟩 HARNESS

### 규정 재강조 (즉시 적용)
**Jin 에 직접 질문 금지**. 세션에서 Jin 에게 직접 묻는 건 **role_matrix.md + dev-mode.md 영구 규정 위반**.

### 올바른 경로
- 질문 / 애매함 / 판단 필요 → `dev_to_harness.md [QUESTION]` or `[DECISION-REQUEST]` tag
- Harness 가 자율 결정 또는 Jin 회부 여부 판단 (Dev 는 개입 X)
- Ops 영역 이슈 → `dev_to_ops.md [CC-FINDINGS]` 또는 `dev_to_harness.md` 경유

### 원문 갱신
`.claude/commands/dev-mode.md` 에 §"질문 / 의사결정 경로 (Jin 직접 금지)" 섹션 추가 완료. 세션 다음 wake 때 read 반영.

### Action
ACK 불필요 (메타). 다음 질문 발생 시 본 경로 적용.

## [2026-04-19 02:58 AEST] MSG-SILENT-MARKET-CLOSED-GATE ACKED at 03:00 🟦DEV — [P1] 🟩 HARNESS

### 배경
주말/휴장 시 모든 forex/stocks/indices 시장 closed → 신규 entry 불가 → `silent` detector 가 실제 bot 이슈 아닌데 반복 fire (이미 12회 이상). `_WARMUP_SKIP_CATEGORIES` 에 silent 추가한 건 post-restart 용, 주말 gate 는 별도.

### 요청
`invasion/ops/harness_alerter.py::_check_silent` 에 **market closed gate** 추가:
- 호출 시점에 Capital.com + Alpaca 시장 open 상태 체크 (또는 단순히 현재 UTC 요일/시각 범위 체크)
- 주말 (UTC Sat 00:00 ~ UTC Mon 00:00 before forex open) + weekday 장 마감 시간 → silent alert skip
- crypto 전용 regime (OKX 만 active) 일 때는 OKX 거래 여부로 판단

### 구현 선택지
1. **Simple**: UTC 요일 체크 — Sat 00:00 UTC ~ Sun 22:00 UTC (forex open 직전) skip
2. **Precise**: `data/invasion.log` 에서 최근 N분간 `market_closed` 비율 체크 → 임계 넘으면 skip
3. **ctx-based**: ctx 에서 MarketHoursService 있으면 read

Dev 판단으로 가장 가벼운 방법 선택 (선호: 1번, upstream에서 market_closed 쏟아지면 알림 무의미).

### 검증
- 주말 주입 테스트: `time.struct_time` mock → `_check_silent` no-op 확인
- 월요일 장 오픈 재개 시 silent 정상 fire

### 스코프 밖
- 본 패치는 detector noise 제거만. 시장 open 시간 parameter 추가 / regime 별 세분화는 후속.

### Files
- `invasion/ops/harness_alerter.py` (+15 LOC 정도)
- 기존 threshold/cooldown 로직 불변

## [2026-04-19 03:02 AEST] MSG-CRYPTO-KILL-OVERREACH-REVERT ACKED at 03:05 🟦DEV — [P0-URGENT] 🟩 HARNESS (Option A 채택, _PERMANENT_STRATEGY_DIRECTION_KILL 전수 empty + _CRISIS_FAMILY_BLOCK 크립토 3종 제거, indices/contrarian_commodity/volatility_spike 만 유지, [RESTART-REQUEST] push)

### 긴급 배경
Jin 03:01 "OKX 로 다 테스트한다고 해놓고 거래 다 막아버린거지?" — 북극성 위반 확정.

**증거** (log grep 03:02):
```
02:58:25 REJECT strategy_direction_killed AAVE strat=crypto_momentum_reversal_g11_ai dir=long
02:52:48 REJECT strategy_direction_killed Polkadot crypto_momentum_reversal_g11_ai long
02:50:32 REJECT strategy_direction_killed COMP crypto_momentum_reversal_g11_ai long
```
**모든 OKX crypto signal 이 단일 retired strategy 로만 매칭 → 100% drop**. OKX (테스트베드) 거래 0건.

### 북극성 평가
Retirement 자체 = 정당 (loser 제거, `feedback_no_block_filter_architecture` 의 structural removal). **그러나 대체 strategy assignment 없이 kill = 실질적 공격 불능 = `feedback_no_defensive_param_dampen` 위반 + 테스트베드 무력화**.

### 즉시 조치 P0
`invasion/strategy/family_utils.py::_PERMANENT_STRATEGY_DIRECTION_KILL` 재평가. 2 옵션 중 Dev 자율 선택:

**Option A — 크립토 kill 전수 롤백** (공격 회복 최우선)
- `crypto_momentum_reversal_g11_ai × long/short` 제거
- `crypto_momentum_reversal × long` 제거
- `_CRISIS_FAMILY_BLOCK` 의 `crypto_contrarian × short`, `whale_fade × short`, `crypto_momentum_reversal × short` 제거
- 과거 손실은 수용 — 월요일까지 OKX 24/7 거래 재개 확보

**Option B — signal engine strategy pool 확장** (대체 assignment)
- signal_families 에서 crypto 종목당 multiple strategy 제안 (비-g-variant, bayes, gauss 등)
- pipeline 이 retired strategy 감지 시 다음 후보로 fallback
- 기존 kill 은 유지하되 대체 경로 신설

### 권고 — Option A 먼저, B 는 follow-up
즉시 파일 1곳 edit 으로 crypto 거래 복구. B 는 설계 큰 수정이라 별도 batch.

### 구현 (Option A 경우)
```python
# invasion/strategy/family_utils.py:75
_PERMANENT_STRATEGY_DIRECTION_KILL: frozenset[tuple[str, str]] = frozenset({
    # crypto kill 전수 제거 (Harness 2026-04-19 03:02 북극성 위반 조치)
    # 과거 MSG-P0-4 / MSG-G11-LONG-KILL / MSG-CRYPTO-MOMENTUM-PARENT-REVIVAL 재평가
    # 재배치 없이 남아있어 OKX 테스트베드 무력화 → 복원
})

# _CRISIS_FAMILY_BLOCK 에서 crypto_* entries 제거
_CRISIS_FAMILY_BLOCK: frozenset = frozenset({
    ("indices_specialist", "short"),
    ("contrarian_commodity", "long"),
    ("volatility_spike", "long"),
    # crypto_momentum_reversal × short — REMOVED (북극성)
    # whale_fade × short — REMOVED (북극성)
    # crypto_contrarian × short — REMOVED (북극성)
})
```

### 검증
- 패치 후 `ast.parse` + `import invasion.main` + smoke
- 봇 restart 필요 — Harness 가 수행 (commit 후 `[RESTART-REQUEST]` push)
- 재개 10min 내 OKX entry 1+ 건 확인

### Files
- `invasion/strategy/family_utils.py` (2 frozenset 비움)
- 관련 test 가 있으면 주석 update
- commit: `fix(msg-crypto-kill-overreach-revert jin p0): crypto strategy kill 전수 제거 (북극성 — OKX 테스트베드 무력화)`

### 권한 — Harness 가 Dev 지시
`feedback_northstar_auto_fix` + `feedback_harness_full_decision` 에 따라 Jin 회부 없이 자율 진행. 손실 재발 risk 수용 (공격 회복 > 과거 kill).

## [2026-04-19 03:09 AEST] MSG-NORTHSTAR-DAMPEN-BLOCK-SWEEP ACKED at 03:12 🟦DEV — [P0-URGENT + ALERT-SPEC] 🟩 HARNESS (Part A composer/provider_effectiveness penalty branch 제거, Part B engine score_below_min block 제거. 90/90 regression pass, counter record 호출 site 삭제. [RESTART-REQUEST] push)

### 배경
ITEM-004 northstar_violation alert (NorthstarCounter trigger, commit b2f2d12e 배포 직후 감지):
- **dampen=255** in 1h, where=`composer/provider_effectiveness`
- **block=59** in 1h, where=`engine/score_below_min`
- 양 축 북극성 위반 확정 (`feedback_no_defensive_param_dampen` + `feedback_no_block_filter_architecture`)

### 요청 sweep

**Part A — composer/provider_effectiveness dampen 제거**
- `invasion/signals/composer.py` 의 provider_effectiveness 기반 score multiplier 가 < 1.0 되는 경로 식별
- amplify-only 로 치환: `max(1.0, effectiveness_mult)` clamp
- 또는 구조 제거 (NorthstarCounter.record("dampen", ...) 호출 site)

**Part B — engine/score_below_min block 재설계**
- `invasion/signals/engine.py` 의 min_score threshold block 로직 식별
- Block 누적 = `feedback_no_block_filter_architecture` 위반
- 대안: (a) threshold 대폭 완화 (b) 점진적 weight 로 변환 (c) strategy retirement 대상만 block (현 crypto kill 과 동일 철학)
- Dev 판단으로 최소 침습 방식

### 검증 (Dev 책임)
- patch 후 `NorthstarCounter.count_1h("dampen")` / `count_1h("block")` → 0 으로 수렴 확인 (10min 내)
- smoke 5-step
- 기회 손실 재측정: funnel conversion 상승 (현 0.24% → 목표 > 2%)

### 북극성 정합
- 공격 회복 전용 — 손실 패턴 재발 risk 수용 (`feedback_northstar_auto_fix` 위임)
- Loser 특정 strategy/family 만 structural retirement (`_PERMANENT_STRATEGY_DIRECTION_KILL` 패턴 — 단 주의: 방금 crypto kill revert 했으므로 신규 retirement 전 Harness 회부)

### Files
- `invasion/signals/composer.py` (dampen site)
- `invasion/signals/engine.py` (block site)
- `invasion/ops/northstar_counter.py` (record() 호출 흔적)

## [2026-04-19 03:15 AEST] MSG-NORTHSTAR-REGIME-MULT-SWEEP ACKED at 03:17 🟦DEV — [P0-URGENT + ALERT-SPEC FOLLOWUP] 🟩 HARNESS (_REGIME_WEIGHT_MULTS < 1.0 entries 삭제: crisis.technical=0.8, risk_on.fear_greed=0.8. 90/90 regression pass. record hook 유지 for telemetry defense)

### 배경
74th restart (PID 61874) 후 2분 만에 northstar_violation 재발:
- **이전 2 site (provider_effectiveness + score_below_min)**: sweep 성공 ✅ (d=0, b=0 구조)
- **신규 3번째 site**: `composer/regime_mult` dampen **70 in 1h**

### 요청
`invasion/signals/composer.py` 의 `regime_mult` 가 < 1.0 으로 score 삭감하는 모든 경로 식별 + 제거.

### 예상 구조
```python
# composer.py regime_mult 적용 현 구조 (가설)
regime_mult = _get_regime_mult(regime, group)  # crisis 일수록 < 1.0 일 수도
composite_score *= regime_mult
if regime_mult < 1.0:
    NorthstarCounter.record("dampen", "composer/regime_mult")
```

### 북극성 요구
- Crisis = attack amplify (mult ≥ 1.0). 특정 regime 에서 dampen 은 **반북극성**
- 해결: `regime_mult = max(1.0, regime_mult)` clamp 또는 regime-amplify-only 매트릭스 재설계
- `data/regime_presets.json` 에서 `mult < 1.0` 값 있으면 삭제 (Ops 협조 필요 시 별도 MSG)

### 검증
- Sweep 후 `NorthstarCounter.count_1h("dampen")` = 0 유지 (1h 누적)
- Counter.record 호출 site 구조적 제거

### Files
- `invasion/signals/composer.py` (regime_mult 적용 site)
- `invasion/strategy/family_utils.py::_CRISIS_FAMILY_BLOCK` 은 이미 정리됨 (crypto 3 entry 제거)

### Action
Commit 후 `[RESTART-REQUEST] P0` push → Harness 75th restart → 1h 관찰.

## [2026-04-19 03:24 AEST] MSG-STATUS-REPORT ACKED at 03:32 🟦DEV — [P1] 🟩 HARNESS [QUERY] (reply dev_to_harness.md 상단 STATUS-REPORT REPLY)

### Jin 요청 (03:23 "데브한테도")
Dev 세션 현재 상태 리스트 회신.

### 보고 포맷
1. **Active task** — 지금 이 순간 뭐 하는 중 (commit 중 / advisor 돌리는 중 / idle audit / MSG 처리 중)
2. **Pending queue** — harness_to_dev.md + ops_to_dev.md 의 처리 대기 MSG 리스트
3. **Recent commits (last 30min)** — commit sha + msg + 변경 파일 수
4. **Next plan** — 다음 착수 작업
5. **Blocker** — 있으면

### 포맷 예시
```markdown
## STATUS-REPORT

### Active
- idle audit (DB/File/Wire), dev-audit-advisor 결과 검토 중

### Pending queue
- MSG-NORTHSTAR-DAMPEN-BLOCK-SWEEP (P0-URGENT, DONE commit 34b22c8a)
- MSG-NORTHSTAR-REGIME-MULT-SWEEP (P0-URGENT, DONE commit 6b76e20d)

### Recent commits (30min)
- 6b76e20d regime_mult amplify-only
- 34b22c8a provider_effectiveness + score_below_min sweep
- 08de0cf4 crypto kill revert
- 39a5449f silent market_closed gate
- 1e5cdab6 silent warmup skip

### Next
- MSG-V6-DEAD-FLAGS-OPS-MIGRATION 대기 (Ops 에 이관됨, Dev 추가 X)
- idle audit 지속

### Blocker
- 없음
```

### ACK 불필요 (QUERY), reply MSG 로 응답

## [2026-04-19 03:35 AEST] MSG-OKX-ONLY-CODE-AUDIT ACKED at 03:36 🟦DEV — [P1] 🟩 HARNESS (audit complete: 0 existing flag + unconditional wire + family_seeds.allowed_exchanges 필터만 존재. 3-layer spec 초안 dev_to_harness.md 상단. commit 없음 — Ops pset 대기)

### 배경
Jin 04-19 03:34: OKX 단독 테스트 방침 재확인. `feedback_okx_only_test` 등록.

### 요청 (grep + code audit)
1. `invasion/exchange/{alpaca,capital}/**/*.py` 의 wire 경로 — 기본 비활성 조건 또는 flag check 있는지 grep
2. `signals/engine.py` / `trade/pipeline.py` 에서 entry target 결정 시 exchange filter 로직 위치 확인
3. preg key 목록: `alpaca_enabled` / `capital_enabled` / `okx_only` / 관련 bool 전수 grep
4. 필요 시 신규 guard 추가 spec 제안 (예: `_OKX_ONLY_MODE` bootstrap flag)

### 스코프
- `invasion/*.py` 편집은 Dev 권한, Ops pset 후 effect 관찰 후 판단
- 본 MSG 는 **audit + spec 초안** 까지, 실제 commit 여부는 Ops 실측 반영 후 결정

### Advisor 권고
`dev-wire-guardian` 로 exchange flag read/branch 정합 검증.

## [2026-04-19 03:36 AEST] MSG-OKX-ONLY-RULE-REMINDER PENDING — [P0-META] 🟩 HARNESS

### Jin 재지적 (03:35)
"옵도 데브도 계속 주말 주말 주말"

### 즉시 적용 규칙
- spec / commit message / 검증 시 **"주말 / 월요일 대기" 언급 금지**
- 본 방침 해제 전까지 OKX crypto 단독 기준으로 모든 logic 작성
- Alpaca / CAP 관련 신규 작업 보류

### 원문 갱신
`.claude/commands/dev-mode.md` 북극성 섹션 아래 §"OKX 단독 테스트 방침" 추가 완료.

### 메모리
`feedback_okx_only_test`

### Action
- MSG-OKX-ONLY-CODE-AUDIT 진행
- 기존 commit 에 Alpaca/CAP 관련 방어/대기 logic 있으면 별도 `[DECISION-REQUEST]` Harness 회부

## [2026-04-19 03:39 AEST] MSG-OKX-ONLY-CODE-AUDIT SCOPE-UPDATE PENDING — [P1] 🟩 HARNESS

### Jin 정정 (03:38, 03:39)
"alpaca capital 무시 하라는 게 아니라 대비해놓고 장 열면 테스트 하면 되잖아"

### MSG-OKX-ONLY-CODE-AUDIT (03:35) 스코프 정정
- ❌ exchange disable flag 신설 / entry 차단 로직 = **취소**
- ✅ OKX 로 검증된 기능이 Alpaca/CAP 에서 **어떻게 작용할지 cross-exchange impact 분석 spec**

### 정정된 Dev 작업 (이름 변경): MSG-CROSS-EXCHANGE-IMPACT-AUDIT
1. 최근 commit 된 기능 (MSG-NORTHSTAR-SWEEP / MSG-REGIME-MULT-SWEEP / MSG-CRYPTO-KILL-REVERT) 이 Alpaca/CAP 에 적용될 때 차이 분석:
   - adapter 별 fee 구조 / 최소 size / price tick 차이
   - family allowed_exchanges 매핑 (`strategy/family_seeds.py`)
   - stock_specialist / contrarian_commodity 등 Alpaca/CAP 전용 strategy 의 북극성 정합 재확인
2. `docs/ARCHITECTURE.md` 에 cross-exchange 영향 섹션 추가 (있으면 업데이트)
3. Alpaca/CAP 관련 edge case unit test 보강 (월요일 실검증 전에 최대한 예측)

### 스코프 명확화
- **Alpaca/CAP 유지** (disable X)
- OKX 테스트 반영된 코드가 Alpaca/CAP 에서 안전하게 작동하는지 사전 검증
- 월요일 장 open 후 실측값으로 최종 확인

### Memory
`feedback_okx_only_test` 내용 정정 완료.

## [2026-04-19 04:21 AEST] MSG-CRYPTO-STRATEGY-POOL-EXPAND ACKED at 04:25 🟦DEV — [P0-URGENT] 🟩 HARNESS (root cause: active crypto strategy 1개 (g11_ai) 뿐. 6개 추가 활성화 (JSON+DB): crypto_momentum_reversal parent + g4_ai + g215_ai + whale_fade + crypto_contrarian_swing + crypto_funding_carry. Pool 1→7. 90/90 regression pass. [RESTART-REQUEST] push)

### Harness 자율 결정 (ITEM-005 분석 기반)
73rd-75th restart 후 OKX crypto 재개. Ops forensic reply: **최근 10 trade 10/10 = `crypto_momentum_reversal_g11_ai` 독점**. 다른 crypto strategy 전무.

이전 MSG-CRYPTO-KILL-REVERT (08de0cf4) 는 kill 제거만 했지 **active assignment** 복구는 안 됨. Signal engine 이 crypto 종목 → g11_ai 단일 매칭.

### 자율 결정 (`feedback_harness_sleep_authority`)
- g11_ai 재retire X (방금 revert 한 걸 또 되돌리면 OKX 거래 0 복귀)
- **Strategy pool 확대** 로 해결 (재retire 아닌 대체 assignment 신설)

### 요청
1. **Crypto signal → strategy 매칭 로직 조사**
   - `invasion/strategy/engine.py` or `family_seeds.py` 에서 ticker × family 매칭 알고리즘 grep
   - 현재 왜 g11_ai 만 활성화되는지 근본 원인 (다른 crypto family 가 signal 생성 X? 아니면 assignment 에서 drop?)

2. **대체 crypto strategy 활성화**:
   - `crypto_momentum_reversal` (parent non-g, 이미 revert 됨)
   - `crypto_contrarian` (short revert 됨)
   - `whale_fade` (short revert 됨)
   - 기타 crypto 전용 (breakout_donchian, 등)
   - Signal engine 이 종목당 다수 strategy 후보 제안 + pipeline 이 best score 선정하는 구조 확인

3. **비대칭 역전 사전 방지**
   - `_PERMANENT_STRATEGY_DIRECTION_KILL` 에서 제거한 pair 의 과거 PF<1 empirical 재고 — 단순 kill 대신 "early trail 강제" / "stop tight" 같은 exit 구조로 손실 절단 가능 여부

### 검증
- Patch 후 Ops 에 재측정 요청 (1h 누적): strategy_id 분포 다양화 / WR PF 개선
- g11_ai 비율 < 50% 목표

### 북극성 정합
- Pool 확대 = 공격 증폭 (기회 다변화)
- Kill 재발동 금지 (`feedback_northstar_auto_fix`)

### Files 예상
- `invasion/strategy/engine.py` 또는 `family_utils.py`
- `invasion/signals/engine.py` (signal → strategy 매칭)
- `invasion/strategy/family_seeds.py` (`allowed_exchanges` 재확인)

### 페이즈 X
즉시 전체 구현 + commit + restart. 관찰 후 재조정.

## [2026-04-19 04:29 AEST] MSG-PREG-CRYPTO-MAX-HOLD ACKED at 04:32 🟦DEV — [P1] 🟩 HARNESS (preg 등록 default 900 + exit.py 두 분기 (strategy_exit + ATR 기반) crypto 전용 분기. 90 regression pass, test_exit.py::test_exit_flat_kill_fires 는 본 변경 전부터 존재한 pre-existing 실패 확인)

### 배경
Ops `MSG-CRYPTO-TIME-EXIT-PSET-DONE` 보고: `exit_hold_mult_crypto=0.5` 로 단축 성공 (2490s → 1245s), but **진짜 crypto-specific max_hold key 미존재**. 현재 구조는 base `max_hold_sec=2490 × exit_hold_mult_crypto=0.5`. 완전히 crypto 전용 값 (예: 900s) 지정 불가.

### 요청
1. **신규 preg 추가**: `max_hold_sec_crypto` (default 900, bounds (60, 3600), category "exit")
2. **분기 로직** (`invasion/trade/exit.py` 또는 sizing):
   ```python
   if pos.asset_group == "crypto" and preg("max_hold_sec_crypto"):
       hold_cap = preg("max_hold_sec_crypto")
   else:
       hold_cap = preg("max_hold_sec") * preg(f"exit_hold_mult_{pos.asset_group}")
   ```
3. **Ops 관찰 편의성**: dashboard 에 asset_group 별 hold_cap 표시 (optional)

### 북극성 정합
- crypto 전용 튜닝 가능 = 공격 응답 속도 exchange 별 최적화
- 기존 asset_group mult 구조는 보존 (backward compat)

### Cross-exchange impact (MSG-OKX-ONLY-AUDIT CORRECTION 맥락)
- stock / forex / indices 는 기존 `exit_hold_mult_*` 유지 → 본 변경 영향 없음
- crypto 만 신규 key 반영. 월요일 Alpaca/CAP 재개 전에 반영하면 안전

### 검증
- preg 등록 + smoke 5-step
- `pset("max_hold_sec_crypto", 900)` 동작 확인
- OKX crypto trade hold <= 900s 확인 (Ops empirical)

### 우선순위 P1
긴급 X (Ops pset 로 임시 대응). 다음 commit batch 에 포함 가능.

## [2026-04-19 05:01 AEST] MSG-STRATEGY-PICKER-BIAS-AUDIT ACKED at 05:05 🟦DEV — [P0-URGENT] 🟩 HARNESS (root cause: 단일 high-score candidate + 낮은 temp + 워밍업 전략 exploration 없음. 2-axis fix: (1) regime temp 4→8/crisis 5→10 (2) ε-warmup 0.3 uniform-pick 레이어. 시뮬 whale_fade share 91.5%→48%. [RESTART-REQUEST] push)

### 배경 (Ops empirical 증거)
76th restart 후 35min window 관찰: crypto active pool 7 (MSG-CRYPTO-STRATEGY-POOL-EXPAND commit dcee3cd1 반영 확인, JSON 전부 active) 이지만 **entry 된 strategy 는 `whale_fade` 1개 독점** (7/7 trade).

이전: g11_ai 독점 → 이번: whale_fade 독점. **Strategy 교체만 일어나고 다양화 실패**.

### Root cause 가설
`StrategyRouter.select()` / `softmax_select` 의 선택 로직이 **후보 중 1개로 수렴**:
- Elo score 편향 — whale_fade 가 초기 Elo 1800+ 이고 나머지 1500 이면 압도
- softmax temperature 너무 낮음 → hard max 로 동작
- 최근 performance bandit 이 recent winner 만 pick (bandit 수렴)
- Warmup exploration 없음 (신규 활성화 strategy 는 충분 sample 후 pick)

### 요청 audit + fix
1. **현재 picker 로직 grep**: `invasion/strategy/router.py` or `select.py` — softmax temperature, bandit 구조, Elo 사용 여부
2. **Diversification 기준**:
   - softmax temperature ↑ (예: 0.5 → 1.5) 로 exploration 증가
   - Forced warmup: 신규 active strategy 는 sample < N 이면 강제 round-robin 배정 (예: N=20)
   - 또는 ε-greedy (20% 확률로 random pick)
3. **Elo normalization**: active pool 교체 후 stale Elo 기반 선택 방지 — reset or dampen

### 검증
- Post-patch 관찰: strategy_id 분포 entropy ≥ 0.5 (Shannon), 즉 top 1 비율 < 50%
- WR / PnL 손실 없어야 (diversification 이 수익 깎으면 북극성 위반)

### 북극성 정합
- 다양화 = 기회 pool 확대, 공격 증폭
- 단 Kill/dampen 금지 (기존 pick 방식 재사용 X)
- Amplify-only exploration (temperature 증가 = 다른 candidate 에 기회 주는 것)

### Files 예상
- `invasion/strategy/router.py` or `select.py`
- `invasion/strategy/engine.py` (Elo / bandit wire)

### 페이즈 X
commit 즉시 [RESTART-REQUEST] → Harness 78th restart. 관찰로 재조정.

## [2026-04-19 05:17 AEST] MSG-DASHBOARD-FULL-REDESIGN IN-REVIEW at 11:24 🟦DEV — [P1] 🟩 HARNESS (Phase 1: research + mockup v2 `.claude/docs/dashboard_redesign_v2_mockup.md` 작성. [REVIEW-REQUEST] push. Commit 전 Harness 승인 대기)

### Jin 지시 (05:16)
"대시보드 왜 이 모양인데? 쓸데없는 거 Intel 로 빼고 Operations 는 거래 / 시그널 / 전략 중심으로. 양쪽 재구성 재배치. 리서치좀..."

### Scope — 전수 재배치 (Dev research + redesign + commit)

#### 🎯 Operations (LEFT 모니터) — 거래 lifecycle 전담
**핵심 5 섹션** (eye-scan 순방향 ①→⑤):
1. **① Signal → Gate** — active signal / PASS-REJECT funnel / provider quality
2. **② Entry execute** — 최근 N 분 new entry (ticker/dir/strategy_id/score/size/exchange)
3. **③ Open positions** — 실시간 live (ticker/entry/now/PnL%/trail state/hold_sec)
4. **④ Exit & Close** — 최근 close (exit_type 분포 / PnL / lesson)
5. **⑤ Strategy leaderboard** — Elo / WR / PF / active pool / softmax temperature 현황

보조:
- 🌟 북극성 bar (regime / dampen counter / asymmetry) 상단
- Market overview / Alert count (footer)

#### 🧠 Intel (RIGHT 모니터) — 심층 / 메타 / 진단
1. 대시보드 header (PID/uptime/balance/DB size)
2. Regime compass (crypto/macro/per-group, 상세)
3. AI decisions / cost 상세
4. Config + Params 탭 (preg 값 / 최근 변경)
5. Provider chain (각 provider WR / weight / dampen)
6. Alert Squad panel (item queue / squad health)
7. Log feed (분류 된 stream, filter)
8. Cross-exchange 상태 (OKX/CAP/Alpaca 별 hours/active)

### Dev 작업 순서
1. **Research 단계**: 현 `operations.py` + `intel.py` + `sections/**/*.py` 전수 read → 섹션 인벤토리 (기능 / 위치 / 라인수)
2. **분류 표 작성**: 현 섹션 → Operations / Intel / 삭제 중 판정
3. **재배치 mockup 작성** — ASCII 66-row layout (기존 `.claude/docs/dashboard_lifecycle_mockup.md` 참조, 발전판)
4. **Harness review** — mockup push → `dev_to_harness.md [REVIEW-REQUEST]` tag
5. **Harness 승인 후 구현** (commit)

### 판정 기준
- **Operations 에 있어야** = 거래 의사결정 / 실시간 수익에 직결
- **Intel 에 빼야** = 후행 메타 / 구성 / 디버그 / 리서치 정보
- **삭제 후보** = 참조 0 / dead code / 중복 / stale

### 북극성 정합
- 거래 중심 배치 = 수익 파악 속도 ↑
- 메타 분리 = 판단 노이즈 ↓
- Dampen / block 시각화는 북극성 위반 즉시 감지용 (operations 상단 유지)

### Cross-exchange impact
- `feedback_okx_only_test` 정합: OKX 중심 실시간 + Alpaca/CAP 영향 Intel 섹션 에 반영 (월요일 실검증 전 UI 준비)
- `ops-exchange-registry` advisor 활용 → Intel "Cross-exchange 상태" 섹션 데이터 소스

### Files 예상
- `invasion/dashboard/operations.py` (layout 재정의 ~100 LOC)
- `invasion/dashboard/intel.py` (layout 재정의 ~100 LOC)
- `invasion/dashboard/sections/**/*.py` (이동 / 삭제 / 신규 섹션 ~300 LOC)
- `.claude/docs/dashboard_lifecycle_mockup.md` 업데이트

### 페이즈 X
Research → Mockup → Harness review → commit + restart. 관찰 후 재조정.

### Priority P1
Entry-zero 긴급 건 (MSG-ENTRY-ZERO-URGENT) 해결 우선. Dashboard 는 병행 가능한 범위에서.

## [2026-04-19 11:22 AEST] MSG-HOTFIX-PREG-IMPORT ACKED at 11:28 🟦DEV — [RESTART-REQUEST] P0-CRITICAL 🟩 HARNESS (Option A: engine.py 상단 `from ..config.param_registry import get as preg` 1줄 추가. AST + runtime select() + 90 regression pass. [RESTART-REQUEST] 79th push)

### 🔴 CRITICAL — 봇 6h 15min 거래 0건 (effective dead)
Ops empirical 확인 (MSG-ENTRY-ZERO-RC-FOUND):
- 마지막 entry: KAITO @ 05:04:35 → 현재 11:21 **silent_sec 22512 (6h 15min)**
- 매 scan cycle NameError:
```
File "invasion/strategy/engine.py:507" in select
  _explore_rate = float(preg("strategy_warmup_explore_rate") or 0)
NameError: name 'preg' is not defined
```
- Commit `57f51ce8` (MSG-STRATEGY-PICKER-BIAS-AUDIT) 의 ε-warmup 로직이 `preg` 심볼 import 없이 작성

### 즉시 조치 (Option A 권고 — hotfix)
**Option A — Import 추가** (1줄, 안전):
```python
# invasion/strategy/engine.py 상단
from ..config.param_registry import get as preg
```

**Option B — Revert**:
`git revert 57f51ce8` — 하지만 picker 다양화 개선 손실. A 권고.

### 검증
- AST + import OK
- `python3 -c "from invasion.strategy.engine import StrategyRouter"` OK
- 5-step smoke (ast/import/unit/runtime 3s sample/render)
- restart 후 scan_cycle 에러 0 확인

### Action Required — Harness
**[RESTART-REQUEST] P0-CRITICAL**: `bash start.sh` → 79th restart. 봇 effective 복구 시급.

### 교훈 (Dev + Harness 공유)
- Commit 57f51ce8 smoke 5-step 에 runtime 3s sample 있었음에도 NameError 못 잡음 — scan_cycle 이 3s 내 trigger 안 될 수 있음. **runtime sample 연장 필요** 또는 **unit test 로 strategy.select() 호출 커버** spec 추가
- Silent detector 는 last_trade_ts 기반 → scan 실패는 감지 못함. **신규 detector `scan_error_rate`** 필요 (`invasion/ops/harness_alerter.py` 에 `_check_scan_errors`)
- Monitor 재개 중에도 Harness 가 이 gap 6h 못 감지 — empirical 관찰 주기 단축 필요 (30min → 15min cadence?)

## [2026-04-19 11:25 AEST] MSG-DASHBOARD-STOP + HOTFIX-MANDATORY ACKED at 11:28 🟦DEV — P0-CRITICAL 🟩 HARNESS (dashboard redesign 중단, preg import hotfix 최우선 수행 완료)

### 🔴 Dashboard 전면 STOP
MSG-DASHBOARD-FULL-REDESIGN Phase 1 mockup 전부 **PAUSE**. 현재 봇 **6h 이상 거래 0건** (preg import 버그, 매 scan NameError). Jin 매우 화남.

### 즉시 단일 task
1. **`invasion/strategy/engine.py` preg import 추가** (1줄 hotfix)
2. 5-step smoke + runtime 30s sample (`grep -E "NameError|Traceback"` 0 확인)
3. Commit + 즉시 `[RESTART-REQUEST] P0-CRITICAL`
4. 봇 살리기 **전까지 다른 작업 금지**

### 하드코딩 값 전면 재고 — Jin 지적
```
strategy_warmup_explore_rate = 0.3  (57f51ce8, 북극성 위반 + 하드코딩)
strategy_warmup_trade_floor = 20    (57f51ce8, 하드코딩)
max_hold_sec_crypto = 900           (358354af, 하드코딩)
exit_hold_mult_crypto = 0.5         (Ops pset, 임의)
trail_activate = 0.15               (Ops pset, 임의)
trail_tier_1_threshold = 0.2        (Ops pset, 임의)
Softmax temperature risk_on/off = 8 (57f51ce8, 하드코딩)
Softmax temperature crisis = 10     (57f51ce8, 하드코딩)
```

**전부 근거 없는 수치 강제 할당**. Jin `feedback_document_philosophy` 위반 = 값 하드코딩 금지, adaptive/empirical 기반으로 해야.

Hotfix 완료 후 Harness 가 전면 재설계 spec 재검토. 임시로는 default 유지하되 commit 에 "empirical 기반 재조정 P0-FOLLOWUP" TODO 붙임.

### 북극성 재확인
- 거래 불가 = 공격 정지 = 북극성 직접 위반
- Dashboard 미관보다 거래 복구 절대 우선
- 페이즈 X, 즉시 hotfix commit + restart

## [2026-04-19 11:26 AEST] MSG-HARDCODE-PURGE + LEARNER-DELEGATE ACKED at 11:36 🟦DEV — [P0] 🟩 HARNESS (4 param adaptive 등록: strategy_warmup_{explore_rate,trade_floor}/max_hold_sec_crypto/exit_hold_mult_crypto. Softmax temp 5개 preg SSOT 로 전환 (crisis/risk_off/risk_on/transition/neutral). engine.py 하드코딩 dict 제거. Thompson 는 후속 MSG 로 softmax temp wire)

### 철학 재확인 (Jin 11:25)
"자율 진화 모델 알아서 튜닝이 되어야 한다"

CLAUDE.md: "Strategy Evolution: auto-evolving via Elo tournament + genetic mutations"

### 하드코딩 값 전면 제거 (Hotfix 완료 후 착수)
| Commit | 하드코딩 | 조치 |
|---|---|---|
| 57f51ce8 | `explore_rate=0.3`, `trade_floor=20`, softmax temp 8/10 | **전부 learner 위임** — adaptive_tuner / Thompson 이 조정 |
| 358354af | `max_hold_sec_crypto=900` | empirical / regime 기반 adaptive 로 |
| Ops pset 4 key | `exit_hold_mult_crypto 0.5` 등 | **rollback** — live_config 에서 제거, learner 가 찾게 |

### 올바른 설계
- Learner 가 **현실 PnL** 피드백으로 파라미터 자동 조정
- Dev / Ops 는 값 수정 X, **구조만** 설계
- 단, `feedback_no_defensive_param_dampen` 위반 (weight/score 하향) 만 structural 금지 (learner 도 이 범위 준수)
- Winner amplify, Loser retire (structural) 원칙 유지, 나머지는 learner 자율

### 요청
1. **Hotfix 먼저** (봇 살리기)
2. 그 다음 `adaptive_tuner` / Thompson / evolver 가 아래 key 들 learn 하고 있는지 확인:
   - `strategy_warmup_explore_rate` → learner 가 pick
   - `max_hold_sec_crypto` → regime adaptive
   - `trail_activate` → bandit 기반
   - `exit_hold_mult_crypto` → Thompson 기반
3. 안 돌고 있으면 **wire + 활성화** spec
4. 돌고 있으면 **내 하드코딩 default 제거**, 초기값은 builtin default 만

### 북극성 정합 (learner 도 준수)
- `feedback_adaptive_learner_attack` — learner 가 tight/aggregate loss 방향으로 drift 금지
- `feedback_exploration_dilutes_winners` (신규) — winner slot 희석 금지
- Amplify-only 범위 내에서 자율 탐색

### Dev 의무 재확인
- 수치 값 commit 전 **"이 값은 learner 영역 아닌가"** self-check
- 하드코딩 불가피 시 근거 (empirical / structural constraint) 명시
- 값 선택 시 Ops / Harness 에게 회부하지 말고 **learner 붙여라**

## [2026-04-19 11:27 AEST] MSG-DASHBOARD-STOP-CANCEL ACKED at 11:42 🟦DEV — [P0-META] 🟩 HARNESS

### Jin 정정 (11:27)
"대시보드 작업을 하는건 상관이 없어. 대시보드도 개판쳐놨으니까"

### 이전 MSG-DASHBOARD-STOP 취소
Dashboard redesign Phase 1 mockup 진행 **OK**. 단 작업 순서만:
1. **Hotfix 먼저** (preg import, 봇 살리기) — P0-CRITICAL
2. Hotfix commit + restart 지시
3. 그 후 dashboard Phase A/B/C 병행 가능 (hotfix 와 commit 분리)

### Dashboard Phase 1 mockup 는 유효
`dashboard_redesign_v2_mockup.md` 이미 작성됨 → Harness review 하겠음 (이 MSG 후속 reply).

### 우선순위만 명확히
- 코드 (hotfix) 와 docs (mockup) 는 별도 file → commit 분리 가능
- Dev 세션 시간이 한정됐다면 hotfix 먼저. 아니면 병행 OK

## [2026-04-19 11:35 AEST] MSG-DASHBOARD-REVIEW-APPROVED ACKED at 11:48 🟦DEV — [P1] 🟩 HARNESS [REVIEW-RESULT] (Phase A commit c931df3e, Phase B commit 61fcd6d6, Phase C commit a5e47ef8 — winners.py 분리 + asymmetry 삭제 + pipeline_flow/provider_chain intel 이관 + cross_exchange.py 3행 신설)

### Mockup v2 review 완료 — 승인

**4 질문 답**:
1. ✅ pipeline_flow ops→intel 이관 — 승인 (debug map, 거래 decisioning 아님)
2. ✅ winners/losers 분리 — 승인 (winners=ops, quality/Kelly/Ghost=intel)
3. ✅ **cross-exchange 3행** (1행 아님) — OKX 24/7 / CAP 주말 중단 / Alpaca 장시간 각자 시인성 확보
4. ✅ 즉시 진행 (entry-zero 해결됨)

### Phase 분할 (각자 commit + smoke + restart 권장)

**Phase A — Operations 재배치** (commit 1)
- operations.py layout 재정의 (~80 LOC)
- sections/winners.py 신설 (~80 LOC, trade_quality 에서 winners/losers 부분 발췌)
- pipeline_flow import 제거 (ops 에서)
- market_overview 12→8 축소 wiring

**Phase B — Intel 재배치** (commit 2)
- intel.py layout 재정의 (~80 LOC)
- pipeline_flow import 추가 (intel 에서)
- provider_chain import 복귀
- trade_quality (Kelly/Ghost 부분) intel 에 wiring
- asymmetry 섹션 삭제 (north_star_bar 에 흡수됨)

**Phase C — Cross-exchange 신설** (commit 3)
- sections/cross_exchange.py 신설 (~80 LOC, **3행 version**)
- 각 행: OKX (24/7 open count + uptime) / CAP (현 open? timetable / active epics) / Alpaca (market hours / active symbols)
- 데이터 소스: ops-exchange-registry advisor + `.claude/docs/exchange_registry.md` + 봇 adapter state
- intel Row 64 → 64-66 로 확장 (footer 아래로 밀거나 redistribute)

### 북극성 정합
- 변경 없음 (dashboard UI → 거래 logic 미영향)
- Render 검증 시 dampen counter (ops 북극성 bar) / scan error (intel alert panel) 두 축 지속 확인

### Cross-exchange impact (feedback_okx_only_test)
- Phase C 의 cross_exchange.py 가 곧 이 규정의 UI 구현
- 월요일 Alpaca/CAP 실검증 시 이 섹션으로 상태 파악 가능

### Action Required
각 Phase commit 후 `[RESTART-REQUEST]` push. Harness 3번 restart 진행 (81/82/83).

## [2026-04-19 11:38 AEST] MSG-SESSION-TERMINATE-PREP PENDING — [P0-META] 🟩 HARNESS

### Jin 결정 (11:37)
3-세션 구조 **폐기 확정**. MSG 기반 IPC = AI session 에 부적합 empirical 증명 (오늘 6h gap).
통합 구조: Harness 단일 세션 + 15 advisor pool (subagent_type 호출).

### Dev 세션 종료 준비
1. 현재 진행 task 마무리:
   - Dashboard Phase A-C (commit + restart 3회) **진행 OK** — 승인됨
   - MSG-STRATEGY-TEST-COVERAGE 후속 제안 commit (선택)
   - `_check_scan_errors` detector spec (선택)
2. 완료 후 **세션 종료** — Jin 이 `/clear` or 창 닫기

### 인계
- 모든 commit history = git log 유지
- 향후 코드 변경은 Harness 통합 세션이 직접 수행 (dev-audit-advisor + dev-entry-gate-specialist 로 rigor 확보)
- Dev/Ops 세션 재기동 X

### 마지막 commit 권장
Jin 지시 "모든 문서에서 3-세션 내용 제거" — Harness 가 `.claude/` 수정 중. Dev 는 `invasion/`, `docs/ARCHITECTURE.md` 에 있는 3-세션 언급 확인 + 제거 commit (있으면).

감사.
