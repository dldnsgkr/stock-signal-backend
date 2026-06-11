import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { AdminService } from '../admin/admin.service';

@Injectable()
export class SchedulerService {
  private readonly logger = new Logger(SchedulerService.name);

  constructor(private readonly adminService: AdminService) {}

  // ── 한국 시장 ─────────────────────────────────────────────
  // 장 마감(15:30 KST) 후 충분히 여유를 두고 16:30에 전체 파이프라인 실행
  // 종목 동기화 → 가격·뉴스·재무·매크로 수집 → 시그널 생성 순서로 자동 진행
  @Cron('0 30 16 * * 1-5', { timeZone: 'Asia/Seoul' })
  async runKrPipeline() {
    this.logger.log('[Scheduler] KR 전체 파이프라인 시작');
    await this.adminService.triggerRunPipeline('KR');
  }

  // ── 미국 시장 ─────────────────────────────────────────────
  // 장 마감(16:00 ET) 후 1시간 30분 뒤 17:30 ET에 전체 파이프라인 실행
  @Cron('0 30 17 * * 1-5', { timeZone: 'America/New_York' })
  async runUsPipeline() {
    this.logger.log('[Scheduler] US 전체 파이프라인 시작');
    await this.adminService.triggerRunPipeline('US');
  }

  // ── 성과 평가 ─────────────────────────────────────────────
  // 매일 자정(KST) — 7일·30일 시그널 결과 업데이트
  @Cron('0 0 0 * * *', { timeZone: 'Asia/Seoul' })
  async evaluateRecommendations() {
    this.logger.log('[Scheduler] 성과 평가 시작');
    await this.adminService.triggerEvaluateRecommendations();
  }
}
