import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { BullModule } from '@nestjs/bull';
import { PrismaModule } from './prisma/prisma.module';
import { AlertModule } from './modules/alert/alert.module';
import { WorkerModule } from './modules/worker/worker.module';

@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true }),
    BullModule.forRootAsync({
      inject: [ConfigService],
      useFactory: (config: ConfigService) => ({
        redis: config.get('REDIS_URL', 'redis://localhost:6379'),
        settings: {
          stalledInterval: 60000,
          maxStalledCount: 2,
          lockDuration: 300000,
          lockRenewTime: 120000,
        },
        defaultJobOptions: {
          removeOnComplete: 50,
          removeOnFail: 20,
        },
      }),
    }),
    PrismaModule,
    AlertModule,
    WorkerModule,
  ],
})
export class WorkerAppModule {}
