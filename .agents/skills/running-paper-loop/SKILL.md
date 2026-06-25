---
name: running-paper-loop
description: Use to start, monitor, or stop the Polaris paper-trade main loop across OKX SPOT demo, Capital CFD demo, and Alpaca paper. Boot = ignite_p1 (boot orchestrator) handing off to run_production_paper_loop (bar pipeline + live recalc + env-gated P5 tick engine). Start command SSOT = AGENTS.md Quick reference.
---

# running-paper-loop (P0 skill)

## When to use
- Jin 이 페이퍼 트레이딩 시작 / 중지 / 상태 요청
- 24h 세션 재기동, daily reset 후

## 기동 (커맨드 SSOT = AGENTS.md "Quick reference" — 여기 복붙 금지)
- 골격: `python3 -m polaris.scripts.ignite_p1 --paper ...` — 정확한 플래그는
  AGENTS.md Quick reference 에서 읽는다. 항상 백그라운드 detach
  (Codex 세션 kill 금지 — feedback_never_kill_claude_session 참조)
- **P5 tick engine**: env `TICK_ENGINE_ENABLED=1` 로 활성
  (SSOT: `polaris/core/ticks/config.py`)
- 중지: `kill -SIGTERM <PID>` · 대시보드: `./scripts/start_dashboard.sh`

## 구조 (code = SSOT)
- `polaris/scripts/ignite_p1.py` — boot orchestrator: DB schema, Layer 0
  universe 확인, Layer 5 learner scheduler 기동 후 production loop 에 위임
- `polaris/scripts/production_paper_loop.py` — 본체 `run_production_paper_loop`
  (default phase=P1): bar ingest → G1→G8 → live recalc (per-position G6/G7)
  → close / PnL
- `polaris/scripts/_production_tick_engine.py` — P5 tick decision engine
  (~500ms WS 틱 루프, env-gated)
- venue adapters: `polaris/venues/` (OKX `us.okx.com` demo / Capital demo /
  Alpaca paper)

## Outputs
- SQLite events / fills / positions (`--db` 경로)
- runtime log (`--log-file`)
- session-end vault digest (material change 시)

## Failure handling
- Strategy 예외 → strategy-scoped HALT, 나머지 전략 continue (Layer 7)
- Venue 장애 → 해당 venue 경로만 영향, 타 venue continue (degrade-never-halt)
- Halt 는 무결성 고장에만 — P&L / 손실로는 절대 정지하지 않는다
  (circuit breaker 철학)

## Cross-ref
- [[ADR-003]] 8-layer · [[ADR-004]] gates
- skills: gating-pipeline · signaling-strategies · reconciling-portfolio
