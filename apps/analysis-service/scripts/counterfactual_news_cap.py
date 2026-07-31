"""
뉴스 조회에 날짜 상한을 두면 나아지는가 — 배포 전 반사실 검증.

배경
----
`feature_builder` 의 뉴스 조회에는 **날짜 필터가 없다** (해당 종목 최신 30건).
그런데 뉴스 수집이 매달 같은 ~150종목에 집중돼, 2026-07-31 실측 기준
US 는 뉴스 보유 4,837종목 중 최근 30일 신선한 건 152종목뿐이다.

반감기 7일 감쇠로 30~90일 된 기사는 가중치가 0.05 이하로 떨어지지만 **0 은 아니다.**
`_sentiment_score` 는 `if sentiment_w != 0` 으로 데이터 유무를 판단하므로
낡은 뉴스도 `data_points += 1` 을 만들어 **감성 품질만 올리고 점수는 중립 50** 이 된다.
즉 실질 정보 없는 감성이 0.23 가중치를 얻어 총점을 50 쪽으로 희석한다.

VIX 와는 성격이 다르다 — VIX 는 같은 날 모든 종목에 동일한 상수라 순위 정보가
원천적으로 0 이었지만(그래서 기각), 뉴스 신선도는 **종목마다 다르다.**

방법
----
스냅샷에는 뉴스 **파생 피처만** 있고 기사 날짜가 없어 VIX 처럼 단순 주입이 안 된다.
그래서 원본(`news_articles` × `news_stock_relations`)에서 as-of 재구성한다.

  A arm = 상한 없음 : 런 실행시각 T 이전 기사 최신 30건
  B arm = 상한 CAP일: T-CAP ~ T 기사 최신 30건

두 arm 모두 **재구성값**을 쓴다(저장된 스냅샷을 A 로 쓰면 재구성 오차가 효과로
둔갑한다). 재구성 충실도는 A 와 저장 스냅샷을 대조해 따로 보고한다.

감쇠 기준시각은 원 코드가 `datetime.utcnow()` 를 쓰므로, 재구성 시 그 자리에
**런 실행시각 T** 를 넣는다(`feature_builder.datetime` 을 스텁으로 교체).
로직을 복제하지 않고 원 함수를 그대로 호출하므로 구현 드리프트가 없다.

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/counterfactual_news_cap.py --market US --cap-days 30
  .venv/bin/python scripts/counterfactual_news_cap.py --market US --cap-days 14 --top-n 20
"""

import argparse
import asyncio
import json
import os
import sys
from bisect import bisect_left
from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from _common import Acc, add_common_args, parse_dates, report, resolve_dsn, select  # noqa: E402
from app.engine import feature_builder, scorer  # noqa: E402

# `_build_news_features` 가 기대하는 행 모양 (속성 접근)
NewsRow = namedtuple("NewsRow", "sentiment_score published_at relevance_score")

MAX_ARTICLES = 30  # feature_builder 의 .limit(30) 과 맞춘다


@contextmanager
def clock_at(when):
    """`feature_builder` 안의 `datetime.utcnow()` 가 when 을 반환하게 만든다."""
    original = feature_builder.datetime

    class _Stub(datetime):
        @classmethod
        def utcnow(cls):
            return when

    feature_builder.datetime = _Stub
    try:
        yield
    finally:
        feature_builder.datetime = original


NEWS_SQL = """
    SELECT nsr.stock_id, na.published_at, na.sentiment_score, nsr.relevance_score
    FROM news_stock_relations nsr
    JOIN news_articles na ON na.id = nsr.news_article_id
    JOIN stocks s ON s.id = nsr.stock_id
    JOIN markets m ON m.id = s.market_id
    WHERE m.code = $1 AND na.sentiment_score IS NOT NULL AND na.published_at IS NOT NULL
    ORDER BY nsr.stock_id, na.published_at
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


def window(entries, dates, lo, hi):
    """published_at 오름차순 리스트에서 lo < t <= hi 구간의 최신 MAX_ARTICLES 건."""
    left = bisect_left(dates, lo) if lo else 0
    right = bisect_left(dates, hi)
    chunk = entries[left:right]
    return list(reversed(chunk[-MAX_ARTICLES:]))  # 최신순


async def main():
    ap = add_common_args(
        argparse.ArgumentParser(description="뉴스 날짜 상한 효과 반사실 검증")
    )
    ap.add_argument("--cap-days", type=int, default=30,
                    help="이 일수보다 오래된 기사는 제외 (기본 30)")
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else scorer.BUY_THRESHOLD
    ret_col = f"return_{args.horizon}"
    alpha_col = f"alpha_{args.horizon}"
    dsn = resolve_dsn(args.dsn, os.environ)
    d_from, d_to = parse_dates(args.fromdate, args.todate)
    cap = timedelta(days=args.cap_days)

    conn = await asyncpg.connect(dsn)
    try:
        news_rows = await conn.fetch(NEWS_SQL, args.market)
        by_stock: dict = {}
        for r in news_rows:
            by_stock.setdefault(r["stock_id"], []).append(
                (r["published_at"], float(r["sentiment_score"]),
                 float(r["relevance_score"]) if r["relevance_score"] is not None else None)
            )
        dates_by_stock = {sid: [e[0] for e in v] for sid, v in by_stock.items()}

        runs = await conn.fetch(RUNS_SQL.format(ret_col=ret_col), args.market, d_from, d_to)
        if not runs:
            sys.exit(f"대상 런이 없습니다 (market={args.market}, {ret_col} 평가 완료분 기준).")

        cur = Acc("현행 (상한 없음)")
        capped = Acc(f"상한 {args.cap_days}일")
        only_cur = Acc("현행 단독 선택")
        only_cap = Acc("상한 단독 선택")
        both = Acc("공통 선택")

        n_rows = n_skipped = n_outliers = 0
        n_changed = n_dropped_all = 0
        fid_n = fid_count_ok = 0
        overlap, per_run = [], []
        rows_sql = ROWS_SQL.format(ret_col=ret_col, alpha_col=alpha_col)

        for run_id, T in runs:
            rows = await conn.fetch(rows_sql, run_id)
            if not rows:
                continue

            scored_cur, scored_cap = [], []
            run_changed = 0
            with clock_at(T):
                for rec in rows:
                    ret = rec["ret"]
                    alpha = rec["alpha"]
                    ret_f = float(ret) if ret is not None else None
                    alpha_f = float(alpha) if alpha is not None else None

                    if args.max_abs_ret and ret_f is not None and abs(ret_f) > args.max_abs_ret:
                        n_outliers += 1
                        continue

                    sid = rec["stock_id"]
                    entries = by_stock.get(sid)
                    try:
                        snapshot = rec["feature_snapshot_json"]
                        feat = json.loads(snapshot) if isinstance(snapshot, str) else snapshot

                        if entries:
                            dts = dates_by_stock[sid]
                            all_rows = [NewsRow(s, p, rel)
                                        for p, s, rel in window(entries, dts, None, T)]
                            cap_rows = [NewsRow(s, p, rel)
                                        for p, s, rel in window(entries, dts, T - cap, T)]
                        else:
                            all_rows, cap_rows = [], []

                        news_a = feature_builder._build_news_features(all_rows)
                        news_b = feature_builder._build_news_features(cap_rows)

                        # 재구성 충실도: A 가 저장 스냅샷을 재현하는가 (news_count 기준)
                        stored = (feat.get("news") or {}).get("news_count")
                        if stored is not None:
                            fid_n += 1
                            if int(stored) == news_a["news_count"]:
                                fid_count_ok += 1

                        if news_a["news_count"] != news_b["news_count"]:
                            run_changed += 1
                            if news_a["news_count"] > 0 and news_b["news_count"] == 0:
                                n_dropped_all += 1

                        feat["news"] = news_a
                        s_cur = scorer.calculate_total_score(feat)["total_score"]
                        feat["news"] = news_b
                        s_cap = scorer.calculate_total_score(feat)["total_score"]
                    except Exception:
                        n_skipped += 1
                        continue

                    n_rows += 1
                    scored_cur.append((sid, s_cur, ret_f, alpha_f))
                    scored_cap.append((sid, s_cap, ret_f, alpha_f))

            if not scored_cur:
                continue
            n_changed += run_changed

            keys_cur, picked_cur = select(scored_cur, threshold, args.top_n)
            keys_cap, picked_cap = select(scored_cap, threshold, args.top_n)
            inter = keys_cur & keys_cap

            run_cur, run_cap = Acc(""), Acc("")
            for k, _, r, a in picked_cur:
                cur.add(r, a)
                run_cur.add(r, a)
                if k not in inter:
                    only_cur.add(r, a)
            for k, _, r, a in picked_cap:
                capped.add(r, a)
                run_cap.add(r, a)
                if k in inter:
                    both.add(r, a)
                else:
                    only_cap.add(r, a)

            union = keys_cur | keys_cap
            overlap.append(len(inter) / len(union) if union else 1.0)
            per_run.append((T, f"변경 {run_changed:,}종목", len(scored_cur),
                            run_cur.n, run_cur.avg_alpha, run_cap.n, run_cap.avg_alpha))
    finally:
        await conn.close()

    sel = f"top{args.top_n}" if args.top_n else f"점수>={threshold}"
    meta = [
        f"기간 {args.fromdate or '처음'} ~ {args.todate or '끝'} · 런 {len(per_run)}개 · "
        f"뉴스 보유 종목 {len(by_stock):,}개",
        f"채점 {n_rows:,}건 · 스냅샷 오류 {n_skipped:,} · 이상치 제외 {n_outliers:,}"
        f" (|ret|>{args.max_abs_ret})",
        f"상한으로 뉴스가 달라진 건 {n_changed:,} (그중 뉴스가 완전히 사라진 건 {n_dropped_all:,})",
    ]
    if fid_n:
        rate = fid_count_ok / fid_n * 100
        line = f"재구성 충실도: news_count 가 저장 스냅샷과 일치 {rate:.1f}% ({fid_n:,}건)"
        if rate < 90:
            line += "\n   ⚠️ 재구성이 원본을 잘 못 맞춥니다 — 결과 해석에 주의하세요."
        meta.append(line)

    report(f"뉴스 날짜 상한 반사실 검증 — {args.market} / {args.horizon} / "
           f"상한 {args.cap_days}일 / 선택규칙 {sel}",
           meta, cur, capped, both, only_cur, only_cap, overlap, per_run)


if __name__ == "__main__":
    asyncio.run(main())
