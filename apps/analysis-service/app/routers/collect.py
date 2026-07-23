from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.database import get_db
from app.collectors.price_collector import collect_prices
from app.collectors.news_collector import collect_news
from app.collectors.macro_collector import collect_macro_indicators
from app.collectors.financial_collector import collect_financials
from app.collectors.stock_list_collector import collect_stock_list
from app.collectors.investor_flow_collector import collect_investor_flow

router = APIRouter(prefix="/collect", tags=["collect"])


class CollectRequest(BaseModel):
    market: str = "US"
    offset: int = 0
    limit: int = 300
    days: int | None = None   # 지수 이력 백필용 (macro)


@router.post("/prices")
async def trigger_collect_prices(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    result = await collect_prices(db, market_code=body.market, offset=body.offset, limit=body.limit)
    return {"status": "ok", "market": body.market, "offset": body.offset, **result}


@router.post("/news")
async def trigger_collect_news(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    result = await collect_news(db, market_code=body.market, offset=body.offset, limit=body.limit)
    return {"status": "ok", "market": body.market, "offset": body.offset, **result}


@router.post("/macro")
async def trigger_collect_macro(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    kwargs = {"days": body.days} if body.days else {}
    result = await collect_macro_indicators(db, market_code=body.market, **kwargs)
    return {"status": "ok", "market": body.market, **result}


@router.post("/financials")
async def trigger_collect_financials(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    result = await collect_financials(db, market_code=body.market, offset=body.offset, limit=body.limit)
    return {"status": "ok", "market": body.market, "offset": body.offset, **result}


@router.post("/investor-flow")
async def trigger_collect_investor_flow(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    kwargs = {"days": body.days} if body.days else {}
    result = await collect_investor_flow(db, market_code=body.market, **kwargs)
    return {"status": "ok", "market": body.market, **result}


@router.post("/stock-list")
async def trigger_collect_stock_list(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    result = await collect_stock_list(db, market_code=body.market)
    return {"status": "ok", "market": body.market, **result}
