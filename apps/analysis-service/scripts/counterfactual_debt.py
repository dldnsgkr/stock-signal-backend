"""
부채비율을 가치 전략에 넣으면 나아지는가 — 배포 전 반사실 검증 (KR 전용).

배경
----
`financial_metrics.debt_ratio` 는 수집만 하고 스코어링에 전혀 안 쓰인다
(커버리지 KR 82% / US 76%). 분위 분석에서 KR 만 관계가 확인됐다.

3단계 사전 검증 결과 (2026-08-09, 90일):
  1단계 효과      — KR Q1(저부채) 적중 37.1% vs Q5 33.5%
  2단계 규모 대조 — 거래대금 5분위 안에서 다시 나눠도 3.0%p 유지 (US 는 절반이 사이즈)
  3단계 기간 분리 — KR 은 전·후반 모두 Q1 우세 (US 는 전반 Q5/후반 Q4 로 방향 불일치 → 기각)
→ **KR 전용 피처로만 검토한다.**

밴드 설계
--------
절대 구간별 적중률: <0.3 36.0% / 0.3~0.6 33.9% / 0.6~1.0 33.7% / 1.0~2.0 33.7% / >=2.0 32.9%.
**중간 구간이 서로 구분되지 않으므로**(33.7~33.9%) 5단 사다리를 맞추면 과최적화다.
신뢰할 수 있는 건 "0.3 미만이 확실히 낫다" 하나 + 고부채 끝의 약한 열위.
→ 저부채 가점 / 중간 중립 / 초고부채 소폭 감점으로만 둔다.

`data_points` 는 건드리지 않는다 — 부채비율을 품질에 포함시키면 가치 전략의 가중치가
전 종목에서 재분배되어 부수효과가 커진다. 이번엔 **점수만** 바꿔 순수 효과를 본다.

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/counterfactual_debt.py --market KR
  .venv/bin/python scripts/counterfactual_debt.py --market KR --top-n 20
"""

import argparse
import asyncio
import json
import os
import sys
from bisect import bisect_right
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from _common import Acc, add_common_args, parse_dates, report, resolve_dsn, select  # noqa: E402
from _common import Verifier, add_scorer_arg, scorer_arm  # noqa: E402
from app.engine import scorer  # noqa: E402

LOW_DEBT = 0.3      # 이 아래가 뚜렷하게 낫다 (적중 36.0% vs 나머지 ~33.5%)
HIGH_DEBT = 2.0     # 끝단만 약하게 열위 (32.9%)
LOW_BONUS = 8.0
HIGH_PENALTY = -5.0


COUNT_QUALITY = False   # --count-quality 로 켠다
BAND_OFF = False        # --band-off: 점수 밴드를 끄고 품질만 올린다(가중치 효과 분리용)


def make_value_score_with_debt(original):
    """기존 `_value_score` 에 부채비율 항만 더한 버전을 만든다."""
    def patched(features: dict):
        score, quality = original(features)
        debt = (features.get("fundamental") or {}).get("debt_ratio")
        if debt is None:
            return score, quality
        if not BAND_OFF:
            if debt < LOW_DEBT:
                score += LOW_BONUS
            elif debt >= HIGH_DEBT:
                score += HIGH_PENALTY
        if COUNT_QUALITY:
            # 부채비율을 '아는 것' 으로 세면 가치 품질이 올라 가중치가 커진다.
            # 원본 quality 는 data_points/3 이므로 1/3 을 더한 것과 같다.
            quality = min(1.0, quality + 1.0 / 3.0)
        return max(0.0, min(100.0, score)), quality
    return patched


@contextmanager
def debt_aware_value_score():
    original = scorer._value_score
    scorer._value_score = make_value_score_with_debt(original)
    try:
        yield
    finally:
        scorer._value_score = original


FIN_SQL = """
    SELECT fm.stock_id, fm.period_end, fm.debt_ratio
    FROM financial_metrics fm
    JOIN stocks s ON s.id = fm.stock_id
    JOIN markets m ON m.id = s.market_id
    WHERE m.code = $1 AND fm.debt_ratio IS NOT NULL
    ORDER BY fm.stock_id, fm.period_end
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
    SELECT r.stock_id, r.score AS stored_score, r.feature_snapshot_json,
           res.{ret_col} AS ret, res.{alpha_col} AS alpha
    FROM recommendations r
    JOIN recommendation_results res ON res.recommendation_id = r.id
    WHERE r.recommendation_run_id = $1
      AND res.{ret_col} IS NOT NULL
"""


def asof(keys, values, when):
    i = bisect_right(keys, when)
    return values[i - 1] if i else None


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="부채비율 주입 효과 반사실 검증"),
        default_market="KR",
    )
    ap.add_argument("--count-quality", action="store_true",
                    help="부채비율을 데이터 품질에 포함 (가치 가중치가 올라간다)")
    ap.add_argument("--band-off", action="store_true",
                    help="점수 밴드를 끄고 품질만 반영 — '부채 신호' 와 '가중치 상승' 을 분리한다")
    add_scorer_arg(ap)
    args = ap.parse_args()

    global COUNT_QUALITY, BAND_OFF
    COUNT_QUALITY = args.count_quality
    BAND_OFF = args.band_off

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    arm = args.scorer
    verifier = Verifier()

    conn = await asyncpg.connect(dsn)
    try:
        fin_rows = await conn.fetch(FIN_SQL, args.market)
        debt_by_stock: dict = {}
        for r in fin_rows:
            debt_by_stock.setdefault(r["stock_id"], []).append(
                (r["period_end"], float(r["debt_ratio"]))
            )
        debt_dates = {k: [e[0] for e in v] for k, v in debt_by_stock.items()}
        debt_vals = {k: [e[1] for e in v] for k, v in debt_by_stock.items()}

        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}).")

        cur = Acc("현행 (부채 미사용)")
        inj = Acc("부채비율 반영")
        only_cur = Acc("현행 단독 선택")
        only_inj = Acc("부채 단독 선택")
        both = Acc("공통 선택")

        n_rows = n_skipped = n_outliers = n_have_debt = 0
        overlap, per_run = [], []
        rows_sql = ROWS_SQL.format(ret_col=ret_col, alpha_col=alpha_col)

        for run_id, T in runs:
            t_date = T.date()
            rows = await conn.fetch(rows_sql, run_id)
            if not rows:
                continue

            scored_cur, scored_inj = [], []
            run_have = 0
            for rec in rows:
                ret = rec["ret"]
                alpha = rec["alpha"]
                ret_f = float(ret) if ret is not None else None
                alpha_f = float(alpha) if alpha is not None else None
                if args.max_abs_ret and ret_f is not None and abs(ret_f) > args.max_abs_ret:
                    n_outliers += 1
                    continue

                sid = rec["stock_id"]
                try:
                    snapshot = rec["feature_snapshot_json"]
                    feat = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                    with scorer_arm(arm):
                        s_cur = scorer.calculate_total_score(feat)["total_score"]
                    verifier.check(rec["stored_score"], s_cur)

                    debt = None
                    if sid in debt_by_stock:
                        debt = asof(debt_dates[sid], debt_vals[sid], t_date)
                    fund = feat.get("fundamental") or {}
                    feat["fundamental"] = fund
                    original = fund.get("debt_ratio")
                    fund["debt_ratio"] = debt
                    if debt is not None:
                        run_have += 1
                    try:
                        with debt_aware_value_score():
                            with scorer_arm(arm):
                                s_inj = scorer.calculate_total_score(feat)["total_score"]
                    finally:
                        fund["debt_ratio"] = original
                except Exception:
                    n_skipped += 1
                    continue

                n_rows += 1
                scored_cur.append((sid, s_cur, ret_f, alpha_f))
                scored_inj.append((sid, s_inj, ret_f, alpha_f))

            if not scored_cur:
                continue
            n_have_debt += run_have

            keys_cur, picked_cur = select(scored_cur, threshold, args.top_n)
            keys_inj, picked_inj = select(scored_inj, threshold, args.top_n)
            inter = keys_cur & keys_inj

            run_cur, run_inj = Acc(""), Acc("")
            for k, _, r, a in picked_cur:
                cur.add(r, a); run_cur.add(r, a)
                if k not in inter:
                    only_cur.add(r, a)
            for k, _, r, a in picked_inj:
                inj.add(r, a); run_inj.add(r, a)
                if k in inter:
                    both.add(r, a)
                else:
                    only_inj.add(r, a)

            union = keys_cur | keys_inj
            overlap.append(len(inter) / len(union) if union else 1.0)
            per_run.append((T, f"부채보유 {run_have:,}", len(scored_cur),
                            run_cur.n, run_cur.avg_alpha, run_inj.n, run_inj.avg_alpha))
    finally:
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    meta = [
        f"기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(per_run)}개",
        f"채점 {n_rows:,}건 · 부채비율 있는 건 {n_have_debt:,} · 이상치 {n_outliers:,} · 오류 {n_skipped:,}",
        f"밴드: <{LOW_DEBT} → {LOW_BONUS:+.0f} / >={HIGH_DEBT} → {HIGH_PENALTY:+.0f} / 그 사이 0 "
        f"(data_points 미변경)",
        f"스코어러 arm: {args.scorer}",
    ]
    meta += verifier.lines()
    report(f"부채비율 반영 반사실 검증 — {args.market} / {args.horizon} / 선택규칙 {sel}",
           meta, cur, inj, both, only_cur, only_inj, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
