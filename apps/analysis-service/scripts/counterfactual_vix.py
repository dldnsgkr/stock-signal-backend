"""
KR 에 VIX 를 붙이면 나아지는가 — 배포 전 반사실 검증.

배경
----
`macro_indicators` 의 VIX 는 `market_code='US'` 로만 적재되고,
`feature_builder` 가 거시지표를 `market_code == market_code` 로 필터하기 때문에
**KR 스냅샷의 `macro.vix` 는 항상 None** 이다(2026-07-30 런 실측 커버리지 0.0%).

그 결과 `_sentiment_score` 의 `data_points` 가 1 줄어 감성 품질이 0 에 가까워지고,
KR 은 가치(PER/PBR 0%)·뉴스(7.4%)까지 비어 있어 **적응형 가중치가 모멘텀으로
쏠린다** — KR 모멘텀 가중치 평균 0.889, 모멘텀 100%로 채점되는 종목이 40.7%.

주의할 점 — VIX 는 대부분의 날 '점수'가 아니라 '가중치'만 바꾼다
--------------------------------------------------------------
VIX 시계열 134일 중 **97일(72%)이 15~20 구간이라 점수 기여가 0** 이다
(`>20 -5`, `>25 -15`, `<15 +10`). 그런데 값이 있기만 하면 `data_points += 1` 이라
감성 품질이 0 → 0.25 로 오른다. 즉 KR 종목 다수에서 VIX 주입은
**중립 50점을 0.25 가중치로 섞는 것** = 총점을 50 쪽으로 압축하는 효과다.
모멘텀이 KR 의 유일한 작동 신호인 상황에서 이게 이득인지 손해인지는 선험적으로
알 수 없다. 그래서 배포 전에 측정한다.

방법
----
같은 기간·같은 종목 스냅샷을 현행 스코어러로 두 번 채점한다.
  A arm = 현행      : 스냅샷 그대로 (KR 은 vix=None)
  B arm = VIX 주입  : `macro.vix` 에 **런 실행일 기준 as-of VIX** 를 넣고 재채점
as-of(관측일 <= 런 실행일 중 최신)만 쓰므로 look-ahead 가 없다.
모멘텀 버전은 양쪽 동일하므로 v2.0/v2.1 구간을 섞어도 되고, 덕분에 7일 수익률이
이미 성숙한 과거 KR 런 전체(41개)를 표본으로 쓸 수 있다.

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/counterfactual_vix.py --market KR
  .venv/bin/python scripts/counterfactual_vix.py --market KR --top-n 20
"""

import argparse
import asyncio
import json
import os
import sys
from bisect import bisect_right
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from _common import Acc, add_common_args, parse_dates, report, resolve_dsn, select  # noqa: E402
from app.engine import scorer  # noqa: E402

# VIX 는 US 로만 적재돼 있다. 시장 무관 전역 위험지표로 보고 그대로 가져온다.
VIX_SQL = """
    SELECT observed_at, value FROM macro_indicators
    WHERE indicator_type = 'VIX' ORDER BY observed_at
"""

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
    SELECT r.stock_id, r.feature_snapshot_json,
           res.{ret_col} AS ret, res.{alpha_col} AS alpha
    FROM recommendations r
    JOIN recommendation_results res ON res.recommendation_id = r.id
    WHERE r.recommendation_run_id = $1
      AND res.{ret_col} IS NOT NULL
"""


def asof_vix(dates, values, when):
    """관측일 <= when 중 가장 최근 VIX. 없으면 None (look-ahead 방지)."""
    i = bisect_right(dates, when)
    return values[i - 1] if i else None


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="VIX 주입/제거 효과 반사실 검증"),
        default_market="KR",
    )
    # VIX 는 같은 날 모든 종목에 동일한 상수라 종목 간 순위 정보가 0 이다.
    # 그런데 값이 있으면 감성 품질을 0.25 올려 '판별력 있는' 신호를 희석시킨다.
    # inject 는 그 희석을 KR 에 추가했을 때, remove 는 US 에서 걷어냈을 때를 본다.
    ap.add_argument("--mode", default="inject", choices=["inject", "remove"],
                    help="inject: VIX 없는 스냅샷에 주입(KR) / remove: 있는 VIX 제거(US)")
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    if args.mode == "inject" and args.market == "US":
        print("⚠️ US 스냅샷에는 이미 VIX 가 100% 들어 있어 주입해도 차이가 없습니다. "
              "--mode remove 를 쓰거나 KR 로 돌리세요.", file=sys.stderr)
    if args.mode == "remove" and args.market == "KR":
        print("⚠️ KR 스냅샷에는 VIX 가 0% 라 제거해도 차이가 없습니다.", file=sys.stderr)

    conn = await asyncpg.connect(dsn)
    try:
        vix_rows = await conn.fetch(VIX_SQL)
        if not vix_rows:
            sys.exit("macro_indicators 에 VIX 가 없습니다.")
        vix_dates = [r["observed_at"] for r in vix_rows]
        vix_values = [float(r["value"]) for r in vix_rows]

        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}, {ret_col} 평가 완료분 기준).")

        b_label = "VIX 주입" if args.mode == "inject" else "VIX 제거"
        cur = Acc("현행")
        inj = Acc(b_label)
        only_cur = Acc("현행 단독 선택")
        only_inj = Acc(f"{b_label} 단독 선택")
        both = Acc("공통 선택")

        n_rows = n_skipped = n_outliers = n_already = 0
        overlap, per_run, no_vix_runs = [], [], 0
        rows_sql = ROWS_SQL.format(ret_col=ret_col, alpha_col=alpha_col)

        for run_id, executed_at in runs:
            if args.mode == "inject":
                vix = asof_vix(vix_dates, vix_values, executed_at)
                if vix is None:
                    no_vix_runs += 1
                    continue
            else:
                vix = None  # remove: 스냅샷의 VIX 를 None 으로 덮는다

            rows = await conn.fetch(rows_sql, run_id)
            if not rows:
                continue

            scored_cur, scored_inj = [], []
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
                    s_cur = scorer.calculate_total_score(feat)["total_score"]

                    macro = feat.get("macro") or {}
                    original = macro.get("vix")
                    # 두 arm 이 같아져 효과가 희석되는 건수 (inject: 이미 있음 / remove: 원래 없음)
                    if (original is not None) == (args.mode == "inject"):
                        n_already += 1
                    macro["vix"] = vix
                    feat["macro"] = macro
                    try:
                        s_inj = scorer.calculate_total_score(feat)["total_score"]
                    finally:
                        macro["vix"] = original  # 스냅샷 원복 (같은 dict 재사용 방지)
                except Exception:
                    n_skipped += 1
                    continue

                n_rows += 1
                key = rec["stock_id"]
                scored_cur.append((key, s_cur, ret_f, alpha_f))
                scored_inj.append((key, s_inj, ret_f, alpha_f))

            if not scored_cur:
                continue

            keys_cur, picked_cur = select(scored_cur, threshold, args.top_n)
            keys_inj, picked_inj = select(scored_inj, threshold, args.top_n)
            inter = keys_cur & keys_inj

            run_cur, run_inj = Acc(""), Acc("")
            for k, _, r, a in picked_cur:
                cur.add(r, a)
                run_cur.add(r, a)
                if k not in inter:
                    only_cur.add(r, a)
            for k, _, r, a in picked_inj:
                inj.add(r, a)
                run_inj.add(r, a)
                if k in inter:
                    both.add(r, a)
                else:
                    only_inj.add(r, a)

            union = keys_cur | keys_inj
            overlap.append(len(inter) / len(union) if union else 1.0)
            note = f"VIX {vix:.2f}" if vix is not None else "VIX 제거"
            per_run.append((executed_at, note, len(scored_cur),
                            run_cur.n, run_cur.avg_alpha, run_inj.n, run_inj.avg_alpha))
    finally:
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    meta = [
        f"기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(per_run)}개"
        + (f" (VIX 없는 런 {no_vix_runs}개 제외)" if no_vix_runs else ""),
        f"채점 {n_rows:,}건 · 스냅샷 오류 {n_skipped:,} · 이상치 제외 {n_outliers:,}"
        f" (|ret|>{args.max_abs_ret})",
    ]
    if n_already:
        state = "이미 VIX 가 있던" if args.mode == "inject" else "원래 VIX 가 없던"
        meta.append(f"⚠️ {state} 스냅샷 {n_already:,}건 — 그만큼 두 arm 이 동일해져 효과가 희석됩니다")
    if args.top_n is None:
        meta.append("선택 규칙이 임계값이라 두 arm 의 선택 종목 수가 다를 수 있습니다 "
                    "(BUY 수 변화 자체가 VIX 의 주 효과)")

    mode_label = "주입" if args.mode == "inject" else "제거"
    report(f"VIX {mode_label} 반사실 검증 — {args.market} / {args.horizon} / 선택규칙 {sel}",
           meta, cur, inj, both, only_cur, only_inj, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
