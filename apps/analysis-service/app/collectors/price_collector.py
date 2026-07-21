import logging
from datetime import datetime, timedelta
import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.models.db_models import Stock, Market

logger = logging.getLogger(__name__)


async def collect_prices(
    db: AsyncSession,
    market_code: str = "US",
    days: int = 90,
    offset: int = 0,
    limit: int = 300,
) -> dict:
    result = await db.execute(
        select(Stock)
        .join(Market)
        .where(Market.code == market_code, Stock.is_active == True)
        .offset(offset)
        .limit(limit)
    )
    stocks = result.scalars().all()

    if not stocks:
        return {"collected": 0, "skipped": 0, "errors": 0, "total_in_batch": 0}

    now = datetime.now()
    start_date = now - timedelta(days=days)
    # yfinance 의 end 는 exclusive → +1 해야 당일 종가까지 포함된다.
    # (KST 거래소는 타임존 차이로 하루를 더 잃어 KR 가격이 이틀씩 밀려 있었음)
    end_date = now + timedelta(days=1)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    collected = 0
    skipped = 0
    errors = 0

    for stock in stocks:
        try:
            ticker = yf.Ticker(stock.symbol)
            df = ticker.history(start=start_str, end=end_str, auto_adjust=True)

            if df is None or df.empty:
                skipped += 1
                continue

            # 장중이거나 미완성 봉이면 yfinance 가 OHLC 를 NaN 으로 준다.
            # 그대로 저장하면 수익률 계산이 오염되므로 버린다.
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            df = df.fillna({"Volume": 0})
            if df.empty:
                skipped += 1
                continue

            rows = [
                {
                    "stock_id": stock.id,
                    "date": date_idx.date(),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                    "adj_close": float(row["Close"]),
                }
                for date_idx, row in df.iterrows()
            ]

            if rows:
                await db.execute(
                    text("""
                        INSERT INTO price_daily (stock_id, date, open, high, low, close, volume, adj_close)
                        VALUES (:stock_id, :date, :open, :high, :low, :close, :volume, :adj_close)
                        ON CONFLICT (stock_id, date) DO UPDATE SET
                            open = EXCLUDED.open, high = EXCLUDED.high,
                            low = EXCLUDED.low, close = EXCLUDED.close,
                            volume = EXCLUDED.volume, adj_close = EXCLUDED.adj_close
                    """),
                    rows,
                )
                await db.commit()
                collected += len(rows)

        except Exception as e:
            logger.error(f"Error collecting prices for {stock.symbol}: {e}")
            await db.rollback()
            errors += 1

    logger.info(f"Price batch [offset={offset} limit={limit}]: {collected} rows, {skipped} skipped, {errors} errors")
    return {"collected": collected, "skipped": skipped, "errors": errors, "total_in_batch": len(stocks)}
