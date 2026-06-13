import { Module, Global } from '@nestjs/common';
import { AlertService } from './alert.service';
import { EmailService } from './email.service';

@Global()
@Module({
  providers: [AlertService, EmailService],
  exports: [AlertService, EmailService],
})
export class AlertModule {}
