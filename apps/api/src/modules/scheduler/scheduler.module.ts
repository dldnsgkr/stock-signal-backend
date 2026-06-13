import { Module } from '@nestjs/common';
import { SchedulerService } from './scheduler.service';
import { AdminModule } from '../admin/admin.module';
import { AlertModule } from '../alert/alert.module';
import { PrismaModule } from '../../prisma/prisma.module';

@Module({
  imports: [AdminModule, AlertModule, PrismaModule],
  providers: [SchedulerService],
})
export class SchedulerModule {}
