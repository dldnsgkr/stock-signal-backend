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

  // ── 헬스체크 (평일 오전 9시 KST) ────────────────────────────
  @Cron('0 0 9 * * 1-5', { timeZone: 'Asia/Seoul' })
  async runDailyHealthCheck() {
    this.logger.log('[Scheduler] 일일 헬스체크 시작');
    try {
      const health = await this.adminService.getDataHealth();
      const { summary, markets, news } = health;

      // 위험 항목 로그
      for (const m of markets) {
        if (m.signal.status === 'danger') {
          this.logger.error(
            `[헬스체크] ${m.market} 시그널 ${m.signal.ageHours}시간 미업데이트 — 파이프라인 확인 필요`,
          );
        } else if (m.signal.status === 'warn') {
          this.logger.warn(
            `[헬스체크] ${m.market} 시그널 ${m.signal.ageHours}시간 미업데이트`,
          );
        }

        if (m.price.status === 'danger') {
          this.logger.error(
            `[헬스체크] ${m.market} 가격 데이터 ${m.price.ageDays}일 미수집`,
          );
        } else if (m.price.status === 'warn') {
          this.logger.warn(
            `[헬스체크] ${m.market} 가격 데이터 ${m.price.ageDays}일 미수집`,
          );
        }
      }

      if (news.status === 'warn') {
        this.logger.warn('[헬스체크] 최근 24h 뉴스 수집 0건');
      }

      if (summary.totalFailedJobs > 0) {
        this.logger.warn(`[헬스체크] 실패 Job ${summary.totalFailedJobs}건 누적`);
      }

      if (!summary.hasWarning && !summary.hasDanger) {
        this.logger.log('[헬스체크] 모든 데이터 정상');
      }
    } catch (e) {
      this.logger.error(`[헬스체크] 실행 오류: ${e}`);
    }
  }
}
