# Alert Squad — Health Verification

"멀쩡하게 도는지" 체크리스트.

## 체크 (Harness event 기반, 또는 / Jin `/alert-triage health`)

### 1) EMIT ↔ ROUTE 정합 (dropout)
```bash
E=$(wc -l<data/alert_emit.jsonl); R=$(wc -l<data/alert_route.jsonl)
```
- Pass: E−R ≤ 1 (in-flight 1건 허용)
- Fail: ≥2 & 최근 10m 내 route 없음 → Monitor 재arm

### 2) ROUTE ↔ HANDLE 정합
- HIGH dispatch 대비 handler 완료 ≥95%
- 미완료 → timeout 후 재dispatch or CLOSED_FP

### 3) Alert 파일 누적 (cooldown 작동?)
```bash
ls .claude/harness_alerts/*.md | wc -l
```
- Pass: 최근 1h < 15
- Fail: cooldown 무시 detector 색출 → Dev fix

### 4) Queue 상태
```bash
grep -cE "^## \[.*OPEN" tasks/harness_items.md
grep -cE "^## \[.*IN_PROGRESS" tasks/harness_items.md
```
- Pass: OPEN<5, IN_PROGRESS<3 (HIGH IN_PROG<60s)
- Fail: jammed → 수동 재dispatch

### 5) jsonl 무결성
```bash
python3 -c "import json; [json.loads(l) for l in open('data/alert_emit.jsonl')]"
```
- Pass: 예외 0 / Fail: atomic write 요청 Dev

## INTEL 대시보드 Alert 패널 헤더

정상:
```
🚨 Squad: ✅ emit 24h 18 / route 18 (0 drop) / handled 16 / closed 14 | files 2 | OPEN 3
```

Fail:
```
🚨 Squad: ⚠️ ROUTE DROPOUT 3 (last 10m) | Monitor arm? | jammed 6 OPEN
```

## 추가 감사 (Alert item 누적 ≥ 5 시 트리거)

- Handler cost_tokens 합 → AI 예산 검토
- FP 비율 (>25% → threshold 재조정)
- Handler 별 평균 duration_ms → outlier 튜닝

## 상호 참조

`alert_squad.md` · `alert_lifecycle.md` · `alert_routing.md`
