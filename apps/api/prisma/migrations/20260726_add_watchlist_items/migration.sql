CREATE TABLE "watchlist_items" (
    "id"         SERIAL       NOT NULL,
    "user_id"    INTEGER      NOT NULL,
    "stock_id"   INTEGER      NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "watchlist_items_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "watchlist_items_user_id_stock_id_key" ON "watchlist_items"("user_id", "stock_id");
CREATE INDEX "watchlist_items_user_id_idx" ON "watchlist_items"("user_id");

ALTER TABLE "watchlist_items"
    ADD CONSTRAINT "watchlist_items_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "watchlist_items"
    ADD CONSTRAINT "watchlist_items_stock_id_fkey"
    FOREIGN KEY ("stock_id") REFERENCES "stocks"("id") ON DELETE CASCADE ON UPDATE CASCADE;
