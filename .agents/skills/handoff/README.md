# Polaris Neural Cloud v4 — CLI 핸드오프

**1개 파일**: `polaris-neural-cloud-v4.html` (~145KB, self-contained, 더블클릭으로 실행)

지금은 mock 데이터로 도는 시각화. CLI가 해야 할 일:
1. 실제 AI 모듈 이름 자동 매핑
2. 라이브 데이터 (옵션) WS 연결

---

## 1. 어디 둘지

아무데나. 단독 HTML이라 의존성 없음. 권장:

```
<repo>/dashboards/neural-cloud/polaris-neural-cloud-v4.html
```

브라우저로 그냥 열거나 (file://), 가벼운 정적 서버:
```bash
python3 -m http.server 8080  # 그 폴더에서
```

---

## 2. AI 모듈 자동 바인딩

### 2-1. CLI가 코드베이스에서 추출할 것

각 AI 모듈에 대해 다음을 결정:

| 필드 | 의미 | 예시 |
|---|---|---|
| `id` | 내부 식별자 | `master_judge`, `regime_clf` |
| `name` | 화면 표기 라벨 (짧게, 14자 이하 권장) | `MASTER JUDGE` |
| `tier` | 궤도 분류 (아래 가이드) | `high` / `mid` / `low` / `tool` |
| `weight` | 가중치 0~1 (시각 크기에 영향, 옵션) | `0.4` |
| `color` | RGB 배열 (옵션, 안 주면 기본색) | `[215, 175, 255]` |

### 2-2. tier 결정 가이드

CLI가 모듈을 분류할 때:

- **`high`** — 시스템 전체를 보고 큰 결정 내림. 호출 빈도 낮음, context 큼  
  e.g. 전략 선택, 자본 배분, 메인 의사결정
- **`mid`** — 전술적 AI. 시그널 검증, critic, conviction scoring  
  e.g. 신호 평가, 진입 판단, 점수 산출
- **`low`** — 빠르고 좁은 거래성 AI. 호출 빈도 높음  
  e.g. exit timing, gate AI, 미세 조정
- **`tool`** — AI 아닌 결정론적/룰베이스 시스템 모듈 (다이아몬드 마커로 구분 표시)  
  e.g. 레짐 분류기, evolver, scheduler

### 2-3. 적용 코드

HTML 안의 `<script>` 마지막에 한 줄 추가하거나, 별도 `<script>` 블록:

```html
<script>
window.addEventListener('load', () => {
  // 페이지 로드 + sphere 부트 후 한 박자 기다리고 바인딩
  setTimeout(() => {
    window.PolarisCloud.bindAIModules({
      high: [
        { id: 'master_judge',     name: 'MASTER JUDGE',  weight: 0.45 },
        { id: 'capital_allocator', name: 'ALLOCATOR',    weight: 0.30 },
      ],
      mid: [
        { id: 'critic',     name: 'CRITIC',     weight: 0.30 },
        { id: 'conviction', name: 'CONVICTION', weight: 0.25 },
        { id: 'scout',      name: 'SCOUT',      weight: 0.20 },
      ],
      low: [
        { id: 'exit_timer', name: 'EXIT TIMER', weight: 0.12 },
        { id: 'gate_ai',    name: 'GATE AI',    weight: 0.10 },
        { id: 'micro_tuner', name: 'MICRO',     weight: 0.08 },
      ],
      tools: [
        { id: 'regime_detector', name: 'REGIME DETECTOR' },
        { id: 'evolver',         name: 'EVOLVER' },
      ],
    });
  }, 500);
});
</script>
```

**중요**: 슬롯 수가 정해져 있음.
- `high`: 2 슬롯
- `mid`: 3 슬롯
- `low`: 3 슬롯
- `tools`: 2 슬롯

더 많이 필요하면 알려줘 → 슬롯 추가해서 v5 빌드.  
배열에 더 많이 넘기면 처음 N개만 사용됨.

---

## 3. 라이브 데이터 (옵션)

지금은 mock JSON + 가짜 events.jsonl로 도는데, 실제 데이터 꽂으려면 두 가지:

### 옵션 A — JSON 파일 교체

`data/` 폴더에 다음 7개 파일을 실시간으로 갱신하면 reload시 반영:
- `regime.json` — 현재 레짐, F&G, VIX, BTC.D
- `providers.json` — AI judges + data feeds 메타
- `pipeline.json` — open positions + scan candidates
- `strategies.json` — strategy cells (elo, status)
- `exit.json` — exit quality metrics
- `obs.json` — system health checks
- `actions.json` — action queue

스키마는 ui_kits/galaxy/data/*.json (mock) 참고.

### 옵션 B — WebSocket

```js
window.PolarisCloud.connectWS('ws://localhost:7777');
```

서버는 다음 형태 메시지 push:
```json
{"type": "regime", "value": "RISK_OFF"}
{"type": "nsi", "value": 0.87}
{"type": "pulse", "from": "providers", "to": "strategy"}
{"type": "cluster", "cluster": "exit", "n": 3}
{"type": "trade", "ticker": "BTC-USDT", "dir": "L", "exchange": "okx", "pnl": 1.2}
{"type": "kill", "value": true}
{"type": "bind_ai", "modules": { "high": [...], "mid": [...], ... }}
```

---

## 4. 직접 호출 가능한 API

콘솔에서 또는 외부에서 트리거:

```js
PolarisCloud.setRegime('CRISIS')           // 배경색 + 라벨 변경
PolarisCloud.setNSI(0.42)                  // 0~1
PolarisCloud.setKillSwitch(true)           // ACTION 위성 폭발
PolarisCloud.pulseShell('strategy', 6)     // 한 shell 안에서 펄스 6번
PolarisCloud.pulseEdge('providers', 'signals')  // 두 shell 사이 라디얼
PolarisCloud.pulseCluster('exit', 4)       // 위성 클러스터 펄스
PolarisCloud.fireTrade({                    // 풀 cascade (market→core→supernova→exit)
  ticker: 'ETH-USDT-SWAP',
  dir: 'L',
  exchange: 'okx',  // 'okx' | 'cap' | 'alp' | 'bin'
  pnl: 2.1
})
PolarisCloud.stats()                        // 디버그용
```

---

## 5. 토폴로지 cheatsheet

```
                ╭──── MARKET / REGIME ────╮       outermost shell
               ╱   ◆ PROVIDERS shell        ╲
              │      ◆ SIGNALS shell          │
              │        ◆ STRATEGY shell        │
              │          ◆ GATE shell           │
              │     ╭─ inner cores ─╮            │
              │     │ OKX CAP ALP BIN │           │   center
              │     ╰────────────────╯            │
               ╲                                 ╱
                ╰────────────────────────────────╯

  궤도 (밖→안):
    AI HIGH  r≈1.30   2개 슬롯
    AI MID   r≈1.00   3개 슬롯
    DIRECT TOOLS r≈0.78   2개 (다이아몬드)
    AI LOW   r≈0.50   3개 슬롯

  외곽 위성:
    OBS     r≈1.45  (square, 시스템 헬스 watcher)
    ACTION  r≈1.55  (square, 출력)
    EXIT    r≈0.28  (square, 코어 근처)
```

## 6. 컨트롤

- 마우스 드래그 → 회전
- 휠 → 줌
- Space → 자동회전 토글
- R → 카메라 리셋
- 노드 클릭 → 상세 패널 + provenance chain 강조
- 위성 클릭 → 위성 상세 + 운반 중인 entity 리스트
- 우측 레전드 클릭 → 그 shell/위성 펄스
- Esc → 패널 닫기
