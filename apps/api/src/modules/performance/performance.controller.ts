import { Controller, Get, Query } from '@nestjs/common';
import { ApiTags, ApiOperation, ApiQuery } from '@nestjs/swagger';
import { PerformanceService } from './performance.service';

@ApiTags('performance')
@Controller('performance')
export class PerformanceController {
  constructor(private readonly performanceService: PerformanceService) {}

  @Get('overview')
  @ApiOperation({ summary: '성과 요약 조회' })
  @ApiQuery({ name: 'market', required: false, enum: ['US', 'KR'] })
  @ApiQuery({ name: 'period', required: false, enum: ['7d', '30d', '90d'] })
  getOverview(
    @Query('market') market = 'US',
    @Query('period') period: '7d' | '30d' | '90d' = '30d',
  ) {
    return this.performanceService.getOverview(market, period);
  }

  @Get('model-versions')
  @ApiOperation({ summary: '모델 버전별 성과 비교' })
  getModelVersions() {
    return this.performanceService.getModelVersionComparison();
  }

  @Get('timeline')
  @ApiOperation({ summary: '주별 평균 수익률 추이' })
  @ApiQuery({ name: 'market', required: false, enum: ['US', 'KR'] })
  @ApiQuery({ name: 'period', required: false, enum: ['30d', '90d', '180d'] })
  getTimeline(
    @Query('market') market = 'US',
    @Query('period') period: '30d' | '90d' | '180d' = '90d',
  ) {
    return this.performanceService.getTimeline(market, period);
  }

  @Get('by-sector')
  @ApiOperation({ summary: '섹터별 성과 분석' })
  @ApiQuery({ name: 'market', required: false, enum: ['US', 'KR'] })
  @ApiQuery({ name: 'period', required: false, enum: ['7d', '30d', '90d'] })
  getBySector(
    @Query('market') market = 'US',
    @Query('period') period: '7d' | '30d' | '90d' = '30d',
  ) {
    return this.performanceService.getBySector(market, period);
  }

  @Get('recommendations')
  @ApiOperation({ summary: '개별 추천 성과 목록' })
  @ApiQuery({ name: 'market', required: false, enum: ['US', 'KR'] })
  @ApiQuery({ name: 'period', required: false, enum: ['7d', '30d', '90d'] })
  @ApiQuery({ name: 'limit', required: false })
  getRecommendations(
    @Query('market') market = 'US',
    @Query('period') period: '7d' | '30d' | '90d' = '30d',
    @Query('limit') limit = 100,
  ) {
    return this.performanceService.getRecommendationsWithResults(market, period, +limit);
  }
}
