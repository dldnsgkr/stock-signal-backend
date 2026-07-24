import { Module, Global } from '@nestjs/common';
import { AlertService } from './alert.service';
import { EmailService } from './email.service';
import { PushService } from './push.service';
import { PushController } from './push.controller';

@Global()
@Module({
  controllers: [PushController],
  providers: [AlertService, EmailService, PushService],
  exports: [AlertService, EmailService, PushService],
})
export class AlertModule {}
