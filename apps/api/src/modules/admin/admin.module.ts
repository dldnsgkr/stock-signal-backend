import { Module } from '@nestjs/common';
import { BullModule } from '@nestjs/bull';
import { AdminController } from './admin.controller';
import { AdminService } from './admin.service';

// Processors는 worker 프로세스(main.worker.ts)에서만 등록
// 여기서는 job을 큐에 넣기 위한 InjectQueue 선언만 유지
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
      { name: 'collect-investor-flow' },
      { name: 'check-sell-signals' },
      { name: 'run-pipeline' },
    ),
  ],
  controllers: [AdminController],
  providers: [AdminService],
  exports: [AdminService],
})
export class AdminModule {}
