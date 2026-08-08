import { Injectable } from '@nestjs/common';
import { InjectQueue } from '@nestjs/bull';
import { Queue } from 'bull';
import { PrismaService } from '../../prisma/prisma.service';
import axios from 'axios';
import * as fs from 'fs/promises';
import * as path from 'path';
import * as os from 'os';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

@Injectable()
export class AdminService {
  constructor(
    @InjectQueue('collect-stock-list') private stockListQueue: Queue,
    @InjectQueue('collect-prices') private pricesQueue: Queue,
    @InjectQueue('collect-news') private newsQueue: Queue,
    @InjectQueue('collect-financials') private financialsQueue: Queue,
    @InjectQueue('generate-recommendations') private recsQueue: Queue,
    @InjectQueue('evaluate-recommendations') private evalQueue: Queue,
    @InjectQueue('collect-macro') private macroQueue: Queue,
    @InjectQueue('collect-investor-flow') private investorFlowQueue: Queue,
    @InjectQueue('check-sell-signals') private sellSignalQueue: Queue,
    @InjectQueue('run-pipeline') private pipelineQueue: Queue,
    private readonly prisma: PrismaService,
  ) {}

  async triggerCollectStockList(market = 'US') {
    const job = await this.stockListQueue.add('collect', { market }, {
      attempts: 3, timeout: 120000,
      backoff: { type: 'exponential', delay: 5000 },
    });
    return { jobId: job.id, status: 'queued', message: `Stock list sync queued for ${market}` };
  }

  async triggerCollectPrices(market = 'US') {
    const job = await this.pricesQueue.add('collect', { market }, {
      attempts: 4,
      backoff: { type: 'exponential', delay: 10000 },
    });
    return { jobId: job.id, status: 'queued', message: `Price collection queued for ${market}` };
  }

  async triggerCollectNews(market = 'US') {
    const job = await this.newsQueue.add('collect', { market }, {
      attempts: 4,
      backoff: { type: 'exponential', delay: 10000 },
    });
    return { jobId: job.id, status: 'queued', message: `News collection queued for ${market}` };
  }

  async triggerCollectFinancials(market = 'US') {
    const job = await this.financialsQueue.add('collect', { market }, {
      attempts: 4,
      backoff: { type: 'exponential', delay: 10000 },
    });
    return { jobId: job.id, status: 'queued', message: `Financial collection queued for ${market}` };
  }

  async triggerGenerateRecommendations(market = 'US') {
    const job = await this.recsQueue.add('generate', { market }, {
      attempts: 3,
      backoff: { type: 'exponential', delay: 15000 },
    });
    return { jobId: job.id, status: 'queued', message: `Recommendation generation queued for ${market}` };
  }

  async triggerRunPipeline(market = 'US') {
    // 파이프라인은 오케스트레이터 — 재시도 없음, 자식 job이 각자 retry 처리
    const job = await this.pipelineQueue.add('run', { market }, { attempts: 1 });
    return { jobId: job.id, status: 'queued', message: `Full pipeline queued for ${market}` };
  }

  async triggerCollectMacro(market = 'US') {
    const job = await this.macroQueue.add('collect', { market }, {
      attempts: 3,
      backoff: { type: 'exponential', delay: 5000 },
    });
    return { jobId: job.id, status: 'queued', message: `Macro collection queued for ${market}` };
  }

  async triggerCollectInvestorFlow(market = 'KR', days?: number) {
    const job = await this.investorFlowQueue.add('collect', { market, ...(days ? { days } : {}) }, {
      attempts: 3,
      backoff: { type: 'exponential', delay: 10000 },
    });
    return { jobId: job.id, status: 'queued', message: `Investor flow collection queued for ${market}${days ? ` (days=${days})` : ''}` };
  }

  async triggerCheckSellSignals(market = 'US') {
    const job = await this.sellSignalQueue.add('check', { market }, {
      attempts: 2,
      backoff: { type: 'exponential', delay: 5000 },
    });
    return { jobId: job.id, status: 'queued', message: `SELL signal check queued for ${market}` };
  }

  // 백테스트는 FastAPI(analysis)에 있는데 외부 노출이 안 되므로 여기서 프록시한다.
  async runBacktestRescore(body: Record<string, unknown>) {
    const baseUrl = process.env.ANALYSIS_SERVICE_URL ?? 'http://localhost:8000';
    try {
      const res = await axios.post(`${baseUrl}/backtest/rescore`, body, {
        timeout: 600000,
        validateStatus: () => true,
      });
      return res.data;
    } catch (err: any) {
      return { error: err?.message ?? 'backtest failed' };
    }
  }

  async triggerEvaluateRecommendations() {
    const job = await this.evalQueue.add('evaluate', {}, {
      attempts: 3,
      backoff: { type: 'exponential', delay: 5000 },
    });
    return { jobId: job.id, status: 'queued', message: 'Recommendation evaluation queued' };
  }

  async getJobStatus(queueName: string, jobId: string) {
    const queues: Record<string, Queue> = {
      'collect-stock-list': this.stockListQueue,
      'collect-prices': this.pricesQueue,
      'collect-news': this.newsQueue,
      'collect-financials': this.financialsQueue,
      'generate-recommendations': this.recsQueue,
      'evaluate-recommendations': this.evalQueue,
      'collect-macro': this.macroQueue,
      'collect-investor-flow': this.investorFlowQueue,
      'check-sell-signals': this.sellSignalQueue,
      'run-pipeline': this.pipelineQueue,
    };
    const queue = queues[queueName];
    if (!queue) return null;
    const job = await queue.getJob(jobId);
    if (!job) return null;
    return {
      id: job.id,
      name: job.name,
      status: await job.getState(),
      progress: job.progress(),
      data: job.data,
      createdAt: new Date(job.timestamp),
      processedAt: job.processedOn ? new Date(job.processedOn) : null,
      finishedAt: job.finishedOn ? new Date(job.finishedOn) : null,
      failedReason: job.failedReason,
    };
  }

  async getRecentRuns(limit = 20) {
    return this.prisma.recommendationRun.findMany({
      include: {
        modelVersion: true,
        _count: { select: { recommendations: true } },
      },
      orderBy: { executedAt: 'desc' },
      take: limit,
    });
  }

  async getModelVersions() {
    return this.prisma.modelVersion.findMany({
      orderBy: { deployedAt: 'desc' },
    });
  }

  async createModelVersion(data: {
    versionName: string;
    strategyType: string;
    config: Record<string, unknown>;
  }) {
    return this.prisma.modelVersion.create({
      data: {
        versionName: data.versionName,
        strategyType: data.strategyType,
        configJson: data.config as object,
        isActive: false,
      },
    });
  }

  async activateModelVersion(id: number) {
    await this.prisma.modelVersion.updateMany({ data: { isActive: false } });
    return this.prisma.modelVersion.update({
      where: { id },
      data: { isActive: true },
    });
  }

  async getLogs(service: string, lines: number) {
    const logDir = path.join(os.homedir(), '.pm2', 'logs');
    const fileMap: Record<string, string> = {
      'api':          'stock-signal-api-out-0.log',
      'api-error':    'stock-signal-api-error-0.log',
      'analysis':     'stock-signal-analysis-error.log',
    };
    const filename = fileMap[service] ?? fileMap['api'];
    const logFile = path.join(logDir, filename);

    try {
      const content = await fs.readFile(logFile, 'utf-8');
      const allLines = content.split('\n').filter(l => l.trim());
      const recent = allLines.slice(-lines);
      // ANSI 색상 코드 제거
      const cleaned = recent.map(l => l.replace(/\x1B\[[0-9;]*[mGKHF]/g, ''));
      return { lines: cleaned, service, total: allLines.length };
    } catch {
      return { lines: [], service, total: 0, error: 'Log file not found' };
    }
  }

  async getDataHealth() {
    const now = new Date();

    // 시장별 마지막 추천 실행
    type RunRow = { market_code: string; last_run: Date; run_count: bigint };
    const runRows = await this.prisma.$queryRaw<RunRow[]>`
      SELECT market_code, MAX(executed_at) AS last_run, COUNT(*) AS run_count
      FROM recommendation_runs
      WHERE executed_at >= NOW() - INTERVAL '30 days'
      GROUP BY market_code
    `;

    // 시장별 마지막 가격 수집일
    type PriceRow = { market_code: string; last_date: Date; stock_count: bigint };
    const priceRows = await this.prisma.$queryRaw<PriceRow[]>`
      SELECT m.code AS market_code, MAX(pd.date) AS last_date, COUNT(DISTINCT pd.stock_id) AS stock_count
      FROM price_daily pd
      JOIN stocks s ON s.id = pd.stock_id
      JOIN markets m ON m.id = s.market_id
      WHERE pd.date >= NOW() - INTERVAL '30 days'
      GROUP BY m.code
    `;

    // 최근 24h / 7d 뉴스 수집 건수
    type NewsRow = { period: string; count: bigint };
    const newsRows = await this.prisma.$queryRaw<NewsRow[]>`
      SELECT '24h' AS period, COUNT(*) AS count FROM news_articles WHERE created_at >= NOW() - INTERVAL '24 hours'
      UNION ALL
      SELECT '7d'  AS period, COUNT(*) AS count FROM news_articles WHERE created_at >= NOW() - INTERVAL '7 days'
    `;

    // 재무 데이터 최신 period_end
    type FinRow = { market_code: string; latest_period: Date; count: bigint };
    const finRows = await this.prisma.$queryRaw<FinRow[]>`
      SELECT m.code AS market_code, MAX(fm.period_end) AS latest_period, COUNT(*) AS count
      FROM financial_metrics fm
      JOIN stocks s ON s.id = fm.stock_id
      JOIN markets m ON m.id = s.market_id
      GROUP BY m.code
    `;

    // Bull 큐 현재 상태
    const queueStats: Record<string, any> = {};
    const queues: Record<string, Queue> = {
      'run-pipeline':              this.pipelineQueue,
      'generate-recommendations':  this.recsQueue,
      'collect-prices':            this.pricesQueue,
      'collect-news':              this.newsQueue,
      'collect-financials':        this.financialsQueue,
      'collect-investor-flow':     this.investorFlowQueue,
    };
    for (const [name, q] of Object.entries(queues)) {
      try {
        const [waiting, active, failed] = await Promise.all([
          q.getWaitingCount(), q.getActiveCount(), q.getFailedCount(),
        ]);
        queueStats[name] = { waiting, active, failed };
      } catch { queueStats[name] = null; }
    }

    const hoursAgo = (d: Date | null) =>
      d ? Math.round((now.getTime() - new Date(d).getTime()) / 3600000) : null;

    const newsMap = Object.fromEntries(newsRows.map((r: NewsRow) => [r.period, Number(r.count)]));

    const markets = ['US', 'KR'].map(code => {
      const run   = runRows.find((r: RunRow)   => r.market_code === code);
      const price = priceRows.find((r: PriceRow) => r.market_code === code);
      const fin   = finRows.find((r: FinRow)   => r.market_code === code);

      const signalAgeH  = hoursAgo(run?.last_run ?? null);
      const priceAgeDays = price?.last_date
        ? Math.round((now.getTime() - new Date(price.last_date).getTime()) / 86400000)
        : null;

      return {
        market: code,
        signal: {
          lastRunAt: run?.last_run ?? null,
          ageHours: signalAgeH,
          runCount30d: Number(run?.run_count ?? 0),
          status: signalAgeH === null ? 'unknown'
                : signalAgeH > 72 ? 'danger'
                : signalAgeH > 48 ? 'warn'
                : 'ok',
        },
        price: {
          lastDate: price?.last_date ?? null,
          ageDays: priceAgeDays,
          stockCount: Number(price?.stock_count ?? 0),
          status: priceAgeDays === null ? 'unknown'
                : priceAgeDays > 5 ? 'danger'
                : priceAgeDays > 2 ? 'warn'
                : 'ok',
        },
        financial: {
          latestPeriod: fin?.latest_period ?? null,
          count: Number(fin?.count ?? 0),
          status: fin ? 'ok' : 'unknown',
        },
      };
    });

    const totalFailed = Object.values(queueStats).reduce(
      (sum, q) => sum + (q?.failed ?? 0), 0,
    );

    return {
      checkedAt: now,
      markets,
      news: {
        last24h: newsMap['24h'] ?? 0,
        last7d:  newsMap['7d']  ?? 0,
        status: (newsMap['24h'] ?? 0) === 0 ? 'warn' : 'ok',
      },
      queues: queueStats,
      summary: {
        hasWarning: markets.some(m => m.signal.status !== 'ok' || m.price.status !== 'ok')
                    || (newsMap['24h'] ?? 0) === 0,
        hasDanger:  markets.some(m => m.signal.status === 'danger' || m.price.status === 'danger'),
        totalFailedJobs: totalFailed,
      },
    };
  }

  async getDataQualityIssues(market = 'US') {
    type PriceAnomalyRow = {
      symbol: string;
      name: string;
      date: Date;
      close: string;
      prev_close: string;
      change_ratio: string;
    };

    // 가격 이상치: 전일 대비 50% 이상 급변 (최근 7일)
    const priceAnomalies = await this.prisma.$queryRaw<PriceAnomalyRow[]>`
      WITH daily_changes AS (
        SELECT
          s.symbol,
          s.name,
          pd.date,
          pd.close,
          LAG(pd.close) OVER (PARTITION BY pd.stock_id ORDER BY pd.date) AS prev_close
        FROM price_daily pd
        JOIN stocks s  ON s.id  = pd.stock_id
        JOIN markets m ON m.id  = s.market_id
        WHERE m.code     = ${market}
          AND pd.date    >= NOW() - INTERVAL '7 days'
          AND pd.close   > 0
      )
      SELECT
        symbol, name, date, close, prev_close,
        ABS((close - prev_close) / prev_close) AS change_ratio
      FROM daily_changes
      WHERE prev_close IS NOT NULL
        AND prev_close > 0
        AND ABS((close - prev_close) / prev_close) > 0.5
      ORDER BY change_ratio DESC
      LIMIT 30
    `;

    type ZeroPriceRow = { symbol: string; name: string; date: Date; close: string };

    // 0 또는 음수 가격 (최근 7일)
    const zeroPrices = await this.prisma.$queryRaw<ZeroPriceRow[]>`
      SELECT s.symbol, s.name, pd.date, pd.close
      FROM price_daily pd
      JOIN stocks s  ON s.id  = pd.stock_id
      JOIN markets m ON m.id  = s.market_id
      WHERE m.code  = ${market}
        AND pd.date >= NOW() - INTERVAL '7 days'
        AND pd.close <= 0
      LIMIT 20
    `;

    type FinAnomalyRow = {
      symbol: string;
      name: string;
      roe: string | null;
      per: string | null;
      pbr: string | null;
      period_end: Date;
    };

    // 재무 이상치: ROE > ±500%, PER < 0 or > 500, PBR < 0 or > 50
    const finAnomalies = await this.prisma.$queryRaw<FinAnomalyRow[]>`
      SELECT DISTINCT ON (s.id)
        s.symbol, s.name,
        fm.roe, fm.per, fm.pbr, fm.period_end
      FROM financial_metrics fm
      JOIN stocks s  ON s.id  = fm.stock_id
      JOIN markets m ON m.id  = s.market_id
      WHERE m.code = ${market}
        AND (
          ABS(fm.roe) > 5
          OR (fm.per IS NOT NULL AND (fm.per < 0 OR fm.per > 500))
          OR (fm.pbr IS NOT NULL AND (fm.pbr < 0 OR fm.pbr > 50))
        )
      ORDER BY s.id, fm.period_end DESC
      LIMIT 30
    `;

    const priceIssues = [
      ...priceAnomalies.map((r: PriceAnomalyRow) => ({
        type: 'price_spike' as const,
        severity: Number(r.change_ratio) > 0.8 ? 'danger' : 'warn',
        symbol: r.symbol,
        name: r.name,
        detail: `${(Number(r.change_ratio) * 100).toFixed(1)}% 급변 (${Number(r.prev_close).toFixed(2)} → ${Number(r.close).toFixed(2)})`,
        date: r.date,
      })),
      ...zeroPrices.map((r: ZeroPriceRow) => ({
        type: 'zero_price' as const,
        severity: 'danger' as const,
        symbol: r.symbol,
        name: r.name,
        detail: `가격 ${Number(r.close).toFixed(4)} (0 이하)`,
        date: r.date,
      })),
    ];

    const finIssues = finAnomalies.map((r: FinAnomalyRow) => {
      const flags: string[] = [];
      if (r.roe && Math.abs(Number(r.roe)) > 5)  flags.push(`ROE ${(Number(r.roe) * 100).toFixed(0)}%`);
      if (r.per && (Number(r.per) < 0 || Number(r.per) > 500)) flags.push(`PER ${Number(r.per).toFixed(1)}`);
      if (r.pbr && (Number(r.pbr) < 0 || Number(r.pbr) > 50))  flags.push(`PBR ${Number(r.pbr).toFixed(1)}`);
      return {
        type: 'financial_anomaly' as const,
        severity: 'warn' as const,
        symbol: r.symbol,
        name: r.name,
        detail: flags.join(', '),
        date: r.period_end,
      };
    });

    const allIssues = [...priceIssues, ...finIssues]
      .sort((a, b) => (a.severity === 'danger' ? -1 : 1));

    return {
      market,
      checkedAt: new Date(),
      total: allIssues.length,
      danger: allIssues.filter(i => i.severity === 'danger').length,
      warn:   allIssues.filter(i => i.severity === 'warn').length,
      issues: allIssues,
    };
  }

  async getSystemStatus() {
    try {
      const [pm2Out, memOut, diskOut, uptimeOut] = await Promise.all([
        execAsync('pm2 jlist').then(r => r.stdout).catch(() => '[]'),
        execAsync('free -m').then(r => r.stdout).catch(() => ''),
        execAsync("df -h / | tail -1").then(r => r.stdout).catch(() => ''),
        execAsync('uptime').then(r => r.stdout).catch(() => ''),
      ]);

      const processes = JSON.parse(pm2Out || '[]');

      return {
        processes: processes.map((p: any) => ({
          id: p.pm_id,
          name: p.name,
          status: p.pm2_env?.status,
          uptimeMs: p.pm2_env?.pm_uptime ? Date.now() - p.pm2_env.pm_uptime : null,
          restarts: p.pm2_env?.restart_time ?? 0,
          memoryBytes: p.monit?.memory ?? 0,
          cpu: p.monit?.cpu ?? 0,
          pid: p.pid,
        })),
        memory: memOut.trim(),
        disk: diskOut.trim(),
        uptime: uptimeOut.trim(),
      };
    } catch (e) {
      return { processes: [], error: String(e) };
    }
  }

  async getRecentFailures(limit = 30) {
    const queueMap: Record<string, Queue> = {
      '전체 파이프라인':  this.pipelineQueue,
      '시그널 생성':      this.recsQueue,
      '주가 수집':        this.pricesQueue,
      '뉴스 수집':        this.newsQueue,
      '재무 수집':        this.financialsQueue,
      '종목 동기화':      this.stockListQueue,
      '거시지표 수집':    this.macroQueue,
    };

    const failures: any[] = [];
    for (const [label, queue] of Object.entries(queueMap)) {
      try {
        const failed = await queue.getFailed(0, 20);
        for (const job of failed) {
          failures.push({
            queue: label,
            jobId: String(job.id),
            market: job.data?.market ?? null,
            failedAt: job.finishedOn ? new Date(job.finishedOn) : null,
            reason: job.failedReason ?? 'Unknown error',
            attemptsMade: job.attemptsMade,
          });
        }
      } catch { /* queue empty or unavailable */ }
    }

    return failures
      .sort((a, b) => (b.failedAt?.getTime() ?? 0) - (a.failedAt?.getTime() ?? 0))
      .slice(0, limit);
  }

  async getRecentRunsDetailed(limit: number) {
    const runs = await this.prisma.recommendationRun.findMany({
      include: {
        modelVersion: true,
        _count: { select: { recommendations: true } },
      },
      orderBy: { executedAt: 'desc' },
      take: limit,
    });

    // 각 run의 실패 여부: 추천 수가 0이면 실패로 간주
    return runs.map((run: (typeof runs)[0]) => ({
      id: run.id,
      marketCode: run.marketCode,
      executedAt: run.executedAt,
      runType: run.runType,
      modelVersion: run.modelVersion?.versionName ?? '-',
      count: run._count.recommendations,
      notes: run.notes,
    }));
  }

  async getScoringAnalysis(market = 'US') {
    // 집계를 SQL 로 내린다. 예전에는 추천 행을 Node 로 퍼올려 배열로 계산했는데
    // 두 가지가 잘못됐다:
    //
    //  1) `take` 가 필터보다 먼저 적용돼, 하루 BUY 가 1,000건을 넘자 최근 N건이
    //     통째로 '7일 미성숙' 구간에 들어가 항상 0건이 됐다 (v3.12.1 에서 수정).
    //  2) 조회 대상이 BUY 뿐이라 **65점 미만 임계값을 검증할 수 없었다.** BUY 는
    //     정의상 점수 >= 65 라, 임계값 50/55/60/65 가 전부 같은 표본이 되어
    //     "최적 임계값 50점" 같은 아티팩트가 나왔다. 임계값을 낮추라는 실행 가능한
    //     조언 형태라 그대로 두면 위험했다.
    //
    // 그래서 **채점된 전 종목(WATCH/AVOID 포함)** 을 대상으로 바꿨다. 그러면 행이
    // 시장당 30만 건대라 Node 로 들고 오는 방식은 성립하지 않는다 — 세 집계를
    // 모두 Postgres 에서 계산한다(실측 각 1~2초).
    // ⚠️ `${WINDOW_DAYS}::int` 의 캐스트를 빼지 말 것 — Prisma 는 JS number 를
    //    bigint 로 바인딩하는데 make_interval(days => ...) 은 int 만 받는다.
    //    psql 에서 리터럴 90 으로 테스트하면 통과하고 운영에서 500 이 난다(실제로 겪음).
    const WINDOW_DAYS = 90;
    const CURRENT_THRESHOLD = 65;

    type ThresholdRow = { thr: number; cnt: number; hit_rate: number | null; avg_return: number | null; avg_alpha: number | null };
    const thresholdRows = await this.prisma.$queryRaw<ThresholdRow[]>`
      WITH ev AS (
        SELECT rec.score, res.hit_7d, res.return_7d, res.alpha_7d
        FROM recommendations rec
        JOIN recommendation_runs r ON r.id = rec.recommendation_run_id
        JOIN recommendation_results res ON res.recommendation_id = rec.id
        WHERE r.market_code = ${market}
          AND res.hit_7d IS NOT NULL
          AND rec.recommended_at >= NOW() - make_interval(days => ${WINDOW_DAYS}::int)
      )
      SELECT t.thr::int AS thr,
             COUNT(ev.score)::int AS cnt,
             AVG(CASE WHEN ev.hit_7d THEN 1.0 ELSE 0 END)::float AS hit_rate,
             AVG(ev.return_7d)::float AS avg_return,
             AVG(ev.alpha_7d)::float AS avg_alpha
      FROM (VALUES (50),(55),(60),(65),(70),(75),(80)) AS t(thr)
      LEFT JOIN ev ON ev.score >= t.thr
      GROUP BY t.thr
      ORDER BY t.thr
    `;

    type BandRow = { band: string; lo: number; cnt: number; hit_rate: number | null; avg_return: number | null; avg_alpha: number | null };
    const bandRows = await this.prisma.$queryRaw<BandRow[]>`
      WITH ev AS (
        SELECT rec.score, res.hit_7d, res.return_7d, res.alpha_7d
        FROM recommendations rec
        JOIN recommendation_runs r ON r.id = rec.recommendation_run_id
        JOIN recommendation_results res ON res.recommendation_id = rec.id
        WHERE r.market_code = ${market}
          AND res.hit_7d IS NOT NULL
          AND rec.recommended_at >= NOW() - make_interval(days => ${WINDOW_DAYS}::int)
      )
      SELECT CASE WHEN score < 50 THEN '<50'   WHEN score < 55 THEN '50–55'
                  WHEN score < 60 THEN '55–60' WHEN score < 65 THEN '60–65'
                  WHEN score < 70 THEN '65–70' WHEN score < 75 THEN '70–75'
                  ELSE '75+' END AS band,
             MIN(score)::float AS lo,
             COUNT(*)::int AS cnt,
             AVG(CASE WHEN hit_7d THEN 1.0 ELSE 0 END)::float AS hit_rate,
             AVG(return_7d)::float AS avg_return,
             AVG(alpha_7d)::float AS avg_alpha
      FROM ev GROUP BY 1 ORDER BY 2
    `;

    // 주도 전략 = momentum·value·sentiment 서브스코어 중 가장 높은 것.
    // 원래 TS 판정식(>= / >)을 그대로 옮겨 결과가 달라지지 않게 했다.
    type StratRow = { strat: string; cnt: number; hit_rate: number | null; avg_return: number | null; avg_alpha: number | null };
    const stratRows = await this.prisma.$queryRaw<StratRow[]>`
      WITH ev AS (
        SELECT res.hit_7d, res.return_7d, res.alpha_7d,
               COALESCE((rec.score_detail_json->>'momentum_score')::numeric, 0)  AS mom,
               COALESCE((rec.score_detail_json->>'value_score')::numeric, 0)     AS val,
               COALESCE((rec.score_detail_json->>'sentiment_score')::numeric, 0) AS sent
        FROM recommendations rec
        JOIN recommendation_runs r ON r.id = rec.recommendation_run_id
        JOIN recommendation_results res ON res.recommendation_id = rec.id
        WHERE r.market_code = ${market}
          AND res.hit_7d IS NOT NULL
          AND rec.recommended_at >= NOW() - make_interval(days => ${WINDOW_DAYS}::int)
      )
      SELECT CASE WHEN mom >= val AND mom >= sent THEN 'momentum'
                  WHEN val > mom AND val >= sent  THEN 'value'
                  ELSE 'sentiment' END AS strat,
             COUNT(*)::int AS cnt,
             AVG(CASE WHEN hit_7d THEN 1.0 ELSE 0 END)::float AS hit_rate,
             AVG(return_7d)::float AS avg_return,
             AVG(alpha_7d)::float AS avg_alpha
      FROM ev GROUP BY 1
    `;

    // 임계값 50 이상 건수를 전체로 쓰면 안 된다 — 50점 미만이 통째로 빠진다
    // (KR 은 <50 구간이 90,162건으로 전체의 72% 다). 구간별 합이 진짜 전체다.
    const totalEvaluated = bandRows.reduce((sum: number, r: BandRow) => sum + r.cnt, 0);

    const thresholdSensitivity = thresholdRows.map((r: ThresholdRow) => ({
      threshold:   r.thr,
      count:       r.cnt,
      hitRate7d:   r.cnt > 0 ? r.hit_rate : null,
      avgReturn7d: r.cnt > 0 ? r.avg_return : null,
      avgAlpha7d:  r.cnt > 0 ? r.avg_alpha : null,
      isCurrent:   r.thr === CURRENT_THRESHOLD,
    }));

    const scoreBands = bandRows.map((r: BandRow) => ({
      band:             r.band,
      count:            r.cnt,
      hitRate7d:        r.hit_rate,
      avgReturn7d:      r.avg_return,
      avgAlpha7d:       r.avg_alpha,
      isCurrentBuyZone: r.lo >= CURRENT_THRESHOLD,
    }));

    type StratKey = 'momentum' | 'value' | 'sentiment';
    const WEIGHTS: Record<StratKey, number> = { momentum: 0.45, value: 0.25, sentiment: 0.30 };
    const LABELS:  Record<StratKey, string> = { momentum: '모멘텀', value: '가치', sentiment: '감성' };
    const strategyBreakdown = (['momentum', 'value', 'sentiment'] as StratKey[]).map(strat => {
      const row = stratRows.find((r: StratRow) => r.strat === strat);
      return {
        strategy:      strat,
        label:         LABELS[strat],
        count:         row?.cnt ?? 0,
        hitRate7d:     row?.hit_rate ?? null,
        avgReturn7d:   row?.avg_return ?? null,
        avgAlpha7d:    row?.avg_alpha ?? null,
        currentWeight: WEIGHTS[strat],
      };
    });

    const currentThr = thresholdSensitivity.find(t => t.isCurrent);
    const bestThr = thresholdSensitivity
      .filter(t => t.count >= 5 && t.hitRate7d != null)
      .sort((a, b) => (b.hitRate7d ?? 0) - (a.hitRate7d ?? 0))[0];
    const bestStrat = [...strategyBreakdown]
      .filter(s => s.count >= 3 && s.hitRate7d != null)
      .sort((a, b) => (b.hitRate7d ?? 0) - (a.hitRate7d ?? 0))[0];

    return {
      market,
      windowDays: WINDOW_DAYS,
      totalEvaluated,
      thresholdSensitivity,
      scoreBands,
      strategyBreakdown,
      insight: {
        currentThreshold:        CURRENT_THRESHOLD,
        currentThresholdHitRate: currentThr?.hitRate7d ?? null,
        bestThreshold:           bestThr?.threshold ?? null,
        bestThresholdHitRate:    bestThr?.hitRate7d  ?? null,
        dominantStrategy:        bestStrat?.label    ?? null,
        dominantStrategyHitRate: bestStrat?.hitRate7d ?? null,
      },
    };
  }
}
