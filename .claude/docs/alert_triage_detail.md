# Alert Triage — 실행 모드 / 출력 상세

`.claude/commands/alert-triage.md` 본문 보조. 실행 모드 파이프라인 / 출력 포맷 / 운영 예시.

## 1) Auto mode 상세 파이프라인

```
Monitor "ALERT harness_alerts ..." notification
  → 최신 .claude/harness_alerts/*.md 파일 read
  → frontmatter parse (category, severity, trigger_value, threshold)
  → data/alert_route.jsonl append
  → tasks/harness_items.md append (state=OPEN, ITEM-NNN 증가)
  → routing table lookup → handler inline invoke
  → handler 결과 → data/alert_handler.jsonl
  → item state 전이 (IN_PROGRESS → SPEC'D / CLOSED_FP)
  → SPEC'D 면 dev-coder / ops-executor inline dispatch
```

## 2) Manual mode 상세

- `/alert-triage` (arg 없음) → 최근 10개 alert 파일 triage 상태 표로 보고
- `/alert-triage <item_id>` → 특정 item 재분석, handler 재실행 (force flag)
- `/alert-triage health` → verification.md 체크리스트 전체 실행 + 결과 보고

## 출력 포맷 예시

```
Alert Triage — YYYY-MM-DD HH:MM

| ITEM-ID   | Cat           | Sev  | State      | Handler         | Action         |
|-----------|---------------|------|------------|-----------------|----------------|
| ITEM-042  | wr_1h         | MED  | SPEC'D     | strategy_drift  | MSG-R4E Dev    |
| ITEM-041  | loss_streak   | HIGH | CLOSED     | codex-rescue    | FP (warmup)    |
| ITEM-040  | silent        | HIGH | IN_PROG    | health_ping     | feed reconnect |

Squad health: emit 18 / route 18 (0 drop) / handled 16
Queue: OPEN 1 / IN_PROG 1 / SPEC'D 1 / CLOSED_24h 18
```

## State 전이 (lifecycle.md 와 일치)

- `OPEN` — route 완료, handler 미 dispatch
- `IN_PROGRESS` — handler 실행 중
- `SPEC'D` — fix spec 확정, Dev/Ops 위임 대기
- `CLOSED_FP` — false positive 판정
- `CLOSED_FIXED` — spec 구현 + restart 반영
- `CLOSED_NORTHSTAR_VIOLATION` — dampen/block spec 거부

## 참조 back → [alert-triage.md](../commands/alert-triage.md)
