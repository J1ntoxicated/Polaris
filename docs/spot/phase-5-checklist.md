# SPOT Bot Phase 5 — 1 Week Operational Checklist

> Spec: `docs/superpowers/specs/2026-04-30-spot-scalp-paper-bot-design.md`
> Plan: `docs/superpowers/plans/2026-04-30-spot-scalp-paper-bot.md`

## Pre-flight

- [ ] OKX_DEMO_API_KEY / SECRET / PASSPHRASE env 설정 확인
- [ ] `python3 -m invasion.spot --dry-run` 통과 (boot OK)
- [ ] `data/invasion_spot.sqlite` 생성 + WAL 모드 확인
- [ ] 메인 봇 (`python3 -m invasion --headless`) 정상 운영 중 — SPOT 봇과 격리 검증
- [ ] 시각화 reload — `[SPOT BOT]` 패널 + lime green cluster 표시

## 시작

```bash
# 별도 process — 메인 봇과 분리
python3 -m invasion.spot --headless --log-level INFO 2>&1 | tee logs/spot_$(date +%Y%m%d).log
```

## 매일 SQL — KPI

```sql
-- 1. Daily KPI
SELECT date(entry_ts, 'unixepoch') AS day, COUNT(*) n,
  ROUND(AVG(CASE WHEN net_pnl_usd>0 THEN 1.0 ELSE 0 END)*100,1) wr,
  ROUND(SUM(net_pnl_usd),2) net,
  ROUND(AVG(net_pnl_usd),3) avg_pnl,
  ROUND(100.0*SUM(CASE WHEN fill_type='maker' THEN 1 ELSE 0 END)/COUNT(*),1) maker_pct
FROM trades WHERE status='closed' GROUP BY day ORDER BY day DESC;

-- 2. Per-signal WR
SELECT strategy_id, COUNT(*) n,
  ROUND(AVG(CASE WHEN net_pnl_usd>0 THEN 1.0 ELSE 0 END)*100,1) wr
FROM trades WHERE status='closed' GROUP BY strategy_id ORDER BY n DESC;

-- 3. Cell sparse %
SELECT COUNT(*) total,
  SUM(CASE WHEN exit_optim_n_samples > 0 THEN 1 ELSE 0 END) learned,
  ROUND(100.0*SUM(CASE WHEN exit_optim_n_samples > 0 THEN 1 ELSE 0 END)/COUNT(*), 1) pct
FROM cell_matrix_spot;

-- 4. Reconcile zombie 24h
SELECT COUNT(*) FROM trades WHERE exit_type='zombie_cleanup' AND entry_ts >= strftime('%s','now')-86400;

-- 5. Maker fill rate (24h)
SELECT
  SUM(CASE WHEN fill_type='maker' THEN 1 ELSE 0 END) maker,
  SUM(CASE WHEN fill_type='taker' THEN 1 ELSE 0 END) taker,
  ROUND(100.0*SUM(CASE WHEN fill_type='maker' THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1) maker_pct
FROM trades WHERE status='closed' AND entry_ts >= strftime('%s','now')-86400;

-- 6. Exit type breakdown
SELECT exit_type, COUNT(*) n,
  ROUND(SUM(net_pnl_usd),2) net,
  ROUND(AVG(pnl_pct)*100,3) avg_pp
FROM trades WHERE status='closed' AND entry_ts >= strftime('%s','now')-86400
GROUP BY exit_type ORDER BY n DESC;
```

## Day-by-day

### D1 — 격리 + 데이터 흐름 검증
- [ ] 격리: 메인 봇 SIGTERM → SPOT 봇 무영향 확인
- [ ] n ≥ 50 (봇이 진입 가능한 상태인지)
- [ ] Maker fill rate 측정 (목표 ≥ 80%)
- [ ] WS uptime ≥ 99% (`grep -c WS_RECONNECT logs/spot_*.log`)
- [ ] Reconcile zombie 0 (이상 감지)

### D2 — 회복선 도달 여부
- [ ] Daily NET 추세 확인 (목표 양수)
- [ ] WS / API 에러 누적 0 (또는 자동 복구)
- [ ] cell sparse 누적 — 시작 0% → D2 5%+

### D3 — WR 추세
- [ ] 7-bucket per-strategy WR (어느 signal 조합이 best?)
- [ ] WR ≥ 75% 1차 합격선 도달 여부
- [ ] avg_pp / TP exit avg → expectancy 계산

### D4-D6 — 안정 운영
- [ ] cell sparse 누적 추세 (목표 D7 ≥ 40%)
- [ ] Daily NET 5/7일 양수
- [ ] zombie 0 유지
- [ ] 메인 봇과 cross-impact 0 (mainbot KPI 영향 없음)

### D7 — Pivot 결정 frame

| 결과 | 다음 행동 |
|---|---|
| **합격선 모두 + WR ≥ 75%** | **확장**: Phase 2 — Alpaca SPOT 추가, capital up $10k → $50k |
| **합격선 + WR < 75%** | **튜닝**: signal threshold 조정 (gate min 3→2, BB k 2.5→2.0 등) 1주 추가 |
| **합격선 일부 미달** | **분석**: 어느 KPI? root-cause INSIGHT 작성, 재검토 |
| **실패 트리거 hit** | **중단**: post-mortem INSIGHT, 결함 패턴 학습 |

### 합격선 (필수)
- Daily NET >0 (5/7일 양수)
- Expectancy/trade > $0
- Reconcile zombie 0건
- Maker fill rate ≥ 80%
- WS uptime ≥ 99%

### Stretch (도전)
- WR ≥ 75% (1차) → 85% (stretch)
- Daily n ≥ 200
- Total NET 7일 > +$200

### 실패 트리거 (즉시 중단)
- 3일 연속 NET 음수
- Reconcile zombie 5+건
- Maker fill rate < 50%
- Crash uptime < 95%

### 회색지대 (50~79% maker fill, WR 50~74%, NET 약양수)
1주 추가 튜닝 후 재평가.

## 매일 vault 갱신 (의무)

- `vault/log.md` chronological 1줄 추가:
  ```
  ## YYYY-MM-DD — SPOT bot Day N
  WR X%, NET $Y, maker Z%, n N. (관련 [[ticker]] 등)
  ```
- D7 종료 시: `vault/03_knowledge/insights/INSIGHT-XXX-spot-1week-results-YYYY-MM-DD.md` 작성 (full week findings)

## North Star self-check (매일)

- ✅ `feedback_aggressive_always_profit` — 수익 source 강화 검증
- ✅ `feedback_loss_profit_asymmetry` — long-only + tight, expectancy 양수 추구
- ✅ `feedback_no_quick_patch_ever` — pivot decision frame 따라 결정
- ✅ `feedback_no_block_filter_architecture` — block X (ramp/extend 으로 진화)
