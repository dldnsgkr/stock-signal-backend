CREATE TABLE "investor_flow_daily" (
    "id"             SERIAL       NOT NULL,
    "stock_id"       INTEGER      NOT NULL,
    "trade_date"     DATE         NOT NULL,
    "investor_type"  VARCHAR(20)  NOT NULL,
    "net_buy_value"  BIGINT       NOT NULL,
    "net_buy_volume" BIGINT,
    "created_at"     TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "investor_flow_daily_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "investor_flow_daily_stock_date_investor_key"
    ON "investor_flow_daily"("stock_id", "trade_date", "investor_type");
CREATE INDEX "investor_flow_daily_trade_date_idx" ON "investor_flow_daily"("trade_date");

ALTER TABLE "investor_flow_daily"
    ADD CONSTRAINT "investor_flow_daily_stock_id_fkey"
    FOREIGN KEY ("stock_id") REFERENCES "stocks"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
