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

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
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
