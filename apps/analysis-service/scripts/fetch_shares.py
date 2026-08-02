"""
발행주식수 + 자기자본 수집 → /tmp/shares.json
(`counterfactual_per.py` / `counterfactual_pbr.py` 전용 사전 준비)

as-of 재구성에 쓴다:
    PER(t) = shares × close(t) / net_income(period_end <= t)   ← net_income 은 DB
    PBR(t) = shares × close(t) / equity(연차 결산 <= t)         ← equity 는 여기서 수집

주가와 순이익은 DB 에 과거가 남아 있지만 발행주식수·자기자본은 저장하지 않는다.
- `shares` : 현재값(yfinance `info`). 몇 달 단위로 거의 안 변하므로 근사로 쓴다.
  타당성은 `counterfactual_per.py --market US --check-only` 가 검증.
- `equity` : yfinance `balance_sheet` 의 **연차 결산 시계열**. 최신 결산이
  2025-12-31 로 백테스트 구간(2026-05~07)보다 앞서므로 look-ahead 가 없다.
  KR 티커도 정상 제공된다(2026-08-01 확인).

**재실행하면 이미 받은 종목은 건너뛴다**(파일 병합). 중간에 끊겨도 다시 돌리면 이어받는다.

대상: KR 은 net_income 이 있는 전 종목, US 는 검증 대조용 표본 400종목.
소요: 종목당 약 0.8초 (2,000종목 ≈ 27분).

  cd ~/stock-signal-backend/apps/analysis-service
  export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
  .venv/bin/python scripts/fetch_shares.py
"""

import asyncio
import json
import os
import time
from pathlib import Path

import asyncpg
import yfinance as yf

OUT_PATH = "/tmp/shares.json"
US_SAMPLE = 400
SAVE_EVERY = 100

# 티커마다 행 이름이 다르다. 앞쪽을 우선한다.
EQUITY_KEYS = ("Stockholders Equity", "Common Stock Equity",
               "Total Equity Gross Minority Interest")

SQL = """
    SELECT DISTINCT s.symbol, m.code
    FROM financial_metrics fm
    JOIN stocks s ON s.id = fm.stock_id
    JOIN markets m ON m.id = s.market_id
    WHERE fm.net_income IS NOT NULL AND s.is_active
      AND (m.code = 'KR' OR (m.code = 'US' AND fm.per IS NOT NULL))
    ORDER BY 2, 1
"""


def extract_equity(ticker):
    """balance_sheet 에서 자기자본 시계열 → [[YYYY-MM-DD, value], ...] 최신순."""
    try:
        bs = ticker.balance_sheet
    except Exception:
        return None
    if bs is None or getattr(bs, "empty", True):
        return None
    row = next((k for k in EQUITY_KEYS if k in bs.index), None)
    if row is None:
        return None
    out = []
    for col in bs.columns:
        try:
            val = float(bs.loc[row][col])
        except (TypeError, ValueError):
            continue
        if val == val and val > 0:  # NaN 제외
            out.append([str(col)[:10], val])
    return out or None


async def main():
    dsn = os.environ["DATABASE_URL"].strip().strip('"')
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()

    kr = [r["symbol"] for r in rows if r["code"] == "KR"]
    us = [r["symbol"] for r in rows if r["code"] == "US"][:US_SAMPLE]
    targets = kr + us

    out = {}
    if Path(OUT_PATH).exists():
        with open(OUT_PATH) as f:
            out = json.load(f)
    todo = [s for s in targets
            if s not in out or "shares" not in out[s] or "equity" not in out[s]]
    print(f"대상 {len(targets)}종목 (KR {len(kr)} / US 표본 {len(us)}) · "
          f"기존 {len(out)}종목 · 받을 것 {len(todo)}종목", flush=True)

    t0, errs = time.time(), 0
    for i, sym in enumerate(todo, 1):
        rec = out.get(sym, {})
        try:
            ticker = yf.Ticker(sym)
            if "shares" not in rec:
                info = ticker.info or {}
                shares = info.get("sharesOutstanding")
                if shares:
                    rec["shares"] = float(shares)
                    rec["mcap"] = info.get("marketCap")
                    rec["trailingPE"] = info.get("trailingPE")
                    rec["priceToBook"] = info.get("priceToBook")
            if "equity" not in rec:
                rec["equity"] = extract_equity(ticker)
            if rec:
                out[sym] = rec
        except Exception:
            errs += 1
        if i % SAVE_EVERY == 0:
            with open(OUT_PATH, "w") as f:
                json.dump(out, f)
            have_eq = sum(1 for v in out.values() if v.get("equity"))
            print(f"  {i}/{len(todo)} · 누적 {len(out)}종목(자기자본 {have_eq}) · "
                  f"{errs} 실패 · {time.time() - t0:.0f}s", flush=True)

    with open(OUT_PATH, "w") as f:
        json.dump(out, f)
    have_sh = sum(1 for v in out.values() if v.get("shares"))
    have_eq = sum(1 for v in out.values() if v.get("equity"))
    print(f"완료: {len(out)}종목 → {OUT_PATH} (주식수 {have_sh} / 자기자본 {have_eq}), "
          f"실패 {errs}, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
