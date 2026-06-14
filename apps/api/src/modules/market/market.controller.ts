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
}
