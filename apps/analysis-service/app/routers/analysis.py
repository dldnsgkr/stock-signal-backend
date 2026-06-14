import logging
import math
import datetime
import requests as _requests
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel
from typing import List, Optional
from app.database import get_db
from app.models.db_models import Stock, Market, PriceDaily
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
        # KRX 2026년 이후 인증 필요 — LOGOUT 응답이 JSON 파싱 실패로 도달
        # resp.json()이 "LOGOUT" 텍스트를 파싱하면 "Expecting value: line 1 column 1" 발생
        if any(kw in err_str for kw in ("LOGOUT", "Expecting value", "column 1", "400 Client", "JSONDecode")):
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


@router.get("/foreign-top-stocks")
async def get_foreign_top_stocks(
    market: str = Query("KOSPI", description="KOSPI 또는 KOSDAQ"),
    date: Optional[str] = Query(None, description="YYYYMMDD, 기본값 오늘"),
    limit: int = Query(30, description="순매수/순매도 상위 종목 수"),
):
    """외국인 순매수·순매도 상위 종목 (KRX MDCSTAT02401, invstTpCd=9000)"""
    try:
        from pykrx.website.krx.market.core import 투자자별_순매수상위종목
    except ImportError:
        return JSONResponse(content={"error": "pykrx 미설치"}, status_code=500)

    today = datetime.date.today()
    if not date:
        date = today.strftime("%Y%m%d")

    market_upper = market.upper()
    if market_upper not in ("KOSPI", "KOSDAQ"):
        return JSONResponse(content={"error": "market must be KOSPI or KOSDAQ"}, status_code=400)

    mkt_id = "STK" if market_upper == "KOSPI" else "KSQ"

    try:
        core = 투자자별_순매수상위종목()
        df = core.fetch(date, date, mkt_id, "9000")  # 9000 = 외국인
    except Exception as e:
        err_str = str(e)
        logger.error(f"foreign-top-stocks error ({market_upper} {date}): {err_str}")
        if any(kw in err_str for kw in ("LOGOUT", "Expecting value", "column 1", "400 Client")):
            return JSONResponse(content={"error": "KRX 인증 필요"}, status_code=503)
        return JSONResponse(content={"error": f"데이터 조회 실패: {err_str}"}, status_code=500)

    if df is None or df.empty:
        return JSONResponse(content={"market": market_upper, "date": date, "topBuy": [], "topSell": []})

    if df is not None and not df.empty:
        logger.info(f"foreign-top-stocks columns: {list(df.columns)}")

    df = df.fillna(0)
    df["_netval"] = df["NETBID_TRDVAL"].apply(_safe_int)
    df = df.sort_values("_netval", ascending=False)

    def to_stock(row: dict) -> dict:
        return {
            "code":      str(row.get("ISU_SRT_CD", "")),
            "name":      str(row.get("ISU_NM", "")),
            "netBuyVol": _safe_int(row.get("NETBID_TRDVOL", 0)),
            "netBuyVal": _safe_int(row.get("NETBID_TRDVAL", 0)),
            "buyVal":    _safe_int(row.get("BID_TRDVAL", 0)),
            "sellVal":   _safe_int(row.get("ASK_TRDVAL", 0)),
        }

    records = df.to_dict("records")
    top_buy  = [to_stock(r) for r in records[:limit] if _safe_int(r.get("NETBID_TRDVAL", 0)) > 0]
    top_sell = [to_stock(r) for r in reversed(records[-limit:]) if _safe_int(r.get("NETBID_TRDVAL", 0)) < 0]

    return JSONResponse(content=_sanitize({
        "market": market_upper,
        "date": date,
        "topBuy": top_buy,
        "topSell": top_sell,
    }))


# ── 지지선·저항선 + 가격 전망 ────────────────────────────────────────────────

def _find_support_resistance(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    current_price: float, window: int = 3
) -> dict:
    """로컬 극값 탐지 → 클러스터링 → 지지/저항 레벨 반환."""
    n = len(closes)
    if n < window * 2 + 2:
        return {"support": [], "resistance": []}

    resistance_raw, support_raw = [], []
    for i in range(window, n - window):
        hi = float(highs.iloc[i])
        lo = float(lows.iloc[i])
        if all(hi >= float(highs.iloc[i - j]) for j in range(1, window + 1)) and \
           all(hi >= float(highs.iloc[i + j]) for j in range(1, window + 1)):
            resistance_raw.append(hi)
        if all(lo <= float(lows.iloc[i - j]) for j in range(1, window + 1)) and \
           all(lo <= float(lows.iloc[i + j]) for j in range(1, window + 1)):
            support_raw.append(lo)

    def cluster(levels: list, pct: float = 0.015) -> list:
        if not levels:
            return []
        groups: list[list] = [[sorted(levels)[0]]]
        for lv in sorted(levels)[1:]:
            if (lv - groups[-1][-1]) / max(groups[-1][-1], 1e-9) <= pct:
                groups[-1].append(lv)
            else:
                groups.append([lv])
        return [round(sum(g) / len(g), 4) for g in groups]

    all_r = cluster(resistance_raw)
    all_s = cluster(support_raw)

    resistance = sorted([r for r in all_r if current_price < r <= current_price * 1.30])[:3]
    support    = sorted([s for s in all_s if current_price * 0.70 <= s < current_price], reverse=True)[:3]
    return {"support": support, "resistance": resistance}


def _calc_price_targets(
    closes: pd.Series, highs: pd.Series, lows: pd.Series, current_price: float
) -> dict:
    """ATR 기반 단기·중기 가격 범위 + 선형 추세 반영."""
    if len(closes) < 15:
        return {}

    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.ewm(com=13, adjust=False).mean().iloc[-1])

    trend_1w = trend_1m = 0.0
    if len(closes) >= 20:
        x = np.arange(20, dtype=float)
        slope = float(np.polyfit(x, closes.tail(20).values.astype(float), 1)[0])
        trend_1w = slope * 5    # 5 거래일
        trend_1m = slope * 20   # 20 거래일

    center_1w = round(current_price + trend_1w, 4)
    center_1m = round(current_price + trend_1m, 4)
    band_1w   = round(atr, 4)
    band_1m   = round(atr * (20 ** 0.5), 4)

    return {
        "week1": {
            "low":    round(center_1w - band_1w, 4),
            "center": center_1w,
            "high":   round(center_1w + band_1w, 4),
        },
        "month1": {
            "low":    round(center_1m - band_1m, 4),
            "center": center_1m,
            "high":   round(center_1m + band_1m, 4),
        },
    }


@router.get("/technical-levels")
async def get_technical_levels(
    symbol: str = Query(..., description="종목 심볼"),
    market: str = Query("US", description="US 또는 KR"),
    days: int = Query(90, description="조회 기간(일)"),
    db: AsyncSession = Depends(get_db),
):
    """지지선·저항선 + 이동평균 + 가격 전망 범위 반환."""
    market_upper = market.upper()
    symbol_upper = symbol.upper()

    stock_result = await db.execute(
        select(Stock)
        .join(Market)
        .where(Stock.symbol == symbol_upper, Market.code == market_upper, Stock.is_active == True)
    )
    stock = stock_result.scalar_one_or_none()
    if not stock:
        return JSONResponse(content={"error": "Stock not found"}, status_code=404)

    since = datetime.date.today() - datetime.timedelta(days=days)
    price_result = await db.execute(
        select(PriceDaily)
        .where(PriceDaily.stock_id == stock.id, PriceDaily.date >= since)
        .order_by(PriceDaily.date)
    )
    prices = price_result.scalars().all()

    if len(prices) < 20:
        return JSONResponse(content={"error": "Insufficient price data"}, status_code=422)

    closes = pd.Series([float(p.close) for p in prices])
    highs  = pd.Series([float(p.high)  for p in prices])
    lows   = pd.Series([float(p.low)   for p in prices])
    current_price = float(closes.iloc[-1])

    ma20 = round(float(closes.tail(20).mean()), 4) if len(closes) >= 20 else None
    ma60 = round(float(closes.tail(60).mean()), 4) if len(closes) >= 60 else None

    sr      = _find_support_resistance(highs, lows, closes, current_price)
    targets = _calc_price_targets(closes, highs, lows, current_price)

    # US 종목: yfinance 애널리스트 컨센서스 목표가
    analyst_target = None
    if market_upper == "US":
        try:
            import yfinance as yf
            info = yf.Ticker(symbol_upper).fast_info
            # fast_info doesn't have targetMeanPrice; use .info but with timeout
            ticker_info = yf.Ticker(symbol_upper).info
            val = ticker_info.get("targetMeanPrice")
            if val is not None:
                analyst_target = round(float(val), 2)
        except Exception as e:
            logger.debug(f"yfinance analyst target failed for {symbol_upper}: {e}")

    return JSONResponse(content=_sanitize({
        "symbol": symbol_upper,
        "market": market_upper,
        "currentPrice": current_price,
        "ma20": ma20,
        "ma60": ma60,
        "support": sr["support"],
        "resistance": sr["resistance"],
        "priceTargets": targets,
        "analystTarget": analyst_target,
    }))
