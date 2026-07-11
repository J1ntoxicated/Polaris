#!/bin/bash
# monitor_tick.sh — 모니터링 루프 틱의 결정적 수치 수집 (read-only).
#
# 배경: Haiku 틱 에이전트가 매 틱 자유 쿼리를 짜다 필드별 오보 반복
# (전체합산 equity -396/-2261, '5h 로그 스톨', '신호 0' — 전부 쿼리 이탈).
# 이 스크립트가 여섯 수치를 고정 쿼리로 출력하고, 에이전트는 실행+판정만.
#
# 사용: bash tools/ops/monitor_tick.sh   (인자 없음, 항상 exit 0, read-only)
set -u
cd "$(dirname "$0")/../.." || exit 0
DB="file:data/polaris_live.sqlite?mode=ro"
LOG="data/paper/polaris_runtime.log"
DEPLOY_MS=1783537395000   # 활발거래 배포 경계 (2026-07-08 19:03 UTC) — equity 누적 기준점

Q() { sqlite3 "$DB" "$1" 2>/dev/null || echo "QUERY_FAIL"; }

B=$(Q "SELECT strftime('%s','now','-1 hour');")
NOW_UTC=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "now_utc=$NOW_UTC"
echo "boundary_epoch=$B"

# ① 락 (최근 1h, 로그)
CUT=$(date -u -v-1H '+%Y-%m-%dT%H:%M:%S' 2>/dev/null)
LOCKS=$(awk -v c="$CUT" 'substr($0,1,19) > c && (/database is locked/||/db_writer.*failed/){n++} END{print n+0}' "$LOG" 2>/dev/null || echo "LOG_FAIL")
echo "locks_1h=$LOCKS"

# ② 신호 (최근 1h) + 유니크 전략 + 마지막 신호 시각
echo "signals_1h=$(Q "SELECT COUNT(*) FROM signals WHERE ts>$B;")"
echo "signal_strats_1h=$(Q "SELECT COUNT(DISTINCT strategy_id) FROM signals WHERE ts>$B;")"
echo "last_signal_utc=$(Q "SELECT datetime(MAX(ts),'unixepoch') FROM signals;")"
echo "volume_burst_1h=$(Q "SELECT COUNT(*) FROM signals WHERE ts>$B AND strategy_id='volume_burst';")"

# ③ 체결 (최근 1h)
echo "opens_1h=$(Q "SELECT COUNT(*) FROM fills WHERE ts_ms>$B*1000 AND is_close=0;")"
echo "closes_1h=$(Q "SELECT COUNT(*) FROM fills WHERE ts_ms>$B*1000 AND is_close=1;")"
echo "last_fill_utc=$(Q "SELECT datetime(MAX(ts_ms)/1000,'unixepoch') FROM fills;")"
echo "recent_closes_r=$(Q "SELECT GROUP_CONCAT(symbol||':'||ROUND(pnl_r,3),' ') FROM (SELECT symbol, pnl_r FROM positions WHERE closed_ts>$B ORDER BY closed_ts DESC LIMIT 5);")"

# ④ WAL + 최근 60s ERROR
WAL=$(ls -l data/polaris_live.sqlite-wal 2>/dev/null | awk '{print $5}' || echo 0)
echo "wal_bytes=$WAL"
CUT60=$(date -u -v-60S '+%Y-%m-%dT%H:%M:%S' 2>/dev/null)
echo "errors_60s=$(awk -v c="$CUT60" 'substr($0,1,19) > c && /ERROR/ && !/kapow|boom/{n++} END{print n+0}' "$LOG" 2>/dev/null || echo "LOG_FAIL")"

# ⑤ 프로세스
echo "bot_pid=$(pgrep -f ignite_p1 | head -1 || echo NONE)"
echo "dash_8770=$(lsof -ti :8770 >/dev/null 2>&1 && echo UP || echo DOWN)"
echo "log_last_ts=$(tail -1 "$LOG" 2>/dev/null | cut -c1-24)"

# ⑥ equity (배포누적 — 고정 경계, 전체합산 아님)
echo "equity_deploy_cum=$(Q "SELECT ROUND(SUM(pnl_usd),2) FROM fills WHERE ts_ms>$DEPLOY_MS;")"

# ⑦ 섀도우 채널 건강 (behavior-0 계측 6종, read-only) — design:
# vault/50_research/backgate-plan/design-monitoring.md W1 §A. 6채널 전부
# created_ts(epoch초) 보유 — 위 $B 1h 경계 재사용. STALL = 평일(UTC 월-금)
# 에 inc_1h=0 AND rows>0(이전엔 기록 있었음) — 주말은 세션 인지로 정상
# (휴장-holiday 캘린더는 W1 범위 밖, 주말만 감지).
# KNOWN 오탐 2종 (판정자 참고용, W1 범위 내 정상 — 로직 변경 아님):
#   (1) 평일 US 증시 휴장 — equity 채널(vwap/news/sector_rank/meta_labels)이
#       조용해지며 STALL=1 (2026-06-28 재발 클래스와 동형). holiday calendar
#       는 W-later 트래킹 대상, W1은 주말만 인지.
#   (2) 이벤트-드리븐 저빈도 채널 — meta_labels는 trade CLOSE 시에만,
#       sector_rank_shadow는 리밸런스 사이클에만 증가 → 평일 정상 조용한
#       1h 구간도 inc_1h=0 AND rows>0 로 STALL=1 오탐 가능.
# 두 경우 모두 numbers-only 판독(sizing/gate 미접촉)이라 non-blocking.
DOW=$(date -u '+%u')  # 1=Mon..7=Sun
if [ "$DOW" -le 5 ]; then IS_WEEKDAY=1; else IS_WEEKDAY=0; fi
echo "is_weekday_utc=$IS_WEEKDAY"
for CH in calibration_pairs vwap_timing_shadow news_timing_shadow sector_rank_shadow gate_shadow_events meta_labels; do
    ROW=$(Q "SELECT COUNT(*), COALESCE(MAX(created_ts),0), COALESCE(SUM(CASE WHEN created_ts>$B THEN 1 ELSE 0 END),0) FROM $CH;")
    if [ "$ROW" = "QUERY_FAIL" ]; then
        echo "shadow_${CH}_rows=QUERY_FAIL"
        echo "shadow_${CH}_age_s=QUERY_FAIL"
        echo "shadow_${CH}_inc_1h=QUERY_FAIL"
        echo "shadow_${CH}_stall=QUERY_FAIL"
        continue
    fi
    IFS='|' read -r ROWS LAST_TS INC <<< "$ROW"
    if [ "${LAST_TS:-0}" -gt 0 ] 2>/dev/null; then
        AGE=$(( $(date -u +%s) - LAST_TS ))
    else
        AGE="NULL"
    fi
    STALL=0
    if [ "$IS_WEEKDAY" = "1" ] && [ "${ROWS:-0}" -gt 0 ] 2>/dev/null && [ "${INC:-0}" = "0" ]; then
        STALL=1
    fi
    echo "shadow_${CH}_rows=$ROWS"
    echo "shadow_${CH}_age_s=$AGE"
    echo "shadow_${CH}_inc_1h=$INC"
    echo "shadow_${CH}_stall=$STALL"
done

# ⑧ 척후 피드 신선도 (log_scan 신선도 마커, design-monitoring.md W1 §C) —
# 뉴스 최신 ingestion age / DFII10 최신 신호 age / momentum_z 최신 age.
# 숫자만(age seconds) — stale 판정(임계값)은 사람+/debate 몫, 여기선 미판정.
NOWS=$(date -u +%s)
NEWS_TS=$(Q "SELECT MAX(ingestion_ts) FROM news_timing_shadow;")
if [ "${NEWS_TS:-0}" -gt 0 ] 2>/dev/null; then
    echo "feed_news_ingestion_age_s=$(( NOWS - NEWS_TS ))"
else
    echo "feed_news_ingestion_age_s=NULL"
fi
# DFII10 (#5 골드 컨빅션) 은 GOLD/XAUUSD 신호의 payload_json tags.dfii10 로만
# 스탬프됨(persist_tags, signal_persist.py) — 별도 테이블 없음, signals.ts 재사용.
DFII10_TS=$(Q "SELECT MAX(ts) FROM signals WHERE payload_json LIKE '%\"dfii10\"%';")
if [ "${DFII10_TS:-0}" -gt 0 ] 2>/dev/null; then
    echo "feed_dfii10_signal_age_s=$(( NOWS - DFII10_TS ))"
else
    echo "feed_dfii10_signal_age_s=NULL"
fi
# momentum_z (#3 XS-모멘텀) 는 universe.momentum_z UPDATE 자체엔 타임스탬프가
# 없음 — sector_rank_shadow.created_ts 가 momentum_z 를 보유한 유일한 시각
# 컬럼(위 ⑦의 shadow_sector_rank_shadow_age_s 와 동일 값, 의도적 재사용).
MZ_TS=$(Q "SELECT MAX(created_ts) FROM sector_rank_shadow;")
if [ "${MZ_TS:-0}" -gt 0 ] 2>/dev/null; then
    echo "feed_momentum_z_age_s=$(( NOWS - MZ_TS ))"
else
    echo "feed_momentum_z_age_s=NULL"
fi

# ⑨ 섀도우 분포 가드 (design-monitoring.md W1 §A 분포 가드 [R1-B5], read-only)
# — 채널별 평균/표준편차/n_distinct/dominant_share/top_symbol/dedup_ratio +
# input fingerprint(tools/ops/shadow_distribution_guard.py). §⑦의 행수/신선도
# 만으로 못 잡는 "쌓이고는 있는데 이상한 데이터" 구멍 봉쇄. 결정적 CLI 그대로
# 실행+캡처 — Haiku 자유쿼리 아님(§⑧ 판정주체 고정 계약 유지). 출력은 guard의
# run_report() 라인 그대로(channel=... 로 시작, 접두사 재라벨 없음 — 단독 실행
# 결과와 1:1 diff 가능해야 검증이 성립). venv 부재/실패해도 틱은 계속(exit 0).
if [ -x ".venv/bin/python" ]; then
    .venv/bin/python -m tools.ops.shadow_distribution_guard 2>/dev/null \
        || echo "shadow_distribution_guard_FAIL=1"
else
    echo "shadow_distribution_guard_FAIL=1"
fi

exit 0
