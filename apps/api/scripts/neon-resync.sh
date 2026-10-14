#!/bin/bash
# EC2(원본) → Neon 전체 재동기화 (병행 기간 동안 EC2 가 원본). EC2 에서 실행:
#   ssh stock-signal 'bash -s' < apps/api/scripts/neon-resync.sh
# ⚠️ Neon 의 모든 데이터를 비우고 다시 채운다 — 컷오버(=Neon 이 원본) 이후에는 쓰지 말 것.
#    사용자 테이블(users·구독·관심종목·설정)도 EC2 값으로 덮어쓰므로, 실행 전 양쪽 행수를 비교할 것.
# 2026-10-14 사용 이력: cron 지연으로 시장이 뒤바뀌어 오염된 Neon 런을 EC2 기준으로 복구.
#
# 원래 설명: EC2(원본) → Neon 슬림 복사. 보존 창: 추천/결과/SELL 90일, 가격 120일, 수급 90일, 뉴스 30일.
# 스냅샷(feature_snapshot_json)은 '{}' 로 치환 — 전 이력은 로컬 덤프에 보존돼 있다.
set -euo pipefail
export $(grep "^DATABASE_URL" ~/stock-signal-backend/apps/api/.env | sed 's/"//g')
export $(grep "^NEON_DATABASE_URL" ~/.stock-signal-neon.env)
SRC="$DATABASE_URL"
# 벌크 복사·DDL 은 풀러를 우회해 직결 엔드포인트로 (호스트의 -pooler 제거)
DST="${NEON_DATABASE_URL/-pooler/}"

# ── 전체 비우기 (스키마 유지) ────────────────────────────────────────────
psql "$DST" -q -c "DO \$\$ DECLARE t text; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
    EXECUTE format('TRUNCATE TABLE %I CASCADE', t);
  END LOOP; END \$\$;"
echo "OK truncated"

copy() { # copy <라벨> <SELECT...> <대상테이블(컬럼목록)>
  local label="$1" sel="$2" tgt="$3"
  psql "$SRC" -q -c "\copy ($sel) TO STDOUT" | psql "$DST" -q -c "\copy $tgt FROM STDIN"
  echo "OK $label"
}

# ── 차원/소형 테이블 (전체) ──────────────────────────────────────────────
copy _prisma_migrations "SELECT * FROM _prisma_migrations" "_prisma_migrations"
for t in markets model_versions; do
  copy "$t" "SELECT * FROM $t" "$t"
done
copy stocks "SELECT * FROM stocks" "stocks"
copy recommendation_runs "SELECT * FROM recommendation_runs" "recommendation_runs"
copy users "SELECT * FROM users" "users"
copy macro_indicators "SELECT * FROM macro_indicators" "macro_indicators"
copy financial_metrics "SELECT * FROM financial_metrics" "financial_metrics"
copy quarterly_financials "SELECT * FROM quarterly_financials" "quarterly_financials"

# ── 추천 (90일, 스냅샷 치환) ─────────────────────────────────────────────
copy recommendations \
  "SELECT id, recommendation_run_id, stock_id, action, score, confidence, entry_price,
          reasons_json, '{}'::jsonb, score_detail_json, recommended_at
   FROM recommendations WHERE recommended_at >= now() - interval '90 days'" \
  "recommendations (id, recommendation_run_id, stock_id, action, score, confidence, entry_price, reasons_json, feature_snapshot_json, score_detail_json, recommended_at)"

# ── 결과 (복사된 추천에 딸린 것만) ───────────────────────────────────────
copy recommendation_results \
  "SELECT res.* FROM recommendation_results res
   JOIN recommendations r ON r.id = res.recommendation_id
   WHERE r.recommended_at >= now() - interval '90 days'" \
  "recommendation_results"

# ── SELL (90일 & 부모 BUY 가 복사된 것만) ────────────────────────────────
copy sell_signals \
  "SELECT ss.* FROM sell_signals ss
   JOIN recommendations r ON r.id = ss.buy_recommendation_id
   WHERE ss.generated_at >= now() - interval '90 days'
     AND r.recommended_at >= now() - interval '90 days'" \
  "sell_signals"

# ── 시세/수급 ────────────────────────────────────────────────────────────
copy price_daily \
  "SELECT * FROM price_daily WHERE date >= current_date - 120" "price_daily"
copy investor_flow_daily \
  "SELECT * FROM investor_flow_daily WHERE trade_date >= current_date - 90" "investor_flow_daily"

# ── 뉴스 (30일 + 관계는 부모 있는 것만) ──────────────────────────────────
copy news_articles \
  "SELECT * FROM news_articles WHERE published_at >= now() - interval '30 days'" "news_articles"
copy news_stock_relations \
  "SELECT nr.* FROM news_stock_relations nr JOIN news_articles n ON n.id = nr.news_article_id
   WHERE n.published_at >= now() - interval '30 days'" "news_stock_relations"

# ── 사용자 부속 (전체 — 소형) ────────────────────────────────────────────
for t in push_subscriptions alert_subscriptions watchlist_items user_settings; do
  copy "$t" "SELECT * FROM $t" "$t"
done
copy notification_log "SELECT * FROM notification_log WHERE created_at >= now() - interval '30 days'" "notification_log"

# ── 시퀀스 재설정 ────────────────────────────────────────────────────────
psql "$DST" -q -t -c "
SELECT 'SELECT setval(' || quote_literal(pg_get_serial_sequence(c.table_name, c.column_name)) ||
       ', COALESCE((SELECT MAX(' || quote_ident(c.column_name) || ') FROM ' || quote_ident(c.table_name) || '), 0) + 1, false);'
FROM information_schema.columns c
WHERE c.table_schema='public' AND c.column_default LIKE 'nextval%'" | psql "$DST" -q > /dev/null
echo "OK sequences"

# ── 무료 한도 맞춤 (neon-retention.sql 과 같은 규칙) ─────────────────────
psql "$DST" -q -c "
UPDATE recommendations SET score_detail_json = '{}', reasons_json = '[]'
WHERE action <> 'BUY' AND recommendation_run_id NOT IN (
  SELECT DISTINCT ON (market_code) id FROM recommendation_runs ORDER BY market_code, executed_at DESC);
DELETE FROM investor_flow_daily WHERE trade_date < current_date - 30;"
psql "$DST" -q -c "VACUUM FULL recommendations" -c "VACUUM FULL investor_flow_daily" -c "VACUUM ANALYZE"
echo "OK slimmed"

# ── 검증: 행 수 + 용량 ──────────────────────────────────────────────────
echo "=== Neon 행수/용량 ==="
psql "$DST" -c "
SELECT relname, n_live_tup AS rows, pg_size_pretty(pg_total_relation_size(relid)) AS size
FROM pg_stat_user_tables WHERE n_live_tup > 0 ORDER BY pg_total_relation_size(relid) DESC LIMIT 12;"
psql "$DST" -tA -c "SELECT '전체: ' || pg_size_pretty(pg_database_size(current_database()));"
