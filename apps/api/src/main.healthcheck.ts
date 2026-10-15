// 일일 헬스체크 원샷 실행기 — GitHub Actions(healthcheck.yml) 용.
//
// EC2 시절엔 API 프로세스의 @Cron(SchedulerService.runDailyHealthCheck)이 돌았다.
// 서버리스에는 상시 프로세스가 없으므로, 같은 점검을 한 번 실행하고 종료하는 진입점을 둔다.
// Bull/Redis 가 필요 없도록 AdminService 대신 필요한 조회를 직접 한다(같은 임계값).
import { Injectable, Logger, Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { NestFactory } from '@nestjs/core';
import { PrismaModule } from './prisma/prisma.module';
import { PrismaService } from './prisma/prisma.service';
import { AlertModule } from './modules/alert/alert.module';
import { AlertService } from './modules/alert/alert.service';

// scheduler.service.ts 와 같은 값 — 바꾸면 둘 다 바꿀 것(컷오버 후엔 이쪽이 원본)
const SIGNAL_DANGER_HOURS = 72;
const PRICE_DANGER_DAYS = 5;
const STREAK_ALERT_DAYS = 2;

/** 두 날짜 사이의 영업일 수(주말 제외). 시작일 다음날부터 끝일까지 센다. */
function businessDaysBetween(from: Date, to: Date): number {
  let n = 0;
  const d = new Date(from);
  d.setUTCHours(0, 0, 0, 0);
  const end = new Date(to);
  end.setUTCHours(0, 0, 0, 0);
  while (d < end) {
    d.setUTCDate(d.getUTCDate() + 1);
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) n++;
  }
  return n;
}

@Injectable()
class HealthCheckRunner {
  private readonly logger = new Logger('HealthCheck');

  constructor(
    private readonly prisma: PrismaService,
    private readonly alert: AlertService,
  ) {}

  async run(): Promise<number> {
    const now = new Date();
    let alerts = 0;

    for (const market of ['US', 'KR']) {
      const lastRun = await this.prisma.recommendationRun.findFirst({
        where: { marketCode: market },
        orderBy: { executedAt: 'desc' },
        select: { executedAt: true },
      });

      // 1) 연속 미실행 — 영업일 수를 제목에 박고 길수록 문구를 세게(같은 알림 반복은 무시된다)
      if (!lastRun) {
        await this.alert.send({ type: 'error', title: '파이프라인 성공 이력 없음', market, detail: '추천 런이 한 번도 기록되지 않았습니다' });
        alerts++;
      } else {
        const days = businessDaysBetween(lastRun.executedAt, now);
        if (days >= STREAK_ALERT_DAYS) {
          const sev = days >= 5 ? '🚨 심각' : days >= 3 ? '⚠️ 지속' : '주의';
          await this.alert.send({
            type: 'error',
            title: `${sev} — ${market} 파이프라인 ${days}일 연속 미실행`,
            market,
            detail: `${days}영업일 연속 시그널 미생성 (마지막 성공 ${lastRun.executedAt.toISOString().slice(0, 10)}). `
              + `GitHub Actions 의 pipeline 워크플로 실패 로그를 확인하세요.`,
          });
          alerts++;
        } else {
          const ageH = Math.round((now.getTime() - lastRun.executedAt.getTime()) / 3600000);
          if (ageH > SIGNAL_DANGER_HOURS) {
            await this.alert.send({ type: 'error', title: '시그널 미갱신', market, detail: `${ageH}시간 미업데이트 — 파이프라인 확인 필요` });
            alerts++;
          }
          this.logger.log(`${market} 마지막 런 ${ageH}시간 전`);
        }
      }

      // 2) 가격 신선도
      type Row = { last_date: Date | null };
      const [p] = await this.prisma.$queryRaw<Row[]>`
        SELECT MAX(pd.date) AS last_date FROM price_daily pd
        JOIN stocks s ON s.id = pd.stock_id JOIN markets m ON m.id = s.market_id
        WHERE m.code = ${market}
      `;
      if (p?.last_date) {
        const ageDays = Math.round((now.getTime() - new Date(p.last_date).getTime()) / 86400000);
        if (ageDays > PRICE_DANGER_DAYS) {
          await this.alert.send({ type: 'error', title: '가격 데이터 미수집', market, detail: `${ageDays}일째 수집 안 됨` });
          alerts++;
        }
        this.logger.log(`${market} 최신 가격일 ${new Date(p.last_date).toISOString().slice(0, 10)} (${ageDays}일 전)`);
      }
    }

    // 3) Neon 무료 한도 — 512MB 를 넘으면 쓰기가 막혀 파이프라인이 통째로 멈춘다
    type SizeRow = { bytes: bigint };
    const [sz] = await this.prisma.$queryRaw<SizeRow[]>`SELECT pg_database_size(current_database()) AS bytes`;
    const mb = Number(sz.bytes) / 1024 / 1024;
    this.logger.log(`DB 용량 ${mb.toFixed(0)}MB / 512MB`);
    if (mb > 480) {
      await this.alert.send({
        type: 'warning',
        title: 'DB 용량 한도 임박',
        detail: `Neon ${mb.toFixed(0)}MB / 512MB — neon-retention.sql 의 보존 창을 줄일 것(추천 90→75일 등)`,
      });
      alerts++;
    }

    this.logger.log(alerts === 0 ? '모든 점검 정상' : `경고 ${alerts}건 발송`);
    return alerts;
  }
}

@Module({
  imports: [ConfigModule.forRoot({ isGlobal: true }), PrismaModule, AlertModule],
  providers: [HealthCheckRunner],
})
class HealthCheckModule {}

async function main() {
  const app = await NestFactory.createApplicationContext(HealthCheckModule, { logger: ['log', 'warn', 'error'] });
  try {
    await app.get(HealthCheckRunner).run();
  } finally {
    await app.close();
  }
}

main().catch((e) => {
  console.error('[HealthCheck] 실행 오류:', e);
  process.exit(1);
});
