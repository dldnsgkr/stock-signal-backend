"""KR 종목별 투자자 순매수(수급) 일별 수집.

KRX MDCSTAT02401(투자자별 순매수상위종목)이 과거 일자에 대해 전 종목을
반환하는 것을 이용한다. 조회 구간 전체를 upsert 하므로 파이프라인이 며칠
걸러도 다음 실행에서 자연 복구된다(macro_collector 와 같은 원칙).

⚠️ 점수 반영은 하지 않는다 — 2026-07-21 가설 검증이 단일 하락장 구간이라
   부호 고정 반영은 시기상조로 결론. 이 수집기는 다른 국면 데이터를 쌓아
   재검증할 수 있게 하는 적재 전용이다.

투자자 코드(pykrx MDCSTAT02401 기준):
  9000=외국인, 7050=기관합계, 8000=개인
  ※ 7-21 검증 스크립트는 institution 에 8000(개인)을 잘못 썼다.
    여기서는 기관합계(7050)를 쓴다.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.db_models import Stock, Market

logger = logging.getLogger(__name__)

# mktId → stocks.symbol 접미사 (yfinance 표기)
_MARKETS = {"STK": ".KS", "KSQ": ".KQ"}
# 개인(8000)도 수집 — 7-24 재검증 결과 |flow| 신호의 실제 출처가 개인이었을
# 가능성이 높아(기관 7050 은 신호 없음) 국면별 재검증용 데이터를 쌓는다.
_INVESTORS = {"foreign": "9000", "institution": "7050", "individual": "8000"}

_KRX_CALL_GAP_SEC = 0.3  # KRX 부담 완화


def _fetch_flow_sync(strt_dd: str, end_dd: str, mkt_id: str, invst_tp_cd: str) -> list:
    """pykrx 내부 클래스 직접 호출(동기). 실패는 호출부에서 처리."""
    from pykrx.website.krx.market.core import 투자자별_순매수상위종목

    df = 투자자별_순매수상위종목().fetch(strt_dd, end_dd, mkt_id, invst_tp_cd)
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def _to_int(raw) -> int | None:
    s = str(raw).replace(",", "").strip()
    if s in ("", "-", "None", "nan"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


async def collect_investor_flow(
    db: AsyncSession, market_code: str = "KR", days: int = 7
) -> dict:
    if market_code != "KR":
        return {"collected": 0, "errors": 0, "skipped_market": market_code}

    result = await db.execute(
        select(Stock.id, Stock.symbol)
        .join(Market)
        .where(Market.code == "KR", Stock.is_active == True)  # noqa: E712
    )
    symbol_to_id = {symbol: sid for sid, symbol in result.all()}
    if not symbol_to_id:
        return {"collected": 0, "errors": 0, "reason": "no KR stocks"}

    # 조회 일자: 최근 days 일 중 주중만. 휴장일은 KRX 가 빈 응답을 줘 자연 스킵.
    today = datetime.now().date()
    dates = [
        (today - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(days)
        if (today - timedelta(days=i)).weekday() < 5
    ]

    collected = 0
    errors = 0
    unmatched: set[str] = set()

    for d in sorted(dates):
        for mkt_id, suffix in _MARKETS.items():
            for inv_name, inv_code in _INVESTORS.items():
                try:
                    records = await asyncio.to_thread(
                        _fetch_flow_sync, d, d, mkt_id, inv_code
                    )
                except Exception as e:
                    logger.error(f"KRX fetch failed {d} {mkt_id} {inv_name}: {e}")
                    errors += 1
                    continue

                rows = []
                for r in records:
                    code = str(r.get("ISU_SRT_CD", "")).strip()
                    net_val = _to_int(r.get("NETBID_TRDVAL"))
                    net_vol = _to_int(r.get("NETBID_TRDVOL"))
                    if not code or net_val is None:
                        continue
                    stock_id = symbol_to_id.get(code + suffix)
                    if stock_id is None:
                        unmatched.add(code + suffix)
                        continue
                    rows.append(
                        {
                            "stock_id": stock_id,
                            "trade_date": datetime.strptime(d, "%Y%m%d").date(),
                            "investor_type": inv_name,
                            "net_buy_value": net_val,
                            "net_buy_volume": net_vol,
                        }
                    )

                if rows:
                    try:
                        await db.execute(
                            text("""
                                INSERT INTO investor_flow_daily
                                    (stock_id, trade_date, investor_type, net_buy_value, net_buy_volume)
                                VALUES
                                    (:stock_id, :trade_date, :investor_type, :net_buy_value, :net_buy_volume)
                                ON CONFLICT (stock_id, trade_date, investor_type) DO UPDATE SET
                                    net_buy_value  = EXCLUDED.net_buy_value,
                                    net_buy_volume = EXCLUDED.net_buy_volume
                            """),
                            rows,
                        )
                        await db.commit()
                        collected += len(rows)
                    except Exception as e:
                        logger.error(f"Upsert failed {d} {mkt_id} {inv_name}: {e}")
                        await db.rollback()
                        errors += 1

                await asyncio.sleep(_KRX_CALL_GAP_SEC)

    if unmatched:
        logger.info(f"Unmatched symbols (비활성/미등록): {len(unmatched)}개")
    logger.info(f"Investor flow done: {collected} rows, {errors} errors, {len(dates)} days")
    return {"collected": collected, "errors": errors, "days": len(dates)}
