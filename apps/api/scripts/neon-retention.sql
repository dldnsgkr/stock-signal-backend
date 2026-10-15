-- Neon 무료 한도(512MB) 유지용 보존 창 정리 — evaluate 워크플로 말미에 매일 실행.
-- 창을 바꾸면 serverless-migration-plan.md 의 보존 창 표도 같이 고칠 것.
--
-- 보존 창 (2026-10-12 확정):
--   추천·결과·SELL 90일 / 가격 120일 / 수급 30일 / 뉴스 30일 / 알림로그 30일
--   score_detail_json·reasons_json 은 BUY 전체 + (시장별) 최신 런만 원문 유지
--   피처 스냅샷 전 이력은 아카이브(덤프)에 보존 — DB 에는 처음부터 '{}' 로 적재됨

BEGIN;

-- 자식 먼저 (FK RESTRICT)
DELETE FROM recommendation_results res USING recommendations r
  WHERE r.id = res.recommendation_id AND r.recommended_at < now() - interval '90 days';
DELETE FROM sell_signals ss USING recommendations r
  WHERE r.id = ss.buy_recommendation_id AND r.recommended_at < now() - interval '90 days';
DELETE FROM recommendations WHERE recommended_at < now() - interval '90 days';

-- 비 BUY 과거분의 JSON 원문 제거 (최신 런은 비교·상세 화면용으로 유지)
UPDATE recommendations SET score_detail_json = '{}', reasons_json = '[]'
WHERE action <> 'BUY'
  AND (score_detail_json <> '{}' OR reasons_json <> '[]')
  AND recommendation_run_id NOT IN (
    SELECT DISTINCT ON (market_code) id FROM recommendation_runs
    ORDER BY market_code, executed_at DESC);

DELETE FROM price_daily WHERE date < current_date - 120;
DELETE FROM investor_flow_daily WHERE trade_date < current_date - 30;

DELETE FROM news_stock_relations nr USING news_articles n
  WHERE n.id = nr.news_article_id AND n.published_at < now() - interval '30 days';
DELETE FROM news_articles WHERE published_at < now() - interval '30 days';

DELETE FROM notification_log WHERE created_at < now() - interval '30 days';

COMMIT;

-- ⚠️ 일반 VACUUM 은 지운 공간을 '재사용 가능' 으로만 표시하고 파일은 줄이지 않는다 —
-- 그래서 pg_database_size 가 계속 불어 2026-10-15 에 517MB 로 무료 한도(512MB)를 넘었다.
-- 큰 테이블은 매일 VACUUM FULL 로 재작성한다(합계 1~2분, 그동안 해당 테이블 쓰기는 대기).
-- price_daily 는 인덱스가 데이터보다 커져 있었다(77MB vs 75MB) — 재작성으로 인덱스도 재생성된다.
VACUUM FULL ANALYZE recommendations;
VACUUM FULL ANALYZE price_daily;
VACUUM FULL ANALYZE recommendation_results;
VACUUM FULL ANALYZE sell_signals;
VACUUM (ANALYZE) investor_flow_daily, news_articles;

SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;
