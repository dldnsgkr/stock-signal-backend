import { Controller, Get, Query } from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { NotificationsService } from './notifications.service';

@ApiTags('notifications')
@Controller('notifications')
export class NotificationsController {
  constructor(private readonly service: NotificationsService) {}

  @Get()
  @ApiOperation({ summary: '유저 알림 이력 (본인 타겟 + 시장 전체)' })
  list(@Query('userId') userId: string, @Query('limit') limit?: string) {
    return this.service.listForUser(Number(userId), limit ? Number(limit) : 50);
  }
}
