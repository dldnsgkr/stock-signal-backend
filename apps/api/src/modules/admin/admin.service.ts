import { Injectable } from '@nestjs/common';
import { InjectQueue } from '@nestjs/bull';
import { Queue } from 'bull';
import { PrismaService } from '../../prisma/prisma.service';
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
}
