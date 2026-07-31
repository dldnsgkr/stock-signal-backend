"""
모멘텀 v2.0 vs v2.1 반사실(counterfactual) 재채점 비교.

왜 필요한가
-----------
v2.1 배포 전후를 기간으로 갈라서 비교하면 **시장 국면이 교란한다.**
실제로 2026-07-31 확인 결과 v2.1 구간의 벤치마크 1일 수익률이 훨씬 나빴다
(KR -1.30% → -3.99%, US -0.11% → -0.67%). v2.1 은 과열 모멘텀을 감점해
고베타를 덜 고르므로, 급락장에서는 모델이 개선되지 않았어도 알파가 자동으로 뜬다.
P2-9 수급 신호가 규모/베타 아티팩트였던 것과 같은 함정이다.

그래서 이 스크립트는 **기간을 나누지 않는다.** 같은 기간·같은 종목의 저장된
`feature_snapshot_json` 을 두 모델로 각각 재채점해, 두 모델이 고른 종목의
실현 수익률을 비교한다. 국면·유니버스·데이터 품질이 완전히 상쇄된다.

  v2.1 arm = 현행 `app.engine.scorer` 로 재채점
  v2.0 arm = `_momentum_score` 만 동결 사본(scripts/scorer_v20_frozen.py)으로 교체해 재채점

`recommendations` 에는 BUY 뿐 아니라 **채점한 전 종목(WATCH/AVOID 포함)** 이
저장되고 셋 다 평가되므로(2026-07-31 확인: US 98%+ 커버리지), v2.0 이 골랐을
종목에도 실현 수익률이 있다. 선택 편향 없이 반사실을 구성할 수 있는 이유다.

한계
----
피처 계산 자체는 스냅샷에 고정돼 있으므로 피처를 바꾸는 실험에는 못 쓴다.
(`/backtest/rescore` 와 같은 제약. 그쪽은 가중치·임계값만 바꿔서 모멘텀 함수
교체에는 아예 쓸 수 없어 이 스크립트를 따로 둔다.)

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  .venv/bin/python scripts/counterfactual_momentum.py --market US --from 2026-07-27

  # 방법 자체 검증: v2.0 운영 구간에 돌려 07-27 오프라인 백테스트 결과가 재현되는지
  .venv/bin/python scripts/counterfactual_momentum.py --market US \
      --from 2026-06-01 --to 2026-07-24 --top-n 20
"""

import argparse
import asyncio
import json
import os
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

# scripts/ 에서 실행해도 app 패키지를 찾도록
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from app.engine import scorer  # noqa: E402
from scorer_v20_frozen import momentum_score_v20  # noqa: E402


@contextmanager
def momentum_v20():
    """블록 안에서만 `_momentum_score` 를 v2.0 동결 사본으로 바꾼다."""
    original = scorer._momentum_score
    scorer._momentum_score = momentum_score_v20
    try:
        yield
    finally:
        scorer._momentum_score = original


class Acc:
    """선택된 종목들의 실현 수익률 누적기."""

    def __init__(self, label: str):
        self.label = label
        self.n = 0
        self.ret_sum = 0.0
        self.alpha_sum = 0.0
        self.alpha_n = 0
        self.hits = 0

    def add(self, ret, alpha):
        if ret is None:
            return
        self.n += 1
        self.ret_sum += ret
        if ret > 0:
            self.hits += 1
        if alpha is not None:
            self.alpha_sum += alpha
            self.alpha_n += 1

    @property
    def avg_ret(self):
        return self.ret_sum / self.n if self.n else None

    @property
    def avg_alpha(self):
        return self.alpha_sum / self.alpha_n if self.alpha_n else None

    @property
    def hit_rate(self):
        return self.hits / self.n if self.n else None

    def row(self):
        def pct(v):
            return f"{v * 100:+7.3f}%" if v is not None else "      -"

        hit = f"{self.hit_rate * 100:5.1f}%" if self.hit_rate is not None else "    -"
        return f"  {self.label:<22} n={self.n:>7,}  ret={pct(self.avg_ret)}  alpha={pct(self.avg_alpha)}  hit={hit}"


RUNS_SQL = """
    SELECT rr.id, rr.executed_at, mv.version_name
    FROM recommendation_runs rr
    JOIN model_versions mv ON mv.id = rr.model_version_id
    WHERE rr.market_code = $1
      AND ($2::date IS NULL OR rr.executed_at >= $2::date)
      AND ($3::date IS NULL OR rr.executed_at < $3::date + 1)
      AND EXISTS (
          SELECT 1 FROM recommendations r
          JOIN recommendation_results res ON res.recommendation_id = r.id
          WHERE r.recommendation_run_id = rr.id AND res.{ret_col} IS NOT NULL
      )
    ORDER BY rr.executed_at
"""

ROWS_SQL = """
    SELECT r.id, r.stock_id, r.score, r.action, r.feature_snapshot_json,
           res.{ret_col} AS ret, res.{alpha_col} AS alpha
    FROM recommendations r
    JOIN recommendation_results res ON res.recommendation_id = r.id
    WHERE r.recommendation_run_id = $1
      AND res.{ret_col} IS NOT NULL
"""


def select(scored, threshold, top_n):
    """(key, score, ret, alpha) 목록에서 선택 규칙 적용 → key 집합과 항목 리스트."""
    if top_n:
        picked = sorted(scored, key=lambda x: x[1], reverse=True)[:top_n]
    else:
        picked = [x for x in scored if x[1] >= threshold]
    return {x[0] for x in picked}, picked


async def main():
    ap = argparse.ArgumentParser(description="모멘텀 v2.0 vs v2.1 같은 기간 반사실 비교")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--from", dest="fromdate", default=None, help="YYYY-MM-DD (포함)")
    ap.add_argument("--to", dest="todate", default=None, help="YYYY-MM-DD (포함)")
    # 1d 는 7일 성숙 전 조기 확인용. 노이즈가 크므로 방향 참고로만 쓸 것.
    ap.add_argument("--horizon", default="7d", choices=["1d", "7d", "30d"])
    ap.add_argument("--top-n", type=int, default=None,
                    help="런당 상위 N종목 선택. 미지정 시 임계값 기준")
    ap.add_argument("--threshold", type=float, default=None,
                    help=f"BUY 임계값 (기본 {scorer.BUY_THRESHOLD})")
    ap.add_argument("--max-abs-ret", type=float, default=1.0,
                    help="이상치 제외: |수익률| 이 이 값을 넘으면 버림 (0 이면 미적용)")
    ap.add_argument("--dsn", default=None, help="기본값: 환경변수 DATABASE_URL")
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL 이 없습니다. --dsn 으로 주거나 환경변수를 설정하세요.")
    dsn = dsn.strip().strip('"').replace("postgresql+asyncpg://", "postgresql://")

    # asyncpg 는 ::date 파라미터에 문자열을 받지 않는다 (date 객체여야 함).
    try:
        d_from = date.fromisoformat(args.fromdate) if args.fromdate else None
        d_to = date.fromisoformat(args.todate) if args.todate else None
    except ValueError as e:
        sys.exit(f"날짜 형식 오류 (YYYY-MM-DD 여야 합니다): {e}")

    conn = await asyncpg.connect(dsn)
    try:
        runs = await conn.fetch(
            RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to
        )
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}, {args.fromdate}~{args.todate}, "
                     f"{ret_col} 평가 완료분 기준). 평가 job 이 아직 안 돌았을 수 있습니다.")

        v20 = Acc("v2.0 (반사실)")
        v21 = Acc("v2.1 (현행)")
        only20 = Acc("v2.0 단독 선택")
        only21 = Acc("v2.1 단독 선택")
        both = Acc("공통 선택")

        n_rows = n_skipped = n_outliers = 0
        drift_sum = 0.0
        drift_n = drift_exact = 0
        overlap_stats = []
        versions = {}
        per_run = []

        rows_sql = ROWS_SQL.format(ret_col=ret_col, alpha_col=alpha_col)

        for run_id, executed_at, version_name in runs:
            versions[version_name] = versions.get(version_name, 0) + 1
            rows = await conn.fetch(rows_sql, run_id)
            if not rows:
                continue

            scored20, scored21 = [], []
            for rec in rows:
                ret = rec["ret"]
                alpha = rec["alpha"]
                ret_f = float(ret) if ret is not None else None
                alpha_f = float(alpha) if alpha is not None else None

                if args.max_abs_ret and ret_f is not None and abs(ret_f) > args.max_abs_ret:
                    n_outliers += 1
                    continue

                snapshot = rec["feature_snapshot_json"]
                try:
                    feat = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                    s21 = scorer.calculate_total_score(feat)["total_score"]
                    with momentum_v20():
                        s20 = scorer.calculate_total_score(feat)["total_score"]
                except Exception:
                    # 스냅샷 구조가 다른 과거 데이터는 건너뛴다.
                    n_skipped += 1
                    continue

                # 자기검증: 현행 스코어러 재채점값이 저장된 점수를 재현하는가.
                # (v2.1 런에서만 의미 있다. v2.0 런이면 당연히 어긋난다.)
                if version_name == "ensemble_v2.1" and rec["score"] is not None:
                    diff = abs(s21 - float(rec["score"]))
                    drift_sum += diff
                    drift_n += 1
                    if diff < 0.01:
                        drift_exact += 1

                n_rows += 1
                key = rec["stock_id"]
                scored20.append((key, s20, ret_f, alpha_f))
                scored21.append((key, s21, ret_f, alpha_f))

            if not scored20:
                continue

            keys20, picked20 = select(scored20, threshold, args.top_n)
            keys21, picked21 = select(scored21, threshold, args.top_n)

            for _, _, r, a in picked20:
                v20.add(r, a)
            for _, _, r, a in picked21:
                v21.add(r, a)

            inter = keys20 & keys21
            for k, _, r, a in picked20:
                if k not in inter:
                    only20.add(r, a)
            for k, _, r, a in picked21:
                if k in inter:
                    both.add(r, a)
                else:
                    only21.add(r, a)

            union = keys20 | keys21
            overlap_stats.append(len(inter) / len(union) if union else 1.0)

            run20 = Acc("")
            run21 = Acc("")
            for _, _, r, a in picked20:
                run20.add(r, a)
            for _, _, r, a in picked21:
                run21.add(r, a)
            per_run.append((executed_at, version_name, len(scored20),
                            run20.n, run20.avg_alpha, run21.n, run21.avg_alpha))
    finally:
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    print()
    print("=" * 88)
    print(f" 모멘텀 반사실 비교 — {args.market} / {args.horizon} / 선택규칙 {sel}")
    print(f" 기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(runs)}개 "
          f"({', '.join(f'{k} {v}건' for k, v in sorted(versions.items()))})")
    print("=" * 88)
    print(f" 채점 {n_rows:,}건 · 스냅샷 오류 {n_skipped:,} · 이상치 제외 {n_outliers:,}"
          f" (|ret|>{args.max_abs_ret})")

    if drift_n:
        print(f" 자기검증: v2.1 런 재채점 vs 저장 점수 — 평균 절대오차 {drift_sum / drift_n:.4f}, "
              f"정확 일치 {drift_exact / drift_n * 100:.1f}% ({drift_n:,}건)")
        if drift_sum / drift_n > 0.5:
            print("   ⚠️ 오차가 큽니다. 스냅샷/스코어러 불일치 가능성 — 결과를 신뢰하지 마세요.")
    else:
        print(" 자기검증: v2.1 런이 구간에 없어 생략 (v2.0 구간에 돌린 경우 정상)")

    if overlap_stats:
        print(f" 선택 중복도(Jaccard) 평균 {sum(overlap_stats) / len(overlap_stats) * 100:.1f}%")
    print("-" * 88)
    print(" 전체 선택 집합")
    print(v20.row())
    print(v21.row())
    if v20.avg_alpha is not None and v21.avg_alpha is not None:
        d_alpha = (v21.avg_alpha - v20.avg_alpha) * 100
        d_hit = (v21.hit_rate - v20.hit_rate) * 100
        print(f"  → v2.1 - v2.0 :  alpha {d_alpha:+.3f}%p   적중률 {d_hit:+.2f}%p")
    print("-" * 88)
    print(" 차이가 나는 부분만 (두 모델이 갈라선 종목 — 여기가 실제 효과)")
    print(both.row())
    print(only20.row())
    print(only21.row())
    if only20.avg_alpha is not None and only21.avg_alpha is not None:
        print(f"  → 단독 선택끼리 alpha 차 {(only21.avg_alpha - only20.avg_alpha) * 100:+.3f}%p")
    print("-" * 88)
    print(" 런별 (alpha)")
    print(f"  {'실행일':<12} {'버전':<14} {'채점':>7} {'v2.0 n':>7} {'v2.0 α':>9} {'v2.1 n':>7} {'v2.1 α':>9}")
    for executed_at, ver, scored_n, n20, a20, n21, a21 in per_run:
        f20 = f"{a20 * 100:+.3f}%" if a20 is not None else "-"
        f21 = f"{a21 * 100:+.3f}%" if a21 is not None else "-"
        print(f"  {executed_at.strftime('%Y-%m-%d'):<12} {ver:<14} {scored_n:>7,} "
              f"{n20:>7,} {f20:>9} {n21:>7,} {f21:>9}")
    print("=" * 88)
    print()


if __name__ == "__main__":
    asyncio.run(main())
