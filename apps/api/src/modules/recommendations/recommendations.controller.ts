import { Controller, Get, Param, Query } from '@nestjs/common';
import { ApiTags, ApiOperation, ApiQuery } from '@nestjs/swagger';
import { RecommendationsService } from './recommendations.service';

@ApiTags('recommendations')
@Controller('recommendations')
export class RecommendationsController {
  constructor(private readonly recommendationsService: RecommendationsService) {}

  @Get('latest')
  @ApiOperation({ summary: '최신 추천 목록 조회' })
  @ApiQuery({ name: 'market', required: false, enum: ['US', 'KR'] })
  @ApiQuery({ name: 'action', required: false, enum: ['BUY', 'WATCH', 'AVOID'] })
  getLatest(
    @Query('market') market = 'US',
    @Query('action') action?: string,
    @Query('page') page = 1,
    @Query('pageSize') pageSize = 20,
  ) {
    return this.recommendationsService.getLatest(market, action, +page, +pageSize);
  }

  @Get('history')
  @ApiOperation({ summary: '추천 이력 조회' })
  getHistory(
    @Query('market') market = 'US',
    @Query('days') days = 30,
    @Query('page') page = 1,
    @Query('pageSize') pageSize = 50,
  ) {
    return this.recommendationsService.getHistory(market, +days, +page, +pageSize);
  }

  @Get('stock/:symbol')
  @ApiOperation({ summary: '종목별 추천 이력 조회' })
  getByStock(@Param('symbol') symbol: string, @Query('limit') limit = 10) {
    return this.recommendationsService.getByStock(symbol, +limit);
  }
}
