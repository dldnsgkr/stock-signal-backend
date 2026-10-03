"""마지막 실험 2단계: KR 가치 가중치 × 유동성 하한 — 비용 후 알파가 양수가 되는가.

net_alpha_experiment.sql(저장 점수)이 현행 스코어러로는 KR 전멸임을 보였다.
남은 팔: 가치 가중치 상향(9/1 안건에서 3단계 방향 통과)과 유동성 하한의 조합.
재채점이 필요하므로 sweep_weights.py 구조를 재사용한다.

지표 (비용 모델은 운영 trading-cost.ts 와 동일):
  net = EW알파 − 왕복비용
  dm  = (수익률 − 같은 하한을 통과한 전 채점종목의 그날 평균) − 비용
        → 하한 유니버스 효과를 제거한 **선택 능력** 지표. 이것이 양수여야 진짜다.

결과 (2026-10-04 — **3단계 탈락, 최종 기각**):
  v2.1 구간(08-11~25, 11런, 자기검증 100%):   dm 은 가치비중↑·하한↑일수록 좋아져
    5/90/5+100억 dm +0.50% (선택 능력 존재) — 그러나 net 은 전부 음수.
  v2.2 구간(08-26~09-15, 15런, 자기검증 99.9%): **순위가 정반대로 뒤집힘** —
    현행 45/25/30+100억 dm +0.48 / net +0.33 이 최선, 5/90/5+100억 dm −0.76 최악.
  → 어떤 (가중치×하한) 조합도 두 구간 모두에서 dm>0·net>0 을 만족하지 못한다.
  부수 결론: 9/1 가치 가중치 안건도 이것으로 기각 — 08-27 사전 측정의 "3단계 통과"는
  08-11~25 한 국면 안의 이야기였고, 다음 3주에서 부호가 뒤집혔다.
  "런 7개로 결정하지 말라"는 원칙이 옳았다.

사용법 (EC2, 구간에 맞는 --scorer 필수):
  .venv/bin/python scripts/net_alpha_value.py --market KR --scorer v21 \
      --from 2026-08-11 --to 2026-08-25
  .venv/bin/python scripts/net_alpha_value.py --market KR --scorer current \
      --from 2026-08-26 --to 2026-09-15
두 구간이 그대로 3단계(기간 분리)다.
"""
import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from _common import Verifier, add_scorer_arg, parse_dates, resolve_dsn, scorer_arm  # noqa: E402
from app.engine import scorer  # noqa: E402

# (모멘텀, 가치, 감성) — 현행 / 중간(권고 후보) / 극단(참고)
WEIGHTS = [(0.45, 0.25, 0.30), (0.25, 0.50, 0.25), (0.05, 0.90, 0.05)]
FLOORS_KR = [0, 1e9, 1e10]      # 없음 / 10억 / 100억
FLOORS_US = [0, 1e6, 1e7]

RUNS_SQL = """
    SELECT rr.id, rr.executed_at::date AS d
    FROM recommendation_runs rr
    WHERE rr.market_code = $1
      AND ($2::date IS NULL OR rr.executed_at >= $2::date)
      AND ($3::date IS NULL OR rr.executed_at < $3::date + 1)
      AND EXISTS (
          SELECT 1 FROM recommendations r
          JOIN recommendation_results res ON res.recommendation_id = r.id
          WHERE r.recommendation_run_id = rr.id AND res.return_7d IS NOT NULL
      )
    ORDER BY rr.executed_at
"""

ROWS_SQL = """
    SELECT r.stock_id, r.score AS stored_score, r.feature_snapshot_json,
           res.return_7d AS ret, res.alpha_7d AS alpha,
           (SELECT p.close * p.volume FROM price_daily p
            WHERE p.stock_id = r.stock_id AND p.date <= $2
            ORDER BY p.date DESC LIMIT 1) AS tv
    FROM recommendations r
    JOIN recommendation_results res ON res.recommendation_id = r.id
    WHERE r.recommendation_run_id = $1 AND res.return_7d IS NOT NULL
"""


def round_trip_cost(market: str, tv) -> float:
    """trading-cost.ts 와 동일. tv 미상은 최악 구간(모르는 걸 싸게 가정하지 않는다)."""
    explicit = 0.0018 if market == "KR" else 0.0
    if market == "KR":
        tiers = [(1e10, 0.0005), (1e9, 0.0015), (1e8, 0.0040), (0, 0.0100)]
    else:
        tiers = [(1e7, 0.0005), (1e6, 0.0015), (1e5, 0.0040), (0, 0.0100)]
    side = tiers[-1][1]
    if tv is not None:
        for lo, s in tiers:
            if tv >= lo:
                side = s
                break
    return explicit + 2 * side


def has_value_data(feat) -> bool:
    f = feat.get("fundamental") or {}
    return f.get("per_relative") is not None or f.get("pbr_relative") is not None


class Cell:
    __slots__ = ("n", "alpha", "cost", "net", "dm", "hits")

    def __init__(self):
        self.n = 0
        self.alpha = self.cost = self.net = self.dm = 0.0
        self.hits = 0

    def add(self, alpha, cost, ret, day_mean):
        self.n += 1
        self.alpha += alpha
        self.cost += cost
        self.net += alpha - cost
        self.dm += ret - day_mean - cost
        if ret > 0:
            self.hits += 1


async def main():
    ap = argparse.ArgumentParser(description="가치 가중치 × 유동성 하한 — 비용 후 알파")
    ap.add_argument("--market", default="KR", choices=["US", "KR"])
    ap.add_argument("--from", dest="fromdate", default=None)
    ap.add_argument("--to", dest="todate", default=None)
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--max-abs-ret", type=float, default=1.0)
    ap.add_argument("--dsn", default=None)
    add_scorer_arg(ap)
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    floors = FLOORS_KR if args.market == "KR" else FLOORS_US
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)
    arm = args.scorer
    verifier = Verifier()
    cells = defaultdict(Cell)  # (weights, floor) -> Cell
    n_rows = n_skipped = n_outliers = n_novalue = 0

    conn = await asyncpg.connect(dsn)
    try:
        runs = await conn.fetch(RUNS_SQL, args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}).")

        for run_id, run_date in runs:
            rows = await conn.fetch(ROWS_SQL, run_id, run_date)
            # 그날 유니버스 평균(하한별) — 가중치·임계값과 무관한 '그냥 다 들고 있기' 기준
            prepared = []
            for rec in rows:
                ret = rec["ret"]
                if ret is None:
                    continue
                ret_f = float(ret)
                if args.max_abs_ret and abs(ret_f) > args.max_abs_ret:
                    n_outliers += 1
                    continue
                tv = float(rec["tv"]) if rec["tv"] is not None else None
                prepared.append((rec, ret_f, tv))
            day_mean = {}
            for fl in floors:
                vals = [r for (_, r, tv) in prepared if (tv or 0) >= fl]
                day_mean[fl] = sum(vals) / len(vals) if vals else None

            for rec, ret_f, tv in prepared:
                alpha = rec["alpha"]
                if alpha is None:
                    continue
                alpha_f = float(alpha)
                try:
                    snapshot = rec["feature_snapshot_json"]
                    feat = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                    if not has_value_data(feat):
                        n_novalue += 1
                        continue
                    with scorer_arm(arm):
                        s_base = scorer.calculate_total_score(feat)["total_score"]
                    verifier.check(rec["stored_score"], s_base)

                    cost = round_trip_cost(args.market, tv)
                    for w in WEIGHTS:
                        base = {"momentum": w[0], "value": w[1], "sentiment": w[2]}
                        with scorer_arm(arm):
                            s = scorer.calculate_total_score(feat, base_weights=base)["total_score"]
                        if s < threshold:
                            continue
                        for fl in floors:
                            if (tv or 0) >= fl and day_mean[fl] is not None:
                                cells[(w, fl)].add(alpha_f, cost, ret_f, day_mean[fl])
                except Exception:
                    n_skipped += 1
                    continue
                n_rows += 1
    finally:
        await conn.close()

    print()
    print("=" * 84)
    print(f" 가치 가중치 × 유동성 하한 — {args.market} / 7d / 임계값 {threshold} / "
          f"{args.fromdate or '처음'}~{args.todate or '끝'}")
    print(f" 런 {len(runs)}개 · 채점 {n_rows:,}건 · 가치없음 제외 {n_novalue:,} · "
          f"이상치 {n_outliers:,} · 오류 {n_skipped:,} · 스코어러 arm: {arm}")
    for line in verifier.lines():
        print(f" {line}")
    print("=" * 84)
    print(f" {'모/가/감':<11}{'하한':>8}{'n':>8}{'알파%':>8}{'비용%':>8}"
          f"{'net%':>8}{'dm%':>8}{'적중%':>8}")
    print("-" * 84)
    for w in WEIGHTS:
        for fl in floors:
            c = cells.get((w, fl))
            label = f"{w[0]*100:.0f}/{w[1]*100:.0f}/{w[2]*100:.0f}"
            fl_label = "없음" if fl == 0 else (f"{fl/1e8:.0f}억" if args.market == "KR" else f"${fl/1e6:.0f}M")
            if not c or c.n == 0:
                print(f" {label:<11}{fl_label:>8}{'0':>8}")
                continue
            print(f" {label:<11}{fl_label:>8}{c.n:>8,}{c.alpha/c.n*100:>8.2f}{c.cost/c.n*100:>8.2f}"
                  f"{c.net/c.n*100:>8.2f}{c.dm/c.n*100:>8.2f}{c.hits/c.n*100:>8.1f}")
    print("-" * 84)
    print(" 판단: net>0 **그리고 dm>0** 이 두 구간(v21/current) 모두에서 나와야 통과.")
    print()


if __name__ == "__main__":
    asyncio.run(main())


