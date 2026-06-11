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
}
