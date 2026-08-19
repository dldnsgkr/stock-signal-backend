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

  // ⚠️ 'band-stats' 는 파라미터 라우트보다 **앞**에 둔다.
  // (@Get('stock/:symbol') 류가 먼저 오면 경로가 그쪽으로 먹힌다 — 이 프로젝트에서
  //  jobs/failures 가 :queue 로 잡혔던 것과 같은 부류)
  @Get('band-stats')
  @ApiOperation({ summary: '점수 밴드별 과거 성과 (최근 90일)' })
  getBandStats(@Query('market') market = 'US') {
    return this.recommendationsService.getBandStats(market);
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

  @Get('sell-signals')
  @ApiOperation({ summary: '최근 SELL 시그널 목록' })
  @ApiQuery({ name: 'market', required: false, enum: ['US', 'KR'] })
  @ApiQuery({ name: 'limit', required: false, type: Number })
  getSellSignals(
    @Query('market') market = 'US',
    @Query('limit') limit = 20,
  ) {
    return this.recommendationsService.getSellSignals(market, +limit);
  }

  @Get('stock/:symbol')
  @ApiOperation({ summary: '종목별 추천 이력 조회' })
  getByStock(@Param('symbol') symbol: string, @Query('limit') limit = 10) {
    return this.recommendationsService.getByStock(symbol, +limit);
  }
}
