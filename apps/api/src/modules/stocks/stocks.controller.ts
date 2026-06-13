import { Controller, Get, Param, Query } from '@nestjs/common';
import { ApiTags, ApiOperation, ApiQuery } from '@nestjs/swagger';
import { StocksService } from './stocks.service';

@ApiTags('stocks')
@Controller('stocks')
export class StocksController {
  constructor(private readonly stocksService: StocksService) {}

  @Get()
  @ApiOperation({ summary: '종목 목록 조회 (cursor 기반 무한스크롤)' })
  @ApiQuery({ name: 'market', required: false, enum: ['US', 'KR'] })
  @ApiQuery({ name: 'cursorId', required: false, type: Number })
  @ApiQuery({ name: 'pageSize', required: false, type: Number })
  @ApiQuery({ name: 'search', required: false, type: String })
  findAll(
    @Query('market') market?: string,
    @Query('cursorId') cursorId?: string,
    @Query('pageSize') pageSize = 50,
    @Query('search') search?: string,
  ) {
    return this.stocksService.findAll(market, cursorId ? +cursorId : undefined, +pageSize, search);
  }

  @Get(':symbol')
  @ApiOperation({ summary: '종목 상세 조회' })
  findOne(@Param('symbol') symbol: string) {
    return this.stocksService.findBySymbol(symbol);
  }

  @Get(':symbol/prices')
  @ApiOperation({ summary: '종목 일봉 가격 조회' })
  @ApiQuery({ name: 'days', required: false, type: Number })
  getPrices(@Param('symbol') symbol: string, @Query('days') days = 90) {
    return this.stocksService.getPrices(symbol, +days);
  }

  @Get(':symbol/news')
  @ApiOperation({ summary: '종목 관련 뉴스 조회' })
  getNews(@Param('symbol') symbol: string, @Query('limit') limit = 20) {
    return this.stocksService.getNews(symbol, +limit);
  }

  @Get(':symbol/financials')
  @ApiOperation({ summary: '종목 재무 지표 조회' })
  getFinancials(@Param('symbol') symbol: string) {
    return this.stocksService.getFinancials(symbol);
  }

  @Get(':symbol/recommendations')
  @ApiOperation({ summary: '종목 BUY 추천 이력 (SELL 시그널 포함)' })
  @ApiQuery({ name: 'limit', required: false, type: Number })
  getRecommendations(@Param('symbol') symbol: string, @Query('limit') limit = 10) {
    return this.stocksService.getRecommendations(symbol, +limit);
  }
}
