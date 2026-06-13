import { Controller, Post, Delete, Get, Body, Query } from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { SubscriptionService } from './subscription.service';

@ApiTags('subscriptions')
@Controller('subscriptions')
export class SubscriptionController {
  constructor(private readonly service: SubscriptionService) {}

  @Post()
  @ApiOperation({ summary: '종목 BUY 시그널 알림 구독' })
  subscribe(@Body() body: { email: string; symbol: string }) {
    return this.service.subscribe(body.email, body.symbol);
  }

  @Delete()
  @ApiOperation({ summary: '알림 구독 해제' })
  unsubscribe(@Body() body: { email: string; symbol: string }) {
    return this.service.unsubscribe(body.email, body.symbol);
  }

  @Get()
  @ApiOperation({ summary: '이메일별 구독 목록 조회' })
  listByEmail(@Query('email') email: string) {
    return this.service.listByEmail(email);
  }
}
