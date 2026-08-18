-- 임계값 민감도 — **거래대금 5분위 대조 포함** (2026-09-01 재검토용)
--
-- 왜 사이즈 대조가 필수인가
-- ------------------------
-- 2026-08-10 에 새 벤치마크(동일가중 유니버스)로 재면 KR·US 모두 '임계값을 올릴수록
-- 알파가 단조 감소' 로 나왔다. 그런데 그건 **1단계뿐**이다. 거래대금 5분위 안에서
-- 다시 재보니 KR >=70 의 -0.64% 는 대부분 Q5 쏠림(n=4,214)이었고 분위 내 방향은
-- 일정하지 않았다. 사이즈 대조 없이 임계값을 건드리면 '고점수 = 대형주' 를
-- '고점수가 나쁘다' 로 오독한다.
--
-- 이 쿼리는 재채점을 하지 않는다 — 저장된 점수를 **거르기만** 한다.
-- 따라서 스코어러 교체 구간 문제(07-26 경계)와 무관하고 전 구간을 쓸 수 있다.
--
-- 사용법
--   psql "$DATABASE_URL" -v mkt="'KR'" -v days=90 -f scripts/threshold_size_control.sql
--   (기본값: KR / 90일. US 는 -v mkt="'US'")

\set mkt :mkt
\set days :days
\if :{?mkt}
\else
  \set mkt '''KR'''
\endif
\if :{?days}
\else
  \set days 90
\endif

\echo '=== 대상 ==='
SELECT :mkt AS 시장, :days AS 창일수;

WITH base AS (
  SELECT r.stock_id, rr.executed_at::date d, r.score,
         res.return_7d ret, res.alpha_7d alp
  FROM recommendations r
  JOIN recommendation_runs rr ON rr.id = r.recommendation_run_id
  JOIN recommendation_results res ON res.recommendation_id = r.id
  WHERE rr.market_code = :mkt
    AND rr.executed_at >= NOW() - make_interval(days => (:days)::int)
    AND res.alpha_7d IS NOT NULL
    AND abs(res.return_7d) <= 1.0        -- price_daily 조정시점 혼재 이상치 컷
), px AS (
  SELECT base.*, p.close * p.volume AS tv
  FROM base
  JOIN LATERAL (
    SELECT close, volume FROM price_daily p
    WHERE p.stock_id = base.stock_id AND p.date <= base.d
    ORDER BY p.date DESC LIMIT 1
  ) p ON p.close > 0
), q AS (
  -- 분위는 **날짜별로** 나눈다. 전체 기간을 한꺼번에 나누면 시장 전체가
  -- 오르내린 효과가 분위에 섞인다.
  SELECT *, ntile(5) OVER (PARTITION BY d ORDER BY tv) AS tvq FROM px
)
\echo ''
\echo '=== 1단계: 임계값별 알파 (사이즈 대조 없음 — 이것만 보면 안 된다) ==='
SELECT t.threshold AS 임계값,
       count(*) FILTER (WHERE q.score >= t.threshold) AS n,
       round(avg(q.alp) FILTER (WHERE q.score >= t.threshold) * 100, 3) AS "알파_%",
       round(avg(CASE WHEN q.ret > 0 THEN 1.0 ELSE 0 END)
             FILTER (WHERE q.score >= t.threshold) * 100, 1) AS "적중_%"
FROM q CROSS JOIN (VALUES (50),(55),(60),(65),(70),(75),(80)) AS t(threshold)
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== 2단계: 거래대금 5분위 **안에서** 다시 잰다 (1=최저, 5=최고) ==='
\echo '   분위 안에서도 같은 방향이면 진짜다. 방향이 흩어지면 사이즈 아티팩트다.'
WITH base AS (
  SELECT r.stock_id, rr.executed_at::date d, r.score,
         res.return_7d ret, res.alpha_7d alp
  FROM recommendations r
  JOIN recommendation_runs rr ON rr.id = r.recommendation_run_id
  JOIN recommendation_results res ON res.recommendation_id = r.id
  WHERE rr.market_code = :mkt
    AND rr.executed_at >= NOW() - make_interval(days => (:days)::int)
    AND res.alpha_7d IS NOT NULL AND abs(res.return_7d) <= 1.0
), px AS (
  SELECT base.*, p.close * p.volume AS tv
  FROM base
  JOIN LATERAL (
    SELECT close, volume FROM price_daily p
    WHERE p.stock_id = base.stock_id AND p.date <= base.d
    ORDER BY p.date DESC LIMIT 1
  ) p ON p.close > 0
), q AS (
  SELECT *, ntile(5) OVER (PARTITION BY d ORDER BY tv) AS tvq FROM px
)
SELECT q.tvq AS 거래대금분위,
       round(avg(q.alp) FILTER (WHERE q.score >= 50) * 100, 3) AS ">=50",
       round(avg(q.alp) FILTER (WHERE q.score >= 65) * 100, 3) AS ">=65_현행",
       round(avg(q.alp) FILTER (WHERE q.score >= 70) * 100, 3) AS ">=70",
       round(avg(q.alp) FILTER (WHERE q.score >= 80) * 100, 3) AS ">=80",
       count(*) FILTER (WHERE q.score >= 65) AS "n_65",
       count(*) FILTER (WHERE q.score >= 70) AS "n_70"
FROM q GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== 3단계: 기간을 반으로 갈라 같은 방향인가 ==='
WITH base AS (
  SELECT rr.executed_at::date d, r.score, res.return_7d ret, res.alpha_7d alp
  FROM recommendations r
  JOIN recommendation_runs rr ON rr.id = r.recommendation_run_id
  JOIN recommendation_results res ON res.recommendation_id = r.id
  WHERE rr.market_code = :mkt
    AND rr.executed_at >= NOW() - make_interval(days => (:days)::int)
    AND res.alpha_7d IS NOT NULL AND abs(res.return_7d) <= 1.0
), h AS (
  SELECT *, CASE WHEN d <= (SELECT min(d) + (max(d) - min(d)) / 2 FROM base)
                 THEN '전반' ELSE '후반' END AS half
  FROM base
)
SELECT half AS 기간,
       min(d) AS 시작, max(d) AS 끝,
       round(avg(alp) FILTER (WHERE score >= 65) * 100, 3) AS ">=65_현행",
       round(avg(alp) FILTER (WHERE score >= 70) * 100, 3) AS ">=70",
       round((avg(alp) FILTER (WHERE score >= 70)
            - avg(alp) FILTER (WHERE score >= 65)) * 100, 3) AS "Δ(70-65)"
FROM h GROUP BY half ORDER BY half;

\echo ''
\echo '판단: 2단계에서 분위 내 방향이 일정하고, 3단계에서 전·후반 Δ 부호가 같아야 채택한다.'
