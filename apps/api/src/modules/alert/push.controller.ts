import { Controller, Get, Post, Body, BadRequestException } from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { PushService } from './push.service';

interface SubscribeBody {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

@ApiTags('push')
@Controller('push')
export class PushController {
  constructor(private readonly push: PushService) {}

  @Get('public-key')
  @ApiOperation({ summary: 'VAPID 공개키 조회 (미설정 시 null)' })
  getPublicKey() {
    return { publicKey: this.push.getPublicKey() };
  }

  @Post('subscribe')
  @ApiOperation({ summary: '브라우저 푸시 구독 등록' })
  subscribe(@Body() body: SubscribeBody) {
    if (!body?.endpoint || !body?.keys?.p256dh || !body?.keys?.auth) {
      throw new BadRequestException('endpoint, keys.p256dh, keys.auth required');
    }
    return this.push.subscribe(body.endpoint, body.keys.p256dh, body.keys.auth);
  }

  @Post('unsubscribe')
  @ApiOperation({ summary: '브라우저 푸시 구독 해지' })
  unsubscribe(@Body() body: { endpoint: string }) {
    if (!body?.endpoint) throw new BadRequestException('endpoint required');
    return this.push.unsubscribe(body.endpoint);
  }
}
