"""
KR 에 PER / PBR 을 채우면 나아지는가 — 배포 전 반사실 검증.

배경
----
yfinance 는 `.KS` 티커에 `trailingPE`·`priceToBook` 을 주지 않는다(2026-07-31 실측).
그래서 `financial_metrics` 의 KR 5,011행 전체에 PER·PBR 이 0건이고, 스냅샷의
`fundamental.per_relative`/`pbr_relative` 도 KR 커버리지 0.0%(US 는 44.6%/76.6%)다.
가치 전략이 통째로 죽어 KR 앙상블이 모멘텀 단일 모델로 붕괴한다(모멘텀 가중치 0.889).

look-ahead 를 어떻게 피하는가 — 이게 이 검증의 핵심
--------------------------------------------------
"지금 yfinance 에서 값을 받아 과거 런에 주입" 하면 **안 된다.** 현재 PER/PBR 은
현재 주가를 담고 있어 과거 시점 채점에 미래 정보를 흘린다. 대신 as-of 재구성한다:

    PER(t) = shares × close(t) / net_income(period_end <= t 중 최신)
    PBR(t) = shares × close(t) / equity(연차 결산 <= t 중 최신)

  - `close(t)`     : `price_daily` — t 시점 실제 종가
  - `net_income`   : `financial_metrics` 에 **월별로 과거가 남아 있다**
  - `equity`       : yfinance `balance_sheet` 의 **연차 결산**. 최신이 2025-12-31 로
                     백테스트 구간(2026-05~07)보다 앞서 look-ahead 가 원천적으로 없다.
  - `shares`       : 현재 발행주식수 (유일한 근사. 아래 충실도 참조)

**PBR 을 `net_income/ROE` 로 파생하면 안 된다** — 그러면 PBR = PER × ROE 라는 항등식이라
이미 모델이 쓰는 두 피처의 곱일 뿐 새 정보가 아니다. 그래서 실제 재무상태표를 쓴다.

부수 효과: 이렇게 만든 PER 은 **트레일링**이라 US `trailingPE` 와 정의가 같다.
`forwardPE` 폴백을 쓸 때 걱정했던 "US 는 trailing, KR 은 forward 인데 임계값은 공용"
문제가 없다.

  A arm = 현행   : 스냅샷 그대로 (KR 은 해당 필드 None)
  B arm = 주입   : as-of 값을 넣고 재점수화

사전 준비
--------
  .venv/bin/python scripts/fetch_shares.py      # /tmp/shares.json 생성 (재실행 시 이어받음)

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/counterfactual_value.py --market KR --metric per
  .venv/bin/python scripts/counterfactual_value.py --market KR --metric pbr --top-n 20
  .venv/bin/python scripts/counterfactual_value.py --market US --metric pbr --check-only
"""

import argparse
import asyncio
import json
import os
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from _common import Acc, add_common_args, parse_dates, report, resolve_dsn, select  # noqa: E402
from app.engine import scorer  # noqa: E402

SHARES_PATH = "/tmp/shares.json"

# metric → (스냅샷 필드, financial_metrics 대조 컬럼, 상한)
METRICS = {
    "per": ("per_relative", "per", 1000.0),
    "pbr": ("pbr_relative", "pbr", 100.0),
}

FIN_SQL = """
    SELECT fm.stock_id, s.symbol, fm.period_end, fm.net_income, fm.per, fm.pbr
    FROM financial_metrics fm
    JOIN stocks s ON s.id = fm.stock_id
    JOIN markets m ON m.id = s.market_id
    WHERE m.code = $1
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


def ratio(shares, close, denom, cap):
    """shares * close / denom. 비정상값은 None."""
    if not shares or close is None or not denom or denom <= 0:
        return None
    val = shares * close / denom
    if val <= 0 or val > cap:
        return None
    return round(val, 4)


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="KR PER/PBR 주입 효과 반사실 검증"),
        default_market="KR",
    )
    # both = 실제 배포 시나리오(둘 다 채움). 효과가 더해지는지 상쇄되는지 확인용.
    ap.add_argument("--metric", default="per", choices=["per", "pbr", "both"])
    ap.add_argument("--per-asof", default="run", choices=["run", "period"],
                    help="run: 런 시점 주가로 매일 갱신 / period: US 처럼 월별 수집 시점 고정")
    ap.add_argument("--check-only", action="store_true",
                    help="재구성 충실도만 확인하고 종료 (US 에서 저장값과 대조)")
    args = ap.parse_args()

    metrics = ["per", "pbr"] if args.metric == "both" else [args.metric]
    # 충실도 대조는 단일 metric 일 때만 의미가 있다 (both 는 per 기준으로 본다).
    _, cmp_col, _ = METRICS[metrics[0]]
    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)

    if not Path(SHARES_PATH).exists():
        sys.exit(f"{SHARES_PATH} 가 없습니다. 먼저 `.venv/bin/python scripts/fetch_shares.py` 를 실행하세요.")
    with open(SHARES_PATH) as f:
        raw = json.load(f)

    conn = await asyncpg.connect(dsn)
    try:
        fin_rows = await conn.fetch(FIN_SQL, args.market)
        fin: dict = {}
        symbol_of: dict = {}
        for r in fin_rows:
            sid = r["stock_id"]
            symbol_of[sid] = r["symbol"]
            fin.setdefault(sid, []).append((
                r["period_end"],
                float(r["net_income"]) if r["net_income"] is not None else None,
                float(r[cmp_col]) if r[cmp_col] is not None else None,
            ))
        fin_dates = {sid: [e[0] for e in v] for sid, v in fin.items()}

        price_rows = await conn.fetch(PRICE_SQL, args.market)
        prices: dict = {}
        for r in price_rows:
            prices.setdefault(r["stock_id"], []).append((r["date"], float(r["close"])))
        price_dates = {sid: [e[0] for e in v] for sid, v in prices.items()}
        price_closes = {sid: [c for _, c in v] for sid, v in prices.items()}

        shares_by_id, equity_by_id = {}, {}
        for sid, sym in symbol_of.items():
            rec = raw.get(sym)
            if not rec:
                continue
            if rec.get("shares"):
                shares_by_id[sid] = rec["shares"]
            eq = rec.get("equity")
            if eq:
                pairs = sorted((date.fromisoformat(d), v) for d, v in eq)
                equity_by_id[sid] = ([p[0] for p in pairs], [p[1] for p in pairs])

        def denom_at(metric, sid, when):
            """metric 별 분모 (as-of). per→net_income, pbr→equity."""
            if metric == "per":
                entry = asof(fin_dates.get(sid, []), fin.get(sid, []), when)
                return entry[1] if entry else None
            eq = equity_by_id.get(sid)
            return asof(eq[0], eq[1], when) if eq else None

        # ── 재구성 충실도: 파생값이 저장된 값을 재현하는가 (US 에서만 가능) ──
        base_metric = metrics[0]
        _, _, base_cap = METRICS[base_metric]
        checked = ok10 = ok25 = 0
        for sid, entries in fin.items():
            sh = shares_by_id.get(sid)
            if not sh or sid not in prices:
                continue
            for period_end, _, stored in entries:
                if stored is None or stored <= 0:
                    continue
                close = asof(price_dates[sid], price_closes[sid], period_end)
                derived = ratio(sh, close, denom_at(base_metric, sid, period_end), base_cap)
                if derived is None:
                    continue
                checked += 1
                err = abs(derived - stored) / stored
                ok10 += err <= 0.10
                ok25 += err <= 0.25

        if checked:
            fidelity = [f"재구성 충실도({base_metric}): 저장값 대비 오차 10% 이내 "
                        f"{ok10 / checked * 100:.1f}% · 25% 이내 {ok25 / checked * 100:.1f}% "
                        f"({checked:,}건 대조)"]
        else:
            fidelity = [f"재구성 충실도: 대조 가능한 저장 {base_metric} 이 없습니다 "
                        f"(KR 은 0건이라 정상 — US 로 --check-only 실행해 확인하세요)"]

        if args.check_only:
            print()
            for line in fidelity:
                print(" " + line)
            print()
            return

        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}, {ret_col} 평가 완료분 기준).")

        label = "PER+PBR" if args.metric == "both" else args.metric.upper()
        cur = Acc(f"현행 ({label} 없음)")
        inj = Acc(f"{label} 주입")
        only_cur = Acc("현행 단독 선택")
        only_inj = Acc(f"{label} 단독 선택")
        both = Acc("공통 선택")

        n_rows = n_skipped = n_outliers = n_filled = n_already = 0
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

                    fund = feat.get("fundamental") or {}
                    feat["fundamental"] = fund
                    sh = shares_by_id.get(sid)
                    restore = {}
                    filled_any = False
                    for metric in metrics:
                        m_field, _, m_cap = METRICS[metric]
                        new_val = None
                        if sh and sid in prices:
                            # per 은 월별 수집 시점 고정(period) 옵션이 있다. pbr 의 분모(연차
                            # 결산)는 구간 내 상수라 run/period 차이가 주가에만 걸린다.
                            when = t_date
                            if args.per_asof == "period" and metric == "per":
                                entry = asof(fin_dates.get(sid, []), fin.get(sid, []), t_date)
                                when = entry[0] if entry else t_date
                            close = asof(price_dates[sid], price_closes[sid], when)
                            new_val = ratio(sh, close, denom_at(metric, sid, t_date), m_cap)

                        original = fund.get(m_field)
                        restore[m_field] = original
                        if original is not None:
                            n_already += 1
                        elif new_val is not None:
                            filled_any = True
                        fund[m_field] = new_val if new_val is not None else original
                    if filled_any:
                        run_filled += 1

                    try:
                        s_inj = scorer.calculate_total_score(feat)["total_score"]
                    finally:
                        for k, v in restore.items():
                            fund[k] = v
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
        f"주식수 {len(shares_by_id):,}종목 · 자기자본 {len(equity_by_id):,}종목 · 기준 {args.per_asof}",
        f"채점 {n_rows:,}건 · 스냅샷 오류 {n_skipped:,} · 이상치 제외 {n_outliers:,}"
        f" (|ret|>{args.max_abs_ret})",
        f"{label} 을 새로 채운 건 {n_filled:,}"
        + (f" · 이미 있던 건 {n_already:,}(두 arm 동일 → 효과 희석)" if n_already else ""),
    ] + fidelity

    report(f"KR {label} 주입 반사실 검증 — {args.market} / {args.horizon} / 선택규칙 {sel}",
           meta, cur, inj, both, only_cur, only_inj, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
