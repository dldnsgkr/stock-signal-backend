"""
`volume_growth_rate` 를 종목별 변동성으로 정규화하면 나아지는가 — 배포 전 반사실 검증.

배경 (2026-08-10 조사)
---------------------
`volg = 5일 평균거래량 / 20일 평균거래량 - 1` 는 **유동성 정규화가 안 돼 있다.**
거래가 적은 종목일수록 일간 거래량의 % 변동이 기계적으로 크다:

    거래대금 분위   volg 표준편차   volg>0.5 비율   90점 이상 도달률
    1 (최저 $27K)      1.094          20.6%           1.233%
    5 (최고 $186M)     0.345           5.8%           0.065%

`_momentum_score` 의 `vol_growth > 0.5 → +12` 보너스를 최저분위가 3.5배 쉽게 받고,
임계값 가점이 여러 개 쌓여야 천장(92.9)에 닿으므로 이 우위가 복리로 작용해
90점 도달률이 **19배** 벌어진다. 90점 이상 집단의 72.3% 가 이 보너스를 갖고 있고
(일반 BUY 는 12.5%), 그 집단의 거래대금 중앙값은 **$32,344** — 체결 불가능한 수준이다.
결과적으로 US Top20 알파가 -4.57% 다(전체 BUY 는 +0.10%).

무엇을 바꾸는가
--------------
volg 를 **종목 자신의 과거 volg 표준편차**로 나눈 z 로 바꾸고, 같은 밴드 구조를 z 에 적용한다.
표준편차는 as-of 시점까지의 `price_daily` 거래량만으로 만든다 — look-ahead 없음.

⚠️ **트리거 비율을 원본과 맞춘다(2단계 대조).**
z 임계값을 임의로 정하면 "보너스를 덜 주는 것" 의 효과와 "누구에게 주는가" 의 효과가
섞인다. 그래서 z 임계값을 원본 밴드의 **모집단 비율과 같은 분위수**로 잡는다.
그러면 가점을 받는 **개수는 같고 대상만 바뀌므로** 순수하게 정규화 효과만 본다.

구현 방식
--------
`_momentum_score` 를 복제하지 않고, 스냅샷의 `technical.volume_growth_rate` 를
**목표 밴드에 떨어지는 합성값**으로 바꿔 원본 함수를 그대로 태운다.
(복제하면 원본이 바뀔 때 조용히 어긋난다 — v3.14.2 에서 겪은 부류의 사고)

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/counterfactual_volg.py --market US
  .venv/bin/python scripts/counterfactual_volg.py --market US --top-n 20
  .venv/bin/python scripts/counterfactual_volg.py --market US --from 2026-06-01 --to 2026-07-05
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

from contextlib import contextmanager  # noqa: E402

from _common import Acc, add_common_args, parse_dates, report, resolve_dsn, select  # noqa: E402
from app.engine import scorer  # noqa: E402
from scorer_v20_frozen import momentum_score_v20  # noqa: E402


@contextmanager
def scorer_arm(use_v20: bool):
    """v2.1 배포(2026-07-27) **이전** 런은 v2.0 으로 채점돼 있다.

    현재 코드로 그 구간을 재채점하면 자기검증이 12% 로 무너진다(2026-08-10 실측).
    volg 항은 v2.0/v2.1 이 완전히 동일하므로(+12/+6/-8, 임계값도 같음)
    옛 구간은 동결 사본으로 재채점해야 표본을 크게 쓰면서 자기검증을 지킬 수 있다.
    """
    if not use_v20:
        yield
        return
    original = scorer._momentum_score
    scorer._momentum_score = momentum_score_v20
    try:
        yield
    finally:
        scorer._momentum_score = original

# 원본 밴드 (scorer._momentum_score 와 일치해야 한다)
HI, MID, LO = 0.5, 0.2, -0.3
# 합성값 — 각 밴드 한가운데로 보내 원본 함수가 의도한 가점을 주게 한다
SYNTH = {"hi": 0.6, "mid": 0.3, "neutral": 0.0, "lo": -0.4}

STD_WINDOW = 60      # z 계산용 과거 창(거래일)
MIN_STD_OBS = 20     # 표본이 이보다 적으면 정규화하지 않고 원본 유지

# volg 를 price_daily 에서 재구성하고, 그 시점까지의 표준편차로 z 를 만든다.
# 창은 모두 '현재 행까지' 라 미래를 보지 않는다.
VOLG_SQL = """
WITH v AS (
  SELECT p.stock_id, p.date, p.volume,
         avg(p.volume) OVER w5  AS v5,
         avg(p.volume) OVER w20 AS v20
  FROM price_daily p
  JOIN stocks s   ON s.id = p.stock_id
  JOIN markets m  ON m.id = s.market_id
  WHERE m.code = $1
  WINDOW w5  AS (PARTITION BY p.stock_id ORDER BY p.date ROWS BETWEEN 4  PRECEDING AND CURRENT ROW),
         w20 AS (PARTITION BY p.stock_id ORDER BY p.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
), g AS (
  SELECT stock_id, date,
         CASE WHEN v20 > 0 THEN v5 / v20 - 1 END AS volg
  FROM v
)
SELECT stock_id, date, volg,
       stddev_samp(volg) OVER w  AS volg_std,
       count(volg)       OVER w  AS volg_n
FROM g
WHERE volg IS NOT NULL
WINDOW w AS (PARTITION BY stock_id ORDER BY date ROWS BETWEEN %d PRECEDING AND CURRENT ROW)
ORDER BY stock_id, date
""" % (STD_WINDOW - 1)

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


def asof(dates, values, when):
    i = bisect_right(dates, when)
    return values[i - 1] if i else None


def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="volume_growth_rate 유동성 정규화 반사실 검증"),
        default_market="US",
    )
    ap.add_argument("--scorer", choices=["current", "v20"], default="current",
                    help="v20 = 2026-07-27 이전 런용 동결 사본(그 구간은 v2.0 으로 채점돼 있다)")
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    use_v20 = args.scorer == "v20"

    conn = await asyncpg.connect(dsn)
    try:
        print(f"스코어러 arm: {args.scorer}", flush=True)
        print("volg 시계열 재구성 중...", flush=True)
        vrows = await conn.fetch(VOLG_SQL, args.market)
        vdates, vvolg, vstd = {}, {}, {}
        for r in vrows:
            sid = r["stock_id"]
            vdates.setdefault(sid, []).append(r["date"])
            vvolg.setdefault(sid, []).append(float(r["volg"]))
            std = r["volg_std"]
            vstd.setdefault(sid, []).append(
                float(std) if std is not None and r["volg_n"] >= MIN_STD_OBS else None
            )
        print(f"  {len(vdates):,} 종목 · {len(vrows):,} 행", flush=True)

        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}).")

        rows_sql = ROWS_SQL.format(ret_col=ret_col, alpha_col=alpha_col)

        # ── 1차 통과: z 분포와 원본 밴드 비율을 모아 임계값을 보정한다 ──────────
        print("1차 통과 — z 임계값 보정 중...", flush=True)
        zs, n_hi = [], 0
        n_mid = n_lo = n_tot = n_noz = 0
        cache = {}
        for run_id, T in runs:
            t_date = T.date()
            rows = await conn.fetch(rows_sql, run_id)
            cache[run_id] = rows
            for rec in rows:
                snapshot = rec["feature_snapshot_json"]
                feat = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                tech = (feat or {}).get("technical") or {}
                volg = tech.get("volume_growth_rate")
                if volg is None:
                    continue
                n_tot += 1
                if volg > HI:
                    n_hi += 1
                elif volg > MID:
                    n_mid += 1
                elif volg < LO:
                    n_lo += 1
                sid = rec["stock_id"]
                std = asof(vdates.get(sid, []), vstd.get(sid, []), t_date) if sid in vdates else None
                if std is None or std <= 0:
                    n_noz += 1
                    continue
                zs.append(volg / std)

        if not zs or not n_tot:
            sys.exit("z 를 만들 수 있는 표본이 없습니다.")
        zs.sort()
        # 원본과 같은 비율이 되도록 분위수에서 자른다 (2단계 대조)
        z_hi = quantile(zs, 1 - n_hi / n_tot)
        z_mid = quantile(zs, 1 - (n_hi + n_mid) / n_tot)
        z_lo = quantile(zs, n_lo / n_tot)
        print(f"  원본 비율  상단 {n_hi/n_tot*100:.2f}% · 중단 {n_mid/n_tot*100:.2f}% · "
              f"하단 {n_lo/n_tot*100:.2f}%   (z 없음 {n_noz/n_tot*100:.2f}%)", flush=True)
        print(f"  보정된 z 임계값  hi>{z_hi:.3f} · mid>{z_mid:.3f} · lo<{z_lo:.3f}", flush=True)

        # ── 2차 통과: 재채점 ────────────────────────────────────────────────
        cur = Acc("현행 (원본 volg)")
        alt = Acc("volg 정규화")
        only_cur = Acc("현행 단독 선택")
        only_alt = Acc("정규화 단독 선택")
        both = Acc("공통 선택")

        n_rows = n_skipped = n_outliers = n_changed = 0
        verify_ok = verify_tot = 0
        overlap, per_run = [], []

        for run_id, T in runs:
            t_date = T.date()
            rows = cache[run_id]
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

                sid = rec["stock_id"]
                try:
                    snapshot = rec["feature_snapshot_json"]
                    feat = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                    with scorer_arm(use_v20):
                        s_cur = scorer.calculate_total_score(feat)["total_score"]

                    # 자기검증 — 재채점이 저장된 점수를 재현하는가
                    stored = rec["stored_score"]
                    if stored is not None:
                        verify_tot += 1
                        if abs(float(stored) - s_cur) < 0.011:
                            verify_ok += 1

                    tech = (feat or {}).get("technical") or {}
                    volg = tech.get("volume_growth_rate")
                    std = (asof(vdates.get(sid, []), vstd.get(sid, []), t_date)
                           if sid in vdates else None)

                    if volg is None or std is None or std <= 0:
                        s_alt = s_cur          # 정규화 불가 → 현행 그대로
                    else:
                        z = volg / std
                        band = ("hi" if z > z_hi else "mid" if z > z_mid
                                else "lo" if z < z_lo else "neutral")
                        original = tech["volume_growth_rate"]
                        tech["volume_growth_rate"] = SYNTH[band]
                        try:
                            with scorer_arm(use_v20):
                                s_alt = scorer.calculate_total_score(feat)["total_score"]
                        finally:
                            tech["volume_growth_rate"] = original
                        if abs(s_alt - s_cur) > 1e-9:
                            n_changed += 1
                            run_changed += 1
                except Exception:
                    n_skipped += 1
                    continue

                n_rows += 1
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
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    v_rate = verify_ok / verify_tot * 100 if verify_tot else 0.0
    meta = [
        f"기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(per_run)}개",
        f"채점 {n_rows:,}건 · 점수변동 {n_changed:,} · 이상치 {n_outliers:,} · 오류 {n_skipped:,}",
        f"자기검증: 재채점이 저장 점수를 재현한 비율 {v_rate:.1f}% ({verify_ok:,}/{verify_tot:,})",
        f"z 임계값 hi>{z_hi:.3f} mid>{z_mid:.3f} lo<{z_lo:.3f} — 원본과 트리거 비율 일치",
        f"스코어러: {args.scorer}",
    ]
    if v_rate < 95:
        meta.append("⚠️ 자기검증 실패 — 결과를 믿지 말 것")
    report(f"volg 유동성 정규화 반사실 검증 — {args.market} / {args.horizon} / 선택규칙 {sel}",
           meta, cur, alt, both, only_cur, only_alt, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
