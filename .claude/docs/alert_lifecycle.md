# Alert Lifecycle — 4-Stage 로그 체인

## 흐름

```
Stage 1: EMIT    (봇 detector)          → data/alert_emit.jsonl
   └→ .claude/harness_alerts/{ts}_{cat}.md (Monitor trigger)
Stage 2: ROUTE   (Harness Router)       → data/alert_route.jsonl
   └→ tasks/harness_items.md (state=OPEN)
Stage 3: HANDLE  (Handler skill/agent)  → data/alert_handler.jsonl
   └→ item state=IN_PROGRESS→SPEC'D, harness_to_{dev,ops}.md push
Stage 4: CLOSE   (Dev/Ops commit or FP) → data/alert_close.jsonl
   └→ item state=CLOSED
```

## 로그 파일 schema (jsonl)

### `data/alert_emit.jsonl` (Dev task, 봇 구현)
`{ts, ts_iso, category, severity, trigger_value, threshold, file, cooldown_hit, in_warmup}`

### `data/alert_route.jsonl` (Harness Router)
`{ts, alert_file, category, item_id, handler, route_latency_ms, decision}`
`decision`: DISPATCH / SKIP_DUPLICATE / SKIP_COOLDOWN / ERROR

### `data/alert_handler.jsonl`
`{ts, item_id, handler, duration_ms, result, spec_msg, analysis_summary, cost_tokens}`
`result`: SPEC'D / CLOSED_FP / DEFERRED / ERROR

### `data/alert_close.jsonl`
`{ts, item_id, close_reason, linked_commit, time_to_close_sec, outcome}`
`close_reason`: DEV_COMMIT / OPS_CONFIG / FP / SUPERSEDED / EXPIRED

## item_id

- `ITEM-NNN` 단순 증가 (MSG 번호와 분리)
- squad 재시작 후 `max(existing) + 1` 복구 (queue 파일 scan)

## Latency SLA (informational)

| Stage | Target | 위반 시 조치 |
|---|---|---|
| EMIT→ROUTE | <10s | Monitor 재arm |
| ROUTE→HANDLE | HIGH<60s/MED<5m/LOW<1h | handler 누락 체크 |
| HANDLE→SPEC'D | HIGH<5m/MED<15m | handler hang 의심 |
| SPEC'D→CLOSED | Dev 처리 (SLA 없음) | queue rotation |

## 상호 참조

Squad: `alert_squad.md` · Routing: `alert_routing.md` · Verification: `alert_verification.md` · Queue: `tasks/harness_items.md`
