import { Injectable, NotFoundException, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import axios from 'axios';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class StocksService {
  private readonly logger = new Logger(StocksService.name);
  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
  ) {}

  async findAll(market?: string, cursorId?: number, pageSize = 50, search?: string) {
    const where: any = { isActive: true };
    if (market) where.market = { code: market };
    if (search) {
      where.OR = [
        { symbol: { contains: search, mode: 'insensitive' } },
        { name: { contains: search, mode: 'insensitive' } },
      ];
    }

    const items = await this.prisma.stock.findMany({
      where,
      select: { id: true, symbol: true, name: true, sector: true, exchange: true, market: { select: { code: true } } },
      ...(cursorId ? { cursor: { id: cursorId }, skip: 1 } : {}),
      take: pageSize,
      orderBy: { id: 'asc' },
    });

    const nextCursor = items.length === pageSize ? items[items.length - 1].id : null;
    return { data: items, nextCursor, hasMore: nextCursor !== null };
  }

  async findBySymbol(symbol: string) {
    const stock = await this.prisma.stock.findFirst({
      where: { symbol: symbol.toUpperCase(), isActive: true },
      include: { market: true },
    });
    if (!stock) throw new NotFoundException(`Stock ${symbol} not found`);
    return stock;
  }

  async getPrices(symbol: string, days = 90) {
    const stock = await this.findBySymbol(symbol);
    const where: any = { stockId: stock.id };
    if (days > 0) {
      const since = new Date();
      since.setDate(since.getDate() - days);
      where.date = { gte: since };
    }
    return this.prisma.priceDaily.findMany({
      where,
      orderBy: { date: 'asc' },
    });
  }

  async getNews(symbol: string, limit = 20) {
    const stock = await this.findBySymbol(symbol);
    const relations = await this.prisma.newsStockRelation.findMany({
      where: { stockId: stock.id },
      include: { article: true },
      orderBy: { article: { publishedAt: 'desc' } },
      take: limit,
    });
    return relations.map((r: typeof relations[0]) => ({
      ...r.article,
      relevanceScore: r.relevanceScore,
    }));
  }

  async getFinancials(symbol: string) {
    const stock = await this.findBySymbol(symbol);
    return this.prisma.financialMetrics.findMany({
      where: { stockId: stock.id },
      orderBy: [{ periodType: 'asc' }, { periodEnd: 'desc' }],
      take: 8,
    });
  }

  async getRecommendations(symbol: string, limit = 10) {
    const stock = await this.findBySymbol(symbol);
    return this.prisma.recommendation.findMany({
      where: { stockId: stock.id, action: 'BUY' },
      orderBy: { recommendedAt: 'desc' },
      take: limit,
      include: {
        result: true,
        sellSignal: true,
      },
    });
  }

  async getScoreHistory(symbol: string, days = 90) {
    const stock = await this.findBySymbol(symbol);
    const since = new Date();
    since.setDate(since.getDate() - days);

    const recs = await this.prisma.recommendation.findMany({
      where: {
        stockId: stock.id,
        recommendedAt: { gte: since },
      },
      select: { score: true, action: true, recommendedAt: true },
      orderBy: { recommendedAt: 'asc' },
    });

    return recs.map(r => ({
      date: r.recommendedAt,
      score: Number(r.score),
      action: r.action,
    }));
  }

  // 종목 비교 (최대 4개) — 최신 시그널·점수구성·현재가·재무 스냅샷.
  async compare(symbols: string[]) {
    const uniq = [...new Set(symbols.map(s => s.trim().toUpperCase()).filter(Boolean))].slice(0, 4);
    const out = [];
    for (const sym of uniq) {
      const stock = await this.prisma.stock.findFirst({
        where: { symbol: sym },
        include: { market: true },
      });
      if (!stock) {
        out.push({ symbol: sym, notFound: true });
        continue;
      }

      const [latestRec, fin, prices] = await Promise.all([
        this.prisma.recommendation.findFirst({
          where: { stockId: stock.id },
          orderBy: { recommendedAt: 'desc' },
          include: { result: true },
        }),
        this.prisma.financialMetrics.findFirst({
          where: { stockId: stock.id },
          orderBy: { periodEnd: 'desc' },
        }),
        this.prisma.priceDaily.findMany({
          where: { stockId: stock.id },
          orderBy: { date: 'desc' },
          take: 2,
          select: { close: true },
        }),
      ]);

      const close = prices[0] ? Number(prices[0].close) : null;
      const prev = prices[1] ? Number(prices[1].close) : null;
      const sd = (latestRec?.scoreDetailJson ?? {}) as Record<string, number>;
      const num = (v: unknown) => (v == null ? null : Number(v));

      out.push({
        symbol: stock.symbol,
        name: stock.name,
        sector: stock.sector,
        market: stock.market?.code ?? 'US',
        currentPrice: close,
        changeRate: close != null && prev ? ((close - prev) / prev) * 100 : null,
        action: latestRec?.action ?? null,
        score: latestRec ? Number(latestRec.score) : null,
        confidence: latestRec?.confidence ?? null,
        recommendedAt: latestRec?.recommendedAt ?? null,
        scoreDetail: latestRec
          ? {
              momentum: sd.momentum_score ?? sd.technical_score ?? null,
              value: sd.value_score ?? sd.fundamental_score ?? null,
              sentiment: sd.sentiment_score ?? sd.news_score ?? null,
            }
          : null,
        result: latestRec?.result
          ? { return7d: num(latestRec.result.return7d), return30d: num(latestRec.result.return30d) }
          : null,
        fundamentals: fin
          ? { roe: num(fin.roe), per: num(fin.per), pbr: num(fin.pbr), debtRatio: num(fin.debtRatio) }
          : null,
      });
    }
    return out;
  }

  // KR 전용 — investor_flow_daily 는 KRX 데이터만 적재된다.
  async getInvestorFlow(symbol: string, days = 90) {
    const stock = await this.findBySymbol(symbol);
    const since = new Date();
    since.setDate(since.getDate() - days);

    const rows = await this.prisma.investorFlowDaily.findMany({
      where: { stockId: stock.id, tradeDate: { gte: since } },
      select: { tradeDate: true, investorType: true, netBuyValue: true },
      orderBy: { tradeDate: 'asc' },
    });

    // (date, investorType) 행을 날짜별 한 행으로 피벗
    const byDate = new Map<string, { date: string; foreign: number | null; institution: number | null }>();
    for (const r of rows) {
      const d = r.tradeDate.toISOString().slice(0, 10);
      const entry = byDate.get(d) ?? { date: d, foreign: null, institution: null };
      if (r.investorType === 'foreign') entry.foreign = Number(r.netBuyValue);
      if (r.investorType === 'institution') entry.institution = Number(r.netBuyValue);
      byDate.set(d, entry);
    }

    return { symbol: stock.symbol, market: stock.market.code, flows: [...byDate.values()] };
  }

  // AI 브리핑 — analysis 서비스 프록시 (LLM 호출·캐시는 그쪽에서)
  async getBriefing(symbol: string, market: string) {
    const baseUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    try {
      const res = await axios.get(
        `${baseUrl}/analysis/briefing?symbol=${encodeURIComponent(symbol.toUpperCase())}&market=${market.toUpperCase()}`,
        { timeout: 40000, validateStatus: () => true },
      );
      return res.data;
    } catch (err: any) {
      this.logger.error(`briefing failed for ${symbol}: ${err.message}`);
      return { error: 'briefing unavailable' };
    }
  }

  async getTechnicalLevels(symbol: string, market: string) {
    const baseUrl = this.config.get('ANALYSIS_SERVICE_URL', 'http://localhost:8000');
    try {
      const res = await axios.get(
        `${baseUrl}/analysis/technical-levels?symbol=${symbol.toUpperCase()}&market=${market.toUpperCase()}`,
        { timeout: 30000, validateStatus: () => true },
      );
      return res.data;
    } catch (err: any) {
      this.logger.error(`technical-levels failed for ${symbol}: ${err.message}`);
      return { error: err.message };
    }
  }

  async getSectorSummary(market: string) {
    const stocks = await this.prisma.stock.findMany({
      where: { market: { code: market }, isActive: true, sector: { not: null } },
      select: {
        symbol: true,
        name: true,
        sector: true,
        recommendations: {
          select: { action: true, score: true },
          orderBy: { recommendedAt: 'desc' },
          take: 1,
        },
      },
    });

    const map = new Map<string, {
      stocks: { symbol: string; name: string; score: number | null; action: string }[];
      counts: Record<string, number>;
      totalScore: number;
      scoredCount: number;
    }>();

    for (const s of stocks) {
      const sector = s.sector!;
      const rec = s.recommendations[0];
      if (!map.has(sector)) {
        map.set(sector, { stocks: [], counts: { BUY: 0, WATCH: 0, AVOID: 0 }, totalScore: 0, scoredCount: 0 });
      }
      const entry = map.get(sector)!;
      const score = rec ? Number(rec.score) : null;
      entry.stocks.push({ symbol: s.symbol, name: s.name, score, action: rec?.action ?? 'NONE' });
      if (rec) {
        entry.counts[rec.action] = (entry.counts[rec.action] ?? 0) + 1;
        entry.totalScore += Number(rec.score);
        entry.scoredCount++;
      }
    }

    return Array.from(map.entries())
      .map(([sector, e]) => ({
        sector,
        stockCount: e.stocks.length,
        signals: e.counts,
        avgScore: e.scoredCount > 0 ? Math.round((e.totalScore / e.scoredCount) * 10) / 10 : null,
        topStocks: e.stocks
          .filter(s => s.score != null)
          .sort((a, b) => b.score! - a.score!)
          .slice(0, 3),
      }))
      .sort((a, b) => (b.signals.BUY ?? 0) - (a.signals.BUY ?? 0));
  }

  async getMarketSummary(marketCode: string) {
    const recentPrices = await this.prisma.priceDaily.findMany({
      where: {
        stock: { market: { code: marketCode }, isActive: true },
        date: {
          gte: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000),
        },
      },
      include: { stock: true },
      orderBy: { date: 'desc' },
    });
    return recentPrices;
  }
}
