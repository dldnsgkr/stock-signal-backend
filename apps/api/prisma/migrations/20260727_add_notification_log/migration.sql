CREATE TABLE "notification_log" (
    "id"         SERIAL       NOT NULL,
    "user_id"    INTEGER,
    "kind"       VARCHAR(20)  NOT NULL,
    "title"      VARCHAR(200) NOT NULL,
    "body"       VARCHAR(500) NOT NULL,
    "url"        VARCHAR(300),
    "market"     VARCHAR(4),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "notification_log_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "notification_log_user_id_created_at_idx" ON "notification_log"("user_id", "created_at");
CREATE INDEX "notification_log_created_at_idx" ON "notification_log"("created_at");

ALTER TABLE "notification_log"
    ADD CONSTRAINT "notification_log_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
