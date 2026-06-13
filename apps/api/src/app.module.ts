import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { ScheduleModule } from '@nestjs/schedule';
import { BullModule } from '@nestjs/bull';
import { PrismaModule } from './prisma/prisma.module';
import { StocksModule } from './modules/stocks/stocks.module';
import { RecommendationsModule } from './modules/recommendations/recommendations.module';
import { PerformanceModule } from './modules/performance/performance.module';
import { AdminModule } from './modules/admin/admin.module';
import { SchedulerModule } from './modules/scheduler/scheduler.module';
import { HealthModule } from './modules/health/health.module';
import { AlertModule } from './modules/alert/alert.module';
import { SubscriptionModule } from './modules/subscriptions/subscription.module';
import { MarketModule } from './modules/market/market.module';

@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true }),
    ScheduleModule.forRoot(),
    BullModule.forRootAsync({
      inject: [ConfigService],
      useFactory: (config: ConfigService) => ({
        redis: config.get('REDIS_URL', 'redis://localhost:6379'),
        settings: {
          stalledInterval: 60000,  // 60초마다 stalled job 체크
          maxStalledCount: 2,      // stalled 2회까지 허용 후 failed
          lockDuration: 300000,    // lock 유효기간 5분 (기본 30초 → 장시간 배치 작업 보호)
          lockRenewTime: 120000,   // 2분마다 lock 갱신
        },
        defaultJobOptions: {
          removeOnComplete: 50,   // 완료된 Job은 최근 50개만 보관
          removeOnFail: 20,       // 실패한 Job은 최근 20개만 보관
        },
      }),
    }),
    PrismaModule,
    AlertModule,
    HealthModule,
    StocksModule,
    RecommendationsModule,
    PerformanceModule,
    AdminModule,
    SchedulerModule,
    SubscriptionModule,
    MarketModule,
  ],
})
export class AppModule {}
