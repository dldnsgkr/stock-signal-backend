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
      total7d: bigint;
      total30d: bigint;
      hit7d_count: bigint;
      hit30d_count: bigint;
      avg_return_7d: string | null;
      avg_return_30d: string | null;
    };

    // 7일/30일 지표는 성숙 시점이 달라 분모를 각각 센다.
    // (hit_7d 기준 하나로 나누면 30일 미성숙 구간에서 적중률이 0%로 왜곡됨)
    const rows = await this.prisma.$queryRaw<OverviewRow[]>`
      SELECT
        COUNT(*) FILTER (WHERE res.hit_7d  IS NOT NULL)      AS total7d,
        COUNT(*) FILTER (WHERE res.hit_30d IS NOT NULL)      AS total30d,
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
    `;

    const row = rows[0];
    const total7d = Number(row.total7d);
    const total30d = Number(row.total30d);

    return {
      period,
      totalRecommendations: total7d,
      hitRate7d: total7d > 0 ? Number(row.hit7d_count) / total7d : null,
      hitRate30d: total30d > 0 ? Number(row.hit30d_count) / total30d : null,
      avgReturn7d: row.avg_return_7d != null ? Number(row.avg_return_7d) : null,
      avgReturn30d: row.avg_return_30d != null ? Number(row.avg_return_30d) : null,
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

    return rows.map((row: VersionRow) => {
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

    return rows.map((r: TimelineRow) => ({
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

    return rows.map((r: SectorRow) => ({
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

    return rows.map((r: RecRow) => ({
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

  async getPortfolioSimulation(
    market = 'US',
    period: '30d' | '90d' | '180d' = '90d',
    horizon: '7d' | '30d' = '7d',
  ) {
    const days = period === '30d' ? 30 : period === '90d' ? 90 : 180;
    const since = new Date();
    since.setDate(since.getDate() - days);

    type SimRow = {
      id: number;
      symbol: string;
      name: string;
      sector: string | null;
      score: string;
      confidence: number;
      entry_price: string;
      recommended_at: Date;
      run_executed_at: Date;
      run_id: number;
      return_7d: string | null;
      return_30d: string | null;
      benchmark_return_7d: string | null;
      benchmark_return_30d: string | null;
      alpha_7d: string | null;
      alpha_30d: string | null;
      hit_7d: boolean | null;
      hit_30d: boolean | null;
      score_rank: bigint;
    };

    const rows = await this.prisma.$queryRaw<SimRow[]>`
      WITH ranked AS (
        SELECT
          r.id,
          s.symbol,
          s.name,
          s.sector,
          r.score,
          r.confidence,
          r.entry_price,
          r.recommended_at,
          rr.executed_at          AS run_executed_at,
          rr.id                   AS run_id,
          res.return_7d,
          res.return_30d,
          res.benchmark_return_7d,
          res.benchmark_return_30d,
          res.alpha_7d,
          res.alpha_30d,
          res.hit_7d,
          res.hit_30d,
          -- ⚠️ r.stock_id tie-break 를 빼지 말 것.
          -- 점수 양자화가 심해 동점이 흔하다 — KR 최근 90일 BUY 의 **80.8%** 가 동점에 속하고,
          -- rank 5 경계에는 평균 **15.94 종목**(최대 35)이 같은 점수로 몰려 있다.
          -- tie-break 가 없으면 행 순서가 실행마다 달라 Top5 가 동점 16종목 중 5개를
          -- 매번 다르게 집는다. 2026-08-10 실측: 같은 호출 3회에 Top5 수익률
          -- -0.77% / -1.24% / -2.58%, 알파는 +1.44% / +0.97% / **-0.36%** 로 부호까지 뒤집혔다.
          ROW_NUMBER() OVER (
            PARTITION BY r.recommendation_run_id
            ORDER BY r.score DESC, r.stock_id
          ) AS score_rank
        FROM recommendations r
        JOIN recommendation_runs rr  ON rr.id  = r.recommendation_run_id
        JOIN stocks s                ON s.id   = r.stock_id
        -- ⚠️ LEFT JOIN 이어야 한다. 평가된 것만으로 순위를 매기면 **rank 6 이 5 로 승격**된다
        -- (상장폐지·가격 공백으로 평가가 빠진 종목이 KR BUY 의 20%). 순위는 추천 시점에
        -- 모델이 실제로 고른 순서여야 하므로 전체로 매기고, 평균 낼 때 결측을 뺀다.
        LEFT JOIN recommendation_results res ON res.recommendation_id = r.id
        WHERE rr.market_code = ${market}
          AND rr.executed_at >= ${since}
          AND r.action = 'BUY'
      )
      SELECT * FROM ranked
      ORDER BY run_executed_at DESC, score_rank ASC
    `;

    const isHorizon7d = horizon === '7d';

    const toPosition = (r: SimRow) => ({
      id: r.id,
      symbol: r.symbol,
      name: r.name,
      sector: r.sector,
      score: Number(r.score),
      confidence: r.confidence,
      entryPrice: Number(r.entry_price),
      recommendedAt: r.recommended_at,
      return: isHorizon7d
        ? (r.return_7d != null ? Number(r.return_7d) : null)
        : (r.return_30d != null ? Number(r.return_30d) : null),
      benchmark: isHorizon7d
        ? (r.benchmark_return_7d != null ? Number(r.benchmark_return_7d) : null)
        : (r.benchmark_return_30d != null ? Number(r.benchmark_return_30d) : null),
      alpha: isHorizon7d
        ? (r.alpha_7d != null ? Number(r.alpha_7d) : null)
        : (r.alpha_30d != null ? Number(r.alpha_30d) : null),
      hit: isHorizon7d ? r.hit_7d : r.hit_30d,
      scoreRank: Number(r.score_rank),
    });

    const computePortfolio = (positions: ReturnType<typeof toPosition>[]) => {
      const withReturn = positions.filter(p => p.return !== null);
      if (withReturn.length === 0) return null;

      const returns = withReturn.map(p => p.return as number);
      const hits = withReturn.filter(p => p.hit !== null);
      // 벤치마크가 있는 건만 따로 모은다. 예전엔 mean(returns) - mean(benchmarks) 였는데
      // 두 평균의 모집단이 달라(벤치마크 결측분이 수익률 쪽에만 남아) 알파에 결측 편향이 섞였다.
      // 종목별 (수익률 - 벤치마크) 의 평균으로 계산해 모집단을 일치시킨다.
      const withBench = withReturn.filter(p => p.benchmark !== null);

      const mean = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / arr.length;
      const std = (arr: number[]) => {
        const m = mean(arr);
        return Math.sqrt(arr.reduce((a, b) => a + (b - m) ** 2, 0) / arr.length);
      };

      const portfolioReturn = mean(returns);
      const benchmarkReturn = withBench.length > 0
        ? mean(withBench.map(p => p.benchmark as number)) : null;
      const alpha = withBench.length > 0
        ? mean(withBench.map(p => (p.return as number) - (p.benchmark as number))) : null;
      // ⚠️ stdDev 는 **종목 간 횡단면 산포**이지 포트폴리오 변동성이 아니다.
      // N 종목을 담으면 포트폴리오 변동성은 대략 이 값의 1/√N 이다.
      // 따라서 아래 비율도 **샤프 지수가 아니다** — 무위험수익률도 연율화도 없다.
      // 화면 라벨을 여기에 맞춰 두었으니(종목 간 산포 / 수익-산포 비율) 되돌리지 말 것.
      // 진짜 샤프를 내려면 일별 포트폴리오 시계열이 필요한데, 런이 매일 겹쳐 그게 없다.
      const stdDev = std(returns);
      const sharpe = stdDev > 0 ? portfolioReturn / stdDev : null;
      const hitRate = hits.length > 0 ? hits.filter(p => p.hit).length / hits.length : null;

      const sorted = [...returns].sort((a, b) => a - b);
      return {
        count: withReturn.length,
        portfolioReturn: Math.round(portfolioReturn * 10000) / 10000,
        benchmarkReturn: benchmarkReturn !== null ? Math.round(benchmarkReturn * 10000) / 10000 : null,
        alpha: alpha !== null ? Math.round(alpha * 10000) / 10000 : null,
        stdDev: Math.round(stdDev * 10000) / 10000,
        sharpe: sharpe !== null ? Math.round(sharpe * 100) / 100 : null,
        hitRate: hitRate !== null ? Math.round(hitRate * 1000) / 1000 : null,
        bestReturn:  sorted[sorted.length - 1],
        worstReturn: sorted[0],
      };
    };

    type Position = ReturnType<typeof toPosition>;
    const all = rows.map(toPosition);
    const top5  = all.filter((p: Position) => p.scoreRank <= 5);
    const top10 = all.filter((p: Position) => p.scoreRank <= 10);
    const top20 = all.filter((p: Position) => p.scoreRank <= 20);

    // 상위 종목만 포지션 테이블에 표시 (최대 200개)
    const positions = top20.slice(0, 200);

    // 수익률 분포 (10% 구간)
    const allReturns = all.map((p: Position) => p.return).filter((r: number | null): r is number => r !== null);
    const buckets: Record<string, number> = {};
    for (const r of allReturns) {
      const bucket = Math.floor(r * 100 / 5) * 5; // 5% 단위
      const key = `${bucket >= 0 ? '+' : ''}${bucket}%`;
      buckets[key] = (buckets[key] ?? 0) + 1;
    }
    const distribution = Object.entries(buckets)
      .sort((a, b) => parseFloat(a[0]) - parseFloat(b[0]))
      .map(([range, count]) => ({ range, count }));

    return {
      market,
      period,
      horizon,
      strategies: {
        top5:  computePortfolio(top5),
        top10: computePortfolio(top10),
        top20: computePortfolio(top20),
        all:   computePortfolio(all),
      },
      positions,
      distribution,
      totalRuns: new Set(rows.map((r: SimRow) => r.run_id)).size,
    };
  }
}
