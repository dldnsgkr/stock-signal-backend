import { Module } from '@nestjs/common';
import { SchedulerService } from './scheduler.service';
import { AdminModule } from '../admin/admin.module';

@Module({
  imports: [AdminModule],
  providers: [SchedulerService],
})
export class SchedulerModule {}
