-- CreateTable
CREATE TABLE "markets" (
    "id" SERIAL NOT NULL,
    "code" VARCHAR(10) NOT NULL,
    "name" VARCHAR(100) NOT NULL,

    CONSTRAINT "markets_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "stocks" (
    "id" SERIAL NOT NULL,
    "marketId" INTEGER NOT NULL,
    "symbol" VARCHAR(20) NOT NULL,
    "name" VARCHAR(200) NOT NULL,
    "sector" VARCHAR(100),
    "industry" VARCHAR(100),
    "exchange" VARCHAR(50),
    "isActive" BOOLEAN NOT NULL DEFAULT true,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "stocks_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "price_daily" (
    "id" SERIAL NOT NULL,
    "stockId" INTEGER NOT NULL,
    "date" DATE NOT NULL,
    "open" DECIMAL(15,4) NOT NULL,
    "high" DECIMAL(15,4) NOT NULL,
    "low" DECIMAL(15,4) NOT NULL,
    "close" DECIMAL(15,4) NOT NULL,
    "volume" BIGINT NOT NULL,
    "adjClose" DECIMAL(15,4),

    CONSTRAINT "price_daily_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "financial_metrics" (
    "id" SERIAL NOT NULL,
    "stockId" INTEGER NOT NULL,
    "periodType" VARCHAR(20) NOT NULL,
    "periodEnd" DATE NOT NULL,
    "revenue" DECIMAL(20,2),
    "operatingIncome" DECIMAL(20,2),
    "netIncome" DECIMAL(20,2),
    "roe" DECIMAL(10,4),
    "per" DECIMAL(10,4),
    "pbr" DECIMAL(10,4),
    "debtRatio" DECIMAL(10,4),

    CONSTRAINT "financial_metrics_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "news_articles" (
    "id" SERIAL NOT NULL,
    "source" VARCHAR(100) NOT NULL,
    "title" VARCHAR(500) NOT NULL,
    "summary" TEXT,
    "url" VARCHAR(1000) NOT NULL,
    "publishedAt" TIMESTAMP(3) NOT NULL,
    "sentimentScore" DECIMAL(5,4),
    "language" VARCHAR(10) NOT NULL DEFAULT 'en',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "news_articles_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "news_stock_relations" (
    "id" SERIAL NOT NULL,
    "newsArticleId" INTEGER NOT NULL,
    "stockId" INTEGER NOT NULL,
    "relevanceScore" DECIMAL(5,4) NOT NULL,

    CONSTRAINT "news_stock_relations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "macro_indicators" (
    "id" SERIAL NOT NULL,
    "marketCode" VARCHAR(10) NOT NULL,
    "indicatorType" VARCHAR(100) NOT NULL,
    "value" DECIMAL(15,6) NOT NULL,
    "observedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "macro_indicators_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "model_versions" (
    "id" SERIAL NOT NULL,
    "versionName" VARCHAR(100) NOT NULL,
    "strategyType" VARCHAR(100) NOT NULL,
    "configJson" JSONB NOT NULL DEFAULT '{}',
    "deployedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "isActive" BOOLEAN NOT NULL DEFAULT false,

    CONSTRAINT "model_versions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "recommendation_runs" (
    "id" SERIAL NOT NULL,
    "modelVersionId" INTEGER NOT NULL,
    "runType" VARCHAR(20) NOT NULL,
    "marketCode" VARCHAR(10) NOT NULL,
    "executedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "notes" TEXT,

    CONSTRAINT "recommendation_runs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "recommendations" (
    "id" SERIAL NOT NULL,
    "recommendationRunId" INTEGER NOT NULL,
    "stockId" INTEGER NOT NULL,
    "action" VARCHAR(10) NOT NULL,
    "score" DECIMAL(8,4) NOT NULL,
    "confidence" INTEGER NOT NULL,
    "entryPrice" DECIMAL(15,4) NOT NULL,
    "reasonsJson" JSONB NOT NULL DEFAULT '[]',
    "featureSnapshotJson" JSONB NOT NULL DEFAULT '{}',
    "scoreDetailJson" JSONB NOT NULL DEFAULT '{}',
    "recommendedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "recommendations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "recommendation_results" (
    "id" SERIAL NOT NULL,
    "recommendationId" INTEGER NOT NULL,
    "return1d" DECIMAL(10,6),
    "return7d" DECIMAL(10,6),
    "return30d" DECIMAL(10,6),
    "benchmarkReturn1d" DECIMAL(10,6),
    "benchmarkReturn7d" DECIMAL(10,6),
    "benchmarkReturn30d" DECIMAL(10,6),
    "alpha1d" DECIMAL(10,6),
    "alpha7d" DECIMAL(10,6),
    "alpha30d" DECIMAL(10,6),
    "hit1d" BOOLEAN,
    "hit7d" BOOLEAN,
    "hit30d" BOOLEAN,
    "evaluatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "recommendation_results_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "markets_code_key" ON "markets"("code");

-- CreateIndex
CREATE INDEX "stocks_symbol_idx" ON "stocks"("symbol");

-- CreateIndex
CREATE UNIQUE INDEX "stocks_marketId_symbol_key" ON "stocks"("marketId", "symbol");

-- CreateIndex
CREATE INDEX "price_daily_stockId_date_idx" ON "price_daily"("stockId", "date" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "price_daily_stockId_date_key" ON "price_daily"("stockId", "date");

-- CreateIndex
CREATE UNIQUE INDEX "financial_metrics_stockId_periodType_periodEnd_key" ON "financial_metrics"("stockId", "periodType", "periodEnd");

-- CreateIndex
CREATE UNIQUE INDEX "news_articles_url_key" ON "news_articles"("url");

-- CreateIndex
CREATE INDEX "news_articles_publishedAt_idx" ON "news_articles"("publishedAt" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "news_stock_relations_newsArticleId_stockId_key" ON "news_stock_relations"("newsArticleId", "stockId");

-- CreateIndex
CREATE INDEX "macro_indicators_marketCode_indicatorType_observedAt_idx" ON "macro_indicators"("marketCode", "indicatorType", "observedAt" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "macro_indicators_marketCode_indicatorType_observedAt_key" ON "macro_indicators"("marketCode", "indicatorType", "observedAt");

-- CreateIndex
CREATE UNIQUE INDEX "model_versions_versionName_key" ON "model_versions"("versionName");

-- CreateIndex
CREATE INDEX "recommendation_runs_executedAt_idx" ON "recommendation_runs"("executedAt" DESC);

-- CreateIndex
CREATE INDEX "recommendations_recommendedAt_idx" ON "recommendations"("recommendedAt" DESC);

-- CreateIndex
CREATE INDEX "recommendations_stockId_recommendedAt_idx" ON "recommendations"("stockId", "recommendedAt" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "recommendation_results_recommendationId_key" ON "recommendation_results"("recommendationId");

-- AddForeignKey
ALTER TABLE "stocks" ADD CONSTRAINT "stocks_marketId_fkey" FOREIGN KEY ("marketId") REFERENCES "markets"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "price_daily" ADD CONSTRAINT "price_daily_stockId_fkey" FOREIGN KEY ("stockId") REFERENCES "stocks"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "financial_metrics" ADD CONSTRAINT "financial_metrics_stockId_fkey" FOREIGN KEY ("stockId") REFERENCES "stocks"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "news_stock_relations" ADD CONSTRAINT "news_stock_relations_newsArticleId_fkey" FOREIGN KEY ("newsArticleId") REFERENCES "news_articles"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "news_stock_relations" ADD CONSTRAINT "news_stock_relations_stockId_fkey" FOREIGN KEY ("stockId") REFERENCES "stocks"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recommendation_runs" ADD CONSTRAINT "recommendation_runs_modelVersionId_fkey" FOREIGN KEY ("modelVersionId") REFERENCES "model_versions"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recommendations" ADD CONSTRAINT "recommendations_recommendationRunId_fkey" FOREIGN KEY ("recommendationRunId") REFERENCES "recommendation_runs"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recommendations" ADD CONSTRAINT "recommendations_stockId_fkey" FOREIGN KEY ("stockId") REFERENCES "stocks"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recommendation_results" ADD CONSTRAINT "recommendation_results_recommendationId_fkey" FOREIGN KEY ("recommendationId") REFERENCES "recommendations"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
