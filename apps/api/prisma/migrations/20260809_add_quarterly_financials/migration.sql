-- 분기 손익계산서 (yfinance quarterly_income_stmt).
--
-- financial_metrics 에 period_type='quarterly' 로 섞지 않고 별도 테이블로 둔다.
-- 그쪽은 feature_builder·종목상세·헬스체크·품질검사 5곳이 period_type 필터 없이
-- "가장 최근 period_end" 를 집어가는데, 분기말(6/30)이 연간 스냅샷(6/01)보다
-- 뒤라서 분기 행이 선택되면 roe/per/pbr 이 전부 NULL 이 되어 가치 전략이 죽는다.
--
-- ⚠️ period_end 는 '회계 분기 종료일' 이지 공시일이 아니다. 2026-03-31 분기는
--    5월 중순에나 공시된다. 이 데이터를 쓸 때는 반드시 공시 지연(60일)을 적용할 것.
CREATE TABLE "quarterly_financials" (
    "id"               SERIAL       NOT NULL,
    "stock_id"         INTEGER      NOT NULL,
    "period_end"       DATE         NOT NULL,
    "revenue"          DECIMAL(20,2),
    "operating_income" DECIMAL(20,2),
    "net_income"       DECIMAL(20,2),
    "updated_at"       TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "quarterly_financials_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "quarterly_financials_stock_id_period_end_key"
    ON "quarterly_financials"("stock_id", "period_end");
CREATE INDEX "quarterly_financials_stock_id_period_end_idx"
    ON "quarterly_financials"("stock_id", "period_end" DESC);

ALTER TABLE "quarterly_financials"
    ADD CONSTRAINT "quarterly_financials_stock_id_fkey"
    FOREIGN KEY ("stock_id") REFERENCES "stocks"("id") ON DELETE CASCADE ON UPDATE CASCADE;
