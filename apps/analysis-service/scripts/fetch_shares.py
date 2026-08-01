"""
발행주식수 수집 → /tmp/shares.json (`counterfactual_per.py` 전용 사전 준비)

as-of PER 복원에 쓴다:  PER(t) = shares × close(t) / net_income(period_end <= t)

주가와 순이익은 DB 에 과거가 남아 있어 as-of 로 뽑을 수 있지만 발행주식수는
어디에도 저장하지 않는다. 그래서 현재값을 yfinance 에서 받아 근사로 쓴다 —
발행주식수는 몇 달 단위로 거의 변하지 않으므로, 이 근사의 타당성은
`counterfactual_per.py --market US --check-only` 가 저장된 trailingPE 와
대조해 검증한다.

대상: KR 은 net_income 이 있는 전 종목, US 는 검증 대조용 표본 400종목.
소요: 종목당 약 0.4초 (2,000종목 ≈ 13분).

  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/fetch_shares.py
"""

import asyncio
import json
import os
import time

import asyncpg
import yfinance as yf

OUT_PATH = "/tmp/shares.json"
US_SAMPLE = 400

SQL = """
    SELECT DISTINCT s.symbol, m.code
    FROM financial_metrics fm
    JOIN stocks s ON s.id = fm.stock_id
    JOIN markets m ON m.id = s.market_id
    WHERE fm.net_income IS NOT NULL AND s.is_active
      AND (m.code = 'KR' OR (m.code = 'US' AND fm.per IS NOT NULL))
    ORDER BY 2, 1
"""


async def main():
    dsn = os.environ["DATABASE_URL"].strip().strip('"')
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()

    kr = [r["symbol"] for r in rows if r["code"] == "KR"]
    us = [r["symbol"] for r in rows if r["code"] == "US"][:US_SAMPLE]
    targets = kr + us
    print(f"대상 {len(targets)}종목 (KR {len(kr)} / US 표본 {len(us)})", flush=True)

    out, t0, errs = {}, time.time(), 0
    for i, sym in enumerate(targets, 1):
        try:
            info = yf.Ticker(sym).info or {}
            shares = info.get("sharesOutstanding")
            if shares:
                out[sym] = {"shares": float(shares), "mcap": info.get("marketCap"),
                            "trailingPE": info.get("trailingPE")}
        except Exception:
            errs += 1
        if i % 100 == 0:
            print(f"  {i}/{len(targets)} · {len(out)} 수집 · {errs} 실패 · "
                  f"{time.time() - t0:.0f}s", flush=True)

    with open(OUT_PATH, "w") as f:
        json.dump(out, f)
    print(f"완료: {len(out)}종목 → {OUT_PATH}, 실패 {errs}, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
