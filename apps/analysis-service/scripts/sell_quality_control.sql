-- SELL 시그널 품질 — **거래대금 5분위 대조 + 기간 반분** (2026-09-01 재검토용)
--
-- 배경 (2026-08-10 1차 조사)
-- --------------------------
-- SELL 은 사용자에게 푸시로 나가는 신호인데 그때까지 한 번도 검증한 적이 없었다.
-- 1차 조사에서는 거래대금 5분위 대조에서 **대형주는 맞고 소형주는 역효과**로 갈렸다
-- (SELL 후 7일 알파 — 좋은 SELL 이면 음수여야 한다:
--  Q1 KR +1.07 / US +1.18, Q5 KR -1.50 / US -0.67). 호가 노이즈·평균회귀 추정.
-- 다만 표본이 07-22~08-07 3주뿐이라 3단계(기간 분리)를 못 돌렸다 — 이 스크립트가 그걸 한다.
-- 성립하면 소형주 SELL 억제가 후보.
--
-- 방법
-- ----
-- - 재채점이 아니다. 저장된 sell_signals 를 **측정만** 한다 (스코어러 구간 경계와 무관).
-- - sell_signals 는 미결제 BUY 추천 건마다 한 행이라 같은 종목·같은 날 중복이 많다
--   (한 종목의 열린 BUY 가 수십 건). **(stock, 날짜) 로 dedupe** 해야 보유 건수가 많은
--   종목이 과대 가중되지 않는다.
-- - **백로그 덤프 날은 기본 제외한다.** SELL 생성이 시작된 첫날(KR 07-22 868종목,
--   US 07-23 1,775종목)은 90일치 미결제 BUY 를 한 번에 훑은 것이라, 조건이 걸린 지
--   오래된 묵은 시그널이 대부분이고 한 날짜·한 국면이 표본 절반을 차지한다.
--   포함해서 보려면 -v include_backlog=1.
-- - 수익률: 진입 = SELL 날짜 이전 마지막 종가, 청산 = SELL+7일 이전 마지막 종가.
--   미성숙(생성 8일 미만) 제외, |수익률| > 100% 는 price_daily 조정시점 혼재 이상치로 컷.
-- - 알파: CLAUDE.md 원칙대로 지수가 아니라 **동일가중 유니버스**(EW_INDEX) 기준.
--   같은 진입·청산 날짜의 지수 구간 수익률을 뺀다.
-- - 분위는 날짜별 횡단면(그날 SELL 종목들 사이)으로 나눈다.
--
-- 결과 (2026-08-27 실행, 라이브 시그널 07-23~08-19, KR n=1,202 / US n=2,193 종목일)
-- ------------------------------------------------------------------------
-- **1차 조사의 소형주 패턴은 백로그 덤프가 만든 아티팩트였다.** 백로그 포함으로 돌리면
-- KR Q1 +0.51 / Q5 -3.41 로 기록이 재현되지만, 덤프 날 하나를 빼면 같은 구간이
-- Q1 -2.28 / Q5 +1.34 로 뒤집힌다(2단계 탈락). 라이브만으로 기간을 갈라도(3단계)
-- KR·US 모두 전·후반에서 Q1/Q5 부호가 뒤집힌다 — **소형주 SELL 억제는 기각.**
-- 전체 SELL 품질: KR 알파 -0.40%(약하게 옳음) / US +0.47%(약하게 틀림), 둘 다 0 근처.
--
-- 사용법 (로컬에서 ssh 파이프로 — EC2 에 파일 안 남김)
--   ssh stock-signal 'export $(grep "^DATABASE_URL" ~/stock-signal-backend/apps/api/.env | sed "s/\"//g");
--     psql "$DATABASE_URL" -v mkt="'"'"'KR'"'"'"' -f - < scripts/sell_quality_control.sql
--   (기본값 KR. -v from_d="'2026-07-22'" -v to_d="'2026-08-07'" 로 1차 조사 구간 재현 가능)

\if :{?mkt}
\else
  \set mkt '''KR'''
\endif
\if :{?from_d}
\else
  \set from_d '''2026-07-22'''
\endif
\if :{?to_d}
\else
  \set to_d '''2099-01-01'''
\endif
\if :{?include_backlog}
\else
  \set include_backlog 0
\endif

\echo '=== 대상 ==='
SELECT :mkt AS 시장, :from_d AS 시작, :to_d AS 끝;

-- 종목·날짜로 dedupe 한 SELL 관측치 + 진입/청산가 + EW 지수 + 거래대금
CREATE TEMP TABLE sell_obs AS
WITH dedup AS (
  SELECT DISTINCT ss.stock_id, ss.generated_at::date AS d
  FROM sell_signals ss
  JOIN stocks st ON st.id = ss.stock_id
  JOIN markets mk ON mk.id = st.market_id
  WHERE mk.code = :mkt
    AND ss.generated_at::date >= (:from_d)::date
    AND ss.generated_at::date <= (:to_d)::date
    AND ss.generated_at::date + 8 <= current_date      -- 7일 창 성숙분만
    -- 백로그 덤프 날 제외 (90일치를 하루에 훑은 묵은 시그널)
    AND (:include_backlog = 1 OR NOT (
          (mk.code = 'KR' AND ss.generated_at::date = DATE '2026-07-22') OR
          (mk.code = 'US' AND ss.generated_at::date = DATE '2026-07-23')))
), px AS (
  SELECT dedup.*,
         e.date  AS entry_date, e.close AS entry_close,
         e.close * e.volume AS tv,
         x.date  AS exit_date,  x.close AS exit_close
  FROM dedup
  JOIN LATERAL (
    SELECT date, close, volume FROM price_daily p
    WHERE p.stock_id = dedup.stock_id AND p.date <= dedup.d
    ORDER BY p.date DESC LIMIT 1
  ) e ON e.close > 0
  JOIN LATERAL (
    SELECT date, close FROM price_daily p
    WHERE p.stock_id = dedup.stock_id AND p.date <= dedup.d + 7
    ORDER BY p.date DESC LIMIT 1
  ) x ON x.close > 0 AND x.date > e.date
)
SELECT px.*,
       (exit_close / entry_close - 1)               AS ret,
       (exit_close / entry_close - 1)
         - (bx.value / be.value - 1)                AS alp
FROM px
JOIN LATERAL (
  SELECT value FROM macro_indicators
  WHERE indicator_type = 'EW_INDEX' AND market_code = :mkt
    AND observed_at::date <= px.entry_date
  ORDER BY observed_at DESC LIMIT 1
) be ON be.value > 0
JOIN LATERAL (
  SELECT value FROM macro_indicators
  WHERE indicator_type = 'EW_INDEX' AND market_code = :mkt
    AND observed_at::date <= px.exit_date
  ORDER BY observed_at DESC LIMIT 1
) bx ON bx.value > 0;

\echo ''
\echo '=== 표본 (이상치는 조용히 버리지 않는다 — v3.15.2 원칙) ==='
SELECT count(*)                                    AS "종목일_전체",
       count(*) FILTER (WHERE abs(ret) > 1.0)      AS "이상치_제외",
       count(*) FILTER (WHERE abs(ret) <= 1.0)     AS n,
       min(d)                                      AS 시작,
       max(d)                                      AS 끝
FROM sell_obs;

CREATE TEMP TABLE sell_q AS
SELECT *, ntile(5) OVER (PARTITION BY d ORDER BY tv) AS tvq
FROM sell_obs WHERE abs(ret) <= 1.0;

\echo ''
\echo '=== 1단계: SELL 후 7일 성과 — 좋은 SELL 이면 알파가 음수여야 한다 ==='
SELECT count(*)                                        AS n,
       round(avg(ret) * 100, 3)                        AS "수익률_%",
       round(avg(alp) * 100, 3)                        AS "알파_%",
       round(avg(CASE WHEN alp < 0 THEN 1.0 ELSE 0 END) * 100, 1) AS "알파음수_%"
FROM sell_q;

\echo ''
\echo '=== 2단계: 거래대금 5분위 안에서 (1=최저=소형, 5=최고=대형) ==='
\echo '   1차 조사: Q1 양수(역효과) / Q5 음수(정상). 같은 패턴이 나오는지 본다.'
SELECT tvq AS 거래대금분위,
       count(*)                 AS n,
       round(avg(ret) * 100, 3) AS "수익률_%",
       round(avg(alp) * 100, 3) AS "알파_%",
       round(avg(CASE WHEN alp < 0 THEN 1.0 ELSE 0 END) * 100, 1) AS "알파음수_%"
FROM sell_q GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== 3단계: 기간을 반으로 갈라 분위별 방향이 재현되는가 ==='
\echo '   Q1 양수·Q5 음수가 양쪽 기간에서 다 나와야 "소형주 SELL 억제" 를 검토한다.'
WITH h AS (
  SELECT *, CASE WHEN d <= (SELECT min(d) + (max(d) - min(d)) / 2 FROM sell_q)
                 THEN '1_전반' ELSE '2_후반' END AS half
  FROM sell_q
)
SELECT half AS 기간, min(d) AS 시작, max(d) AS 끝,
       round(avg(alp) * 100, 3)                          AS "전체알파_%",
       round(avg(alp) FILTER (WHERE tvq = 1) * 100, 3)   AS "Q1소형_%",
       round(avg(alp) FILTER (WHERE tvq = 2) * 100, 3)   AS "Q2_%",
       round(avg(alp) FILTER (WHERE tvq = 3) * 100, 3)   AS "Q3_%",
       round(avg(alp) FILTER (WHERE tvq = 4) * 100, 3)   AS "Q4_%",
       round(avg(alp) FILTER (WHERE tvq = 5) * 100, 3)   AS "Q5대형_%",
       count(*) FILTER (WHERE tvq = 1)                   AS n_q1,
       count(*) FILTER (WHERE tvq = 5)                   AS n_q5
FROM h GROUP BY half ORDER BY half;

\echo ''
\echo '판단: 3단계에서 전·후반 모두 Q1 이 양수(억제 후보)이고 Q5 가 음수여야 성립.'
\echo '      한쪽이라도 부호가 갈리면 배포하지 않는다 — 20런쯤 더 쌓고 재측정.'

DROP TABLE sell_obs;
DROP TABLE sell_q;
