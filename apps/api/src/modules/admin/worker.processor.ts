import { Processor, Process, InjectQueue } from '@nestjs/bull';
import { Logger } from '@nestjs/common';
import { Job, Queue } from 'bull';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../../prisma/prisma.service';
import axios from 'axios';
import { throwForRetryPolicy } from '../../common/job-errors';

// FastAPI 호출 헬퍼 — 단일 시도, 재시도는 Bull backoff에 위임
async function callAnalysis(url: string, data: object, timeoutMs = 600000): Promise<any> {
  const res = await axios.post(url, data, { timeout: timeoutMs });
  return res.data;
}

const PRICE_BATCH = 300;
const NEWS_BATCH = 30;       // 뉴스는 종목당 yfinance HTTP 1건 → 배치 작게
const FINANCIAL_BATCH = 200;

// 1회 파이프라인 실행당 최대 수집 종목 수 (무한 루프 방지)
const NEWS_MAX_STOCKS = 150;        // 뉴스: ~5분 이내 완료
const FINANCIAL_MAX_STOCKS = 2000;  // 재무: ~40분
const PRICE_MAX_STOCKS = Infinity;  // 주가: 빠르므로 전체

async function safeProgress(job: Job, pct: number) {
  try { await job.progress(pct); } catch { /* job key expired, ignore */ }
}

@Processor('collect-prices')
export class WorkerProcessor {
  private readonly logger = new Logger(WorkerProcessor.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
  ) {}

  @Process('collect')
  async handleCollectPrices(job: Job<{ market: string }>) {
    const { market } = job.data;
    this.logger.log(`Collecting prices for ${market} (batched)...`);
    const analysisUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    const total = await this.prisma.stock.count({ where: { market: { code: market }, isActive: true } });
    const cap = Math.min(total, PRICE_MAX_STOCKS);
    this.logger.log(`Total active stocks for ${market}: ${total} (cap: ${cap})`);
    let offset = 0;
    let totalCollected = 0;
    try {
      while (offset < cap) {
        await safeProgress(job, Math.round((offset / cap) * 100));
        const data = await callAnalysis(`${analysisUrl}/collect/prices`, { market, offset, limit: PRICE_BATCH });
        totalCollected += data.collected ?? 0;
        this.logger.log(`Price batch offset=${offset}: ${JSON.stringify(data)}`);
        offset += PRICE_BATCH;
        if ((data.total_in_batch ?? 0) === 0) break;
      }
    } catch (err) {
      throwForRetryPolicy(err, `collect-prices/${market} offset=${offset}`);
    }
    await safeProgress(job, 100);
    this.logger.log(`Price collection done for ${market}: ${totalCollected} rows total`);
    return { market, totalCollected };
  }
}

@Processor('collect-stock-list')
export class StockListProcessor {
  private readonly logger = new Logger(StockListProcessor.name);

  constructor(private readonly config: ConfigService) {}

  @Process('collect')
  async handleCollectStockList(job: Job<{ market: string }>) {
    const { market } = job.data;
    this.logger.log(`Collecting stock list for ${market}...`);
    try {
      const analysisUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
      const data = await callAnalysis(`${analysisUrl}/collect/stock-list`, { market }, 120000);
      this.logger.log(`Stock list sync done: ${JSON.stringify(data)}`);
      return data;
    } catch (err: unknown) {
      throwForRetryPolicy(err, `collect-stock-list/${market}`);
    }
  }
}

@Processor('collect-financials')
export class FinancialProcessor {
  private readonly logger = new Logger(FinancialProcessor.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
  ) {}

  @Process('collect')
  async handleCollectFinancials(job: Job<{ market: string }>) {
    const { market } = job.data;
    this.logger.log(`Collecting financials for ${market} (batched)...`);
    const analysisUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    const total = await this.prisma.stock.count({ where: { market: { code: market }, isActive: true } });
    const cap = Math.min(total, FINANCIAL_MAX_STOCKS);
    this.logger.log(`Financials: ${total} total, cap at ${cap}`);
    let offset = 0;
    let totalCollected = 0;
    try {
      while (offset < cap) {
        await safeProgress(job, Math.round((offset / cap) * 100));
        const data = await callAnalysis(`${analysisUrl}/collect/financials`, { market, offset, limit: FINANCIAL_BATCH });
        totalCollected += data.collected ?? 0;
        this.logger.log(`Financial batch offset=${offset}: ${JSON.stringify(data)}`);
        offset += FINANCIAL_BATCH;
        if ((data.total_in_batch ?? 0) === 0) break;
      }
    } catch (err) {
      throwForRetryPolicy(err, `collect-financials/${market} offset=${offset}`);
    }
    await safeProgress(job, 100);
    this.logger.log(`Financial collection done for ${market}: ${totalCollected} collected`);
    return { market, totalCollected };
  }
}

@Processor('collect-news')
export class NewsProcessor {
  private readonly logger = new Logger(NewsProcessor.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
  ) {}

  @Process('collect')
  async handleCollectNews(job: Job<{ market: string }>) {
    const { market } = job.data;
    this.logger.log(`Collecting news for ${market} (batched)...`);
    const analysisUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    const total = await this.prisma.stock.count({ where: { market: { code: market }, isActive: true } });
    const cap = Math.min(total, NEWS_MAX_STOCKS);
    this.logger.log(`News: ${total} total, cap at ${cap}`);
    let offset = 0;
    let totalCollected = 0;
    try {
      while (offset < cap) {
        await safeProgress(job, Math.round((offset / cap) * 100));
        const data = await callAnalysis(`${analysisUrl}/collect/news`, { market, offset, limit: NEWS_BATCH });
        totalCollected += data.collected ?? 0;
        this.logger.log(`News batch offset=${offset}: ${JSON.stringify(data)}`);
        offset += NEWS_BATCH;
        if ((data.total_in_batch ?? 0) === 0) break;
      }
    } catch (err) {
      throwForRetryPolicy(err, `collect-news/${market} offset=${offset}`);
    }
    await safeProgress(job, 100);
    this.logger.log(`News collection done for ${market}: ${totalCollected} articles`);
    return { market, totalCollected };
  }
}

@Processor('generate-recommendations')
export class RecommendationProcessor {
  private readonly logger = new Logger(RecommendationProcessor.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
  ) {}

  @Process('generate')
  async handleGenerateRecommendations(job: Job<{ market: string }>) {
    const { market } = job.data;
    this.logger.log(`Generating recommendations for ${market}...`);

    try {
      const analysisUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
      let responseData: any;
      try {
        responseData = await callAnalysis(`${analysisUrl}/analysis/generate-signals`, { market });
      } catch (err) {
        throwForRetryPolicy(err, `generate-recommendations/${market}`);
      }
      const { recommendations, modelVersion, runNotes } = responseData;

      let mv = await this.prisma.modelVersion.findUnique({
        where: { versionName: modelVersion },
      });

      if (!mv) {
        mv = await this.prisma.modelVersion.create({
          data: {
            versionName: modelVersion,
            strategyType: 'score_based_v1',
            configJson: {},
            isActive: true,
          },
        });
      }

      // 오늘 같은 마켓 run이 이미 있으면 재사용 (중복 방지)
      const todayStart = new Date();
      todayStart.setHours(0, 0, 0, 0);
      const existingRun = await this.prisma.recommendationRun.findFirst({
        where: {
          marketCode: market,
          modelVersionId: mv.id,
          executedAt: { gte: todayStart },
        },
        orderBy: { executedAt: 'desc' },
      });

      let run;
      if (existingRun) {
        // 기존 run의 추천을 모두 삭제하고 새로 생성
        await this.prisma.recommendation.deleteMany({
          where: { recommendationRunId: existingRun.id },
        });
        run = existingRun;
        this.logger.log(`Reusing existing run #${run.id} for today`);
      } else {
        run = await this.prisma.recommendationRun.create({
          data: {
            modelVersionId: mv.id,
            runType: 'SCHEDULED',
            marketCode: market,
            notes: runNotes || null,
          },
        });
      }

      await this.prisma.recommendation.createMany({
        data: recommendations.map((r: any) => ({
          recommendationRunId: run.id,
          stockId: r.stockId,
          action: r.action,
          score: r.score,
          confidence: r.confidence,
          entryPrice: r.entryPrice,
          reasonsJson: r.reasons,
          featureSnapshotJson: r.featureSnapshot || {},
          scoreDetailJson: r.scoreDetail || {},
        })),
      });

      this.logger.log(`Saved ${recommendations.length} recommendations for run ${run.id}`);
      return { runId: run.id, count: recommendations.length };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.error(`Recommendation generation failed: ${msg}`);
      throw err;
    }
  }
}

@Processor('collect-macro')
export class MacroProcessor {
  private readonly logger = new Logger(MacroProcessor.name);

  constructor(private readonly config: ConfigService) {}

  @Process('collect')
  async handleCollectMacro(job: Job<{ market: string }>) {
    const { market } = job.data;
    this.logger.log(`Collecting macro indicators for ${market}...`);
    try {
      const analysisUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
      const data = await callAnalysis(`${analysisUrl}/collect/macro`, { market });
      this.logger.log(`Macro collection done: ${JSON.stringify(data)}`);
      return data;
    } catch (err: unknown) {
      throwForRetryPolicy(err, `collect-macro/${market}`);
    }
  }
}

@Processor('run-pipeline')
export class PipelineProcessor {
  private readonly logger = new Logger(PipelineProcessor.name);

  constructor(
    @InjectQueue('collect-stock-list') private stockListQueue: Queue,
    @InjectQueue('collect-prices') private pricesQueue: Queue,
    @InjectQueue('collect-news') private newsQueue: Queue,
    @InjectQueue('collect-financials') private financialsQueue: Queue,
    @InjectQueue('collect-macro') private macroQueue: Queue,
    @InjectQueue('generate-recommendations') private recsQueue: Queue,
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
  ) {}

  private async isDone(queue: Queue, jobId: string): Promise<boolean> {
    try {
      const j = await queue.getJob(jobId);
      if (!j) return true; // removed = completed (removeOnComplete)
      return (await j.isCompleted()) || (await j.isFailed());
    } catch {
      return true;
    }
  }

  @Process({ name: 'run', concurrency: 2 })
  async handleRunPipeline(job: Job<{ market: string; currentStep?: string }>) {
    const { market } = job.data;
    this.logger.log(`Pipeline starting for ${market}`);

    // Phase 1: 종목 목록 동기화
    try { await job.update({ market, currentStep: 'stock-list' }); } catch {}
    await safeProgress(job, 0);
    this.logger.log(`[Pipeline] Phase 1: stock list`);
    const slJob = await this.stockListQueue.add('collect', { market }, {
      attempts: 3, timeout: 120000,
      backoff: { type: 'exponential', delay: 5000 },
    });
    await slJob.finished();
    await safeProgress(job, 15);

    // Phase 2: 주가 + 뉴스 + 재무 + 거시 병렬 수집
    try { await job.update({ market, currentStep: 'data-collection' }); } catch {}
    this.logger.log(`[Pipeline] Phase 2: parallel data collection`);
    const [pJob, nJob, fJob, mJob] = await Promise.all([
      this.pricesQueue.add('collect',     { market }, { attempts: 4, backoff: { type: 'exponential', delay: 10000 } }),
      this.newsQueue.add('collect',       { market }, { attempts: 4, backoff: { type: 'exponential', delay: 10000 } }),
      this.financialsQueue.add('collect', { market }, { attempts: 4, backoff: { type: 'exponential', delay: 10000 } }),
      this.macroQueue.add('collect',      { market }, { attempts: 3, backoff: { type: 'exponential', delay: 5000  } }),
    ]);

    // job.finished() 대신 15초 폴링 사용:
    // - 15초마다 event loop가 깨어나 Bull lock 갱신 타이머가 정상 작동
    // - job.finished()는 pubsub 의존이라 lock 만료 시 hang될 수 있음
    const POLL_MS = 15_000;
    const MAX_WAIT_MS = 2 * 60 * 60_000; // 최대 2시간 대기
    const phase2Start = Date.now();
    while (true) {
      await new Promise(r => setTimeout(r, POLL_MS));
      const elapsed = Date.now() - phase2Start;
      if (elapsed > MAX_WAIT_MS) {
        this.logger.warn(`[Pipeline] Phase 2 timeout after ${Math.round(elapsed / 60000)}min — proceeding`);
        break;
      }
      const done = await Promise.all([
        this.isDone(this.pricesQueue, String(pJob.id)),
        this.isDone(this.newsQueue, String(nJob.id)),
        this.isDone(this.financialsQueue, String(fJob.id)),
        this.isDone(this.macroQueue, String(mJob.id)),
      ]);
      const pct = 15 + Math.min(64, Math.round((elapsed / MAX_WAIT_MS) * 64));
      await safeProgress(job, pct);
      this.logger.log(`[Pipeline] Phase 2 status: prices=${done[0]} news=${done[1]} financials=${done[2]} macro=${done[3]}`);
      if (done.every(Boolean)) break;
    }
    await safeProgress(job, 80);

    // Phase 3: 추천 시그널 생성
    try { await job.update({ market, currentStep: 'recommendations' }); } catch {}
    this.logger.log(`[Pipeline] Phase 3: recommendations`);
    const rJob = await this.recsQueue.add('generate', { market }, {
      attempts: 3,
      backoff: { type: 'exponential', delay: 15000 },
    });
    await rJob.finished();
    await safeProgress(job, 100);

    this.logger.log(`Pipeline completed for ${market}`);
    return { market, done: true };
  }
}

@Processor('evaluate-recommendations')
export class EvaluationProcessor {
  private readonly logger = new Logger(EvaluationProcessor.name);

  constructor(
    private readonly prisma: PrismaService,
  ) {}

  @Process('evaluate')
  async handleEvaluate(_job: Job) {
    this.logger.log('Evaluating recommendation results...');

    const cutoff1d = new Date(Date.now() - 1 * 24 * 60 * 60 * 1000);
    const cutoff7d = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const cutoff30d = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);

    const pending1d = await this.prisma.recommendation.findMany({
      where: {
        recommendedAt: { lte: cutoff1d },
        result: { is: null },
      },
      include: { stock: true },
    });

    let evaluated = 0;

    for (const rec of pending1d) {
      const targetDate1d = new Date(rec.recommendedAt.getTime() + 1 * 24 * 60 * 60 * 1000);
      const targetDate7d = new Date(rec.recommendedAt.getTime() + 7 * 24 * 60 * 60 * 1000);
      const targetDate30d = new Date(rec.recommendedAt.getTime() + 30 * 24 * 60 * 60 * 1000);

      const [price1d, price7d, price30d] = await Promise.all([
        this.getClosestPrice(rec.stockId, targetDate1d),
        new Date() > cutoff7d ? this.getClosestPrice(rec.stockId, targetDate7d) : null,
        new Date() > cutoff30d ? this.getClosestPrice(rec.stockId, targetDate30d) : null,
      ]);

      const entryPrice = Number(rec.entryPrice);
      const r1d = price1d ? (Number(price1d) - entryPrice) / entryPrice : null;
      const r7d = price7d ? (Number(price7d) - entryPrice) / entryPrice : null;
      const r30d = price30d ? (Number(price30d) - entryPrice) / entryPrice : null;

      await this.prisma.recommendationResult.upsert({
        where: { recommendationId: rec.id },
        create: {
          recommendationId: rec.id,
          return1d: r1d,
          return7d: r7d,
          return30d: r30d,
          hit1d: r1d !== null ? r1d > 0 : null,
          hit7d: r7d !== null ? r7d > 0 : null,
          hit30d: r30d !== null ? r30d > 0 : null,
        },
        update: {
          return1d: r1d,
          return7d: r7d,
          return30d: r30d,
          hit1d: r1d !== null ? r1d > 0 : null,
          hit7d: r7d !== null ? r7d > 0 : null,
          hit30d: r30d !== null ? r30d > 0 : null,
        },
      });
      evaluated++;
    }

    this.logger.log(`Evaluated ${evaluated} recommendations`);
    return { evaluated };
  }

  private async getClosestPrice(stockId: number, targetDate: Date): Promise<number | null> {
    const price = await this.prisma.priceDaily.findFirst({
      where: {
        stockId,
        date: { lte: targetDate },
      },
      orderBy: { date: 'desc' },
    });
    return price ? Number(price.close) : null;
  }
}
