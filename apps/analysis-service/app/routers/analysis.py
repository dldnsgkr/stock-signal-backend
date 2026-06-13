import logging
import math
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List
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
