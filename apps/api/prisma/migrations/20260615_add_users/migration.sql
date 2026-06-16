CREATE TABLE "users" (
  "id" SERIAL NOT NULL,
  "email" VARCHAR(200) NOT NULL,
  "name" VARCHAR(200),
  "google_id" VARCHAR(100) NOT NULL,
  "avatar_url" VARCHAR(500),
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "users_email_key" ON "users"("email");
CREATE UNIQUE INDEX "users_google_id_key" ON "users"("google_id");
