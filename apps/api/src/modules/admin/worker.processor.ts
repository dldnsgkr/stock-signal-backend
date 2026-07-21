import { Processor, Process, InjectQueue } from '@nestjs/bull';
import { Logger } from '@nestjs/common';
import { Job, Queue } from 'bull';
import { ConfigService } from '@nestjs/config';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../../prisma/prisma.service';
import axios from 'axios';
import { throwForRetryPolicy } from '../../common/job-errors';
import { EmailService, SellSignalPayload } from '../alert/email.service';
import { SubscriptionService } from '../subscriptions/subscription.service';

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
    private readonly email: EmailService,
    private readonly subscriptions: SubscriptionService,
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

      // BUY 시그널 구독자에게 이메일 발송 (비동기, 실패해도 job은 성공)
      const buyRecs = recommendations.filter((r: any) => r.action === 'BUY');
      this.sendAlertEmails(market, buyRecs).catch(e =>
        this.logger.error(`Alert email dispatch error: ${e}`),
      );

      return { runId: run.id, count: recommendations.length };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.error(`Recommendation generation failed: ${msg}`);
      throw err;
    }
  }

  private async sendAlertEmails(market: string, buyRecs: any[]) {
    for (const rec of buyRecs) {
      try {
        const subscribers = await this.subscriptions.getActiveSubscribers(rec.stockId);
        if (subscribers.length === 0) continue;

        const stock = await this.prisma.stock.findUnique({
          where: { id: rec.stockId },
          select: { symbol: true, name: true },
        });
        if (!stock) continue;

        const reasons: string[] = Array.isArray(rec.reasons) ? rec.reasons : [];
        for (const email of subscribers) {
          await this.email.sendBuySignalAlert(email, {
            symbol: stock.symbol,
            name: stock.name,
            market,
            score: Number(rec.score),
            confidence: Number(rec.confidence),
            entryPrice: Number(rec.entryPrice),
            reasons,
          });
        }
        if (subscribers.length > 0) {
          this.logger.log(`Sent BUY alert for ${stock.symbol} to ${subscribers.length} subscriber(s)`);
        }
      } catch (e) {
        this.logger.error(`Failed to send alert for stockId=${rec.stockId}: ${e}`);
      }
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

@Processor('check-sell-signals')
export class SellSignalProcessor {
  private readonly logger = new Logger(SellSignalProcessor.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
    private readonly email: EmailService,
    private readonly subscriptions: SubscriptionService,
  ) {}

  @Process('check')
  async handleCheckSellSignals(job: Job<{ market: string }>) {
    const { market } = job.data;
    this.logger.log(`Checking SELL signals for ${market}...`);

    const ninetyDaysAgo = new Date(Date.now() - 90 * 86400000);

    const openBuys = await this.prisma.recommendation.findMany({
      where: {
        action: 'BUY',
        recommendedAt: { gte: ninetyDaysAgo },
        sellSignal: { is: null },
        stock: { market: { code: market } },
      },
      select: { id: true, stockId: true, score: true, entryPrice: true },
    });

    if (openBuys.length === 0) {
      this.logger.log(`No open BUY recommendations for ${market}`);
      return { checked: 0, generated: 0 };
    }

    this.logger.log(`Checking ${openBuys.length} open BUY recs for ${market}`);

    const analysisUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    let responseData: any;
    try {
      responseData = await callAnalysis(`${analysisUrl}/analysis/generate-sell-signals`, {
        market,
        buy_recommendations: openBuys.map(r => ({
          id: r.id,
          stock_id: r.stockId,
          buy_score: Number(r.score),
        })),
      });
    } catch (err) {
      throwForRetryPolicy(err, `check-sell-signals/${market}`);
    }

    const sellSignals: any[] = responseData?.sell_signals ?? [];
    if (sellSignals.length === 0) {
      this.logger.log(`No SELL signals generated for ${market}`);
      return { checked: openBuys.length, generated: 0 };
    }

    const entryPriceMap = new Map(openBuys.map(r => [r.id, r.entryPrice]));

    await this.prisma.sellSignal.createMany({
      data: sellSignals.map((s: any) => ({
        buyRecommendationId: s.buy_recommendation_id,
        stockId: s.stock_id,
        currentScore: s.current_score,
        entryPrice: entryPriceMap.get(s.buy_recommendation_id) ?? s.exit_price,
        exitPrice: s.exit_price ?? null,
        reasons: s.reasons ?? [],
      })),
      skipDuplicates: true,
    });

    this.logger.log(`Generated ${sellSignals.length} SELL signals for ${market}`);

    // SELL 시그널 구독자에게 이메일 발송 (비동기, job 실패에 영향 없음)
    this.sendSellAlertEmails(market, sellSignals, entryPriceMap).catch(e =>
      this.logger.error(`SELL alert email dispatch error: ${e}`),
    );

    return { checked: openBuys.length, generated: sellSignals.length };
  }

  private async sendSellAlertEmails(market: string, sellSignals: any[], entryPriceMap: Map<number, any>) {
    for (const s of sellSignals) {
      try {
        const subscribers = await this.subscriptions.getActiveSubscribers(s.stock_id);
        if (subscribers.length === 0) continue;

        const stock = await this.prisma.stock.findUnique({
          where: { id: s.stock_id },
          select: { symbol: true, name: true },
        });
        if (!stock) continue;

        const buyRec = await this.prisma.recommendation.findUnique({
          where: { id: s.buy_recommendation_id },
          select: { score: true },
        });

        const payload: SellSignalPayload = {
          symbol: stock.symbol,
          name: stock.name,
          market,
          buyScore: buyRec ? Number(buyRec.score) : 0,
          currentScore: Number(s.current_score),
          entryPrice: Number(entryPriceMap.get(s.buy_recommendation_id) ?? s.exit_price),
          exitPrice: s.exit_price != null ? Number(s.exit_price) : null,
          reasons: Array.isArray(s.reasons) ? s.reasons : [],
        };

        for (const emailAddr of subscribers) {
          await this.email.sendSellSignalAlert(emailAddr, payload);
        }
        if (subscribers.length > 0) {
          this.logger.log(`Sent SELL alert for ${stock.symbol} to ${subscribers.length} subscriber(s)`);
        }
      } catch (e) {
        this.logger.error(`Failed to send SELL alert for stockId=${s.stock_id}: ${e}`);
      }
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
    @InjectQueue('check-sell-signals') private sellSignalQueue: Queue,
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
    await safeProgress(job, 95);

    // Phase 4: SELL 시그널 체크
    try { await job.update({ market, currentStep: 'sell-check' }); } catch {}
    this.logger.log(`[Pipeline] Phase 4: SELL signal check`);
    const sellJob = await this.sellSignalQueue.add('check', { market }, {
      attempts: 2,
      backoff: { type: 'exponential', delay: 5000 },
    });
    await sellJob.finished();
    await safeProgress(job, 100);

    this.logger.log(`Pipeline completed for ${market}`);
    return { market, done: true };
  }
}

@Processor('evaluate-recommendations')
export class EvaluationProcessor {
  private readonly logger = new Logger(EvaluationProcessor.name);

  constructor(private readonly prisma: PrismaService) {}

  @Process('evaluate')
  async handleEvaluate(_job: Job) {
    this.logger.log('Evaluating recommendation results...');

    const now = new Date();
    const cutoff1d  = new Date(now.getTime() - 1  * 86400000);
    const cutoff7d  = new Date(now.getTime() - 7  * 86400000);
    const cutoff30d = new Date(now.getTime() - 30 * 86400000);

    // (1) result 없는 신규 (1일+), (2) return7d 미집계 (7일+), (3) return30d 미집계 (30일+)
    const dueFilter: Prisma.RecommendationWhereInput = {
      OR: [
        { recommendedAt: { lte: cutoff1d },  result: { is: null } },
        { recommendedAt: { lte: cutoff7d },  result: { is: { return7d: null } } },
        { recommendedAt: { lte: cutoff30d }, result: { is: { return30d: null } } },
      ],
    };

    // 평가가 밀리면 대상이 수만 건이 되어, 한 번에 적재하면 PM2 메모리 상한(1500M)에 걸려 죽는다.
    // id 커서로 끊어 읽는다. stock 관계는 이 루프에서 쓰지 않으므로 include 하지 않는다.
    // 지수는 시장별 수백 행뿐이라 통째로 올려두고 메모리에서 조회한다.
    // (추천 건마다 DB 를 치면 평가 시간이 몇 배로 늘어난다)
    const benchmarks = await this.loadBenchmarkSeries();

    const BATCH_SIZE = 2000;
    let cursorId = 0;
    let scanned = 0;
    let evaluated = 0;

    for (;;) {
      const recs = await this.prisma.recommendation.findMany({
        where: { AND: [dueFilter, { id: { gt: cursorId } }] },
        include: { result: true, run: { select: { marketCode: true } } },
        orderBy: { id: 'asc' },
        take: BATCH_SIZE,
      });

      if (recs.length === 0) break;

      // 갱신되지 않는 행(updates 없음)도 커서가 넘어가므로 무한 루프가 되지 않는다.
      cursorId = recs[recs.length - 1].id;
      scanned += recs.length;

      for (const rec of recs) {
        const existing = rec.result;
        const needs1d  = existing?.return1d  == null;
        const needs7d  = existing?.return7d  == null;
        const needs30d = existing?.return30d == null;

        if (!needs1d && !needs7d && !needs30d) continue;

        // recommendations.entry_price 는 추천 시점의 '원시가' 스냅샷이다.
        // 반면 price_daily 는 auto_adjust=True 로 수집돼 배당·액면병합 때 과거 종가가
        // 소급 재조정된다. 두 기준을 빼면 수익률이 어긋난다 — 1:250 병합 종목에서
        // 24,900% 같은 값이 나와 전체 평균이 -1.3% 에서 +13.5% 로 뒤집혔다.
        // 진입가도 같은 조정 시계열에서 다시 뽑아 기준을 맞춘다.
        const entry = await this.getClosestPrice(rec.stockId, rec.recommendedAt);
        if (entry === null || entry === 0) continue;

        const [price1d, price7d, price30d] = await Promise.all([
          needs1d  ? this.getClosestPrice(rec.stockId, new Date(rec.recommendedAt.getTime() + 86400000))       : Promise.resolve(null),
          needs7d  ? this.getClosestPrice(rec.stockId, new Date(rec.recommendedAt.getTime() + 7  * 86400000)) : Promise.resolve(null),
          needs30d ? this.getClosestPrice(rec.stockId, new Date(rec.recommendedAt.getTime() + 30 * 86400000)) : Promise.resolve(null),
        ]);

        const updates: Record<string, number | boolean | null> = {};
        if (price1d  !== null) { const r = (price1d  - entry) / entry; updates.return1d  = r; updates.hit1d  = r > 0; }
        if (price7d  !== null) { const r = (price7d  - entry) / entry; updates.return7d  = r; updates.hit7d  = r > 0; }
        if (price30d !== null) { const r = (price30d - entry) / entry; updates.return30d = r; updates.hit30d = r > 0; }

        if (Object.keys(updates).length === 0) continue;

        // 같은 기간 지수 수익률과 초과수익(alpha). 지수 값이 없으면 해당 구간만 건너뛴다.
        const series    = benchmarks.get(rec.run.marketCode);
        const benchBase = this.benchmarkAt(series, rec.recommendedAt);
        if (benchBase !== null && benchBase !== 0) {
          const benchReturn = (days: number): number | null => {
            const b = this.benchmarkAt(
              series, new Date(rec.recommendedAt.getTime() + days * 86400000),
            );
            return b === null ? null : (b - benchBase) / benchBase;
          };
          const setAlpha = (days: number, retKey: string, benchKey: string, alphaKey: string) => {
            if (updates[retKey] === undefined) return;
            const br = benchReturn(days);
            if (br === null) return;
            updates[benchKey] = br;
            updates[alphaKey] = (updates[retKey] as number) - br;
          };
          setAlpha(1,  'return1d',  'benchmarkReturn1d',  'alpha1d');
          setAlpha(7,  'return7d',  'benchmarkReturn7d',  'alpha7d');
          setAlpha(30, 'return30d', 'benchmarkReturn30d', 'alpha30d');
        }

        await this.prisma.recommendationResult.upsert({
          where:  { recommendationId: rec.id },
          create: { recommendationId: rec.id, ...updates },
          update: updates,
        });
        evaluated++;
      }

      this.logger.log(`Evaluation progress: evaluated=${evaluated} scanned=${scanned} cursor=${cursorId}`);
    }

    this.logger.log(`Evaluated ${evaluated} recommendations (scanned ${scanned})`);
    return { evaluated, scanned };
  }

  // 시장별 벤치마크 지수. macro_indicators.indicator_type 값과 일치해야 한다.
  private static readonly BENCHMARK_BY_MARKET: Record<string, string> = {
    US: 'SP500',
    KR: 'KOSPI',
  };

  private async loadBenchmarkSeries(): Promise<Map<string, { t: number; v: number }[]>> {
    const rows = await this.prisma.macroIndicator.findMany({
      where: {
        indicatorType: { in: Object.values(EvaluationProcessor.BENCHMARK_BY_MARKET) },
      },
      select: { marketCode: true, indicatorType: true, observedAt: true, value: true },
      orderBy: { observedAt: 'asc' },
    });

    const series = new Map<string, { t: number; v: number }[]>();
    for (const r of rows) {
      // KR 시장에 SP500 이 섞여 들어오는 경우를 배제한다.
      if (EvaluationProcessor.BENCHMARK_BY_MARKET[r.marketCode] !== r.indicatorType) continue;
      const list = series.get(r.marketCode) ?? [];
      list.push({ t: r.observedAt.getTime(), v: Number(r.value) });
      series.set(r.marketCode, list);
    }

    for (const [market, list] of series) {
      this.logger.log(`Benchmark ${market}: ${list.length} observations`);
    }
    return series;
  }

  // 목표일 이하 최신 지수값. 너무 오래된 값이면 쓰지 않는다(가격과 동일한 기준).
  private benchmarkAt(
    series: { t: number; v: number }[] | undefined,
    target: Date,
  ): number | null {
    if (!series?.length) return null;
    const tt = target.getTime();
    if (tt > Date.now()) return null;
    const floor = tt - EvaluationProcessor.PRICE_MAX_STALE_DAYS * 86400000;
    for (let i = series.length - 1; i >= 0; i--) {
      if (series[i].t <= tt) return series[i].t >= floor ? series[i].v : null;
    }
    return null;
  }

  // 목표일 직전 가격이 이보다 오래되면(상장폐지·수집중단) 평가하지 않는다.
  // 가장 가까운 가격을 무조건 쓰면 entry_price 와 같은 값이 잡혀 수익률이 0으로 왜곡된다.
  // 장기 연휴를 감안해 7일로 둔다.
  private static readonly PRICE_MAX_STALE_DAYS = 7;

  private async getClosestPrice(stockId: number, targetDate: Date): Promise<number | null> {
    if (targetDate > new Date()) return null; // 아직 해당 날짜가 오지 않음
    const oldest = new Date(
      targetDate.getTime() - EvaluationProcessor.PRICE_MAX_STALE_DAYS * 86400000,
    );
    const price = await this.prisma.priceDaily.findFirst({
      where: { stockId, date: { lte: targetDate, gte: oldest } },
      orderBy: { date: 'desc' },
    });
    return price ? Number(price.close) : null;
  }
}
