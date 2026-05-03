---
entity_type: hypothesis
entity_id: HYPO-NNN
auto: false
last_modified: YYYY-MM-DD
expires: YYYY-MM-DD       # 필수: BACKTEST/PAPER 결과 도달 시점 + 30일
editable: true
back_links: ["[[60_alpha/_alpha_index]]", "[[<관련 INSIGHT/ADR>]]"]
mode: alpha
reviewed_by: codex|gemini|jin
tags: [type/hypothesis, status/active|graduated|archived, scope/alpha, polaris]
---

# HYPO-NNN — <가설 제목>

> 한 문장 가설 요약.

## Hypothesis (가설)

<H_0 / H_1 명시>

## Rationale (근거)

- 모태 데이터: `<edge_calibration / tournament_elo / 인용 값>`
- 관련 INSIGHT: [[INSIGHT-NNN]]
- 직관/관찰: <간단 서술>

## Method (검증 방법)

### Backtest
- 데이터: <시작/끝 날짜, 자산, 빈도>
- 메트릭: <Sharpe / hit rate / MDD / expectancy>
- Pass 기준: <정량 임계값>

### Paper (Backtest pass 시)
- 기간: <최소 N trades or X일>
- 메트릭: <위와 동일 + slippage gap>
- Pass 기준: <정량 임계값>

## Fast-fail Gate (BACKTEST 24h 내)

- 수학적 생존 가능성 (예: fee × 2 < expected_TP)
- 기각 시 INSIGHT 작성 후 archived

## Promotion Gate (PAPER → ADR)

- [ ] paper/live behavior diff audit
- [ ] sizing cap 명시
- [ ] kill criteria 명시
- [ ] rollback plan 명시

## Results

- Backtest: <결과 + 통과/실패>
- Paper: <결과 + 통과/실패>
- Promotion 결정: <ADR 승격 / archived>

## Related

- ADR (승격 시): [[ADR-NNN]]
- 결과 INSIGHT: [[INSIGHT-NNN]]
