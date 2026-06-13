import { Injectable, Logger } from '@nestjs/common';
import { Cron } from '@nestjs/schedule';
import { AdminService } from '../admin/admin.service';
import { AlertService } from '../alert/alert.service';
import { PrismaService } from '../../prisma/prisma.service';

const STALE_THRESHOLD_HOURS = 48; // 2일 초과 시 경고

@Injectable()
export class SchedulerService {
  private readonly logger = new Logger(SchedulerService.name);

  constructor(
    private readonly adminService: AdminService,
    private readonly alert: AlertService,
    private readonly prisma: PrismaService,
  ) {}

  // ── 한국 시장 ─────────────────────────────────────────────
  @Cron('0 30 16 * * 1-5', { timeZone: 'Asia/Seoul' })
  async runKrPipeline() {
    this.logger.log('[Scheduler] KR 전체 파이프라인 시작');
    await this.adminService.triggerRunPipeline('KR');
  }

  // ── 미국 시장 ─────────────────────────────────────────────
  @Cron('0 30 17 * * 1-5', { timeZone: 'America/New_York' })
  async runUsPipeline() {
    this.logger.log('[Scheduler] US 전체 파이프라인 시작');
    await this.adminService.triggerRunPipeline('US');
  }

  // ── 성과 평가 ─────────────────────────────────────────────
  @Cron('0 0 0 * * *', { timeZone: 'Asia/Seoul' })
  async evaluateRecommendations() {
    this.logger.log('[Scheduler] 성과 평가 시작');
    await this.adminService.triggerEvaluateRecommendations();
  }

  // ── 데이터 지연 감지 (평일 오전 9시 KST) ─────────────────
  @Cron('0 0 9 * * 1-5', { timeZone: 'Asia/Seoul' })
  async checkDataFreshness() {
    this.logger.log('[Scheduler] 데이터 신선도 점검 시작');

    for (const market of ['US', 'KR']) {
      const lastRun = await this.prisma.recommendationRun.findFirst({
        where: { marketCode: market },
        orderBy: { executedAt: 'desc' },
      });

      if (!lastRun) {
        await this.alert.send({
          type: 'warning',
          title: '분석 데이터 없음',
          market,
          detail: '아직 한 번도 파이프라인이 성공하지 않았습니다.',
        });
        continue;
      }

      const hoursAgo = (Date.now() - lastRun.executedAt.getTime()) / 3600000;

      if (hoursAgo > STALE_THRESHOLD_HOURS) {
        const daysAgo = (hoursAgo / 24).toFixed(1);
        await this.alert.send({
          type: 'warning',
          title: '데이터 지연 경고',
          market,
          detail: `마지막 분석: ${lastRun.executedAt.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} (${daysAgo}일 전)\n파이프라인이 정상 실행되고 있는지 확인하세요.`,
        });
      } else {
        this.logger.log(`[${market}] 최근 분석: ${hoursAgo.toFixed(1)}시간 전 — 정상`);
      }
    }
  }
}
