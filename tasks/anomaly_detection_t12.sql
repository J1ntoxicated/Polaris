-- T12 관찰 전용 anomaly detection (read-only, 포지션/코드 무개입)
-- 사용: sqlite3 data/invasion.sqlite ".read tasks/anomaly_detection_t12.sql"
-- 목적: "peak 면책 + TRAIL 미발동" 구조적 결함 데이터 증거 수집

.mode column
.headers on
.timeout 5000

-- 1. Open 포지션 기본 통계
SELECT '--- Open 포지션 개요 ---' as sec;
SELECT exchange, asset_group,
       COUNT(*) n,
       ROUND(AVG((strftime('%s','now')-entry_ts)/60.0),0) avg_age_min,
       ROUND(MAX((strftime('%s','now')-entry_ts)/60.0),0) max_age_min
FROM trades
WHERE status='open'
GROUP BY exchange, asset_group
ORDER BY avg_age_min DESC;

-- 2. 초장기 open (age > 6h, 구조적 결함 candidate)
SELECT '--- Suspected Stuck Positions (age > 360min) ---' as sec;
SELECT ticker, exchange, asset_group, direction,
       ROUND((strftime('%s','now')-entry_ts)/60.0,0) age_min,
       ROUND(max_profit_pct,3) peak_pct_stored,
       entry_price, size_usd, strategy_id
FROM trades
WHERE status='open'
  AND (strftime('%s','now')-entry_ts) > 21600
ORDER BY entry_ts
LIMIT 50;

-- 3. Anomaly 태그 분류 (구조적 결함 유형별 카운트)
SELECT '--- Anomaly Classification ---' as sec;
SELECT 'total_open' label, COUNT(*) n FROM trades WHERE status='open'
UNION ALL SELECT 'age_gt_6h',  COUNT(*) FROM trades WHERE status='open' AND (strftime('%s','now')-entry_ts) > 21600
UNION ALL SELECT 'age_gt_12h', COUNT(*) FROM trades WHERE status='open' AND (strftime('%s','now')-entry_ts) > 43200
UNION ALL SELECT 'age_gt_24h', COUNT(*) FROM trades WHERE status='open' AND (strftime('%s','now')-entry_ts) > 86400
UNION ALL SELECT 'cap_age_gt_180m', COUNT(*) FROM trades WHERE status='open' AND exchange='cap' AND (strftime('%s','now')-entry_ts) > 10800
UNION ALL SELECT 'okx_age_gt_60m',  COUNT(*) FROM trades WHERE status='open' AND exchange='okx' AND (strftime('%s','now')-entry_ts) > 3600
UNION ALL SELECT 'alpaca_age_gt_240m', COUNT(*) FROM trades WHERE status='open' AND exchange='alpaca' AND (strftime('%s','now')-entry_ts) > 14400;

-- 4. Exchange 별 max_profit_pct == 0 비율 (live tick update 누락 증거)
SELECT '--- Live pnl DB update 누락 (max_profit_pct=0 비율) ---' as sec;
SELECT exchange,
       COUNT(*) n_open,
       SUM(CASE WHEN max_profit_pct = 0 THEN 1 ELSE 0 END) zero_peak,
       ROUND(100.0*SUM(CASE WHEN max_profit_pct=0 THEN 1 ELSE 0 END)/COUNT(*),0)||'%' zero_pct
FROM trades
WHERE status='open'
GROUP BY exchange;

-- 5. Suspected HARVEST infinite wait (peak 있는데 age 과다)
SELECT '--- HARVEST 무한 대기 의심 (peak > 0.1% AND age > 360m) ---' as sec;
SELECT ticker, exchange,
       ROUND((strftime('%s','now')-entry_ts)/60.0,0) age_min,
       ROUND(max_profit_pct,3) peak_pct
FROM trades
WHERE status='open'
  AND max_profit_pct > 0.1
  AND (strftime('%s','now')-entry_ts) > 21600
ORDER BY max_profit_pct DESC, entry_ts
LIMIT 30;
