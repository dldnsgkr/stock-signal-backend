import { Controller, Get, Query } from '@nestjs/common';
import { ApiTags, ApiOperation, ApiQuery } from '@nestjs/swagger';
import { MarketService } from './market.service';

@ApiTags('market')
@Controller('market')
export class MarketController {
  constructor(private readonly marketService: MarketService) {}

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
