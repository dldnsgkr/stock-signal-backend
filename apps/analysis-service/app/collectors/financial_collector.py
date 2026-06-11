import logging
from datetime import datetime, date
import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.db_models import Stock, Market, FinancialMetrics

logger = logging.getLogger(__name__)


async def collect_financials(
    db: AsyncSession,
    market_code: str = "US",
    offset: int = 0,
    limit: int = 200,
) -> dict:
    result = await db.execute(
        select(Stock)
        .join(Market)
        .where(Market.code == market_code, Stock.is_active == True)
        .offset(offset)
        .limit(limit)
    )
    stocks = result.scalars().all()

    collected = 0
    skipped = 0
    errors = 0

    for stock in stocks:
        try:
            ticker = yf.Ticker(stock.symbol)
            info = ticker.info or {}

            roe = _safe_float(info.get("returnOnEquity"))
            per = _safe_float(info.get("trailingPE"))
            pbr = _safe_float(info.get("priceToBook"))
            revenue = _safe_float(info.get("totalRevenue"))
            net_income = _safe_float(info.get("netIncomeToCommon"))
            operating_income = _safe_float(info.get("operatingIncome") or info.get("ebitda"))
            debt_ratio = _safe_float(info.get("debtToEquity"))
            if debt_ratio is not None:
                debt_ratio = debt_ratio / 100

            # 데이터가 하나도 없으면 스킵
            if all(v is None for v in [roe, per, pbr, revenue, net_income]):
                logger.debug(f"{stock.symbol}: no financial data available")
                skipped += 1
                continue

            period_end = date.today().replace(day=1)
            period_type = "annual"

            existing = await db.execute(
                select(FinancialMetrics).where(
                    FinancialMetrics.stock_id == stock.id,
                    FinancialMetrics.period_type == period_type,
                    FinancialMetrics.period_end == period_end,
                )
            )
            fm = existing.scalar_one_or_none()

            if fm:
                fm.roe = roe
                fm.per = per
                fm.pbr = pbr
                fm.revenue = revenue
                fm.operating_income = operating_income
                fm.net_income = net_income
                fm.debt_ratio = debt_ratio
            else:
                fm = FinancialMetrics(
                    stock_id=stock.id,
                    period_type=period_type,
                    period_end=period_end,
                    roe=roe,
                    per=per,
                    pbr=pbr,
                    revenue=revenue,
                    operating_income=operating_income,
                    net_income=net_income,
                    debt_ratio=debt_ratio,
                )
                db.add(fm)

            await db.flush()
            collected += 1
            logger.info(f"{stock.symbol}: ROE={roe}, PER={per}, PBR={pbr}")

        except Exception as e:
            logger.error(f"Error collecting financials for {stock.symbol}: {e}")
            await db.rollback()
            errors += 1
            continue

    await db.commit()
    logger.info(f"Financial batch [offset={offset} limit={limit}]: {collected} collected, {skipped} skipped, {errors} errors")
    return {"collected": collected, "skipped": skipped, "errors": errors, "total_in_batch": len(stocks)}


def _safe_float(value) -> float | None:
    try:
        if value is None or value != value:  # NaN check
            return None
        f = float(value)
        if f == 0.0 or abs(f) > 1e12:
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None
