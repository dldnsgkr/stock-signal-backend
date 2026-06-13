import logging
import math
import datetime
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
from app.database import get_db
from app.models.db_models import Stock, Market
from app.engine.feature_builder import build_features
from app.engine.scorer import (
    calculate_total_score,
    determine_action,
    calculate_confidence,
    generate_reasons,
    WATCH_THRESHOLD,
)


def _sanitize(obj):
    """재귀적으로 NaN/inf를 None으로 교체 (JSON 직렬화 안전)."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger(__name__)

MODEL_VERSION = "ensemble_v2.0"


class GenerateSignalsRequest(BaseModel):
    market: str = "US"


@router.post("/generate-signals")
async def generate_signals(body: GenerateSignalsRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Stock)
        .join(Market)
        .where(Market.code == body.market, Stock.is_active == True)
    )
    stocks = result.scalars().all()

    recommendations = []
    processed = 0
    skipped = 0

    for stock in stocks:
        features = await build_features(db, stock, market_code=body.market)
        if not features:
            skipped += 1
            continue

        score_detail = calculate_total_score(features)
        action = determine_action(score_detail["total_score"])
        confidence = calculate_confidence(score_detail["total_score"], score_detail)
        reasons = generate_reasons(features, score_detail, action)

        recommendations.append({
            "stockId": stock.id,
            "symbol": stock.symbol,
            "action": action,
            "score": score_detail["total_score"],
            "confidence": confidence,
            "entryPrice": features["current_price"],
            "reasons": reasons,
            "scoreDetail": score_detail,
            "featureSnapshot": {
                "technical": features["technical"],
                "fundamental": features["fundamental"],
                "news": features["news"],
                "macro": features["macro"],
                "flow": features["flow"],
            },
        })
        processed += 1

    recommendations.sort(key=lambda x: x["score"], reverse=True)

    logger.info(
        f"Generated {len(recommendations)} signals for {body.market} "
        f"(processed: {processed}, skipped: {skipped})"
    )

    payload = _sanitize({
        "modelVersion": MODEL_VERSION,
        "market": body.market,
        "recommendations": recommendations,
        "processedCount": processed,
        "skippedCount": skipped,
        "runNotes": f"Score-based v1 run for {body.market}, {len(recommendations)} signals",
    })
    return JSONResponse(content=payload)


class BuyRecItem(BaseModel):
    id: int
    stock_id: int
    buy_score: float


class GenerateSellSignalsRequest(BaseModel):
    market: str = "US"
    buy_recommendations: List[BuyRecItem]


@router.post("/generate-sell-signals")
async def generate_sell_signals(body: GenerateSellSignalsRequest, db: AsyncSession = Depends(get_db)):
    sell_signals = []

    for rec in body.buy_recommendations:
        stock = await db.get(Stock, rec.stock_id)
        if not stock:
            continue

        features = await build_features(db, stock, market_code=body.market)
        if not features:
            continue

        score_detail = calculate_total_score(features)
        current_score = score_detail["total_score"]

        if current_score < WATCH_THRESHOLD:
            reasons = generate_reasons(features, score_detail, "SELL")
            sell_signals.append({
                "buy_recommendation_id": rec.id,
                "stock_id": rec.stock_id,
                "current_score": current_score,
                "exit_price": features["current_price"],
                "reasons": reasons,
            })

    logger.info(
        f"SELL signal check for {body.market}: "
        f"checked={len(body.buy_recommendations)}, signals={len(sell_signals)}"
    )

    return JSONResponse(content=_sanitize({"sell_signals": sell_signals}))


# 투자자별 컬럼 → 영문 키 매핑
_INVESTOR_COL_MAP = {
    "금융투자": "financialInvestment",
    "보험": "insurance",
    "투신": "trustFund",
    "사모": "privateEquity",
    "은행": "bank",
    "기타금융": "otherFinance",
    "연기금등": "pension",
    "기관합계": "institution",
    "외국인": "foreign",
    "기타": "individual",   # 개인 + 기타법인 합산
    "전체": "total",
}


@router.get("/investor-trading")
async def get_investor_trading(
    market: str = Query("KOSPI", description="KOSPI 또는 KOSDAQ"),
    fromdate: Optional[str] = Query(None, description="YYYYMMDD, 기본값 30일 전"),
    todate: Optional[str] = Query(None, description="YYYYMMDD, 기본값 오늘"),
):
    """KRX 투자자별 매매동향 (pykrx 이용)"""
    try:
        from pykrx import stock as krx
    except ImportError:
        return JSONResponse(content={"error": "pykrx not installed"}, status_code=500)

    today = datetime.date.today()
    if not todate:
        todate = today.strftime("%Y%m%d")
    if not fromdate:
        fromdate = (today - datetime.timedelta(days=30)).strftime("%Y%m%d")

    market_code = market.upper()
    if market_code not in ("KOSPI", "KOSDAQ"):
        return JSONResponse(content={"error": "market must be KOSPI or KOSDAQ"}, status_code=400)

    try:
        df_net  = krx.get_market_trading_value_by_investor(fromdate, todate, market_code, on="순매수")
        df_buy  = krx.get_market_trading_value_by_investor(fromdate, todate, market_code, on="매수")
        df_sell = krx.get_market_trading_value_by_investor(fromdate, todate, market_code, on="매도")
    except Exception as e:
        logger.error(f"pykrx investor-trading error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

    if df_net is None or df_net.empty:
        return JSONResponse(content={"market": market_code, "fromdate": fromdate, "todate": todate, "data": [], "summary": {}})

    rows = []
    for date_idx in df_net.index:
        date_str = date_idx.strftime("%Y-%m-%d")
        row: dict = {"date": date_str}

        for kr_col, en_key in _INVESTOR_COL_MAP.items():
            net_val  = int(df_net.at[date_idx, kr_col])  if kr_col in df_net.columns  else 0
            buy_val  = int(df_buy.at[date_idx, kr_col])  if (kr_col in df_buy.columns  and date_idx in df_buy.index)  else 0
            sell_val = int(df_sell.at[date_idx, kr_col]) if (kr_col in df_sell.columns and date_idx in df_sell.index) else 0
            row[en_key] = {"net": net_val, "buy": buy_val, "sell": sell_val}

        rows.append(row)

    rows.sort(key=lambda x: x["date"], reverse=True)

    # 기간 합산 순매수 요약
    summary: dict = {}
    for en_key in _INVESTOR_COL_MAP.values():
        summary[en_key] = sum(r[en_key]["net"] for r in rows if en_key in r)

    return JSONResponse(content=_sanitize({
        "market": market_code,
        "fromdate": fromdate,
        "todate": todate,
        "data": rows,
        "summary": summary,
    }))
