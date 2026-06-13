import { Injectable, NotFoundException, ConflictException } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class SubscriptionService {
  constructor(private readonly prisma: PrismaService) {}

  async subscribe(email: string, symbol: string) {
    const stock = await this.prisma.stock.findFirst({
      where: { symbol: symbol.toUpperCase(), isActive: true },
    });
    if (!stock) throw new NotFoundException(`종목을 찾을 수 없습니다: ${symbol}`);

    const existing = await this.prisma.alertSubscription.findUnique({
      where: { email_stockId: { email, stockId: stock.id } },
    });

    if (existing?.isActive) {
      throw new ConflictException(`이미 ${symbol} 알림을 구독 중입니다`);
    }

    if (existing) {
      await this.prisma.alertSubscription.update({
        where: { id: existing.id },
        data: { isActive: true },
      });
    } else {
      await this.prisma.alertSubscription.create({
        data: { email, stockId: stock.id },
      });
    }

    return { subscribed: true, symbol: stock.symbol, name: stock.name };
  }

  async unsubscribe(email: string, symbol: string) {
    const stock = await this.prisma.stock.findFirst({
      where: { symbol: symbol.toUpperCase() },
    });
    if (!stock) throw new NotFoundException(`종목을 찾을 수 없습니다: ${symbol}`);

    await this.prisma.alertSubscription.updateMany({
      where: { email, stockId: stock.id },
      data: { isActive: false },
    });

    return { unsubscribed: true, symbol: stock.symbol };
  }

  async listByEmail(email: string) {
    const subs = await this.prisma.alertSubscription.findMany({
      where: { email, isActive: true },
      include: { stock: { select: { symbol: true, name: true, sector: true, market: { select: { code: true } } } } },
      orderBy: { createdAt: 'desc' },
    });

    return subs.map(s => ({
      symbol: s.stock.symbol,
      name: s.stock.name,
      sector: s.stock.sector,
      market: s.stock.market.code,
      subscribedAt: s.createdAt,
    }));
  }

  // 시그널 발송용: 특정 종목의 활성 구독자 조회
  async getActiveSubscribers(stockId: number): Promise<string[]> {
    const subs = await this.prisma.alertSubscription.findMany({
      where: { stockId, isActive: true },
      select: { email: true },
    });
    return subs.map(s => s.email);
  }
}
