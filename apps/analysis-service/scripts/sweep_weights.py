"""
가중치 스윕 — **모집단을 고정한 채** 비교한다.

왜 필요한가
-----------
`/backtest/rescore` 로 US 가중치를 스윕하면 가치 비중을 올릴수록 알파가 단조
개선되고, 모멘텀 0 에서 최고가 나온다(+0.671%). 그런데 이건 밸류 신호가 아니라
**모집단이 바뀐 결과일 수 있다.**

적응형 가중치는 데이터 품질이 0 인 전략의 비중을 다른 전략에 재분배한다.
그래서 base 가 `가치 100%` 면, PER/PBR 이 없는 종목은 총점이 value_score 기본값
50 에 머물러 임계값 65 를 못 넘고 **통째로 탈락**한다. 즉 "가치로 잘 골랐다" 가
아니라 "재무제표가 있는 회사만 남았다" 일 수 있다.

실제로 US 는 채점 전부터 이만큼 갈린다(2026-08-09 측정, 평가 완료 전 종목):
    가치 데이터 있음 264,677건 → 적중 50.87% / 알파 +0.502%
    없음              62,478건 → 적중 38.80% / 알파 -0.093%
적중률 12%p 차이가 **가중치와 무관하게** 이미 존재한다. 워런트·SPAC·초저유동성
종목이 걸러지는 품질 필터 효과다.

그래서 이 스크립트는 `--require-value` 로 **모집단을 먼저 고정**하고 그 안에서만
가중치를 바꾼다. 그래도 가치 비중이 이기면 진짜 신호이고, 차이가 사라지면
품질 필터였던 것이다. (P2-9 수급에서 '규모 정규화 대조'가 스퓨리어스 팩터를
막아준 것과 같은 구조다.)

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/sweep_weights.py --market US                  # 모집단 고정(기본)
  .venv/bin/python scripts/sweep_weights.py --market US --no-require-value  # 대조: 전 종목
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from _common import Acc, parse_dates, resolve_dsn  # noqa: E402
from _common import Verifier, add_scorer_arg, scorer_arm  # noqa: E402
from app.engine import scorer  # noqa: E402

# (모멘텀, 가치, 감성)
GRID = [
    (0.45, 0.25, 0.30),   # 현행
    (0.35, 0.35, 0.30),
    (0.30, 0.40, 0.30),
    (0.25, 0.50, 0.25),
    (0.20, 0.60, 0.20),
    (0.15, 0.70, 0.15),
    (0.10, 0.80, 0.10),
    (0.05, 0.90, 0.05),
    (0.00, 1.00, 0.00),
    (0.00, 0.70, 0.30),
]

RUNS_SQL = """
    SELECT rr.id, rr.executed_at
    FROM recommendation_runs rr
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
    SELECT r.stock_id, r.score AS stored_score, r.feature_snapshot_json,
           res.{ret_col} AS ret, res.{alpha_col} AS alpha
    FROM recommendations r
    JOIN recommendation_results res ON res.recommendation_id = r.id
    WHERE r.recommendation_run_id = $1
      AND res.{ret_col} IS NOT NULL
"""


def has_value_data(feat) -> bool:
    f = feat.get("fundamental") or {}
    return f.get("per_relative") is not None or f.get("pbr_relative") is not None


async def main():
    ap = argparse.ArgumentParser(description="모집단 고정 가중치 스윕")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--from", dest="fromdate", default=None)
    ap.add_argument("--to", dest="todate", default=None)
    ap.add_argument("--horizon", default="7d", choices=["1d", "7d", "30d"])
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--max-abs-ret", type=float, default=1.0)
    ap.add_argument("--dsn", default=None)
    add_scorer_arg(ap)
    ap.add_argument("--require-value", dest="require_value", action="store_true", default=True,
                    help="가치 데이터가 있는 종목으로 모집단을 고정 (기본)")
    ap.add_argument("--no-require-value", dest="require_value", action="store_false",
                    help="전 종목 — 기존 rescore API 와 같은 조건 (대조군)")
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    accs = {w: Acc(f"{w[0]*100:.0f}/{w[1]*100:.0f}/{w[2]*100:.0f}") for w in GRID}
    n_rows = n_skipped = n_outliers = n_filtered = 0

    use_v20 = args.scorer == "v20"
    verifier = Verifier()

    conn = await asyncpg.connect(dsn)
    try:
        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}).")
        rows_sql = ROWS_SQL.format(ret_col=ret_col, alpha_col=alpha_col)

        for run_id, _executed_at in runs:
            rows = await conn.fetch(rows_sql, run_id)
            for rec in rows:
                ret = rec["ret"]
                alpha = rec["alpha"]
                ret_f = float(ret) if ret is not None else None
                alpha_f = float(alpha) if alpha is not None else None
                if args.max_abs_ret and ret_f is not None and abs(ret_f) > args.max_abs_ret:
                    n_outliers += 1
                    continue
                try:
                    snapshot = rec["feature_snapshot_json"]
                    feat = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                    if args.require_value and not has_value_data(feat):
                        n_filtered += 1
                        continue
                    # 자기검증 — 배포 가중치로 채점하면 저장 점수가 재현돼야 한다.
                    # (스윕값이 아니라 **현행 설정**으로 재는 것이 요점이다)
                    with scorer_arm(use_v20):
                        s_base = scorer.calculate_total_score(feat)["total_score"]
                    verifier.check(rec["stored_score"], s_base)

                    # 한 행을 한 번만 읽고 모든 가중치로 채점한다 (DB 재조회 없음)
                    for w in GRID:
                        base = {"momentum": w[0], "value": w[1], "sentiment": w[2]}
                        total = sum(base.values())
                        if total <= 0:
                            continue
                        base = {k: v / total for k, v in base.items()}
                        with scorer_arm(use_v20):
                            s = scorer.calculate_total_score(feat, base_weights=base)["total_score"]
                        if s >= threshold:
                            accs[w].add(ret_f, alpha_f)
                except Exception:
                    n_skipped += 1
                    continue
                n_rows += 1
    finally:
        await conn.close()

    scope = "가치데이터 보유 종목만" if args.require_value else "전 종목(대조군)"
    print()
    print("=" * 76)
    print(f" 가중치 스윕 — {args.market} / {args.horizon} / 임계값 {threshold} / 모집단: {scope}")
    print(f" 런 {len(runs)}개 · 채점 {n_rows:,}건 · 모집단 제외 {n_filtered:,} · "
          f"이상치 {n_outliers:,} · 오류 {n_skipped:,}")
    print(f" 스코어러 arm: {args.scorer}")
    for line in verifier.lines():
        print(f" {line}")
    print("=" * 76)
    base_alpha = accs[GRID[0]].avg_alpha
    print(f" {'모/가/감':<12}{'선택수':>9}{'적중률':>9}{'수익률':>10}{'알파':>10}{'Δ알파':>11}")
    print("-" * 76)
    for w in GRID:
        a = accs[w]
        if a.n == 0:
            print(f" {a.label:<12}{'0':>9}")
            continue
        d = (a.avg_alpha - base_alpha) * 100 if (a.avg_alpha is not None and base_alpha is not None) else 0.0
        tag = a.label + ("*" if w == GRID[0] else "")
        print(f" {tag:<12}{a.n:>9,}{a.hit_rate*100:>8.2f}%{a.avg_ret*100:>9.3f}%"
              f"{a.avg_alpha*100:>9.3f}%{d:>+10.3f}%p")
    print("-" * 76)
    print(" * = 현행 가중치")
    print()


if __name__ == "__main__":
    asyncio.run(main())
