-- push_subscriptions: 로그인 유저 연결 (관심종목 타겟 알림용)
ALTER TABLE "push_subscriptions" ADD COLUMN "user_id" INTEGER;
CREATE INDEX "push_subscriptions_user_id_idx" ON "push_subscriptions"("user_id");
ALTER TABLE "push_subscriptions"
  ADD CONSTRAINT "push_subscriptions_user_id_fkey"
  FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- alert_subscriptions: 로그인 유저 연결 (이메일 경로 SMTP 대비)
ALTER TABLE "alert_subscriptions" ADD COLUMN "user_id" INTEGER;
CREATE INDEX "alert_subscriptions_user_id_idx" ON "alert_subscriptions"("user_id");
ALTER TABLE "alert_subscriptions"
  ADD CONSTRAINT "alert_subscriptions_user_id_fkey"
  FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
