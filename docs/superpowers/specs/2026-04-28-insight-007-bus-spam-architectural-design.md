# INSIGHT-007 BUS Spam Architectural Spec

**Date**: 2026-04-28
**Status**: Draft
**Vault refs**: [[INSIGHT-007]] (open) [[INSIGHT-015]] (Phase 2 deployed)

---

## 1. Context

INSIGHT-007 status `open` (MED). BUS exit_triggered event 가 너무 많이 emit (spam pattern). INSIGHT-015 Phase 2 (events.jsonl → sqlite) 후 storage 영역 해소, 단 publish 빈도 자체는 그대로.

---

## 2. Root Cause 가설

**Th1 가설 공간**:
- H1: `exit_triggered` event 가 매 candle tick 마다 emit (per-position, per-tick)
- H2: 같은 trade 의 exit reason 이 여러번 emit (TIME→TRAIL routing 도중 중복)
- H3: position health check 가 exit_triggered 만들고 actual close 는 별도

**Vault grounding 필요**:
- bus.py `publish()` callers grep
- exit_cycle.py 의 exit_triggered emit site
- candle tick 별 emit 빈도

---

## 3. Options

| Option | Mechanism | 북극성 | Risk |
|---|---|---|---|
| A. Throttle (per-trade dedup, last 30s) | event 자체 throttle | observability 일부 손실 | low |
| B. Topic redesign (exit_triggered → exit_decision) | 의미 명확화, dedup 자연 | clearer | medium |
| C. Per-tick gate (last_emit_ts 추적) | manual throttle | observability 그대로 | low |
| D. Bus level throttle config | ParamRegistry preg | flexible | low |

**선호**: B + D 결합 — topic redesign + preg throttle.

---

## 4. Implementation (Phase 1)

### Step 1 — exit_triggered emit site grep
```bash
grep -rn 'publish.*exit_triggered\|exit_triggered.*publish' invasion/ --include='*.py'
```

### Step 2 — Throttle per-trade

```python
# bus.py 또는 emit caller
_last_exit_emit: dict[str, float] = {}  # trade_id -> last emit ts

def emit_exit_triggered(trade_id, ...):
    last = _last_exit_emit.get(trade_id, 0)
    if time.time() - last < THROTTLE_SEC:
        return  # skip
    _last_exit_emit[trade_id] = time.time()
    bus.publish("exit_triggered", trade_id=trade_id, ...)
```

### Step 3 — Preg

```python
_reg("bus_exit_triggered_throttle_sec", 30.0, (5.0, 120.0), "signal",
     "trade/exit_cycle.py:emit_exit_triggered (INSIGHT-007)",
     "Per-trade exit_triggered throttle (spam quench)")
```

---

## 5. North Star alignment

- ✅ Block 0 (throttle = same event 만 dedup, 차단 X)
- ✅ Observability 정합 (per-trade unique events 그대로)
- ✅ Architectural fix (topic redesign or throttle layer)

---

## 6. Verification

- BUS event count 30m 측정 (before/after)
- Same trade_id event count (dedup 효과)
- log spam 감소 검증

---

## 7. References

- Vault: [[INSIGHT-007]] [[INSIGHT-015]] (Phase 2 storage 해소)
- Code: `invasion/bus.py`, `invasion/trade/exit_cycle.py`
