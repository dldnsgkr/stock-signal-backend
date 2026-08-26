import { Processor, Process, InjectQueue } from '@nestjs/bull';
import { Logger } from '@nestjs/common';
import { Job, Queue } from 'bull';
import { ConfigService } from '@nestjs/config';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../../prisma/prisma.service';
import axios from 'axios';
import { throwForRetryPolicy } from '../../common/job-errors';
import { EmailService, SellSignalPayload } from '../alert/email.service';
import { PushService } from '../alert/push.service';
import { AlertService } from '../alert/alert.service';
import { SubscriptionService } from '../subscriptions/subscription.service';

// 벤치마크 = 동일가중 유니버스 지수 (macro_indicators.indicator_type, 시장은 market_code 로 구분).
//
// 왜 KOSPI/SP500 지수를 안 쓰는가 — KR 지수 시계열이 구성종목과 앞뒤가 안 맞는다.
// 2026-08-10 실측: KOSPI 일간 +17.91%(07-31) / −10.84%(07-28), 08-03 에는 KR 종목 2,758개
// 동일가중 평균(+1.28%)과 **부호까지 반대**(−5.12%)였다. 지수 하나가 구성종목 평균보다
// 2~3배 출렁이는 건 분산효과상 성립할 수 없다. KODEX 200(069500.KS 일간 최대 24%)·
// EWY(11.8%) 등 대체 티커도 전부 같아서 **티커 교체로는 못 고친다.**
// 이 오염된 지수가 alpha_7d/alpha_30d 에 그대로 들어가 KR 임계값 판단을 뒤집어 놓았었다.
//
// 동일가중이 옳은 기준이기도 하다 — 우리는 종목을 동일가중으로 고르므로, 선택 능력은
// 시총가중 지수가 아니라 **같은 날 고를 수 있었던 종목들의 평균** 대비로 재야 한다.
// US 는 SP500 과 거의 붙으므로(2주 +2.44% vs +4.65%) 구현 검증용 대조로 쓸 수 있다.
const BENCHMARK_INDICATOR = 'EW_INDEX';

// FastAPI 호출 헬퍼 — 단일 시도, 재시도는 Bull backoff에 위임
async function callAnalysis(url: string, data: object, timeoutMs = 600000): Promise<any> {
  const res = await axios.post(url, data, { timeout: timeoutMs });
  return res.data;
}

// 관심종목 타겟 푸시: signals 에 든 종목을 watchlist 에 담은 유저에게 개별 알림.
// 유저 개인 설정(BUY/SELL 토글, 최소 점수) 반영. 유저당 1건으로 묶어 스팸 방지.
// push 미설정/미구독이면 sendToUsers 가 자동 no-op.
async function dispatchWatchlistPush(
  prisma: PrismaService,
  push: PushService,
  logger: Logger,
  signals: { stockId: number; symbol: string; score?: number }[],
  action: 'BUY' | 'SELL',
  market: string,
): Promise<void> {
  const stockIds = [...new Set(signals.map(s => s.stockId))];
  if (stockIds.length === 0) return;
  const infoOf = new Map(signals.map(s => [s.stockId, { symbol: s.symbol, score: s.score }]));

  const watchers = await prisma.watchlistItem.findMany({
    where: { stockId: { in: stockIds } },
    select: { userId: true, stockId: true },
  });
  if (watchers.length === 0) return;

  // 대상 유저들의 개인 설정 일괄 조회 (없으면 기본값: 둘 다 on, 점수 제한 없음)
  const watcherIds = [...new Set(watchers.map(w => w.userId))];
  const settingsRows = await prisma.userSettings.findMany({ where: { userId: { in: watcherIds } } });
  const settingsOf = new Map(settingsRows.map(s => [s.userId, s]));

  // userId -> 알림 대상 심볼 목록 (설정 필터 적용)
  const byUser = new Map<number, string[]>();
  for (const w of watchers) {
    const st = settingsOf.get(w.userId);
    const alertOnBuy = st?.alertOnBuy ?? true;
    const alertOnSell = st?.alertOnSell ?? true;
    const minScore = st?.minAlertScore ?? null;

    if (action === 'BUY' && !alertOnBuy) continue;
    if (action === 'SELL' && !alertOnSell) continue;

    const info = infoOf.get(w.stockId);
    if (!info) continue;
    // BUY 는 최소 점수 필터. SELL 은 토글만.
    if (action === 'BUY' && minScore != null && (info.score ?? 0) < minScore) continue;

    const list = byUser.get(w.userId) ?? [];
    if (!list.includes(info.symbol)) list.push(info.symbol);
    byUser.set(w.userId, list);
  }

  const label = action === 'BUY' ? '매수' : '청산';
  const emoji = action === 'BUY' ? '📈' : '📉';
  let sentUsers = 0;
  for (const [userId, symbols] of byUser) {
    if (symbols.length === 0) continue;
    const head = symbols.slice(0, 3).join(', ');
    const more = symbols.length > 3 ? ` 외 ${symbols.length - 3}` : '';
    const title = `${emoji} 관심종목 ${label} 시그널`;
    const body = `${head}${more}에 ${label} 시그널이 발생했습니다`;
    await push.sendToUsers([userId], { title, body, url: `/watchlist`, tag: `watchlist-${action}-${market}` });
    // 알림 이력 기록 (푸시 구독 여부와 무관하게 남긴다 — 이력 페이지용)
    await prisma.notificationLog.create({
      data: { userId, kind: action, title, body, url: '/watchlist', market },
    }).catch(() => {});
    sentUsers++;
  }
  logger.log(`Watchlist ${action} push: ${sentUsers} user(s) for ${market}`);
}

// 배치 크기는 **HTTP 타임아웃(600초) 대비 여유**로 정한다.
// 300 종목은 정상 속도(0.19초/종목)면 57초지만, yfinance 가 느려지면 그대로 뚫린다 —
// 2026-08-18~25 에 US 가격 수집이 매일 600초 타임아웃으로 죽어 최근일 커버리지가
// 7,489 중 540 종목에 그쳤고, 데이터 계약 게이트가 8일간 파이프라인을 막았다.
// (게이트는 제 역할을 했다. 문제는 수집이 완주하지 못한 것)
// 100 종목이면 최악(1.35초/종목)에도 135초라 4배 여유가 생긴다. 총 호출 수는 늘지만
// 배치당 오버헤드는 작다.
const PRICE_BATCH = 100;
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
    let totalCollected = 0;
    let processed = 0;
    try {
      // ⚠️ offset 을 증가시키지 말 것 (v3.15.4 회귀).
      // 수집기가 '이번 달 미시도' 를 앞에 놓으므로, 한 배치를 처리하면 그 종목들이
      // 뒤로 밀리고 다음 미시도 종목이 앞으로 온다. 여기서 offset 을 올리면 방금
      // 당겨온 종목을 건너뛴다 — US 7,462 중 2,768 종목이 한 달 내내 미시도로 남았다.
      // 대신 pending(이번 달 미시도 수)이 0 이 되면 한 바퀴 돈 것이므로 멈춘다.
      while (processed < cap) {
        await safeProgress(job, Math.round((processed / cap) * 100));
        const data = await callAnalysis(`${analysisUrl}/collect/financials`, { market, offset: 0, limit: FINANCIAL_BATCH });
        totalCollected += data.collected ?? 0;
        this.logger.log(`Financial batch ${processed}/${cap}: ${JSON.stringify(data)}`);
        processed += FINANCIAL_BATCH;
        if ((data.total_in_batch ?? 0) === 0) break;
        // 이번 달 전수를 한 바퀴 돌았다 — 더 돌면 같은 종목을 다시 받는다
        if ((data.pending ?? 0) === 0) {
          this.logger.log(`Financials: 이번 달 전수 완료 (${market})`);
          break;
        }
      }
    } catch (err) {
      throwForRetryPolicy(err, `collect-financials/${market} processed=${processed}`);
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
    private readonly push: PushService,
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

      // 브라우저 푸시 — 데일리 시그널 요약 (비동기, job 실패에 영향 없음)
      if (buyRecs.length > 0) {
        const top = buyRecs[0];
        const bTitle = `📈 ${market} 매수 시그널 ${buyRecs.length}건`;
        // '신뢰도 XX%' 로 쓰지 말 것 — 적중 확률이 아닌데 % 가 확률로 읽힌다.
        // 화면 카드는 2026-08-08 에 '전략 일치도' 로 바꿨는데 알림 본문만 남아 있었다.
        const bBody = `최고 점수: ${top.symbol} ${Number(top.score).toFixed(1)}점 (전략 일치도 ${top.confidence})`;
        const bUrl = `/recommendations?market=${market}&action=BUY`;
        this.push.sendToAll({ title: bTitle, body: bBody, url: bUrl, tag: `buy-signals-${market}` })
          .catch(e => this.logger.error(`BUY push failed: ${e}`));
        this.prisma.notificationLog.create({
          data: { userId: null, kind: 'BROADCAST', title: bTitle, body: bBody, url: bUrl, market },
        }).catch(() => {});

        // 관심종목 타겟 푸시 — 내가 담은 종목에 BUY 가 뜨면 개별 알림
        dispatchWatchlistPush(this.prisma, this.push, this.logger, buyRecs, 'BUY', market)
          .catch(e => this.logger.error(`Watchlist BUY push failed: ${e}`));
      }

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

@Processor('collect-investor-flow')
export class InvestorFlowProcessor {
  private readonly logger = new Logger(InvestorFlowProcessor.name);

  constructor(private readonly config: ConfigService) {}

  @Process('collect')
  async handleCollectInvestorFlow(job: Job<{ market: string; days?: number }>) {
    const { market, days } = job.data;
    if (market !== 'KR') {
      this.logger.log(`Investor flow is KR-only, skipping ${market}`);
      return { market, skipped: true };
    }
    this.logger.log(`Collecting investor flow for ${market}${days ? ` (days=${days})` : ''}...`);
    try {
      const analysisUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
      // 백필(days 큼)은 KRX 호출이 일자당 4건이라 오래 걸린다 — 30분 여유
      const data = await callAnalysis(
        `${analysisUrl}/collect/investor-flow`,
        { market, ...(days ? { days } : {}) },
        1800000,
      );
      this.logger.log(`Investor flow collection done: ${JSON.stringify(data)}`);
      return data;
    } catch (err: unknown) {
      throwForRetryPolicy(err, `collect-investor-flow/${market}`);
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
    private readonly push: PushService,
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

    // 분석 서비스는 종목 단위로 feature 를 계산하므로(중복 캐시), 호출 1건이
    // axios 10분 timeout 안에 끝나도록 유니크 종목 수 기준으로 나눠 보낸다.
    // US 는 유니크 종목이 7천 개라 통짜 호출이 매일 timeout 으로 죽었다.
    const byStock = new Map<number, typeof openBuys>();
    for (const r of openBuys) {
      const list = byStock.get(r.stockId);
      if (list) list.push(r);
      else byStock.set(r.stockId, [r]);
    }
    const stockIds = [...byStock.keys()];
    const CHUNK_STOCKS = 1500;

    const sellSignals: any[] = [];
    try {
      for (let i = 0; i < stockIds.length; i += CHUNK_STOCKS) {
        const chunkRecs = stockIds
          .slice(i, i + CHUNK_STOCKS)
          .flatMap(id => byStock.get(id)!);
        const responseData: any = await callAnalysis(`${analysisUrl}/analysis/generate-sell-signals`, {
          market,
          buy_recommendations: chunkRecs.map(r => ({
            id: r.id,
            stock_id: r.stockId,
            buy_score: Number(r.score),
          })),
        });
        sellSignals.push(...(responseData?.sell_signals ?? []));
        await safeProgress(job, Math.round(Math.min(100, ((i + CHUNK_STOCKS) / stockIds.length) * 100)));
        this.logger.log(`SELL check chunk ${i / CHUNK_STOCKS + 1}/${Math.ceil(stockIds.length / CHUNK_STOCKS)} done (${market})`);
      }
    } catch (err) {
      throwForRetryPolicy(err, `check-sell-signals/${market}`);
    }
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

    // 브라우저 푸시 — SELL 요약
    {
      const sTitle = `📉 ${market} 청산 시그널 ${sellSignals.length}건`;
      const sBody = `미결제 BUY ${openBuys.length}건 중 ${sellSignals.length}건 청산 판정`;
      const sUrl = `/recommendations?market=${market}&action=SELL`;
      this.push.sendToAll({ title: sTitle, body: sBody, url: sUrl, tag: `sell-signals-${market}` })
        .catch(e => this.logger.error(`SELL push failed: ${e}`));
      this.prisma.notificationLog.create({
        data: { userId: null, kind: 'BROADCAST', title: sTitle, body: sBody, url: sUrl, market },
      }).catch(() => {});
    }

    // 관심종목 타겟 푸시 — 심볼 조회 후 전달 (SELL 은 점수 필터 미적용, 토글만)
    (async () => {
      const sellStockIds = [...new Set(sellSignals.map((s: any) => s.stock_id))];
      const stocks = await this.prisma.stock.findMany({
        where: { id: { in: sellStockIds } },
        select: { id: true, symbol: true },
      });
      const signals = stocks.map(st => ({ stockId: st.id, symbol: st.symbol }));
      await dispatchWatchlistPush(this.prisma, this.push, this.logger, signals, 'SELL', market);
    })().catch(e => this.logger.error(`Watchlist SELL push failed: ${e}`));

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
    @InjectQueue('collect-investor-flow') private investorFlowQueue: Queue,
    @InjectQueue('generate-recommendations') private recsQueue: Queue,
    @InjectQueue('check-sell-signals') private sellSignalQueue: Queue,
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
    private readonly alert: AlertService,
  ) {}

  // ── P2-11 데이터 계약 검증 ─────────────────────────────────────────────
  // Phase 2(수집) 직후 · Phase 3(추천 생성) 직전에 검사한다.
  // 오염·결측 데이터로 시그널을 만드는 것보다 파이프라인을 세우는 게 낫다.
  private async validateDataContract(market: string): Promise<string[]> {
    const problems: string[] = [];
    const dayMs = 86400000;

    // 1. 가격 신선도 — 최신 수집일이 4일(주말+휴일 감안) 넘게 오래되면 위반
    type MaxDateRow = { d: Date | null };
    const [priceMax] = await this.prisma.$queryRaw<MaxDateRow[]>`
      SELECT MAX(p.date) AS d FROM price_daily p
      JOIN stocks s ON s.id = p.stock_id
      JOIN markets m ON m.id = s.market_id
      WHERE m.code = ${market}
    `;
    if (!priceMax?.d) {
      problems.push('가격 데이터 없음');
    } else {
      const ageDays = (Date.now() - priceMax.d.getTime()) / dayMs;
      if (ageDays > 4) problems.push(`가격 최신일 ${priceMax.d.toISOString().slice(0, 10)} (${Math.floor(ageDays)}일 경과)`);

      // 2. 커버리지 — 최신일에 가격이 있는 종목이 활성 종목의 절반 미만이면 위반
      type CountRow = { n: bigint };
      const [cov] = await this.prisma.$queryRaw<CountRow[]>`
        SELECT COUNT(DISTINCT p.stock_id) AS n FROM price_daily p
        JOIN stocks s ON s.id = p.stock_id
        JOIN markets m ON m.id = s.market_id
        WHERE m.code = ${market} AND p.date = ${priceMax.d}
      `;
      const active = await this.prisma.stock.count({ where: { market: { code: market }, isActive: true } });
      const covered = Number(cov?.n ?? 0);
      if (active > 0 && covered < Math.max(100, active * 0.5)) {
        problems.push(`가격 커버리지 부족 — 최신일 ${covered}/${active}종목`);
      }

      // 3. 이상 가격 — 최신일에 0 이하 종가가 있으면 위반
      const [bad] = await this.prisma.$queryRaw<CountRow[]>`
        SELECT COUNT(*) AS n FROM price_daily p
        JOIN stocks s ON s.id = p.stock_id
        JOIN markets m ON m.id = s.market_id
        WHERE m.code = ${market} AND p.date = ${priceMax.d} AND p.close <= 0
      `;
      if (Number(bad?.n ?? 0) > 0) problems.push(`0 이하 종가 ${Number(bad!.n)}건`);
    }

    // 4. 벤치마크 지수 — 없으면 평가 시 알파가 전부 비어버린다
    const benchmark = BENCHMARK_INDICATOR;
    const bench = await this.prisma.macroIndicator.findFirst({
      where: { marketCode: market, indicatorType: benchmark },
      orderBy: { observedAt: 'desc' },
      select: { observedAt: true },
    });
    if (!bench) problems.push(`벤치마크(${benchmark}) 지수 없음`);
    else if (Date.now() - bench.observedAt.getTime() > 5 * dayMs) {
      problems.push(`벤치마크(${benchmark}) ${Math.floor((Date.now() - bench.observedAt.getTime()) / dayMs)}일 미갱신`);
    }

    // 5. KR 수급 신선도
    if (market === 'KR') {
      const flow = await this.prisma.investorFlowDaily.findFirst({
        orderBy: { tradeDate: 'desc' },
        select: { tradeDate: true },
      });
      if (flow && Date.now() - flow.tradeDate.getTime() > 5 * dayMs) {
        problems.push(`수급 데이터 ${Math.floor((Date.now() - flow.tradeDate.getTime()) / dayMs)}일 미갱신`);
      }
    }

    return problems;
  }

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
    // 수급(투자자 순매수)은 KR 전용 — KRX 데이터
    const flowJob = market === 'KR'
      ? await this.investorFlowQueue.add('collect', { market }, { attempts: 3, backoff: { type: 'exponential', delay: 10000 } })
      : null;

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
        flowJob ? this.isDone(this.investorFlowQueue, String(flowJob.id)) : Promise.resolve(true),
      ]);
      const pct = 15 + Math.min(64, Math.round((elapsed / MAX_WAIT_MS) * 64));
      await safeProgress(job, pct);
      this.logger.log(`[Pipeline] Phase 2 status: prices=${done[0]} news=${done[1]} financials=${done[2]} macro=${done[3]} flow=${done[4]}`);
      if (done.every(Boolean)) break;
    }
    await safeProgress(job, 80);

    // Phase 2.5: 데이터 계약 검증 — 위반 시 시그널 생성 전에 중단
    try { await job.update({ market, currentStep: 'data-contract' }); } catch {}
    const violations = await this.validateDataContract(market);
    if (violations.length > 0) {
      await this.alert.send({
        type: 'error',
        title: '데이터 계약 위반 — 파이프라인 중단',
        market,
        detail: violations.join(' · '),
      });
      throw new Error(`[데이터 계약 위반] ${violations.join(' · ')}`);
    }
    this.logger.log(`[Pipeline] Data contract OK for ${market}`);

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
    // 벤치마크는 price_daily 파생이므로 읽기 전에 최신 가격으로 다시 만든다.
    await this.rebuildBenchmarkIndex();
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

  // 동일가중 유니버스 지수를 price_daily 에서 다시 만들어 macro_indicators 에 적재한다.
  // 파생값이라 매번 통째로 다시 만든다 — 가격이 소급 조정돼도 지수가 따라 고쳐진다(1.8초).
  //
  // 필터 셋의 의미:
  //   date - pd <= 10  — 장기 거래정지·수집공백 후 재개분. 그 사이 변동이 하루치로 잡힌다
  //   abs(ret) <= 0.5  — 데이터 오류 컷. KR 가격제한은 ±30% 라 정상 봉은 안 잘린다
  //   count(*) >= 50   — 구성종목이 적은 날(휴장 경계 등)은 평균이 못 믿을 값이 된다
  // ret >= -0.5 가 보장되므로 ln(1+ret) 은 정의역을 벗어나지 않는다.
  private async rebuildBenchmarkIndex(): Promise<void> {
    const rows = await this.prisma.$executeRaw`
      INSERT INTO macro_indicators (market_code, indicator_type, value, observed_at)
      WITH px AS (
        SELECT m.code AS mkt, p.date, p.close,
               lag(p.close) OVER w AS pc, lag(p.date) OVER w AS pd
        FROM price_daily p
        JOIN stocks s ON s.id = p.stock_id
        JOIN markets m ON m.id = s.market_id
        WINDOW w AS (PARTITION BY p.stock_id ORDER BY p.date)
      ), r AS (
        SELECT mkt, date, (close - pc) / pc AS ret
        FROM px
        WHERE pc > 0 AND pd IS NOT NULL
          AND date - pd <= 10
          AND abs((close - pc) / pc) <= 0.5
      ), d AS (
        SELECT mkt, date, avg(ret) AS ret
        FROM r GROUP BY 1, 2 HAVING count(*) >= 50
      )
      -- ::varchar 를 빼지 말 것 — INSERT SELECT 목록의 파라미터는 타입 추론이 안 걸릴 수 있다
      -- (v3.12.3 의 make_interval(days => bigint) 과 같은 부류)
      SELECT mkt, ${BENCHMARK_INDICATOR}::varchar,
             round((1000 * exp(sum(ln(1 + ret)) OVER (PARTITION BY mkt ORDER BY date)))::numeric, 6),
             date::timestamp
      FROM d
      ON CONFLICT (market_code, indicator_type, observed_at)
      DO UPDATE SET value = EXCLUDED.value
    `;
    this.logger.log(`Benchmark index rebuilt: ${rows} rows`);
  }

  private async loadBenchmarkSeries(): Promise<Map<string, { t: number; v: number }[]>> {
    const rows = await this.prisma.macroIndicator.findMany({
      where: { indicatorType: BENCHMARK_INDICATOR },
      select: { marketCode: true, observedAt: true, value: true },
      orderBy: { observedAt: 'asc' },
    });

    const series = new Map<string, { t: number; v: number }[]>();
    for (const r of rows) {
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
