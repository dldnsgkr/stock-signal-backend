import { Controller, Get, Post, Delete, Body, Query } from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { WatchlistService } from './watchlist.service';

@ApiTags('watchlist')
@Controller('watchlist')
export class WatchlistController {
  constructor(private readonly service: WatchlistService) {}

  @Get()
  @ApiOperation({ summary: '유저 관심종목 목록 (최신 시그널·가격 포함)' })
  list(@Query('userId') userId: string) {
    return this.service.list(Number(userId));
  }

  @Post()
  @ApiOperation({ summary: '관심종목 추가' })
  add(@Body() body: { userId: number; symbol: string }) {
    return this.service.add(Number(body.userId), body.symbol);
  }

  @Delete()
  @ApiOperation({ summary: '관심종목 제거' })
  remove(@Body() body: { userId: number; symbol: string }) {
    return this.service.remove(Number(body.userId), body.symbol);
  }
}
