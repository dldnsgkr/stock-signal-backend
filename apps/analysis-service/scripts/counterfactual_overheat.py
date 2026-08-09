"""
과열 추격 감점을 강화하면 나아지는가 — 배포 전 반사실 검증.

배경 (2026-08-10 조사)
---------------------
점수-알파 곡선이 ∩자이고 85점 위에서 절벽이다
(US 55~65 +0.346% → 85~90 −0.893% → 90+ **−4.878%**, 적중 31.1%).

유동성 탓이 아니다 — 90+ 안에서는 유동성이 **높을수록 나쁘다**
(<$1M −3.98% / $1~10M −5.47% / ≥$10M **−8.18%**). 65~90 은 정반대다.
유동종목이 90점을 받으려면 보너스가 공짜가 아니라 진짜로 다 터져야 하기 때문.

원인은 **중복 계산**이다. MA20 위치(+15)·MA60(+10)·MACD(+8/+7)·OBV(+8) 이
전부 "주가가 올랐다" 는 같은 사실의 함수인데 각각 가점을 준다. v2.1 이 넣은
과열 감점(모멘텀 −8/−15, RSI −4, BB −4)은 그 합 +66 을 못 이긴다.
90+ 유동종목 중앙값: 20일 **+20.2%**, 5일 +7.8%, MA20 대비 +11.1%, BB 0.90.

확인: 90+ 안에서 상승폭이 클수록 단조로 나빠진다 —
US 20일 <5% −0.67% / 5~15% −1.16% / 15~30% −3.27% / **≥30% −10.78%**.

무엇을 바꾸는가
--------------
`_momentum_score` 에 **20일 모멘텀 기준 추가 감점**을 넣는다. 감점 강도는 `--penalty`.
배포한 스코어러 위에 얹는 **절대 스케줄**이라 v2.0/v2.1 두 구간에 동일하게 적용된다.

⚠️ **클리핑 전에 넣어야 한다.**
`_momentum_score` 는 마지막에 `min(100, ...)` 로 자르는데, 90점 이상 종목의
**67.5%(US)·51.5%(KR)** 가 모멘텀 100 으로 클리핑된 상태다. 사후에 감점하면
바로 그 집단에서 감점이 과대평가된다. 그래서 함수를 복사해 감점을 안쪽에 넣되,
**추가감점 0 일 때 원본과 완전히 일치하는지 매 행 검증**한다(복사본 표류 방지).

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  # A구간(v2.0 으로 채점된 기간) — 자기검증 100% 인 07-01 부터만 쓴다
  .venv/bin/python scripts/counterfactual_overheat.py --market US --scorer v20 \
      --from 2026-07-01 --to 2026-07-26 --penalty strong
  # B구간(v2.1)
  .venv/bin/python scripts/counterfactual_overheat.py --market US --penalty strong
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

from _common import Acc, add_common_args, parse_dates, report, resolve_dsn, select  # noqa: E402
from app.engine import scorer  # noqa: E402
from scorer_v20_frozen import momentum_score_v20  # noqa: E402

# 추가 감점 스케줄 — 측정된 절벽(20일 ≥30%)과 열화 시작점(~15%)에 맞췄다.
# 배포된 밴드 위에 **더해지는** 값이라 v2.0/v2.1 어느 쪽에도 같은 개입이 된다.
PENALTIES = {
    "mild":   [(0.30, -10.0), (0.20, -5.0),  (0.15, -2.0)],
    "strong": [(0.30, -25.0), (0.20, -12.0), (0.15, -5.0)],
    "veto":   [(0.30, -60.0), (0.20, -12.0), (0.15, -5.0)],
}


def extra_penalty(mom_20d, schedule):
    if mom_20d is None:
        return 0.0
    for lo, pen in schedule:
        if mom_20d >= lo:
            return pen
    return 0.0


def _momentum_common(t, score, data_points):
    """v2.0 / v2.1 이 **동일한** 나머지 항. 두 사본이 갈라지지 않게 한 곳에 둔다."""
    vol_growth = t.get("volume_growth_rate", 0)
    score += 12 if vol_growth > 0.5 else (6 if vol_growth > 0.2 else (-8 if vol_growth < -0.3 else 0))
    data_points += 1

    rsi = t.get("rsi")
    if rsi is not None:
        if rsi < 30:
            score += 15
        elif rsi < 45:
            score += 8
        elif rsi > 70:
            score -= 12
        elif rsi > 60:
            score -= 4
        data_points += 1

    macd_h = t.get("macd_histogram")
    macd_h_prev = t.get("macd_histogram_prev")
    if macd_h is not None:
        score += 8 if macd_h > 0 else -8
        if macd_h_prev is not None:
            if macd_h > 0 and macd_h_prev <= 0:
                score += 7
            elif macd_h < 0 and macd_h_prev >= 0:
                score -= 7
        data_points += 1

    bb_pos = t.get("bb_position")
    if bb_pos is not None:
        if bb_pos < 0:
            score += 12
        elif bb_pos < 0.2:
            score += 6
        elif bb_pos > 1:
            score -= 10
        elif bb_pos > 0.8:
            score -= 4
        data_points += 1

    obv = t.get("obv_trend")
    if obv is not None:
        score += 8 if obv > 0.3 else (4 if obv > 0 else (-6 if obv < -0.3 else -2))
        data_points += 1

    return score, data_points


def make_momentum(base: str, schedule):
    """감점을 **클리핑 전에** 넣은 `_momentum_score` 사본을 만든다."""
    def fn(features: dict):
        t = features["technical"]
        score = 50.0
        data_points = 0

        ma20_pos = t.get("ma20_position", 0)
        score += 15 if ma20_pos > 0.05 else (8 if ma20_pos > 0 else (-15 if ma20_pos < -0.05 else -8))
        data_points += 1

        ma60_pos = t.get("ma60_position", 0)
        score += 10 if ma60_pos > 0.03 else (-10 if ma60_pos < -0.03 else 0)
        data_points += 1

        mom_5d = t.get("momentum_5d", 0)
        mom_20d = t.get("momentum_20d", 0)
        if base == "v20":
            score += 8 if mom_5d > 0.05 else (4 if mom_5d > 0.02 else (-8 if mom_5d < -0.05 else 0))
            data_points += 1
            score += 10 if mom_20d > 0.10 else (5 if mom_20d > 0.03 else (-10 if mom_20d < -0.10 else 0))
            data_points += 1
        else:
            score += (-8 if mom_5d > 0.15 else -2 if mom_5d > 0.08 else 6 if mom_5d > 0.02
                      else 3 if mom_5d > 0 else 1 if mom_5d > -0.05 else -4 if mom_5d > -0.15 else -8)
            data_points += 1
            score += (-15 if mom_20d > 0.40 else -8 if mom_20d > 0.20 else -2 if mom_20d > 0.10
                      else 8 if mom_20d > 0.03 else 4 if mom_20d > 0 else 2 if mom_20d > -0.10
                      else -4 if mom_20d > -0.20 else -8)
            data_points += 1

        score, data_points = _momentum_common(t, score, data_points)

        if schedule:                                   # ← 클리핑 **전**
            score += extra_penalty(mom_20d, schedule)

        quality = min(1.0, data_points / 8)
        return max(0.0, min(100.0, score)), quality
    return fn


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


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="과열 감점 강화 반사실 검증"),
        default_market="US",
    )
    ap.add_argument("--scorer", choices=["current", "v20"], default="current",
                    help="v20 = 2026-07-26 이전 런(그 구간은 v2.0 으로 채점돼 있다)")
    ap.add_argument("--penalty", choices=sorted(PENALTIES), default="strong")
    args = ap.parse_args()

    base = "v20" if args.scorer == "v20" else "v21"
    deployed = momentum_score_v20 if base == "v20" else scorer._momentum_score
    copy_zero = make_momentum(base, None)          # 검증용 — 추가감점 없음
    copy_pen = make_momentum(base, PENALTIES[args.penalty])

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    conn = await asyncpg.connect(dsn)
    try:
        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}).")
        rows_sql = ROWS_SQL.format(ret_col=ret_col, alpha_col=alpha_col)

        cur = Acc("현행")
        alt = Acc(f"과열감점 {args.penalty}")
        only_cur = Acc("현행 단독 선택")
        only_alt = Acc("감점강화 단독 선택")
        both = Acc("공통 선택")

        n_rows = n_skipped = n_outliers = n_changed = 0
        verify_ok = verify_tot = 0          # 재채점이 저장 점수를 재현하는가
        copy_ok = copy_tot = 0              # 복사본이 원본 함수를 재현하는가
        overlap, per_run = [], []

        original_mom = scorer._momentum_score
        try:
            for run_id, T in runs:
                rows = await conn.fetch(rows_sql, run_id)
                if not rows:
                    continue
                scored_cur, scored_alt = [], []
                run_changed = 0

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

                        # 복사본 검증 — 추가감점 0 이면 배포 함수와 완전히 같아야 한다
                        copy_tot += 1
                        if abs(copy_zero(feat)[0] - deployed(feat)[0]) < 1e-9:
                            copy_ok += 1

                        scorer._momentum_score = copy_zero
                        s_cur = scorer.calculate_total_score(feat)["total_score"]
                        scorer._momentum_score = copy_pen
                        s_alt = scorer.calculate_total_score(feat)["total_score"]

                        stored = rec["stored_score"]
                        if stored is not None:
                            verify_tot += 1
                            if abs(float(stored) - s_cur) < 0.011:
                                verify_ok += 1
                        if abs(s_alt - s_cur) > 1e-9:
                            n_changed += 1
                            run_changed += 1
                    except Exception:
                        n_skipped += 1
                        continue

                    n_rows += 1
                    sid = rec["stock_id"]
                    scored_cur.append((sid, s_cur, ret_f, alpha_f))
                    scored_alt.append((sid, s_alt, ret_f, alpha_f))

                if not scored_cur:
                    continue

                keys_cur, picked_cur = select(scored_cur, threshold, args.top_n)
                keys_alt, picked_alt = select(scored_alt, threshold, args.top_n)
                inter = keys_cur & keys_alt

                run_cur, run_alt = Acc(""), Acc("")
                for k, _, r, a in picked_cur:
                    cur.add(r, a); run_cur.add(r, a)
                    if k not in inter:
                        only_cur.add(r, a)
                for k, _, r, a in picked_alt:
                    alt.add(r, a); run_alt.add(r, a)
                    if k in inter:
                        both.add(r, a)
                    else:
                        only_alt.add(r, a)

                union = keys_cur | keys_alt
                overlap.append(len(inter) / len(union) if union else 1.0)
                per_run.append((T, f"점수변동 {run_changed:,}", len(scored_cur),
                                run_cur.n, run_cur.avg_alpha, run_alt.n, run_alt.avg_alpha))
        finally:
            scorer._momentum_score = original_mom
    finally:
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    v_rate = verify_ok / verify_tot * 100 if verify_tot else 0.0
    c_rate = copy_ok / copy_tot * 100 if copy_tot else 0.0
    sched = " / ".join(f"20일>={lo:.0%} → {pen:+.0f}" for lo, pen in PENALTIES[args.penalty])
    meta = [
        f"기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(per_run)}개 · 기준 {base}",
        f"채점 {n_rows:,}건 · 점수변동 {n_changed:,} · 이상치 {n_outliers:,} · 오류 {n_skipped:,}",
        f"추가감점({args.penalty}): {sched}   ※ 클리핑 전에 적용",
        f"복사본 검증: 원본 함수 재현 {c_rate:.1f}% ({copy_ok:,}/{copy_tot:,})",
        f"자기검증: 재채점이 저장 점수를 재현 {v_rate:.1f}% ({verify_ok:,}/{verify_tot:,})",
    ]
    if v_rate < 95 or c_rate < 99.99:
        meta.append("⚠️ 검증 실패 — 결과를 믿지 말 것")
    report(f"과열 감점 강화 반사실 검증 — {args.market} / {args.horizon} / 선택규칙 {sel}",
           meta, cur, alt, both, only_cur, only_alt, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
