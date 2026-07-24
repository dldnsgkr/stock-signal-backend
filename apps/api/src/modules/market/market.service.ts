import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import axios from 'axios';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../../prisma/prisma.service';

type FlowRankRow = {
  stock_id: number;
  symbol: string;
  name: string;
  sector: string | null;
  total_net: bigint;
  active_days: bigint;
};

type PriceChangeRow = {
  stock_id: number;
  close: string;
  prev_close: string | null;
};

@Injectable()
export class MarketService {
  private readonly logger = new Logger(MarketService.name);

  constructor(
    private readonly config: ConfigService,
    private readonly prisma: PrismaService,
  ) {}

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

  async getInvestorTopStocks(market: string, date?: string, investorType = 'institution', limit = 20) {
    const baseUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    const params = new URLSearchParams({
      market: market.toUpperCase(),
      investor_type: investorType,
      limit: String(limit),
    });
    if (date) params.set('date', date);
    try {
      const res = await axios.get(
        `${baseUrl}/analysis/investor-top-stocks?${params}`,
        { timeout: 60000, validateStatus: () => true },
      );
      return res.data;
    } catch (err: any) {
      this.logger.error(`investor-top-stocks failed: ${err.message}`);
      return { error: err.message };
    }
  }

  // investor_flow_daily 기반 기간 누적 수급 랭킹 (KR 전용).
  // 기존 investor-top-stocks(당일, KRX 라이브)와 달리 최근 N거래일 누적을 DB에서 집계한다.
  async getFlowRanking(market = 'ALL', investor = 'foreign', days = 20, limit = 20) {
    // KOSPI=.KS / KOSDAQ=.KQ 심볼 접미사로 구분. ALL 은 전체.
    const suffix = market === 'KOSPI' ? '.KS' : market === 'KOSDAQ' ? '.KQ' : null;
    const marketFilter = suffix
      ? Prisma.sql`AND s.symbol LIKE ${'%' + suffix}`
      : Prisma.empty;

    const rankRows = await this.prisma.$queryRaw<FlowRankRow[]>`
      WITH recent_dates AS (
        SELECT DISTINCT trade_date FROM investor_flow_daily
        ORDER BY trade_date DESC LIMIT ${days}
      )
      SELECT s.id AS stock_id, s.symbol, s.name, s.sector,
             SUM(f.net_buy_value)::bigint AS total_net,
             COUNT(*)::bigint             AS active_days
      FROM investor_flow_daily f
      JOIN recent_dates d ON d.trade_date = f.trade_date
      JOIN stocks s ON s.id = f.stock_id
      WHERE f.investor_type = ${investor}
        AND s.is_active = true
        ${marketFilter}
      GROUP BY s.id, s.symbol, s.name, s.sector
      ORDER BY total_net DESC
    `;

    if (rankRows.length === 0) return { market, investor, days, top: [], bottom: [] };

    const top = rankRows.slice(0, limit);
    const bottom = rankRows.slice(-limit).reverse(); // 순매도 큰 순
    const ids = [...new Set([...top, ...bottom].map((r: FlowRankRow) => r.stock_id))];

    // 최신 종가·전일 대비 등락률
    const priceRows = await this.prisma.$queryRaw<PriceChangeRow[]>`
      SELECT stock_id, close, prev_close FROM (
        SELECT stock_id, close,
               LAG(close) OVER (PARTITION BY stock_id ORDER BY date) AS prev_close,
               ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
        FROM price_daily
        WHERE stock_id = ANY(${ids})
      ) t WHERE rn = 1
    `;
    const priceMap = new Map(
      priceRows.map((p: PriceChangeRow) => [
        p.stock_id,
        {
          currentPrice: Number(p.close),
          changeRate:
            p.prev_close && Number(p.prev_close) !== 0
              ? ((Number(p.close) - Number(p.prev_close)) / Number(p.prev_close)) * 100
              : null,
        },
      ]),
    );

    const serialize = (r: FlowRankRow) => ({
      symbol: r.symbol,
      name: r.name,
      sector: r.sector,
      totalNet: Number(r.total_net),
      activeDays: Number(r.active_days),
      ...(priceMap.get(r.stock_id) ?? { currentPrice: null, changeRate: null }),
    });

    return {
      market,
      investor,
      days,
      top: top.map(serialize),
      bottom: bottom.map(serialize),
    };
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
