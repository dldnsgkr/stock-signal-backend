import { Injectable } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class PerformanceService {
  constructor(private readonly prisma: PrismaService) {}

  async getOverview(market = 'US', period: '7d' | '30d' | '90d' = '30d') {
    const days = period === '7d' ? 7 : period === '30d' ? 30 : 90;
    const since = new Date();
    since.setDate(since.getDate() - days);

    const results = await this.prisma.recommendationResult.findMany({
      where: {
        recommendation: {
          run: { marketCode: market, executedAt: { gte: since } },
          action: 'BUY',
        },
        hit7d: { not: null },
      },
      include: { recommendation: true },
    });

    if (results.length === 0) {
      return {
        period,
        totalRecommendations: 0,
        hitRate7d: 0,
        hitRate30d: 0,
        avgReturn7d: 0,
        avgReturn30d: 0,
      };
    }

    const hit7dCount = results.filter((r) => r.hit7d).length;
    const hit30dCount = results.filter((r) => r.hit30d).length;
    const avg7d = results.reduce((s, r) => s + Number(r.return7d ?? 0), 0) / results.length;
    const avg30d = results.reduce((s, r) => s + Number(r.return30d ?? 0), 0) / results.length;

    return {
      period,
      totalRecommendations: results.length,
      hitRate7d: hit7dCount / results.length,
      hitRate30d: hit30dCount / results.length,
      avgReturn7d: avg7d,
      avgReturn30d: avg30d,
    };
  }

  async getModelVersionComparison() {
    const versions = await this.prisma.modelVersion.findMany({
      include: {
        runs: {
          include: {
            recommendations: {
              include: { result: true },
            },
          },
        },
      },
      orderBy: { deployedAt: 'desc' },
    });

    return versions.map((v) => {
      const allRecs = v.runs.flatMap((r) => r.recommendations);
      const withResults = allRecs.filter((r) => r.result?.hit7d !== null);
      const hitCount = withResults.filter((r) => r.result?.hit7d).length;

      return {
        versionName: v.versionName,
        strategyType: v.strategyType,
        deployedAt: v.deployedAt,
        isActive: v.isActive,
        totalRuns: v.runs.length,
        totalRecommendations: allRecs.length,
        hitRate7d: withResults.length > 0 ? hitCount / withResults.length : null,
        avgReturn7d:
          withResults.length > 0
            ? withResults.reduce((s, r) => s + Number(r.result?.return7d ?? 0), 0) / withResults.length
            : null,
      };
    });
  }
}
