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


# 투자자별 컬럼 → 영문 키 매핑 (pykrx 1.x 실제 컬럼명 기준)
# "연기금 등" 공백 포함 / "연기금등" 공백 없음 양쪽 대응
_INVESTOR_COL_MAP = {
    "금융투자": "financialInvestment",
    "보험": "insurance",
    "투신": "trustFund",
    "사모": "privateEquity",
    "은행": "bank",
    "기타금융": "otherFinance",
    "연기금 등": "pension",
    "연기금등": "pension",   # 버전별 컬럼명 차이 대응
    "기관합계": "institution",
    "외국인": "foreign",
    "기타": "individual",   # 개인 + 기타법인 합산
    "전체": "total",
}


def _safe_int(val) -> int:
    """NaN/inf/None 안전 int 변환"""
    try:
        f = float(val)
        return 0 if (math.isnan(f) or math.isinf(f)) else int(f)
    except (TypeError, ValueError):
        return 0


@router.get("/investor-trading")
async def get_investor_trading(
    market: str = Query("KOSPI", description="KOSPI 또는 KOSDAQ"),
    fromdate: Optional[str] = Query(None, description="YYYYMMDD, 기본값 30일 전"),
    todate: Optional[str] = Query(None, description="YYYYMMDD, 기본값 오늘"),
):
    """KRX 투자자별 매매동향 (pykrx 이용, 순매수 단일 조회)"""
    try:
        import pandas as pd
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
        # pykrx 1.x: on 파라미터 없음, MultiIndex(투자자, 매도/매수/순매수) 반환
        df_all = krx.get_market_trading_value_by_investor(fromdate, todate, market_code)
    except Exception as e:
        logger.error(f"pykrx investor-trading error ({market_code} {fromdate}~{todate}): {e}")
        return JSONResponse(content={"error": f"KRX 데이터 조회 실패: {e}"}, status_code=500)

    try:
        if df_all is None or df_all.empty:
            return JSONResponse(content={
                "market": market_code, "fromdate": fromdate, "todate": todate,
                "data": [], "summary": {},
            })

        # NaN → 0 일괄 처리
        df_all = df_all.fillna(0)

        # MultiIndex(투자자type, 매도/매수/순매수) → 순매수 슬라이스
        if isinstance(df_all.columns, pd.MultiIndex):
            level_vals_0 = df_all.columns.get_level_values(0).tolist()
            level_vals_1 = df_all.columns.get_level_values(1).tolist()
            logger.info(f"pykrx MultiIndex level0 sample: {list(set(level_vals_0))[:5]}, level1 sample: {list(set(level_vals_1))[:5]}")

            if "순매수" in level_vals_1:
                df = df_all.xs("순매수", axis=1, level=1)
            elif "순매수" in level_vals_0:
                df = df_all.xs("순매수", axis=1, level=0)
            else:
                # fallback: 마지막 level1 값 사용
                last_l1 = list(set(level_vals_1))[-1]
                df = df_all.xs(last_l1, axis=1, level=1)
                logger.warning(f"순매수 column not found, falling back to: {last_l1}")
        else:
            df = df_all
            logger.info(f"pykrx flat columns: {list(df.columns)}")

        # 실제 컬럼 확인 후 매핑 (중복 en_key 는 첫 번째만 사용)
        seen_en_keys: set = set()
        col_map: list = []
        for kr_col, en_key in _INVESTOR_COL_MAP.items():
            if kr_col in df.columns and en_key not in seen_en_keys:
                col_map.append((kr_col, en_key))
                seen_en_keys.add(en_key)

        logger.info(f"pykrx mapped columns: {col_map}")

        rows = []
        for date_idx in df.index:
            date_str = date_idx.strftime("%Y-%m-%d")
            row: dict = {"date": date_str}
            for kr_col, en_key in col_map:
                row[en_key] = {"net": _safe_int(df.at[date_idx, kr_col]), "buy": 0, "sell": 0}
            rows.append(row)

        rows.sort(key=lambda x: x["date"], reverse=True)

        summary: dict = {en_key: sum(_safe_int(r.get(en_key, {}).get("net", 0)) for r in rows)
                         for _, en_key in col_map}

        return JSONResponse(content=_sanitize({
            "market": market_code,
            "fromdate": fromdate,
            "todate": todate,
            "data": rows,
            "summary": summary,
        }))

    except Exception as e:
        logger.error(f"investor-trading processing error: {e}", exc_info=True)
        return JSONResponse(content={"error": f"데이터 처리 오류: {e}"}, status_code=500)
