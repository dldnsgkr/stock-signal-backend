import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class StocksService {
  constructor(private readonly prisma: PrismaService) {}

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
