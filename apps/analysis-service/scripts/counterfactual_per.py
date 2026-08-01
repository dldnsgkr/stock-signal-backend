"""
KR 에 PER 을 채우면 나아지는가 — 배포 전 반사실 검증.

배경
----
yfinance 는 `.KS` 티커에 `trailingPE`·`priceToBook` 을 주지 않는다(2026-07-31 실측).
그래서 `financial_metrics` 의 KR 5,011행 전체에 PER 이 0건이고, 스냅샷의
`fundamental.per_relative` 도 KR 은 커버리지 0.0%(US 는 44.6%)다. 가치 전략이
통째로 죽어 KR 앙상블이 모멘텀 단일 모델로 붕괴한다(모멘텀 가중치 0.889).

look-ahead 를 어떻게 피하는가 — 이게 이 검증의 핵심
--------------------------------------------------
"지금 yfinance 에서 `forwardPE` 를 받아 과거 런에 주입" 하면 **안 된다.**
현재 PER 은 현재 주가를 담고 있어, 과거 시점 채점에 미래 정보를 흘린다.

대신 저장된 데이터로 as-of 재구성한다:

    PER(t) = shares × close(t) / net_income(period_end <= t 중 최신)

  - `close(t)` : `price_daily` (t 시점 실제 종가)
  - `net_income` : `financial_metrics` 에 **월별로 과거가 남아 있다** (KR 1,617종목 × 3개월)
  - `shares` : 현재 발행주식수 (yfinance, `scripts/fetch_shares.py` → /tmp/shares.json)

시간에 따라 크게 변하는 항(주가·순이익)은 전부 as-of 다. **`shares` 만 현재값**인데
발행주식수는 몇 달 단위로 거의 안 변한다. 이 근사가 허용 범위인지는 US 에서
저장된 `trailingPE` 와 대조해 따로 검증한다(아래 '재구성 충실도').

부수 효과: 이렇게 만든 PER 은 **트레일링**이라 US 의 `trailingPE` 와 정의가 같다.
`forwardPE` 폴백을 쓸 때 걱정했던 "US 는 trailing, KR 은 forward 인데 임계값은 공용"
문제가 없다.

  A arm = 현행     : 스냅샷 그대로 (KR 은 per_relative=None)
  B arm = PER 주입 : `fundamental.per_relative` 에 as-of PER 을 넣고 재점수화

사전 준비
--------
  .venv/bin/python scripts/fetch_shares.py      # /tmp/shares.json 생성 (~13분)

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/counterfactual_per.py --market KR
  .venv/bin/python scripts/counterfactual_per.py --market KR --top-n 20
  .venv/bin/python scripts/counterfactual_per.py --market US --check-only   # 충실도만 확인
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

SHARES_PATH = "/tmp/shares.json"

FIN_SQL = """
    SELECT fm.stock_id, s.symbol, fm.period_end, fm.net_income, fm.per
    FROM financial_metrics fm
    JOIN stocks s ON s.id = fm.stock_id
    JOIN markets m ON m.id = s.market_id
    WHERE m.code = $1 AND fm.net_income IS NOT NULL
    ORDER BY fm.stock_id, fm.period_end
"""

PRICE_SQL = """
    SELECT p.stock_id, p.date, p.close
    FROM price_daily p
    JOIN stocks s ON s.id = p.stock_id
    JOIN markets m ON m.id = s.market_id
    WHERE m.code = $1 AND p.close > 0
    ORDER BY p.stock_id, p.date
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


def asof(keys, values, when):
    """keys 오름차순에서 key <= when 인 마지막 value. 없으면 None."""
    i = bisect_right(keys, when)
    return values[i - 1] if i else None


def build_per(shares, close, net_income):
    """PER = shares * close / net_income. 비정상값은 None."""
    if not shares or close is None or not net_income:
        return None
    per = shares * close / net_income
    # 스코어러가 다루는 범위를 벗어난 값은 노이즈로 보고 버린다.
    if per <= 0 or per > 1000:
        return None
    return round(per, 4)


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="KR PER 주입 효과 반사실 검증"),
        default_market="KR",
    )
    ap.add_argument("--per-asof", default="run", choices=["run", "period"],
                    help="run: 런 시점 주가로 매일 갱신 / period: US 처럼 월별 수집 시점 고정")
    ap.add_argument("--check-only", action="store_true",
                    help="재구성 충실도만 확인하고 종료 (US 에서 저장 PER 과 대조)")
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    if not Path(SHARES_PATH).exists():
        sys.exit(f"{SHARES_PATH} 가 없습니다. 먼저 `.venv/bin/python scripts/fetch_shares.py` 를 실행하세요.")
    with open(SHARES_PATH) as f:
        shares_raw = json.load(f)

    conn = await asyncpg.connect(dsn)
    try:
        # 재무 이력 (as-of net_income)
        fin_rows = await conn.fetch(FIN_SQL, args.market)
        fin: dict = {}
        symbol_of: dict = {}
        for r in fin_rows:
            sid = r["stock_id"]
            symbol_of[sid] = r["symbol"]
            fin.setdefault(sid, []).append(
                (r["period_end"], float(r["net_income"]),
                 float(r["per"]) if r["per"] is not None else None)
            )
        fin_dates = {sid: [e[0] for e in v] for sid, v in fin.items()}

        # 종가 이력
        price_rows = await conn.fetch(PRICE_SQL, args.market)
        prices: dict = {}
        for r in price_rows:
            prices.setdefault(r["stock_id"], []).append((r["date"], float(r["close"])))
        price_dates = {sid: [e[0] for e in v] for sid, v in prices.items()}

        shares_by_id = {sid: shares_raw[sym]["shares"]
                        for sid, sym in symbol_of.items() if sym in shares_raw}

        # ── 재구성 충실도: 파생 PER 이 저장된 trailingPE 를 재현하는가 ──
        # 저장된 per 은 수집 시점(period_end)의 trailingPE 이므로 같은 날 종가로 비교한다.
        checked = ok10 = ok25 = 0
        for sid, entries in fin.items():
            sh = shares_by_id.get(sid)
            if not sh or sid not in prices:
                continue
            for period_end, ni, stored_per in entries:
                if stored_per is None:
                    continue
                close = asof(price_dates[sid], [c for _, c in prices[sid]], period_end)
                derived = build_per(sh, close, ni)
                if derived is None:
                    continue
                checked += 1
                err = abs(derived - stored_per) / stored_per
                ok10 += err <= 0.10
                ok25 += err <= 0.25

        fidelity = []
        if checked:
            fidelity.append(f"재구성 충실도: 저장 trailingPE 대비 오차 10% 이내 "
                            f"{ok10 / checked * 100:.1f}% · 25% 이내 {ok25 / checked * 100:.1f}% "
                            f"({checked:,}건 대조)")
        else:
            fidelity.append("재구성 충실도: 대조 가능한 저장 PER 이 없습니다 "
                            "(KR 은 PER 이 0건이라 정상 — US 로 --check-only 실행해 확인하세요)")

        if args.check_only:
            print()
            for line in fidelity:
                print(" " + line)
            print()
            return

        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}, {ret_col} 평가 완료분 기준).")

        cur = Acc("현행 (PER 없음)")
        inj = Acc("PER 주입")
        only_cur = Acc("현행 단독 선택")
        only_inj = Acc("PER 단독 선택")
        both = Acc("공통 선택")

        n_rows = n_skipped = n_outliers = 0
        n_filled = n_already = 0
        overlap, per_run = [], []
        rows_sql = ROWS_SQL.format(ret_col=ret_col, alpha_col=alpha_col)

        for run_id, T in runs:
            t_date = T.date()
            rows = await conn.fetch(rows_sql, run_id)
            if not rows:
                continue

            scored_cur, scored_inj = [], []
            run_filled = 0
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

                    # as-of PER 산출
                    new_per = None
                    sh = shares_by_id.get(sid)
                    entry = asof(fin_dates.get(sid, []), fin.get(sid, []), t_date) if sid in fin else None
                    if sh and entry:
                        period_end, ni, _ = entry
                        when = t_date if args.per_asof == "run" else period_end
                        if sid in prices:
                            close = asof(price_dates[sid], [c for _, c in prices[sid]], when)
                            new_per = build_per(sh, close, ni)

                    fund = feat.get("fundamental") or {}
                    original = fund.get("per_relative")
                    if original is not None:
                        n_already += 1
                    if new_per is not None and original is None:
                        run_filled += 1

                    fund["per_relative"] = new_per if new_per is not None else original
                    feat["fundamental"] = fund
                    try:
                        s_inj = scorer.calculate_total_score(feat)["total_score"]
                    finally:
                        fund["per_relative"] = original
                except Exception:
                    n_skipped += 1
                    continue

                n_rows += 1
                scored_cur.append((sid, s_cur, ret_f, alpha_f))
                scored_inj.append((sid, s_inj, ret_f, alpha_f))

            if not scored_cur:
                continue
            n_filled += run_filled

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
            per_run.append((T, f"채움 {run_filled:,}종목", len(scored_cur),
                            run_cur.n, run_cur.avg_alpha, run_inj.n, run_inj.avg_alpha))
    finally:
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    meta = [
        f"기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(per_run)}개 · "
        f"주식수 확보 {len(shares_by_id):,}종목 · PER 기준 {args.per_asof}",
        f"채점 {n_rows:,}건 · 스냅샷 오류 {n_skipped:,} · 이상치 제외 {n_outliers:,}"
        f" (|ret|>{args.max_abs_ret})",
        f"PER 을 새로 채운 건 {n_filled:,}"
        + (f" · 이미 PER 이 있던 건 {n_already:,}(두 arm 동일 → 효과 희석)" if n_already else ""),
    ] + fidelity

    report(f"KR PER 주입 반사실 검증 — {args.market} / {args.horizon} / 선택규칙 {sel}",
           meta, cur, inj, both, only_cur, only_inj, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
