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

  A arm = v2.0 : `_momentum_score` 만 동결 사본(scripts/scorer_v20_frozen.py)으로 교체해 재채점
  B arm = v2.1 : 현행 `app.engine.scorer` 로 재채점

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
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/counterfactual_momentum.py --market US --from 2026-07-27

  # 방법 자체 검증: v2.0 운영 구간에 돌려 07-27 오프라인 백테스트 결과가 재현되는지
  .venv/bin/python scripts/counterfactual_momentum.py --market US \
      --from 2026-07-20 --to 2026-07-22 --top-n 20
"""

import argparse
import asyncio
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

# scripts/ 에서 실행해도 app 패키지를 찾도록
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from _common import Acc, add_common_args, parse_dates, report, resolve_dsn, select  # noqa: E402
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
    SELECT r.id, r.stock_id, r.score, r.feature_snapshot_json,
           res.{ret_col} AS ret, res.{alpha_col} AS alpha
    FROM recommendations r
    JOIN recommendation_results res ON res.recommendation_id = r.id
    WHERE r.recommendation_run_id = $1
      AND res.{ret_col} IS NOT NULL
"""


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="모멘텀 v2.0 vs v2.1 같은 기간 반사실 비교")
    )
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    conn = await asyncpg.connect(dsn)
    try:
        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
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
        overlap, versions, per_run = [], {}, []
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
            inter = keys20 & keys21

            run20, run21 = Acc(""), Acc("")
            for k, _, r, a in picked20:
                v20.add(r, a)
                run20.add(r, a)
                if k not in inter:
                    only20.add(r, a)
            for k, _, r, a in picked21:
                v21.add(r, a)
                run21.add(r, a)
                if k in inter:
                    both.add(r, a)
                else:
                    only21.add(r, a)

            union = keys20 | keys21
            overlap.append(len(inter) / len(union) if union else 1.0)
            per_run.append((executed_at, version_name, len(scored20),
                            run20.n, run20.avg_alpha, run21.n, run21.avg_alpha))
    finally:
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    meta = [
        f"기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(runs)}개 "
        f"({', '.join(f'{k} {v}건' for k, v in sorted(versions.items()))})",
        f"채점 {n_rows:,}건 · 스냅샷 오류 {n_skipped:,} · 이상치 제외 {n_outliers:,}"
        f" (|ret|>{args.max_abs_ret})",
    ]
    if drift_n:
        line = (f"자기검증: v2.1 런 재채점 vs 저장 점수 — 평균 절대오차 {drift_sum / drift_n:.4f}, "
                f"정확 일치 {drift_exact / drift_n * 100:.1f}% ({drift_n:,}건)")
        if drift_sum / drift_n > 0.5:
            line += "\n   ⚠️ 오차가 큽니다. 스냅샷/스코어러 불일치 가능성 — 결과를 신뢰하지 마세요."
        meta.append(line)
    else:
        meta.append("자기검증: v2.1 런이 구간에 없어 생략 (v2.0 구간에 돌린 경우 정상)")

    report(f"모멘텀 반사실 비교 — {args.market} / {args.horizon} / 선택규칙 {sel}",
           meta, v20, v21, both, only20, only21, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
