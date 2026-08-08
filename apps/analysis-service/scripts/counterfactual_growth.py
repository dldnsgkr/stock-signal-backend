"""
매출 성장률(분기 YoY)을 가치 전략에 넣으면 나아지는가 — 배포 전 반사실 검증 (KR 전용).

배경
----
`financial_metrics` 의 매출은 yfinance TTM 값을 월별로 스냅샷한 것이라 연속 기간 간
5.9% 만 변한다 → 성장률을 만들 수 없다. `ticker.quarterly_income_stmt` 는 실제 회계
분기를 주므로 여기서 진짜 YoY 를 만든다(`scripts/fetch_quarterly.py` 로 사전 수집).

⚠️ look-ahead — 분기 종료일과 공시일은 다르다. 2026-03-31 분기는 5월 중순에나 공시된다.
   **공시 지연 60일**(KR 분기보고서 법정기한 45일 + 여유)을 적용해 as-of 를 잡는다.
   YoY 기준분기는 1년 전 ±45일 이내로 매칭하고, 기준분기 매출이 0 이하면 버린다.

3단계 사전 검증 (2026-08-09, KR 90일, 매출 YoY 71,878건 = 채점의 57.1%):
  1단계 적중률 스프레드 6.2%p, 정점 Q3(완만한 성장)
  2단계 규모(거래대금) 대조 후 **6.3%p — 감쇠 없음**
  3단계 전반 3.0%p / 후반 8.4%p, **양쪽 모두 Q3 정점**
→ 오늘 검증한 신호 중 가장 강하다(부채비율은 2단계에서 3.6→3.0%p 로 줄었다).

밴드 (절대 구간별 적중률에서 직접):
  <-20% 33.2% / -20~-5% 37.9% / **-5~+15% 40.1%** / +15~40% 37.3% / >+40% 34.5%
→ 정점 +8 / 중간 0 / 양극단 -8 의 ∩자 3단계. 모멘텀 v2.1 과 같은 형태다.

사용법
------
  .venv/bin/python scripts/fetch_quarterly.py KR      # 사전 준비
  .venv/bin/python scripts/counterfactual_growth.py --market KR
"""

import argparse
import asyncio
import json
import os
import sys
from bisect import bisect_right
from datetime import date, timedelta
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from _common import Acc, add_common_args, parse_dates, report, resolve_dsn, select  # noqa: E402
from app.engine import scorer  # noqa: E402

QUARTERLY_PATH = "/tmp/quarterly.json"
LAG_DAYS = 60       # 공시 지연 — 이걸 빼면 look-ahead 다
YOY_TOL = 45        # 1년 전 분기 매칭 허용 오차(일)
PEAK_LO, PEAK_HI = -0.05, 0.15     # 적중 40.1% 구간
BAD_LO, BAD_HI = -0.20, 0.40       # 이 밖은 33~34%
PEAK_BONUS = 8.0
EXTREME_PENALTY = -8.0


COUNT_QUALITY = False   # --count-quality 로 켠다
BAND_OFF = False        # --band-off: 점수 밴드를 끄고 품질만 올린다(가중치 효과 분리용)


def make_value_score_with_growth(original):
    """기존 `_value_score` 에 매출 성장률 항만 더한 버전을 만든다."""
    def patched(features: dict):
        score, quality = original(features)
        g = (features.get("fundamental") or {}).get("revenue_growth_yoy")
        if g is None:
            return score, quality
        if not BAND_OFF:
            if PEAK_LO <= g < PEAK_HI:
                score += PEAK_BONUS
            elif g < BAD_LO or g >= BAD_HI:
                score += EXTREME_PENALTY
        if COUNT_QUALITY:
            # 성장률을 '아는 것' 으로 세면 가치 품질이 올라 가중치가 커진다.
            # 원본 quality 는 data_points/3 이므로 1/3 을 더한 것과 같다.
            quality = min(1.0, quality + 1.0 / 3.0)
        return max(0.0, min(100.0, score)), quality
    return patched


@contextmanager
def growth_aware_value_score():
    original = scorer._value_score
    scorer._value_score = make_value_score_with_growth(original)
    try:
        yield
    finally:
        scorer._value_score = original


SYMBOL_SQL = """
    SELECT s.id, s.symbol FROM stocks s
    JOIN markets m ON m.id = s.market_id WHERE m.code = $1
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


def revenue_yoy(series, when):
    """공시 지연 후 알 수 있는 최신 분기의 매출 YoY. 없으면 None."""
    usable = [(dt, v) for dt, v in series if dt + timedelta(days=LAG_DAYS) <= when]
    if not usable or not series:
        return None
    cur_dt, cur_v = usable[-1]
    target = cur_dt - timedelta(days=365)
    prev = min(series, key=lambda x: abs((x[0] - target).days))
    if abs((prev[0] - target).days) > YOY_TOL or prev[1] is None or prev[1] <= 0:
        return None
    g = (cur_v - prev[1]) / prev[1]
    return g if abs(g) < 10 else None      # 1000% 초과는 기저효과 노이즈


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="매출 성장률 주입 효과 반사실 검증"),
        default_market="KR",
    )
    ap.add_argument("--count-quality", action="store_true",
                    help="성장률을 데이터 품질에 포함 (가치 가중치가 올라간다)")
    ap.add_argument("--band-off", action="store_true",
                    help="점수 밴드를 끄고 품질만 반영 — '성장 신호' 와 '가중치 상승' 을 분리한다")
    args = ap.parse_args()

    global COUNT_QUALITY, BAND_OFF
    COUNT_QUALITY = args.count_quality
    BAND_OFF = args.band_off

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    conn = await asyncpg.connect(dsn)
    try:
        if not Path(QUARTERLY_PATH).exists():
            sys.exit(f"{QUARTERLY_PATH} 가 없습니다. 먼저 scripts/fetch_quarterly.py 를 실행하세요.")
        with open(QUARTERLY_PATH) as f:
            quarterly = json.load(f)
        sym_rows = await conn.fetch(SYMBOL_SQL, args.market)
        rev_by_stock: dict = {}
        for r in sym_rows:
            rec = quarterly.get(r["symbol"])
            revs = (rec or {}).get("revenue")
            if revs:
                rev_by_stock[r["id"]] = sorted(
                    (date.fromisoformat(k), float(v)) for k, v in revs.items()
                )

        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}).")

        cur = Acc("현행 (성장률 미사용)")
        inj = Acc("성장률 반영")
        only_cur = Acc("현행 단독 선택")
        only_inj = Acc("성장률 단독 선택")
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
                    s_cur = scorer.calculate_total_score(feat)["total_score"]

                    g = None
                    if sid in rev_by_stock:
                        g = revenue_yoy(rev_by_stock[sid], t_date)
                    fund = feat.get("fundamental") or {}
                    feat["fundamental"] = fund
                    original = fund.get("revenue_growth_yoy")
                    fund["revenue_growth_yoy"] = g
                    if g is not None:
                        run_have += 1
                    try:
                        with growth_aware_value_score():
                            s_inj = scorer.calculate_total_score(feat)["total_score"]
                    finally:
                        fund["revenue_growth_yoy"] = original
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
            per_run.append((T, f"성장률 {run_have:,}", len(scored_cur),
                            run_cur.n, run_cur.avg_alpha, run_inj.n, run_inj.avg_alpha))
    finally:
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    meta = [
        f"기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(per_run)}개",
        f"채점 {n_rows:,}건 · 성장률 계산된 건 {n_have_debt:,} · 이상치 {n_outliers:,} · 오류 {n_skipped:,}",
        f"밴드: {PEAK_LO:+.0%}~{PEAK_HI:+.0%} → {PEAK_BONUS:+.0f} / <{BAD_LO:+.0%} 또는 >={BAD_HI:+.0%} → "
        f"{EXTREME_PENALTY:+.0f} / 그 사이 0 · 공시지연 {LAG_DAYS}일",
    ]
    report(f"매출 성장률 반영 반사실 검증 — {args.market} / {args.horizon} / 선택규칙 {sel}",
           meta, cur, inj, both, only_cur, only_inj, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
