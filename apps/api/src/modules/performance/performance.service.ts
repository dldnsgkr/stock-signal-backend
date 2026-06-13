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

  async getTimeline(market = 'US', period: '30d' | '90d' | '180d' = '90d') {
    const days = period === '30d' ? 30 : period === '90d' ? 90 : 180;
    const since = new Date();
    since.setDate(since.getDate() - days);

    type TimelineRow = {
      week: Date;
      avg_return_7d: string | null;
      avg_benchmark_7d: string | null;
      avg_alpha_7d: string | null;
      count: bigint;
    };

    const rows = await this.prisma.$queryRaw<TimelineRow[]>`
      SELECT
        DATE_TRUNC('week', rr.executed_at)  AS week,
        AVG(res.return_7d)                  AS avg_return_7d,
        AVG(res.benchmark_return_7d)        AS avg_benchmark_7d,
        AVG(res.alpha_7d)                   AS avg_alpha_7d,
        COUNT(*)                            AS count
      FROM recommendation_results res
      JOIN recommendations r       ON r.id    = res.recommendation_id
      JOIN recommendation_runs rr  ON rr.id   = r.recommendation_run_id
      WHERE rr.market_code  = ${market}
        AND rr.executed_at  >= ${since}
        AND r.action        = 'BUY'
        AND res.return_7d   IS NOT NULL
      GROUP BY DATE_TRUNC('week', rr.executed_at)
      ORDER BY week ASC
    `;

    return rows.map(r => ({
      week: r.week.toISOString().slice(0, 10),
      avgReturn7d: r.avg_return_7d != null ? Number(r.avg_return_7d) : null,
      avgBenchmark7d: r.avg_benchmark_7d != null ? Number(r.avg_benchmark_7d) : null,
      avgAlpha7d: r.avg_alpha_7d != null ? Number(r.avg_alpha_7d) : null,
      count: Number(r.count),
    }));
  }

  async getBySector(market = 'US', period: '7d' | '30d' | '90d' = '30d') {
    const days = period === '7d' ? 7 : period === '30d' ? 30 : 90;
    const since = new Date();
    since.setDate(since.getDate() - days);

    type SectorRow = {
      sector: string | null;
      total: bigint;
      hit_count: bigint;
      avg_return_7d: string | null;
      avg_alpha_7d: string | null;
    };

    const rows = await this.prisma.$queryRaw<SectorRow[]>`
      SELECT
        s.sector,
        COUNT(*)                                         AS total,
        COUNT(*) FILTER (WHERE res.hit_7d = true)       AS hit_count,
        AVG(res.return_7d)                              AS avg_return_7d,
        AVG(res.alpha_7d)                               AS avg_alpha_7d
      FROM recommendation_results res
      JOIN recommendations r       ON r.id   = res.recommendation_id
      JOIN recommendation_runs rr  ON rr.id  = r.recommendation_run_id
      JOIN stocks s                ON s.id   = r.stock_id
      WHERE rr.market_code = ${market}
        AND rr.executed_at >= ${since}
        AND r.action       = 'BUY'
        AND res.hit_7d     IS NOT NULL
        AND s.sector       IS NOT NULL
      GROUP BY s.sector
      ORDER BY avg_return_7d DESC NULLS LAST
    `;

    return rows.map(r => ({
      sector: r.sector ?? '기타',
      total: Number(r.total),
      hitRate: Number(r.total) > 0 ? Number(r.hit_count) / Number(r.total) : 0,
      avgReturn7d: r.avg_return_7d != null ? Number(r.avg_return_7d) : null,
      avgAlpha7d: r.avg_alpha_7d != null ? Number(r.avg_alpha_7d) : null,
    }));
  }

  async getRecommendationsWithResults(market = 'US', period: '7d' | '30d' | '90d' = '30d', limit = 100) {
    const days = period === '7d' ? 7 : period === '30d' ? 30 : 90;
    const since = new Date();
    since.setDate(since.getDate() - days);

    type RecRow = {
      id: number;
      symbol: string;
      name: string;
      sector: string | null;
      action: string;
      score: string;
      confidence: number;
      entry_price: string;
      recommended_at: Date;
      return_1d: string | null;
      return_7d: string | null;
      return_30d: string | null;
      alpha_7d: string | null;
      alpha_30d: string | null;
      hit_1d: boolean | null;
      hit_7d: boolean | null;
      hit_30d: boolean | null;
    };

    const rows = await this.prisma.$queryRaw<RecRow[]>`
      SELECT
        r.id,
        s.symbol,
        s.name,
        s.sector,
        r.action,
        r.score,
        r.confidence,
        r.entry_price,
        r.recommended_at,
        res.return_1d,
        res.return_7d,
        res.return_30d,
        res.alpha_7d,
        res.alpha_30d,
        res.hit_1d,
        res.hit_7d,
        res.hit_30d
      FROM recommendations r
      JOIN recommendation_runs rr  ON rr.id = r.recommendation_run_id
      JOIN stocks s                ON s.id  = r.stock_id
      LEFT JOIN recommendation_results res ON res.recommendation_id = r.id
      WHERE rr.market_code = ${market}
        AND rr.executed_at >= ${since}
        AND r.action       = 'BUY'
      ORDER BY r.recommended_at DESC
      LIMIT ${limit}
    `;

    return rows.map(r => ({
      id: r.id,
      symbol: r.symbol,
      name: r.name,
      sector: r.sector,
      score: Number(r.score),
      confidence: r.confidence,
      entryPrice: Number(r.entry_price),
      recommendedAt: r.recommended_at,
      return1d: r.return_1d != null ? Number(r.return_1d) : null,
      return7d: r.return_7d != null ? Number(r.return_7d) : null,
      return30d: r.return_30d != null ? Number(r.return_30d) : null,
      alpha7d: r.alpha_7d != null ? Number(r.alpha_7d) : null,
      alpha30d: r.alpha_30d != null ? Number(r.alpha_30d) : null,
      hit1d: r.hit_1d,
      hit7d: r.hit_7d,
      hit30d: r.hit_30d,
    }));
  }
}
