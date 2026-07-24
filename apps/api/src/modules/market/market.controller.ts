import { Controller, Get, Query } from '@nestjs/common';
import { ApiTags, ApiOperation, ApiQuery } from '@nestjs/swagger';
import { MarketService } from './market.service';

@ApiTags('market')
@Controller('market')
export class MarketController {
  constructor(private readonly marketService: MarketService) {}

  @Get('foreign-top-stocks')
  @ApiOperation({ summary: '외국인 순매수·순매도 상위 종목 (KRX)' })
  @ApiQuery({ name: 'market', required: false, enum: ['KOSPI', 'KOSDAQ'] })
  @ApiQuery({ name: 'date', required: false, description: 'YYYYMMDD' })
  @ApiQuery({ name: 'limit', required: false, description: '상위 종목 수 (기본 30)' })
  getForeignTopStocks(
    @Query('market') market = 'KOSPI',
    @Query('date') date?: string,
    @Query('limit') limit?: string,
  ) {
    return this.marketService.getForeignTopStocks(market, date, limit ? Number(limit) : 30);
  }

  @Get('flow-ranking')
  @ApiOperation({ summary: '기간 누적 투자자 수급 랭킹 (KR, investor_flow_daily 집계)' })
  @ApiQuery({ name: 'market', required: false, enum: ['ALL', 'KOSPI', 'KOSDAQ'] })
  @ApiQuery({ name: 'investor', required: false, enum: ['foreign', 'institution', 'individual'] })
  @ApiQuery({ name: 'days', required: false, description: '최근 N거래일 (기본 20)' })
  @ApiQuery({ name: 'limit', required: false, description: '상위·하위 종목 수 (기본 20)' })
  getFlowRanking(
    @Query('market') market = 'ALL',
    @Query('investor') investor = 'foreign',
    @Query('days') days?: string,
    @Query('limit') limit?: string,
  ) {
    return this.marketService.getFlowRanking(
      market,
      ['institution', 'individual'].includes(investor) ? investor : 'foreign',
      days ? Math.min(Number(days), 120) : 20,
      limit ? Math.min(Number(limit), 50) : 20,
    );
  }

  @Get('investor-trading')
  @ApiOperation({ summary: '투자자별 매매동향 (KRX pykrx)' })
  @ApiQuery({ name: 'market', required: false, enum: ['KOSPI', 'KOSDAQ'] })
  @ApiQuery({ name: 'fromdate', required: false, description: 'YYYYMMDD' })
  @ApiQuery({ name: 'todate', required: false, description: 'YYYYMMDD' })
  getInvestorTrading(
    @Query('market') market = 'KOSPI',
    @Query('fromdate') fromdate?: string,
    @Query('todate') todate?: string,
  ) {
    return this.marketService.getInvestorTrading(market, fromdate, todate);
  }

  @Get('investor-top-stocks')
  @ApiOperation({ summary: '투자자 유형별 순매수·순매도 상위 종목 + 현재가' })
  @ApiQuery({ name: 'market', required: false, enum: ['KOSPI', 'KOSDAQ'] })
  @ApiQuery({ name: 'date', required: false, description: 'YYYYMMDD' })
  @ApiQuery({ name: 'investor_type', required: false, enum: ['institution', 'foreign', 'individual'] })
  @ApiQuery({ name: 'limit', required: false })
  getInvestorTopStocks(
    @Query('market') market = 'KOSPI',
    @Query('date') date?: string,
    @Query('investor_type') investorType = 'institution',
    @Query('limit') limit?: string,
  ) {
    return this.marketService.getInvestorTopStocks(market, date, investorType, limit ? Number(limit) : 20);
  }
}
