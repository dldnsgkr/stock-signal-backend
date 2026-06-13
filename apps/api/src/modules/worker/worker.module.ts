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
  SellSignalProcessor,
} from '../admin/worker.processor';
import { QueueMonitorService } from './queue-monitor.service';
import { AlertModule } from '../alert/alert.module';
import { SubscriptionModule } from '../subscriptions/subscription.module';

@Module({
  imports: [
    AlertModule,
    SubscriptionModule,
    BullModule.registerQueue(
      { name: 'collect-stock-list' },
      { name: 'collect-prices' },
      { name: 'collect-news' },
      { name: 'collect-financials' },
      { name: 'generate-recommendations' },
      { name: 'evaluate-recommendations' },
      { name: 'collect-macro' },
      { name: 'check-sell-signals' },
      {
        name: 'run-pipeline',
        settings: {
          lockDuration: 7200000,
          lockRenewTime: 300000,
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
    SellSignalProcessor,
    QueueMonitorService,
  ],
})
export class WorkerModule {}
