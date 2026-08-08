"""
분기 손익계산서 수집 → /tmp/quarterly.json (성장률 피처 검증용 사전 준비)

왜 필요한가
-----------
`financial_metrics` 의 매출·영업이익은 yfinance 의 **TTM 값**이고 우리는 그걸 월별로
스냅샷한다. 그래서 연속 기간 간 값이 실제로 바뀌는 비율이 **KR 5.9% / US 29.8%** 뿐이라
성장률을 만들 수 없다(2026-08-09 측정). `feature_builder` 의 `operating_income_growth`
슬롯이 계속 `None` 이었던 이유다.

`ticker.quarterly_income_stmt` 는 **실제 회계 분기**(2026-03-31, 2025-12-31 …)를 주므로
여기서 진짜 성장률을 만들 수 있다. 5~7분기가 조회된다.

⚠️ look-ahead 주의
-----------------
분기 종료일과 **공시일은 다르다.** 2026-03-31 분기는 실제로는 5월 중순에 공시된다.
이걸 4월 추천에 쓰면 미래 정보다. 검증·운영 모두에서 **공시 지연을 반드시 반영**할 것
(KR 분기보고서 법정기한 45일, US 10-Q 40~45일 → 보수적으로 60일 권장).
이 스크립트는 **분기 종료일 그대로** 저장하고, 지연 적용은 사용하는 쪽 책임이다.

사용법
------
  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/fetch_quarterly.py KR
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import asyncpg
import yfinance as yf

OUT_PATH = "/tmp/quarterly.json"
SAVE_EVERY = 100

# 티커마다 행 이름이 다르다. 앞쪽을 우선한다.
REVENUE_KEYS = ("Total Revenue", "Operating Revenue")
OP_INCOME_KEYS = ("Total Operating Income As Reported", "Operating Income", "EBIT")
NET_INCOME_KEYS = (
    "Net Income From Continuing Operation Net Minority Interest",
    "Net Income Common Stockholders",
    "Net Income",
)

SQL = """
    SELECT DISTINCT s.symbol
    FROM stocks s
    JOIN markets m ON m.id = s.market_id
    JOIN financial_metrics fm ON fm.stock_id = s.id
    WHERE m.code = $1 AND s.is_active
    ORDER BY s.symbol
"""


def pick(df, keys):
    row = next((k for k in keys if k in df.index), None)
    if row is None:
        return None
    out = {}
    for col in df.columns:
        try:
            v = float(df.loc[row][col])
        except (TypeError, ValueError):
            continue
        if v == v:  # NaN 제외
            out[str(col)[:10]] = v
    return out or None


async def main():
    market = (sys.argv[1] if len(sys.argv) > 1 else "KR").upper()
    dsn = os.environ["DATABASE_URL"].strip().strip('"')
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL, market)
    await conn.close()
    targets = [r["symbol"] for r in rows]

    out = {}
    if Path(OUT_PATH).exists():
        with open(OUT_PATH) as f:
            out = json.load(f)
    todo = [s for s in targets if s not in out]
    print(f"{market}: 대상 {len(targets)}종목 · 기존 {len(out)} · 받을 것 {len(todo)}", flush=True)

    t0, errs = time.time(), 0
    for i, sym in enumerate(todo, 1):
        try:
            q = yf.Ticker(sym).quarterly_income_stmt
            if q is not None and not q.empty:
                rec = {
                    "revenue": pick(q, REVENUE_KEYS),
                    "op_income": pick(q, OP_INCOME_KEYS),
                    "net_income": pick(q, NET_INCOME_KEYS),
                }
                if any(rec.values()):
                    out[sym] = rec
        except Exception:
            errs += 1
        if i % SAVE_EVERY == 0:
            with open(OUT_PATH, "w") as f:
                json.dump(out, f)
            print(f"  {i}/{len(todo)} · 누적 {len(out)} · 실패 {errs} · "
                  f"{time.time() - t0:.0f}s", flush=True)

    with open(OUT_PATH, "w") as f:
        json.dump(out, f)
    print(f"완료: {len(out)}종목 → {OUT_PATH}, 실패 {errs}, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
