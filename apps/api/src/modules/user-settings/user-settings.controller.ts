import { Controller, Get, Put, Body, Query } from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { UserSettingsService, UserSettingsDto } from './user-settings.service';

@ApiTags('user-settings')
@Controller('user-settings')
export class UserSettingsController {
  constructor(private readonly service: UserSettingsService) {}

  @Get()
  @ApiOperation({ summary: '유저 개인 설정 조회 (없으면 기본값)' })
  get(@Query('userId') userId: string) {
    return this.service.get(Number(userId));
  }

  @Put()
  @ApiOperation({ summary: '유저 개인 설정 저장 (부분 업데이트)' })
  update(@Body() body: { userId: number } & Partial<UserSettingsDto>) {
    const { userId, ...patch } = body;
    return this.service.update(Number(userId), patch);
  }
}
