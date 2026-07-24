CREATE TABLE "push_subscriptions" (
    "id"         SERIAL       NOT NULL,
    "endpoint"   TEXT         NOT NULL,
    "p256dh"     VARCHAR(255) NOT NULL,
    "auth"       VARCHAR(255) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "push_subscriptions_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "push_subscriptions_endpoint_key" ON "push_subscriptions"("endpoint");
