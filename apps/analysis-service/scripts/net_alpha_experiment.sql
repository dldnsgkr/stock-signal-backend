-- 마지막 실험: 비용 후 알파를 양수로 만드는 선택 규칙이 존재하는가 (2026-10-04)
--
-- 배경
-- ----
-- v3.18.0 비용 후 알파 실측: 어떤 전략도 양수가 아니다(US 전체 +0.15% 알파가
-- 평균 왕복 비용 0.47% 에 잡아먹힘). 남은 가설: **유동성 하한 + 선택 집중**이면
-- 비용(대형주 왕복 ~0.1%)이 알파보다 작아질 수 있다. 08-10 측정에서 유동성
-- 하한 $5M 만으로 US 전체 BUY 알파가 +0.102% → +0.445% 로 4배였다(1단계만).
--
-- 방법
-- ----
-- - 저장 점수 필터링(재채점 아님) — 스코어러 경계 무관, 전 구간 사용 가능.
-- - 비용 모델은 운영 trading-cost.ts 와 동일: KR 명시 0.18% + 편도 슬리피지
--   (거래대금 ≥100억 5bp / ≥10억 15bp / ≥1억 40bp / 미만 100bp) × 2,
--   US 명시 0 + ($10M/5bp, $1M/15bp, $0.1M/40bp, 미만 100bp) × 2.
-- - **2단계 대조 ①**: 알파를 저장된 EW 지수 기준(net_alpha)과, **같은 날 같은
--   유동성 하한을 통과한 전체 채점 종목의 평균 수익률 기준**(net_alpha_dm)으로
--   둘 다 잰다. 하한을 걸면 유니버스 자체가 지수와 달라지므로, dm 이 양수가
--   아니면 "하한 유니버스가 좋았던 것"이지 선택 능력이 아니다.
-- - **2단계 대조 ②**: 슬리피지 2배(비관) 가정의 net 도 함께 — 가정에 기대는
--   결론인지 본다.
-- - **3단계**: 기간 반분(net_h1/net_h2). 부호가 갈리면 기각.
--
-- 결과 (2026-10-04 실행, 90일)
-- ------------------------------------------------------------------------
-- **US 7d: 2단계 탈락.** 하한 $10M+임계값65 가 net +0.31%·전후반 양수·슬리피지 2배에도
-- 양수로 "통과처럼 보였으나", dm(유동성 유니버스 내 초과)이 −0.28% — 대형주 유니버스가
-- EW 지수를 이긴 효과일 뿐, 유니버스 안에서 모델 선택은 평균보다 나빴다.
-- **KR 7d: 1단계부터 전멸.** 12개 조합 전부 net·dm 음수.
-- top-N 집중은 양 시장 모두 임계값보다 나빴다.
--
-- 30일 보유 추가 측정 (rcol=return_30d — 회전율을 낮춰 비용 분모를 바꾸는 마지막 레버)
-- ------------------------------------------------------------------------
-- KR: 더 깊은 음수로 전멸. US: 하한0+임계값65 가 net/dm/h1/h2/net2x 전부 양수로
-- "통과"했으나 — **투자 가능한 대안(SP500 매수 후 보유) 대비로 재면 측정된 4개월
-- 전부 음수다**(전체 BUY −2.35%/월, $10M+top50 −1.85%/월). EW 전종목 지수는 같은
-- 기간 SPY 에 월 −2~3%p 뒤처진, 실제로 살 수 없는 벤치마크였다.
-- **교훈: 벤치마크도 '투자 가능한가'를 물어야 한다. EW 를 이겨도 SPY 에 지면 의미 없다.**
--
-- 사용법 (로컬에서 ssh 파이프)
--   psql "$DATABASE_URL" -v mkt="'US'" -v days=90 -f - < scripts/net_alpha_experiment.sql
--   (KR 은 -v mkt="'KR'")

\if :{?mkt}
\else
  \set mkt '''US'''
\endif
\if :{?days}
\else
  \set days 90
\endif
-- 보유기간: rcol/acol 로 7d(기본) 또는 30d 지표를 고른다.
-- 왕복 비용은 보유기간과 무관하게 1회 — 30d 는 같은 비용을 더 긴 알파로 갚는 구조다.
\if :{?rcol}
\else
  \set rcol return_7d
  \set acol alpha_7d
\endif

\echo '=== 대상 ==='
SELECT :mkt AS 시장, :days AS 창일수;

-- 채점 전 종목(BUY/WATCH/AVOID) + 평가 + 진입일 거래대금 + 비용
CREATE TEMP TABLE obs AS
WITH base AS (
  SELECT r.stock_id, r.score, rr.id AS run_id, rr.executed_at::date AS d,
         res.:"rcol" AS ret, res.:"acol" AS alp
  FROM recommendations r
  JOIN recommendation_runs rr ON rr.id = r.recommendation_run_id
  JOIN recommendation_results res ON res.recommendation_id = r.id
  WHERE rr.market_code = :mkt
    AND rr.executed_at >= now() - make_interval(days => (:days)::int)
    AND res.:"rcol" IS NOT NULL AND res.:"acol" IS NOT NULL
    AND abs(res.:"rcol") <= 1.0
)
SELECT base.*, p.close * p.volume AS tv
FROM base
JOIN LATERAL (
  SELECT close, volume FROM price_daily p
  WHERE p.stock_id = base.stock_id AND p.date <= base.d
  ORDER BY p.date DESC LIMIT 1
) p ON p.close > 0;

CREATE TEMP TABLE obs_cost AS
SELECT *,
  -- 왕복 비용 = 명시 + 편도 슬리피지 × 2 (trading-cost.ts 와 동일)
  (CASE WHEN :mkt = 'KR' THEN 0.0018 ELSE 0.0 END) +
  2 * (CASE
    WHEN :mkt = 'KR' THEN CASE WHEN tv >= 1e10 THEN 0.0005 WHEN tv >= 1e9 THEN 0.0015
                               WHEN tv >= 1e8 THEN 0.0040 ELSE 0.0100 END
    ELSE               CASE WHEN tv >= 1e7 THEN 0.0005 WHEN tv >= 1e6 THEN 0.0015
                               WHEN tv >= 1e5 THEN 0.0040 ELSE 0.0100 END
  END) AS cost
FROM obs;

-- 유동성 하한 × 선택 규칙 그리드.
-- 하한: KR 10억/50억/100억, US $1M/$5M/$10M (+하한 없음)
CREATE TEMP TABLE graded AS
SELECT o.*, f.floor_v,
       ROW_NUMBER() OVER (PARTITION BY o.run_id, f.floor_v
                          ORDER BY o.score DESC, o.stock_id) AS rk,
       AVG(o.ret) OVER (PARTITION BY o.run_id, f.floor_v) AS day_mean_ret
FROM obs_cost o
CROSS JOIN (VALUES (0::numeric), (CASE WHEN :mkt='KR' THEN 1e9  ELSE 1e6 END),
                   (CASE WHEN :mkt='KR' THEN 5e9 ELSE 5e6 END),
                   (CASE WHEN :mkt='KR' THEN 1e10 ELSE 1e7 END)) AS f(floor_v)
WHERE o.tv >= f.floor_v;

\echo ''
\echo '=== 결과: 유동성 하한 × 선택 규칙 (net = EW알파 − 비용, dm = 유니버스내 초과 − 비용) ==='
\echo '    net2x = 슬리피지 2배 가정. h1/h2 = 기간 반분. 전부 양수여야 통과.'
WITH half AS (
  SELECT *, CASE WHEN d <= (SELECT min(d) + (max(d) - min(d)) / 2 FROM graded)
                 THEN 1 ELSE 2 END AS h
  FROM graded
), sel AS (
  SELECT g.*, r.rule
  FROM half g
  CROSS JOIN (VALUES ('1_임계값65'), ('2_top50'), ('3_top20')) AS r(rule)
  WHERE (r.rule = '1_임계값65' AND g.score >= 65)
     OR (r.rule = '2_top50' AND g.rk <= 50)
     OR (r.rule = '3_top20' AND g.rk <= 20)
)
SELECT floor_v AS 하한, rule AS 규칙, count(*) AS n,
  round(avg(alp) * 100, 2)                              AS "알파%",
  round(avg(cost) * 100, 2)                             AS "비용%",
  round(avg(alp - cost) * 100, 2)                       AS "net%",
  round(avg(ret - day_mean_ret - cost) * 100, 2)        AS "dm%",
  round(avg(alp - 2 * cost +
    (CASE WHEN :mkt='KR' THEN 0.0018 ELSE 0 END)) * 100, 2) AS "net2x%",
  round(avg(alp - cost) FILTER (WHERE h = 1) * 100, 2)  AS h1,
  round(avg(alp - cost) FILTER (WHERE h = 2) * 100, 2)  AS h2,
  round(avg(CASE WHEN ret > 0 THEN 1.0 ELSE 0 END) * 100, 1) AS "적중%"
FROM sel
GROUP BY floor_v, rule ORDER BY floor_v, rule;

\echo ''
\echo '판단: net>0 이고 dm>0 이고 h1·h2 둘 다 >0 이어야 "비용 후 양수 선택 규칙 존재".'
\echo '      net2x 까지 양수면 가정에도 강건. dm<0 이면 하한 유니버스 효과일 뿐이다.'

DROP TABLE obs; DROP TABLE obs_cost; DROP TABLE graded;
