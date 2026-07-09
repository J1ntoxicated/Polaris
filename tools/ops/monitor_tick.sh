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

exit 0
