import { Injectable, NotFoundException, BadRequestException } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

// userId 는 프론트 Next API 라우트가 NextAuth 세션에서 주입한다.
// (지인용 소규모 서비스 — subscriptions 와 같은 신뢰 수준)
@Injectable()
export class WatchlistService {
  constructor(private readonly prisma: PrismaService) {}

  private async resolve(userId: number, symbol: string) {
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    if (!user) throw new NotFoundException(`User ${userId} not found`);
    const stock = await this.prisma.stock.findFirst({
      where: { symbol: symbol.toUpperCase(), isActive: true },
    });
    if (!stock) throw new NotFoundException(`Stock ${symbol} not found`);
    return { user, stock };
  }

  async add(userId: number, symbol: string) {
    if (!userId || !symbol) throw new BadRequestException('userId, symbol required');
    const { stock } = await this.resolve(userId, symbol);
    await this.prisma.watchlistItem.upsert({
      where: { userId_stockId: { userId, stockId: stock.id } },
      create: { userId, stockId: stock.id },
      update: {},
    });
    return { added: true, symbol: stock.symbol };
  }

  async remove(userId: number, symbol: string) {
    if (!userId || !symbol) throw new BadRequestException('userId, symbol required');
    const stock = await this.prisma.stock.findFirst({
      where: { symbol: symbol.toUpperCase() },
    });
    if (stock) {
      await this.prisma.watchlistItem.deleteMany({ where: { userId, stockId: stock.id } });
    }
    return { added: false, symbol: symbol.toUpperCase() };
  }

  async list(userId: number) {
    if (!userId) throw new BadRequestException('userId required');
    const items = await this.prisma.watchlistItem.findMany({
      where: { userId },
      include: { stock: { include: { market: true } } },
      orderBy: { createdAt: 'desc' },
    });
    if (items.length === 0) return [];

    const stockIds = items.map(i => i.stockId);

    // 종목별 최신 추천 (시그널·점수)
    type LatestRecRow = {
      stock_id: number;
      action: string;
      score: string;
      recommended_at: Date;
    };
    const recRows = await this.prisma.$queryRaw<LatestRecRow[]>`
      SELECT DISTINCT ON (stock_id) stock_id, action, score, recommended_at
      FROM recommendations
      WHERE stock_id = ANY(${stockIds})
      ORDER BY stock_id, recommended_at DESC
    `;
    const recMap = new Map(recRows.map((r: LatestRecRow) => [r.stock_id, r]));

    // 최신 종가·전일 대비
    type PriceRow = { stock_id: number; close: string; prev_close: string | null };
    const priceRows = await this.prisma.$queryRaw<PriceRow[]>`
      SELECT stock_id, close, prev_close FROM (
        SELECT stock_id, close,
               LAG(close) OVER (PARTITION BY stock_id ORDER BY date) AS prev_close,
               ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
        FROM price_daily
        WHERE stock_id = ANY(${stockIds})
      ) t WHERE rn = 1
    `;
    const priceMap = new Map(priceRows.map((p: PriceRow) => [p.stock_id, p]));

    return items.map(i => {
      const rec = recMap.get(i.stockId);
      const price = priceMap.get(i.stockId);
      const close = price ? Number(price.close) : null;
      const prev = price?.prev_close ? Number(price.prev_close) : null;
      return {
        symbol: i.stock.symbol,
        name: i.stock.name,
        sector: i.stock.sector,
        market: i.stock.market?.code ?? 'US',
        addedAt: i.createdAt,
        currentPrice: close,
        changeRate: close != null && prev ? ((close - prev) / prev) * 100 : null,
        latestAction: rec?.action ?? null,
        latestScore: rec ? Number(rec.score) : null,
        latestRecommendedAt: rec?.recommended_at ?? null,
      };
    });
  }
}
