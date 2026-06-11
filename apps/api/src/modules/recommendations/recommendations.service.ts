import { Injectable } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class RecommendationsService {
  constructor(private readonly prisma: PrismaService) {}

  async getLatest(market: string, action?: string, page = 1, pageSize = 20) {
    const latestRun = await this.prisma.recommendationRun.findFirst({
      where: { marketCode: market },
      orderBy: { executedAt: 'desc' },
      include: { modelVersion: true },
    });

    if (!latestRun) return { data: [], total: 0, page, pageSize, totalPages: 0 };

    const where: { recommendationRunId: number; action?: string } = {
      recommendationRunId: latestRun.id,
    };
    if (action) where.action = action;

    const [data, total] = await Promise.all([
      this.prisma.recommendation.findMany({
        where,
        include: {
          stock: { include: { market: true } },
          run: { include: { modelVersion: true } },
          result: true,
        },
        orderBy: [{ score: 'desc' }, { id: 'asc' }],
        skip: (page - 1) * pageSize,
        take: pageSize,
      }),
      this.prisma.recommendation.count({ where }),
    ]);

    return {
      data: data.map(this.formatRecommendation),
      total,
      page,
      pageSize,
      totalPages: Math.ceil(total / pageSize),
      runInfo: {
        executedAt: latestRun.executedAt,
        modelVersion: latestRun.modelVersion.versionName,
        notes: latestRun.notes,
      },
    };
  }

  async getHistory(market: string, days = 30, page = 1, pageSize = 50) {
    const since = new Date();
    since.setDate(since.getDate() - days);

    const where = {
      run: { marketCode: market, executedAt: { gte: since } },
    };

    const [data, total] = await Promise.all([
      this.prisma.recommendation.findMany({
        where,
        include: {
          stock: true,
          run: { include: { modelVersion: true } },
          result: true,
        },
        orderBy: { recommendedAt: 'desc' },
        skip: (page - 1) * pageSize,
        take: pageSize,
      }),
      this.prisma.recommendation.count({ where }),
    ]);

    return {
      data: data.map(this.formatRecommendation),
      total,
      page,
      pageSize,
      totalPages: Math.ceil(total / pageSize),
    };
  }

  async getByStock(symbol: string, limit = 10) {
    const data = await this.prisma.recommendation.findMany({
      where: { stock: { symbol: symbol.toUpperCase() } },
      include: {
        stock: { include: { market: true } },
        run: { include: { modelVersion: true } },
        result: true,
      },
      orderBy: { recommendedAt: 'desc' },
      take: limit,
    });
    return data.map(this.formatRecommendation);
  }

  private formatRecommendation(rec: any) {
    return {
      id: rec.id,
      stock: {
        symbol: rec.stock.symbol,
        name: rec.stock.name,
        sector: rec.stock.sector,
        market: rec.stock.market?.code,
      },
      action: rec.action,
      score: Number(rec.score),
      confidence: rec.confidence,
      entryPrice: Number(rec.entryPrice),
      reasons: rec.reasonsJson as string[],
      scoreDetail: rec.scoreDetailJson,
      featureSnapshot: rec.featureSnapshotJson,
      recommendedAt: rec.recommendedAt,
      modelVersion: rec.run?.modelVersion?.versionName,
      result: rec.result
        ? {
            return7d: rec.result.return7d ? Number(rec.result.return7d) : null,
            return30d: rec.result.return30d ? Number(rec.result.return30d) : null,
            hit7d: rec.result.hit7d,
            hit30d: rec.result.hit30d,
          }
        : null,
    };
  }
}
