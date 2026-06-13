import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import axios from 'axios';

@Injectable()
export class MarketService {
  private readonly logger = new Logger(MarketService.name);

  constructor(private readonly config: ConfigService) {}

  async getInvestorTrading(market: string, fromdate?: string, todate?: string) {
    const baseUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    const params = new URLSearchParams({ market: market.toUpperCase() });
    if (fromdate) params.set('fromdate', fromdate);
    if (todate) params.set('todate', todate);

    try {
      const res = await axios.get(
        `${baseUrl}/analysis/investor-trading?${params}`,
        { timeout: 60000 },
      );
      return res.data;
    } catch (err: any) {
      this.logger.error(`investor-trading failed: ${err.message}`);
      throw err;
    }
  }
}
