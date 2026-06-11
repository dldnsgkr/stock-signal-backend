import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.db_models import Stock, Market

logger = logging.getLogger(__name__)

SEC_HEADERS = {
    "User-Agent": "StockSignal dldnsgkr3326@gmail.com",
    "Accept": "application/json",
}

SEC_MAIN_EXCHANGES = {"Nasdaq", "NYSE", "NYSE Arca", "NYSE American"}
EXCHANGE_MAP = {
    "Nasdaq": "NASDAQ",
    "NYSE": "NYSE",
    "NYSE Arca": "NYSE Arca",
    "NYSE American": "AMEX",
}


async def collect_stock_list(db: AsyncSession, market_code: str = "US") -> dict:
    if market_code == "US":
        return await _collect_us_stocks(db)
    elif market_code == "KR":
        return await _collect_kr_stocks(db)
    return {"added": 0, "updated": 0, "errors": 0}


async def _collect_us_stocks(db: AsyncSession) -> dict:
    market_result = await db.execute(select(Market).where(Market.code == "US"))
    market = market_result.scalar_one_or_none()
    if not market:
        return {"added": 0, "updated": 0, "errors": 1}

    stocks = {}

    # SEC EDGAR company_tickers_exchange.json — 미국 전체 상장 종목
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://www.sec.gov/files/company_tickers_exchange.json",
                headers=SEC_HEADERS,
            )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", [])
        # fields: [cik, name, ticker, exchange]

        for row in rows:
            if len(row) < 4:
                continue
            _, name, symbol, exchange = row[0], row[1], row[2], row[3]

            if not symbol or not name:
                continue
            if exchange not in SEC_MAIN_EXCHANGES:
                continue
            if "$" in symbol or "/" in symbol or "^" in symbol or "." in symbol:
                continue
            if len(symbol) > 5:
                continue

            stocks[symbol] = {
                "name": name,
                "sector": None,
                "industry": None,
                "exchange": EXCHANGE_MAP.get(exchange, exchange),
            }

        logger.info(f"SEC EDGAR: {len(stocks)}개 로드")
    except Exception as e:
        logger.error(f"SEC EDGAR 로드 실패: {e}")
        return {"added": 0, "updated": 0, "errors": 1}

    return await _upsert_stocks(db, market, stocks, "US")


async def _collect_kr_stocks(db: AsyncSession) -> dict:
    market_result = await db.execute(select(Market).where(Market.code == "KR"))
    market = market_result.scalar_one_or_none()
    if not market:
        return {"added": 0, "updated": 0, "errors": 1}

    stocks = {}

    try:
        import FinanceDataReader as fdr

        for market_name, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
            try:
                df = fdr.StockListing(market_name)
                for _, row in df.iterrows():
                    code = str(row["Code"]).zfill(6)
                    name = str(row["Name"]).strip()
                    symbol = f"{code}{suffix}"
                    if not code or not name:
                        continue
                    stocks[symbol] = {
                        "name": name,
                        "sector": None,
                        "industry": None,
                        "exchange": "KRX",
                    }
                logger.info(f"{market_name}: {len(df)}개 로드")
            except Exception as e:
                logger.error(f"{market_name} 로드 실패: {e}")

    except Exception as e:
        logger.error(f"FinanceDataReader 로드 실패: {e}")
        return {"added": 0, "updated": 0, "errors": 1}

    logger.info(f"KR 전체: {len(stocks)}개")
    return await _upsert_stocks(db, market, stocks, "KR")


async def _upsert_stocks(db: AsyncSession, market: Market, stocks: dict, market_code: str) -> dict:
    existing_result = await db.execute(
        select(Stock.symbol, Stock.id, Stock.name, Stock.sector, Stock.industry)
        .where(Stock.market_id == market.id)
    )
    existing = {row.symbol: row for row in existing_result.all()}

    added = 0
    updated = 0
    errors = 0
    batch = []

    for symbol, info in stocks.items():
        if not symbol or len(symbol) > 20:
            continue
        try:
            if symbol in existing:
                row = existing[symbol]
                needs_update = False
                if info["name"] and row.name != info["name"]:
                    needs_update = True
                if needs_update:
                    await db.execute(
                        Stock.__table__.update()
                        .where(Stock.id == row.id)
                        .values(name=info["name"])
                    )
                    updated += 1
            else:
                batch.append(Stock(
                    market_id=market.id,
                    symbol=symbol,
                    name=info["name"] or symbol,
                    sector=info["sector"],
                    industry=info["industry"],
                    exchange=info["exchange"],
                    is_active=True,
                ))
                added += 1

                # 500개 단위로 커밋
                if len(batch) >= 500:
                    db.add_all(batch)
                    await db.commit()
                    batch = []

        except Exception as e:
            logger.debug(f"종목 처리 실패 {symbol}: {e}")
            errors += 1

    if batch:
        db.add_all(batch)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"최종 커밋 실패: {e}")
        return {"added": 0, "updated": updated, "errors": errors + 1}

    logger.info(f"{market_code} 동기화 완료: 추가 {added}개, 업데이트 {updated}개, 오류 {errors}개")
    return {"added": added, "updated": updated, "errors": errors}
