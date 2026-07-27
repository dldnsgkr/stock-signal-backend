CREATE TABLE "user_settings" (
    "id"              SERIAL       NOT NULL,
    "user_id"         INTEGER      NOT NULL,
    "default_market"  VARCHAR(4)   NOT NULL DEFAULT 'US',
    "alert_on_buy"    BOOLEAN      NOT NULL DEFAULT true,
    "alert_on_sell"   BOOLEAN      NOT NULL DEFAULT true,
    "min_alert_score" INTEGER,
    "created_at"      TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"      TIMESTAMP(3) NOT NULL,

    CONSTRAINT "user_settings_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "user_settings_user_id_key" ON "user_settings"("user_id");

ALTER TABLE "user_settings"
    ADD CONSTRAINT "user_settings_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
