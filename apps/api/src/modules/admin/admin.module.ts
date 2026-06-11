import { Module } from '@nestjs/common';
import { BullModule } from '@nestjs/bull';
import { AdminController } from './admin.controller';
import { AdminService } from './admin.service';
import { WorkerProcessor, StockListProcessor, FinancialProcessor, NewsProcessor, RecommendationProcessor, EvaluationProcessor, MacroProcessor, PipelineProcessor } from './worker.processor';

@Module({
  imports: [
    BullModule.registerQueue(
      { name: 'collect-stock-list' },
      { name: 'collect-prices' },
      { name: 'collect-news' },
      { name: 'collect-financials' },
      { name: 'generate-recommendations' },
      { name: 'evaluate-recommendations' },
      { name: 'collect-macro' },
      {
        name: 'run-pipeline',
        settings: {
          lockDuration: 7200000,   // 2시간 lock (파이프라인이 최대 2시간 걸릴 수 있음)
          lockRenewTime: 300000,   // 5분마다 갱신
          stalledInterval: 300000, // 5분마다 stall 체크
          maxStalledCount: 0,      // stall 즉시 실패 (좀비 방지)
        },
      },
    ),
  ],
  controllers: [AdminController],
  providers: [AdminService, WorkerProcessor, StockListProcessor, FinancialProcessor, NewsProcessor, RecommendationProcessor, EvaluationProcessor, MacroProcessor, PipelineProcessor],
  exports: [AdminService],
})
export class AdminModule {}
