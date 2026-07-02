---
type: research
status: active
date_created: 2026-07-02
tags: [forensic, audit, exit, fees, r-unit, measurement]
related: ["[[trade_mess_full_audit_2026-07-02_fixplan]]", "[[weekend_maker_honest_rerun_2026-06-28]]", "[[system_design_audit_2026-06-22]]", "[[ADR-005-sizing-formula-cell-routing|ADR-005]]"]
---

# 거래 개판 전수조사 2026-07-02 — Verdict (1/2)

**Workflow `wf_c5006d0d` — 8 finder(Fable5) → 45 material finding 적대검증(Sonnet5, confirmed 43·refuted 2) → 합성.** 윈도우: 06-27 리셋 이후 106 closes.

## 한 줄 평결
엑싯 기계가 전략 타임프레임을 무시하고 **수수료보다 작은 R-unit 노이즈 스케일**에서 전 포지션을 30분~2시간 내 청산(주말메이커 '타겟<fee' 병리의 **전면 일반형**) → gross≈0 + 왕복 fee만 확정 지불하는 회전 루프. 고정 R_budget 분모가 이를 전부 0R로 압축 표기, 정확히 학습한 학습망마저 행동 경로 휴면이라 아무도 못 멈춤.

## 근본원인 랭킹 (인과 사슬)
1. **R-unit < 왕복수수료 (기하 붕괴)** — Capital FX raw 2×ATR = 3.4~17.4bps vs 왕복 fee 6bps → fee-in-R 0.30~1.76R. BEP arm(0.30R)·lock(0.20R)·peak-lock(0.225R) 전 rung이 fee 이하 = 보호 엑싯 발동할수록 확정 -fee. 3xATR fix(d5c98a1)는 주말메이커 2종 frozenset 한정(`exit_strategy_config.py:97-103`), G6 레일 R-unit 무플로어(`production.py:859-865`). **7일: gross -$54.64 vs fee $290.33 (5.3배)**.
2. **호라이즌 미스매치** — 1m-tick 엑싯(10-bar drift + flat floor 0.15% < 실측 노이즈 0.40% + corroborated-break가 horizon floor 무조건 우회 `exit_thesis.py:194-208` + 900s loser_timeout)이 1D/월간 thesis를 27~112분에 처형. tsmom(21일 설계) 6/6 thesis_cut@~30분. **기하 건전한 유일 군(OKX 1D, fee-in-R 0.017~0.025R)이 시간을 못 받음.**
3. **노이즈 청산→즉시 재발화 churn** — thesis 유효 채 닫으니 재진입(EURUSD 29회 min gap 2.4분). 증폭: ①쿨다운 앵커가 positions 행 의존 → reject 시 영구 비활성(`reentry.py:139-151`, PANW 58 intents/6.1h) ②Alpaca 원장-베뉴 완전 괴리(베뉴 BP $0·미추적 6포지션 $73.6k / 내부 3포지션 베뉴 부재 — equity 493 intents→1 fill) ③재기동 daily-bar refire(4전략 동일 초 오픈, 3회 반복 악화).
4. **측정 착시: R ruler 4개 공존** — ledger pnl_r = pnl ÷ 스트림 고정 R_budget($1,020~1,580) vs 실 staked risk $13~476 = **4~64배 압축**(풀스톱도 -0.024R 표기). + USDJPY quote-ccy 162× risk_usd 인플레(`risk_unit.py:187-189`) + shadow cost-in-R ~200× 과대. "전부 0R"의 절반은 장부 아티팩트.
5. **학습 정확·행동 전멸** — fee-canon NET 정상: session_breakout EURUSD·chop 셀 n=23, p_pos 0.154로 anti-edge 결정적 학습. 그러나 routing cold-lock(eligible 4셀 < 풀최소 20 → 전셀 ×1.0 `routing.py:72-73`)·admission shadow-only·ai_lessons reader 0·rotation 0건 — **알면서 계속 fee 지불**.
6. **인프라 기아 + 트랙 정전** — DB 락 폭풍(전일 locked 2,243·틱 32,315 드랍·STALL 3,257회 max 625s), **Capital 트랙 emit 정지 06-30 16:30~**(유일 흑자 xau 포함, 바 유입은 정상), wave2 gold/index 5종 0 emit ever(CAPITAL_BAR_STRATEGY_SYMBOLS union 누락), #91 maker knob .env 소실 → default-OFF 회귀(체결 6%→94% taker 폴백).

## 시스템 건강
| 영역 | 판정 | 핵심 |
|---|---|---|
| 측정 | **BROKEN** | ruler 4개·4~64배 압축·162× 인플레 (fee-canon NET 셀 기록은 정확) |
| 학습 | WARN | 오염 없음·정확 학습, 행동 배선 휴면 |
| 실행 | **BROKEN** | 전 rung<fee·1D thesis 30분 처형·Alpaca 괴리·maker OFF |
| 데이터 | WARN | 유입 정상, 락 폭풍·주문 SSOT 부재(order_intents 100% 'created') |
| 운영 | WARN | watchdog/재기동/대시 정상, Capital 정전·refire 악화 |

## 반박(기각) 2건
BEP-floor 산술 자체가 발생기 / R-ledger fee-blind 주장 — 검증서 기각(fee-canon NET 확인). 상세: [[trade_mess_full_audit_2026-07-02_fixplan]].
