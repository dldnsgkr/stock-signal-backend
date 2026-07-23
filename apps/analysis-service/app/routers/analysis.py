import logging
import math
import time
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

    # 같은 종목의 미결제 BUY 가 여러 건이면 feature 계산을 반복하지 않는다.
    # US 는 90일 미결제 BUY 가 수만 건(유니크 종목의 수 배)이라, 건별 계산 시
    # 10분 넘게 걸려 워커 timeout 으로 SELL 체크가 매일 실패했다.
    stock_result_cache: dict[int, dict | None] = {}

    for rec in body.buy_recommendations:
        if rec.stock_id in stock_result_cache:
            cached = stock_result_cache[rec.stock_id]
        else:
            cached = None
            stock = await db.get(Stock, rec.stock_id)
            if stock:
                features = await build_features(db, stock, market_code=body.market)
                if features:
                    score_detail = calculate_total_score(features)
                    current_score = score_detail["total_score"]
                    if current_score < WATCH_THRESHOLD:
                        cached = {
                            "current_score": current_score,
                            "exit_price": features["current_price"],
                            "reasons": generate_reasons(features, score_detail, "SELL"),
                        }
                    else:
                        cached = {"current_score": current_score}  # SELL 아님 표식
            stock_result_cache[rec.stock_id] = cached

        if cached and "exit_price" in cached:
            sell_signals.append({
                "buy_recommendation_id": rec.id,
                "stock_id": rec.stock_id,
                **cached,
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


def _get_price_map(date: str, market: str) -> dict:
    """주어진 날짜 전체 시장 종가·등락률 딕셔너리 반환. 실패 시 빈 딕셔너리."""
    try:
        from pykrx import stock as pkrx_stock
        df = pkrx_stock.get_market_ohlcv(date, market=market)
        if df is None or df.empty:
            return {}
        result = {}
        for code, row in df.iterrows():
            try:
                close_val = row.get("종가", 0) or 0
                rate_val  = row.get("등락률", 0) or 0
                result[str(code)] = {
                    "currentPrice": int(float(close_val)),
                    "changeRate":   round(float(rate_val), 2),
                }
            except (TypeError, ValueError):
                pass
        return result
    except Exception as e:
        logger.warning(f"_get_price_map 실패 ({date} {market}): {e}")
        return {}


# MDCSTAT02202 (투자자별 거래실적 일별추이 일반, inqTpCd=2) TRDVAL 컬럼 → 영문 키 매핑
# pykrx source 확인: TRDVAL1=기관합계, TRDVAL2=외국인, TRDVAL3=개인, TRDVAL4=기타법인+기타
_KRX_COL_MAP = [
    ("TRDVAL1",    "institution"),  # 기관합계
    ("TRDVAL2",    "foreign"),      # 외국인
    ("TRDVAL3",    "individual"),   # 개인
    ("TRDVAL4",    "otherCorp"),    # 기타법인+기타
    ("TRDVAL_TOT", "total"),        # 전체합계
]


# KRX 조회는 10~20초가 걸리는데 페이지를 열 때마다 매번 호출한다.
# 확정된 과거 거래일 수치는 바뀌지 않으므로 짧게 캐시한다.
_KRX_CACHE: dict[tuple, tuple[float, list]] = {}
_KRX_CACHE_TTL_SEC = 600
_KRX_CACHE_MAX = 64


def _krx_cache_get(key: tuple) -> Optional[list]:
    hit = _KRX_CACHE.get(key)
    if not hit:
        return None
    stored_at, value = hit
    if time.time() - stored_at > _KRX_CACHE_TTL_SEC:
        _KRX_CACHE.pop(key, None)
        return None
    return value


def _krx_cache_put(key: tuple, value: list) -> None:
    # 오래된 항목부터 정리 (조회 조합이 많지 않아 단순 방식으로 충분)
    if len(_KRX_CACHE) >= _KRX_CACHE_MAX:
        oldest = min(_KRX_CACHE, key=lambda k: _KRX_CACHE[k][0])
        _KRX_CACHE.pop(oldest, None)
    _KRX_CACHE[key] = (time.time(), value)


def _krx_fetch_investor_daily(mkt_id: str, strt_dd: str, end_dd: str) -> list:
    """pykrx 내부 클래스 직접 호출 — 깨진 wrapper 우회 + pykrx 인증 세션 재사용.
    KRX 2026년 이후 인증 필요: EC2 환경변수 KRX_ID, KRX_PW 설정 시 자동 로그인.
    """
    cache_key = ("investor_daily", mkt_id, strt_dd, end_dd)
    cached = _krx_cache_get(cache_key)
    if cached is not None:
        logger.info(f"KRX cache hit: {cache_key}")
        return cached

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

    records = df.to_dict("records")
    _krx_cache_put(cache_key, records)
    return records


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

    price_map = _get_price_map(date, market_upper)

    def to_stock(row: dict) -> dict:
        code = str(row.get("ISU_SRT_CD", ""))
        price_info = price_map.get(code, {})
        return {
            "code":         code,
            "name":         str(row.get("ISU_NM", "")),
            "netBuyVol":    _safe_int(row.get("NETBID_TRDVOL", 0)),
            "netBuyVal":    _safe_int(row.get("NETBID_TRDVAL", 0)),
            "buyVal":       _safe_int(row.get("BID_TRDVAL", 0)),
            "sellVal":      _safe_int(row.get("ASK_TRDVAL", 0)),
            "currentPrice": price_info.get("currentPrice"),
            "changeRate":   price_info.get("changeRate"),
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


_INVESTOR_TYPE_MAP = {
    "institution": "8000",
    "foreign":     "9000",
    "individual":  "5000",
}


@router.get("/investor-top-stocks")
async def get_investor_top_stocks(
    market: str = Query("KOSPI", description="KOSPI 또는 KOSDAQ"),
    date: Optional[str] = Query(None, description="YYYYMMDD, 기본값 오늘"),
    investor_type: str = Query("institution", description="institution | foreign | individual"),
    limit: int = Query(20, description="상위 종목 수"),
):
    """투자자 유형별(기관/외국인/개인) 순매수·순매도 상위 종목 + 현재가·등락률"""
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

    mkt_id   = "STK" if market_upper == "KOSPI" else "KSQ"
    invst_tp = _INVESTOR_TYPE_MAP.get(investor_type, "8000")

    try:
        core = 투자자별_순매수상위종목()
        df   = core.fetch(date, date, mkt_id, invst_tp)
    except Exception as e:
        err_str = str(e)
        logger.error(f"investor-top-stocks error ({market_upper} {date} {investor_type}): {err_str}")
        if any(kw in err_str for kw in ("LOGOUT", "Expecting value", "column 1", "400 Client")):
            return JSONResponse(content={"error": "KRX 인증 필요"}, status_code=503)
        return JSONResponse(content={"error": f"데이터 조회 실패: {err_str}"}, status_code=500)

    if df is None or df.empty:
        return JSONResponse(content={
            "market": market_upper, "date": date,
            "investorType": investor_type, "topBuy": [], "topSell": [],
        })

    df = df.fillna(0)
    df["_netval"] = df["NETBID_TRDVAL"].apply(_safe_int)
    df = df.sort_values("_netval", ascending=False)

    price_map = _get_price_map(date, market_upper)

    def to_stock(row: dict) -> dict:
        code = str(row.get("ISU_SRT_CD", ""))
        price_info = price_map.get(code, {})
        return {
            "code":         code,
            "name":         str(row.get("ISU_NM", "")),
            "netBuyVol":    _safe_int(row.get("NETBID_TRDVOL", 0)),
            "netBuyVal":    _safe_int(row.get("NETBID_TRDVAL", 0)),
            "buyVal":       _safe_int(row.get("BID_TRDVAL", 0)),
            "sellVal":      _safe_int(row.get("ASK_TRDVAL", 0)),
            "currentPrice": price_info.get("currentPrice"),
            "changeRate":   price_info.get("changeRate"),
        }

    records  = df.to_dict("records")
    top_buy  = [to_stock(r) for r in records[:limit]           if _safe_int(r.get("NETBID_TRDVAL", 0)) > 0]
    top_sell = [to_stock(r) for r in reversed(records[-limit:]) if _safe_int(r.get("NETBID_TRDVAL", 0)) < 0]

    return JSONResponse(content=_sanitize({
        "market":       market_upper,
        "date":         date,
        "investorType": investor_type,
        "topBuy":       top_buy,
        "topSell":      top_sell,
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


# ── Forward P/E 목표가 + 시나리오 ────────────────────────────────────────────

# 2024-2025 US 섹터별 기준 P/E (S&P500 구성 섹터 중앙값)
_SECTOR_PE: dict[str, float] = {
    "Technology":              28.0,
    "Information Technology":  28.0,
    "Communication Services":  22.0,
    "Healthcare":              22.0,
    "Consumer Discretionary":  24.0,
    "Consumer Staples":        20.0,
    "Financials":              14.0,
    "Financial Services":      14.0,
    "Energy":                  12.0,
    "Industrials":             20.0,
    "Basic Materials":         17.0,
    "Materials":               17.0,
    "Real Estate":             35.0,
    "Utilities":               18.0,
}
_DEFAULT_PE = 20.0  # S&P500 장기 평균


def _fetch_yfinance_fundamentals(symbol: str) -> dict:
    """yfinance에서 펀더멘털 + 애널리스트 데이터 수집 (US 전용)."""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        def _n(key): return info.get(key)
        return {
            "forwardEps":              _n("forwardEps"),
            "trailingEps":             _n("trailingEps"),
            "trailingPE":              _n("trailingPE"),
            "sector":                  _n("sector") or "",
            "targetMeanPrice":         _n("targetMeanPrice"),
            "targetHighPrice":         _n("targetHighPrice"),
            "targetLowPrice":          _n("targetLowPrice"),
            "numberOfAnalystOpinions": _n("numberOfAnalystOpinions"),
            "earningsGrowth":          _n("earningsGrowth"),
        }
    except Exception as e:
        logger.debug(f"yfinance fundamentals failed for {symbol}: {e}")
        return {}


def _calc_forward_pe_target(fund: dict, current_price: float) -> dict | None:
    """Forward P/E 기반 1년 목표가 산출."""
    forward_eps = fund.get("forwardEps")
    if not forward_eps or forward_eps <= 0:
        return None

    trailing_eps = fund.get("trailingEps")
    trailing_pe  = fund.get("trailingPE")
    sector       = fund.get("sector", "")

    sector_pe = _SECTOR_PE.get(sector, _DEFAULT_PE)

    # 적정 P/E: trailing P/E(40%)와 섹터 기준 P/E(60%) 블렌드
    # 단, trailing P/E가 과열(섹터의 2배 초과) 또는 음수면 섹터 기준만 사용
    if trailing_pe and 5 < trailing_pe < sector_pe * 2.0:
        fair_pe = round(trailing_pe * 0.4 + sector_pe * 0.6, 1)
    else:
        fair_pe = sector_pe

    target = round(float(forward_eps) * fair_pe, 2)

    eps_growth = None
    if trailing_eps and trailing_eps > 0 and forward_eps:
        eps_growth = round((float(forward_eps) - float(trailing_eps)) / float(trailing_eps), 4)

    upside = round((target - current_price) / current_price, 4) if current_price > 0 else None

    return {
        "target":     target,
        "upside":     upside,
        "forwardEps": round(float(forward_eps), 4),
        "fairPE":     fair_pe,
        "sectorPE":   sector_pe,
        "sector":     sector,
        "epsGrowth":  eps_growth,
    }


def _calc_scenarios(
    fund: dict,
    pe_result: dict | None,
    current_price: float,
) -> dict:
    """Bull / Base / Bear 시나리오 산출."""
    target_mean  = fund.get("targetMeanPrice")
    target_high  = fund.get("targetHighPrice")
    target_low   = fund.get("targetLowPrice")
    analyst_cnt  = fund.get("numberOfAnalystOpinions")
    pe_target    = pe_result["target"] if pe_result else None

    # Base: Forward P/E와 애널리스트 컨센서스 평균 (둘 다 없으면 None)
    if pe_target and target_mean:
        base  = round((pe_target + float(target_mean)) / 2, 2)
        source = "pe+analyst"
    elif pe_target:
        base  = pe_target
        source = "pe"
    elif target_mean:
        base  = round(float(target_mean), 2)
        source = "analyst"
    else:
        return {}

    # Bull: 애널리스트 최고 목표가 or base × 1.20
    bull = round(float(target_high), 2) if target_high else round(base * 1.20, 2)
    # Bear: 애널리스트 최저 목표가 or base × 0.80
    bear = round(float(target_low),  2) if target_low  else round(base * 0.80, 2)

    def pct(price: float) -> float | None:
        return round((price - current_price) / current_price, 4) if current_price > 0 else None

    return {
        "bull":          {"price": bull, "upside": pct(bull)},
        "base":          {"price": base, "upside": pct(base)},
        "bear":          {"price": bear, "upside": pct(bear)},
        "analystCount":  analyst_cnt,
        "source":        source,
    }


@router.get("/technical-levels")
async def get_technical_levels(
    symbol: str = Query(..., description="종목 심볼"),
    market: str = Query("US", description="US 또는 KR"),
    days: int = Query(90, description="조회 기간(일)"),
    db: AsyncSession = Depends(get_db),
):
    """지지선·저항선 + 이동평균 + 단기 가격 범위 + 1년 Forward P/E 전망."""
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

    # US 전용: Forward P/E 목표가 + 시나리오
    forward_pe  = None
    scenarios   = None
    if market_upper == "US":
        fund        = _fetch_yfinance_fundamentals(symbol_upper)
        forward_pe  = _calc_forward_pe_target(fund, current_price)
        scenarios   = _calc_scenarios(fund, forward_pe, current_price) or None

    return JSONResponse(content=_sanitize({
        "symbol":       symbol_upper,
        "market":       market_upper,
        "currentPrice": current_price,
        "ma20":         ma20,
        "ma60":         ma60,
        "support":      sr["support"],
        "resistance":   sr["resistance"],
        "priceTargets": targets,
        "forwardPE":    forward_pe,
        "scenarios":    scenarios,
    }))
