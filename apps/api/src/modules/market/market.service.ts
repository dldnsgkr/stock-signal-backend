import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import axios from 'axios';

@Injectable()
export class MarketService {
  private readonly logger = new Logger(MarketService.name);

  constructor(private readonly config: ConfigService) {}

  async getForeignTopStocks(market: string, date?: string, limit = 30) {
    const baseUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    const params = new URLSearchParams({ market: market.toUpperCase(), limit: String(limit) });
    if (date) params.set('date', date);
    try {
      const res = await axios.get(
        `${baseUrl}/analysis/foreign-top-stocks?${params}`,
        { timeout: 60000, validateStatus: () => true },
      );
      return res.data;
    } catch (err: any) {
      this.logger.error(`foreign-top-stocks failed: ${err.message}`);
      return { error: err.message };
    }
  }

  async getInvestorTrading(market: string, fromdate?: string, todate?: string) {
    const baseUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    const params = new URLSearchParams({ market: market.toUpperCase() });
    if (fromdate) params.set('fromdate', fromdate);
    if (todate) params.set('todate', todate);

    try {
      const res = await axios.get(
        `${baseUrl}/analysis/investor-trading?${params}`,
        { timeout: 60000, validateStatus: () => true },  // 모든 상태코드 허용, 에러 전달
      );
      return res.data;
    } catch (err: any) {
      this.logger.error(`investor-trading failed: ${err.message}`);
      return { error: err.message };
    }
  }
}
