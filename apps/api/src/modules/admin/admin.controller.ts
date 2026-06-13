import { Controller, Get, Post, Param, Query, Body } from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { AdminService } from './admin.service';

@ApiTags('admin')
@Controller('admin')
export class AdminController {
  constructor(private readonly adminService: AdminService) {}

  @Post('jobs/collect-stock-list')
  @ApiOperation({ summary: '종목 목록 동기화' })
  triggerCollectStockList(@Query('market') market = 'US') {
    return this.adminService.triggerCollectStockList(market);
  }

  @Post('jobs/collect-prices')
  @ApiOperation({ summary: '가격 데이터 수집 실행' })
  triggerCollectPrices(@Query('market') market = 'US') {
    return this.adminService.triggerCollectPrices(market);
  }

  @Post('jobs/collect-news')
  @ApiOperation({ summary: '뉴스 수집 실행' })
  triggerCollectNews(@Query('market') market = 'US') {
    return this.adminService.triggerCollectNews(market);
  }

  @Post('jobs/collect-financials')
  @ApiOperation({ summary: '재무지표 수집 실행' })
  triggerCollectFinancials(@Query('market') market = 'US') {
    return this.adminService.triggerCollectFinancials(market);
  }

  @Post('jobs/run-pipeline')
  @ApiOperation({ summary: '전체 순차 실행' })
  triggerRunPipeline(@Query('market') market = 'US') {
    return this.adminService.triggerRunPipeline(market);
  }

  @Post('jobs/collect-macro')
  @ApiOperation({ summary: '거시지표 수집 실행' })
  triggerCollectMacro(@Query('market') market = 'US') {
    return this.adminService.triggerCollectMacro(market);
  }

  @Post('jobs/generate-recommendations')
  @ApiOperation({ summary: '추천 생성 실행' })
  triggerGenerateRecommendations(@Query('market') market = 'US') {
    return this.adminService.triggerGenerateRecommendations(market);
  }

  @Post('jobs/evaluate-recommendations')
  @ApiOperation({ summary: '추천 성과 평가 실행' })
  triggerEvaluateRecommendations() {
    return this.adminService.triggerEvaluateRecommendations();
  }

  @Get('jobs/failures')
  @ApiOperation({ summary: '최근 실패 job 목록' })
  getRecentFailures(@Query('limit') limit = 30) {
    return this.adminService.getRecentFailures(+limit);
  }

  @Get('jobs/:queue/:jobId')
  @ApiOperation({ summary: '작업 상태 조회' })
  getJobStatus(@Param('queue') queue: string, @Param('jobId') jobId: string) {
    return this.adminService.getJobStatus(queue, jobId);
  }

  @Get('runs')
  @ApiOperation({ summary: '최근 추천 실행 이력' })
  getRecentRuns(@Query('limit') limit = 50) {
    return this.adminService.getRecentRunsDetailed(+limit);
  }

  @Get('logs')
  @ApiOperation({ summary: '서버 로그 조회' })
  getLogs(@Query('service') service = 'api', @Query('lines') lines = 200) {
    return this.adminService.getLogs(service, +lines);
  }

  @Get('system')
  @ApiOperation({ summary: '시스템 상태 조회' })
  getSystemStatus() {
    return this.adminService.getSystemStatus();
  }

  @Get('model-versions')
  @ApiOperation({ summary: '모델 버전 목록' })
  getModelVersions() {
    return this.adminService.getModelVersions();
  }

  @Post('model-versions')
  @ApiOperation({ summary: '모델 버전 생성' })
  createModelVersion(
    @Body() body: { versionName: string; strategyType: string; config: Record<string, unknown> },
  ) {
    return this.adminService.createModelVersion(body);
  }

  @Post('model-versions/:id/activate')
  @ApiOperation({ summary: '모델 버전 활성화' })
  activateModelVersion(@Param('id') id: string) {
    return this.adminService.activateModelVersion(+id);
  }
}
