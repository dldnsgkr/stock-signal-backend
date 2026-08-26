import { Injectable, Logger } from '@nestjs/common';
import { Cron } from '@nestjs/schedule';
import { AdminService } from '../admin/admin.service';
import { AlertService } from '../alert/alert.service';
import { PrismaService } from '../../prisma/prisma.service';

const STALE_THRESHOLD_HOURS = 48; // 2일 초과 시 경고

// 연속 미실행 임계 — 이 영업일 수부터 '연속' 으로 격상해 알린다.
// 1일은 휴장·지연으로 흔하므로 2일부터 센다.
const STREAK_ALERT_DAYS = 2;

/** 두 날짜 사이의 영업일 수(주말 제외). 시작일 다음날부터 끝일까지 센다. */
function businessDaysBetween(from: Date, to: Date): number {
  let n = 0;
  const d = new Date(from);
  d.setHours(0, 0, 0, 0);
  const end = new Date(to);
  end.setHours(0, 0, 0, 0);
  while (d < end) {
    d.setDate(d.getDate() + 1);
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6) n++;
  }
  return n;
}

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

      // 위험 항목 — 로그 + 알림 발송 (alert.send 는 푸시/Slack 으로 전파)
      // 과거에 danger 가 로그에만 남아 두 달간 아무도 몰랐던 사고의 재발 방지.
      for (const m of markets) {
        if (m.signal.status === 'danger') {
          this.logger.error(
            `[헬스체크] ${m.market} 시그널 ${m.signal.ageHours}시간 미업데이트 — 파이프라인 확인 필요`,
          );
          await this.alert.send({
            type: 'error',
            title: '시그널 미갱신',
            market: m.market,
            detail: `${m.signal.ageHours}시간 미업데이트 — 파이프라인 확인 필요`,
          });
        } else if (m.signal.status === 'warn') {
          this.logger.warn(
            `[헬스체크] ${m.market} 시그널 ${m.signal.ageHours}시간 미업데이트`,
          );
        }

        if (m.price.status === 'danger') {
          this.logger.error(
            `[헬스체크] ${m.market} 가격 데이터 ${m.price.ageDays}일 미수집`,
          );
          await this.alert.send({
            type: 'error',
            title: '가격 데이터 미수집',
            market: m.market,
            detail: `${m.price.ageDays}일째 수집 안 됨`,
          });
        } else if (m.price.status === 'warn') {
          this.logger.warn(
            `[헬스체크] ${m.market} 가격 데이터 ${m.price.ageDays}일 미수집`,
          );
        }
      }

      // ── 파이프라인 연속 미실행 ────────────────────────────────
      // ⚠️ 이 점검이 없어서 US 파이프라인이 **8일간** 죽어 있는 걸 아무도 몰랐다
      // (2026-08-18~25). 시그널 미갱신 알림은 매일 나갔지만 **문구가 매일 같아**
      // 배경 소음이 됐고, '며칠째' 라는 정보가 없어 심각도가 드러나지 않았다.
      // → 연속 일수를 세어 제목에 박고, 길어질수록 문구를 세게 만든다.
      await this.checkRunStreaks();

      if (news.status === 'warn') {
        this.logger.warn('[헬스체크] 최근 24h 뉴스 수집 0건');
      }

      if (summary.totalFailedJobs > 0) {
        this.logger.warn(`[헬스체크] 실패 Job ${summary.totalFailedJobs}건 누적`);
      }

      if (!summary.hasWarning && !summary.hasDanger) {
        this.logger.log('[헬스체크] 모든 데이터 정상');
      }

      // 데이터 품질 검사
      for (const market of ['US', 'KR']) {
        try {
          const quality = await this.adminService.getDataQualityIssues(market);
          if (quality.danger > 0) {
            this.logger.error(
              `[품질검사] ${market} 위험 이상치 ${quality.danger}건 — ` +
              quality.issues
                .filter(i => i.severity === 'danger')
                .slice(0, 3)
                .map(i => `${i.symbol}(${i.detail})`)
                .join(', '),
            );
          } else if (quality.warn > 0) {
            this.logger.warn(`[품질검사] ${market} 주의 이상치 ${quality.warn}건`);
          } else {
            this.logger.log(`[품질검사] ${market} 이상치 없음`);
          }
        } catch (e) {
          this.logger.error(`[품질검사] ${market} 실행 오류: ${e}`);
        }
      }
    } catch (e) {
      this.logger.error(`[헬스체크] 실행 오류: ${e}`);
    }
  }

  /**
   * 시장별 '마지막 성공 런 이후 영업일 수' 를 세어 연속 미실행을 알린다.
   *
   * 성공 판정은 `recommendation_runs` 행 존재로 한다 — 파이프라인은 추천 생성까지
   * 끝나야 이 행을 남기므로, 중간 단계에서 죽으면 행이 없다(데이터 계약 게이트에
   * 막힌 경우도 포함된다). 즉 "실제로 시그널이 나왔는가" 를 그대로 재는 셈이다.
   */
  private async checkRunStreaks() {
    const now = new Date();
    for (const market of ['US', 'KR']) {
      const last = await this.prisma.recommendationRun.findFirst({
        where: { marketCode: market },
        orderBy: { executedAt: 'desc' },
        select: { executedAt: true },
      });

      if (!last) {
        this.logger.error(`[헬스체크] ${market} 파이프라인 성공 이력이 없다`);
        await this.alert.send({
          type: 'error',
          title: '파이프라인 성공 이력 없음',
          market,
          detail: '추천 런이 한 번도 기록되지 않았습니다 — 설정 확인 필요',
        });
        continue;
      }

      const days = businessDaysBetween(last.executedAt, now);
      if (days < STREAK_ALERT_DAYS) {
        if (days > 0) {
          this.logger.log(`[헬스체크] ${market} 마지막 런 ${days}영업일 전 (정상 범위)`);
        }
        continue;
      }

      // 길어질수록 문구를 세게 — 같은 알림이 반복되면 사람이 무시한다
      const sev = days >= 5 ? '🚨 심각' : days >= 3 ? '⚠️ 지속' : '주의';
      const lastStr = last.executedAt.toISOString().slice(0, 10);
      const detail =
        `${days}영업일 연속 시그널 미생성 (마지막 성공 ${lastStr}). ` +
        `실패 로그: /admin 실패 탭 · 가장 흔한 원인은 수집 타임아웃 → 데이터 계약 게이트 중단`;

      this.logger.error(`[헬스체크] ${market} 파이프라인 ${days}영업일 연속 미실행`);
      await this.alert.send({
        type: 'error',
        title: `${sev} — ${market} 파이프라인 ${days}일 연속 미실행`,
        market,
        detail,
      });
    }
  }
}
