import { Injectable } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

/** 점수 밴드 경계. 시그널 하나가 어느 구간에 속하는지 판단한다. */
const SCORE_BANDS: { min: number; max: number; label: string }[] = [
  { min: 90, max: 1000, label: '90점 이상' },
  { min: 85, max: 90, label: '85~90점' },
  { min: 80, max: 85, label: '80~85점' },
  { min: 75, max: 80, label: '75~80점' },
  { min: 70, max: 75, label: '70~75점' },
  { min: 65, max: 70, label: '65~70점' },
  { min: 0, max: 65, label: '65점 미만' },
];

const BAND_WINDOW_DAYS = 90;
const BAND_CACHE_MS = 30 * 60 * 1000;   // 30분. 하루 1회 평가라 더 자주 볼 이유가 없다
const BAND_MIN_SAMPLE = 200;            // 이보다 적으면 수치를 내보내지 않는다

type BandStat = { label: string; alpha7d: number | null; hitRate7d: number | null; sample: number };

@Injectable()
export class RecommendationsService {
  constructor(private readonly prisma: PrismaService) {}

  // 시장별 밴드 성과 캐시. 집계가 수십만 행을 훑으므로 매 요청마다 돌리지 않는다.
  private bandCache = new Map<string, { at: number; stats: BandStat[] }>();

  /**
   * 점수 밴드별 과거 성과 — **이 제품의 취지 그 자체다.**
   *
   * 화면은 score DESC 로 정렬하는데, 실측하면 점수-알파 곡선이 ∩자라
   * 최상단이 가장 나쁘다(2026-08-19, 90일: 화면 상위3 알파 KR -4.29% / US -1.52%,
   * 반면 55~75 구간은 KR -0.37% / US +0.26%). 정렬을 바꾸는 건 선택 규칙 변경이라
   * 3단계 검증을 통과해야 하지만, **우리가 이미 측정한 걸 사용자에게 보여주는 것**은
   * 스코어링 변경이 아니다. 그래서 밴드 성과를 그대로 노출한다.
   *
   * 이상치 컷(|수익률|<=1)은 `price_daily` 조정시점 혼재 때문이다 — 다른 집계와 같은 기준.
   */
  async getBandStats(market: string): Promise<BandStat[]> {
    const cached = this.bandCache.get(market);
    if (cached && Date.now() - cached.at < BAND_CACHE_MS) return cached.stats;

    type Row = { band_min: number; n: bigint; avg_alpha: string | null; hits: bigint };
    const rows = await this.prisma.$queryRaw<Row[]>`
      SELECT
        CASE WHEN r.score >= 90 THEN 90 WHEN r.score >= 85 THEN 85
             WHEN r.score >= 80 THEN 80 WHEN r.score >= 75 THEN 75
             WHEN r.score >= 70 THEN 70 WHEN r.score >= 65 THEN 65
             ELSE 0 END                                  AS band_min,
        COUNT(*)                                         AS n,
        AVG(res.alpha_7d)                                AS avg_alpha,
        COUNT(*) FILTER (WHERE res.return_7d > 0)        AS hits
      FROM recommendations r
      JOIN recommendation_runs rr ON rr.id = r.recommendation_run_id
      JOIN recommendation_results res ON res.recommendation_id = r.id
      WHERE rr.market_code = ${market}
        AND rr.executed_at >= NOW() - make_interval(days => ${BAND_WINDOW_DAYS}::int)
        AND res.alpha_7d IS NOT NULL
        AND abs(res.return_7d) <= 1.0
      GROUP BY 1
    `;

    const byMin = new Map(rows.map((r: Row) => [Number(r.band_min), r]));
    const stats: BandStat[] = SCORE_BANDS.map(b => {
      const row = byMin.get(b.min);
      const n = row ? Number(row.n) : 0;
      // 표본이 적으면 수치를 내보내지 않는다 — 화면에서 근거처럼 읽히면 안 된다
      if (!row || n < BAND_MIN_SAMPLE) {
        return { label: b.label, alpha7d: null, hitRate7d: null, sample: n };
      }
      return {
        label: b.label,
        alpha7d: row.avg_alpha != null ? Number(row.avg_alpha) : null,
        hitRate7d: Number(row.hits) / n,
        sample: n,
      };
    });

    this.bandCache.set(market, { at: Date.now(), stats });
    return stats;
  }

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
          stock: { include: { market: true } },
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

  async getSellSignals(market: string, limit = 20) {
    const sellSignals = await this.prisma.sellSignal.findMany({
      where: {
        buyRecommendation: {
          run: { marketCode: market },
        },
      },
      include: {
        buyRecommendation: {
          include: {
            stock: { include: { market: true } },
          },
        },
      },
      orderBy: { generatedAt: 'desc' },
      take: limit,
    });

    return sellSignals.map(s => ({
      id: s.id,
      stock: {
        symbol: s.buyRecommendation.stock.symbol,
        name: s.buyRecommendation.stock.name,
        sector: s.buyRecommendation.stock.sector,
        market: s.buyRecommendation.stock.market?.code,
      },
      buyScore: Number(s.buyRecommendation.score),
      currentScore: Number(s.currentScore),
      entryPrice: Number(s.entryPrice),
      exitPrice: s.exitPrice ? Number(s.exitPrice) : null,
      reasons: s.reasons as string[],
      buyDate: s.buyRecommendation.recommendedAt,
      sellDate: s.generatedAt,
    }));
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
