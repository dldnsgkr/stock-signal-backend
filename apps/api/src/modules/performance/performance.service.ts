import { Injectable } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class PerformanceService {
  constructor(private readonly prisma: PrismaService) {}

  async getOverview(market = 'US', period: '7d' | '30d' | '90d' = '30d') {
    const days = period === '7d' ? 7 : period === '30d' ? 30 : 90;
    const since = new Date();
    since.setDate(since.getDate() - days);

    type OverviewRow = {
      total: bigint;
      hit7d_count: bigint;
      hit30d_count: bigint;
      avg_return_7d: string | null;
      avg_return_30d: string | null;
    };

    const rows = await this.prisma.$queryRaw<OverviewRow[]>`
      SELECT
        COUNT(*)                                              AS total,
        COUNT(*) FILTER (WHERE res.hit_7d = true)            AS hit7d_count,
        COUNT(*) FILTER (WHERE res.hit_30d = true)           AS hit30d_count,
        AVG(res.return_7d)                                   AS avg_return_7d,
        AVG(res.return_30d)                                  AS avg_return_30d
      FROM recommendation_results res
      JOIN recommendations r      ON r.id   = res.recommendation_id
      JOIN recommendation_runs run ON run.id = r.recommendation_run_id
      WHERE run.market_code   = ${market}
        AND run.executed_at   >= ${since}
        AND r.action          = 'BUY'
        AND res.hit_7d        IS NOT NULL
    `;

    const row = rows[0];
    const total = Number(row.total);

    if (total === 0) {
      return { period, totalRecommendations: 0, hitRate7d: 0, hitRate30d: 0, avgReturn7d: 0, avgReturn30d: 0 };
    }

    return {
      period,
      totalRecommendations: total,
      hitRate7d: Number(row.hit7d_count) / total,
      hitRate30d: Number(row.hit30d_count) / total,
      avgReturn7d: row.avg_return_7d != null ? Number(row.avg_return_7d) : 0,
      avgReturn30d: row.avg_return_30d != null ? Number(row.avg_return_30d) : 0,
    };
  }

  async getModelVersionComparison() {
    type VersionRow = {
      id: number;
      version_name: string;
      strategy_type: string;
      deployed_at: Date;
      is_active: boolean;
      total_runs: bigint;
      total_recommendations: bigint;
      with_results: bigint;
      hit_count: bigint;
      avg_return_7d: string | null;
    };

    const rows = await this.prisma.$queryRaw<VersionRow[]>`
      SELECT
        mv.id,
        mv.version_name,
        mv.strategy_type,
        mv.deployed_at,
        mv.is_active,
        COUNT(DISTINCT rr.id)                                                    AS total_runs,
        COUNT(DISTINCT r.id)                                                     AS total_recommendations,
        COUNT(DISTINCT r.id) FILTER (WHERE res.hit_7d IS NOT NULL)               AS with_results,
        COUNT(DISTINCT r.id) FILTER (WHERE res.hit_7d = true)                    AS hit_count,
        AVG(res.return_7d)   FILTER (WHERE res.hit_7d IS NOT NULL)               AS avg_return_7d
      FROM model_versions mv
      LEFT JOIN recommendation_runs rr ON rr.model_version_id = mv.id
      LEFT JOIN recommendations r      ON r.recommendation_run_id = rr.id
      LEFT JOIN recommendation_results res ON res.recommendation_id = r.id
      GROUP BY mv.id, mv.version_name, mv.strategy_type, mv.deployed_at, mv.is_active
      ORDER BY mv.deployed_at DESC
    `;

    return rows.map((row) => {
      const withResults = Number(row.with_results);
      return {
        versionName: row.version_name,
        strategyType: row.strategy_type,
        deployedAt: row.deployed_at,
        isActive: row.is_active,
        totalRuns: Number(row.total_runs),
        totalRecommendations: Number(row.total_recommendations),
        hitRate7d: withResults > 0 ? Number(row.hit_count) / withResults : null,
        avgReturn7d: row.avg_return_7d != null ? Number(row.avg_return_7d) : null,
      };
    });
  }
}
