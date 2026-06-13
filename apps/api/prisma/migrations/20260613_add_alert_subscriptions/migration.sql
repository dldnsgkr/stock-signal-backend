CREATE TABLE "alert_subscriptions" (
    "id"         SERIAL       NOT NULL,
    "email"      VARCHAR(200) NOT NULL,
    "stock_id"   INTEGER      NOT NULL,
    "is_active"  BOOLEAN      NOT NULL DEFAULT true,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "alert_subscriptions_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "alert_subscriptions_email_stock_id_key" ON "alert_subscriptions"("email", "stock_id");
CREATE INDEX "alert_subscriptions_stock_id_idx" ON "alert_subscriptions"("stock_id");

ALTER TABLE "alert_subscriptions"
    ADD CONSTRAINT "alert_subscriptions_stock_id_fkey"
    FOREIGN KEY ("stock_id") REFERENCES "stocks"("id") ON DELETE CASCADE ON UPDATE CASCADE;
