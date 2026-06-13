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
    """KRX MDCSTAT02202 직접 POST 호출 — 투자자별 거래실적 일별추이 (일반, 순매수, 거래대금)
    pykrx Post 클래스와 동일한 헤더 사용 (X-Requested-With 필수)
    """
    session = _requests.Session()
    # KRX 세션 초기화 (JSESSIONID 쿠키 획득)
    session.get(
        "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
        timeout=10,
    )
    resp = session.post(
        "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd",
            "X-Requested-With": "XMLHttpRequest",
        },
        data={
            "bld":         "dbms/MDC/STAT/standard/MDCSTAT02202",
            "locale":      "ko_KR",
            "mktId":       mkt_id,   # STK=KOSPI / KSQ=KOSDAQ
            "etf":         "",
            "etn":         "",
            "elw":         "",
            "strtDd":      strt_dd,
            "endDd":       end_dd,
            "inqTpCd":     "2",      # 일반(4그룹) 조회 타입
            "trdVolVal":   "2",      # 거래대금
            "askBid":      "3",      # 순매수
            "csvxls_isNo": "false",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("output", [])


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
        logger.error(f"KRX API error ({market_upper} {fromdate}~{todate}): {e}")
        return JSONResponse(content={"error": f"KRX 데이터 조회 실패: {e}"}, status_code=500)

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
