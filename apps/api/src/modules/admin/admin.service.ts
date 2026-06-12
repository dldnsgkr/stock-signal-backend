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
    const job = await this.stockListQueue.add('collect', { market }, { attempts: 2, timeout: 120000 });
    return { jobId: job.id, status: 'queued', message: `Stock list sync queued for ${market}` };
  }

  async triggerCollectPrices(market = 'US') {
    const job = await this.pricesQueue.add('collect', { market }, { attempts: 3 });
    return { jobId: job.id, status: 'queued', message: `Price collection queued for ${market}` };
  }

  async triggerCollectNews(market = 'US') {
    const job = await this.newsQueue.add('collect', { market }, { attempts: 3 });
    return { jobId: job.id, status: 'queued', message: `News collection queued for ${market}` };
  }

  async triggerCollectFinancials(market = 'US') {
    const job = await this.financialsQueue.add('collect', { market }, { attempts: 3 });
    return { jobId: job.id, status: 'queued', message: `Financial collection queued for ${market}` };
  }

  async triggerGenerateRecommendations(market = 'US') {
    const job = await this.recsQueue.add('generate', { market }, { attempts: 2 });
    return { jobId: job.id, status: 'queued', message: `Recommendation generation queued for ${market}` };
  }

  async triggerRunPipeline(market = 'US') {
    const job = await this.pipelineQueue.add('run', { market }, { attempts: 1 });
    return { jobId: job.id, status: 'queued', message: `Full pipeline queued for ${market}` };
  }

  async triggerCollectMacro(market = 'US') {
    const job = await this.macroQueue.add('collect', { market }, { attempts: 3 });
    return { jobId: job.id, status: 'queued', message: `Macro collection queued for ${market}` };
  }

  async triggerEvaluateRecommendations() {
    const job = await this.evalQueue.add('evaluate', {}, { attempts: 3 });
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
    return runs.map(run => ({
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
