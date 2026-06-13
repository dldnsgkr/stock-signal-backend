import logging
import math
import datetime
import requests as _requests
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


def _safe_int(val) -> int:
    """NaN/inf/None/콤마문자열 안전 int 변환"""
    try:
        if isinstance(val, str):
            val = val.replace(",", "").replace(" ", "").strip()
            if not val or val == "-":
                return 0
        f = float(val)
        return 0 if (math.isnan(f) or math.isinf(f)) else int(f)
    except (TypeError, ValueError):
        return 0


# MDCSTAT02202 (투자자별 거래실적 일별추이 일반, inqTpCd=2) TRDVAL 컬럼 → 영문 키 매핑
# pykrx source 확인: TRDVAL1=기관합계, TRDVAL2=외국인, TRDVAL3=개인, TRDVAL4=기타법인+기타
_KRX_COL_MAP = [
    ("TRDVAL1",    "institution"),  # 기관합계
    ("TRDVAL2",    "foreign"),      # 외국인
    ("TRDVAL3",    "individual"),   # 개인
    ("TRDVAL4",    "otherCorp"),    # 기타법인+기타
    ("TRDVAL_TOT", "total"),        # 전체합계
]


def _krx_fetch_investor_daily(mkt_id: str, strt_dd: str, end_dd: str) -> list:
    """pykrx 내부 클래스 직접 호출 — 깨진 wrapper 우회 + pykrx 인증 세션 재사용.
    KRX 2026년 이후 인증 필요: EC2 환경변수 KRX_ID, KRX_PW 설정 시 자동 로그인.
    """
    try:
        from pykrx.website.krx.market.core import 투자자별_거래실적_전체시장_일별추이_일반
    except ImportError:
        raise Exception("pykrx 미설치")

    core = 투자자별_거래실적_전체시장_일별추이_일반()
    # fetch(strtDd, endDd, mktId, etf, etn, els, trdVolVal, askBid)
    # trdVolVal=2(거래대금), askBid=3(순매수)
    df = core.fetch(strt_dd, end_dd, mkt_id, "", "", "", 2, 3)

    if df is None or df.empty:
        return []

    return df.to_dict("records")


@router.get("/investor-trading")
async def get_investor_trading(
    market: str = Query("KOSPI", description="KOSPI 또는 KOSDAQ"),
    fromdate: Optional[str] = Query(None, description="YYYYMMDD, 기본값 30일 전"),
    todate: Optional[str] = Query(None, description="YYYYMMDD, 기본값 오늘"),
):
    """KRX 투자자별 매매동향 — pykrx 우회, KRX MDCSTAT02203 직접 호출 (순매수 거래대금)"""
    today = datetime.date.today()
    if not todate:
        todate = today.strftime("%Y%m%d")
    if not fromdate:
        fromdate = (today - datetime.timedelta(days=30)).strftime("%Y%m%d")

    market_upper = market.upper()
    if market_upper not in ("KOSPI", "KOSDAQ"):
        return JSONResponse(content={"error": "market must be KOSPI or KOSDAQ"}, status_code=400)

    mkt_id = "STK" if market_upper == "KOSPI" else "KSQ"

    try:
        output = _krx_fetch_investor_daily(mkt_id, fromdate, todate)
    except Exception as e:
        err_str = str(e)
        logger.error(f"KRX investor-trading error ({market_upper} {fromdate}~{todate}): {err_str}")
        # KRX 2026년 이후 인증 필요 — 명확한 안내 반환
        if "LOGOUT" in err_str or "JSON" in err_str or "400" in err_str:
            return JSONResponse(content={
                "error": "KRX 인증 필요",
                "detail": "KRX 데이터 포털(data.krx.co.kr)이 2026년부터 로그인을 요구합니다. "
                          "EC2 .env에 KRX_ID=<아이디> KRX_PW=<비밀번호> 를 추가한 뒤 "
                          "pm2 restart stock-signal-analysis 하세요.",
            }, status_code=503)
        return JSONResponse(content={"error": f"KRX 데이터 조회 실패: {err_str}"}, status_code=500)

    if not output:
        return JSONResponse(content={"market": market_upper, "fromdate": fromdate, "todate": todate, "data": [], "summary": {}})

    if output:
        logger.info(f"KRX response sample keys: {list(output[0].keys())}")

    rows = []
    for item in output:
        date_raw = item.get("TRD_DD", "")
        date_str = date_raw.replace("/", "-").strip()
        if not date_str:
            continue

        row: dict = {"date": date_str}
        for col_key, en_key in _KRX_COL_MAP:
            row[en_key] = {"net": _safe_int(item.get(col_key, 0)), "buy": 0, "sell": 0}
        rows.append(row)

    rows.sort(key=lambda x: x["date"], reverse=True)

    all_en_keys = [en_key for _, en_key in _KRX_COL_MAP] + ["institution"]
    summary: dict = {
        en_key: sum(r.get(en_key, {}).get("net", 0) for r in rows)
        for en_key in all_en_keys
    }

    return JSONResponse(content=_sanitize({
        "market": market_upper,
        "fromdate": fromdate,
        "todate": todate,
        "data": rows,
        "summary": summary,
    }))
