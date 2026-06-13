import { Module } from '@nestjs/common';
import { BullModule } from '@nestjs/bull';
import {
  WorkerProcessor,
  StockListProcessor,
  FinancialProcessor,
  NewsProcessor,
  RecommendationProcessor,
  EvaluationProcessor,
  MacroProcessor,
  PipelineProcessor,
} from '../admin/worker.processor';

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
          lockDuration: 7200000,   // 2시간 lock
          lockRenewTime: 300000,   // 5분마다 갱신
          stalledInterval: 300000,
          maxStalledCount: 0,
        },
      },
    ),
  ],
  providers: [
    WorkerProcessor,
    StockListProcessor,
    FinancialProcessor,
    NewsProcessor,
    RecommendationProcessor,
    EvaluationProcessor,
    MacroProcessor,
    PipelineProcessor,
  ],
})
export class WorkerModule {}
