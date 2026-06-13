import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.models.db_models import Stock, PriceDaily, FinancialMetrics, NewsArticle, NewsStockRelation, MacroIndicator

logger = logging.getLogger(__name__)


def _f(value, default=None):
    if value is None:
        return default
    try:
        f = float(value)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return _f(rsi.iloc[-1])


def _macd(closes: pd.Series) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < 26:
        return None, None, None
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return _f(macd_line.iloc[-1]), _f(signal_line.iloc[-1]), _f(histogram.iloc[-1])


def _bollinger(closes: pd.Series, period: int = 20) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < period:
        return None, None, None
    tail = closes.tail(period)
    ma = float(tail.mean())
    std = float(tail.std())
    if std == 0:
        return None, None, None
    return _f(ma + 2 * std), _f(ma), _f(ma - 2 * std)


def _atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> Optional[float]:
    """Average True Range — 변동성 측정."""
    if len(closes) < period + 1:
        return None
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(com=period - 1, adjust=False).mean()
    last_close = closes.iloc[-1]
    if last_close and last_close > 0:
        return _f(atr.iloc[-1] / last_close)  # ATR를 가격으로 정규화
    return _f(atr.iloc[-1])


def _obv_trend(closes: pd.Series, volumes: pd.Series, period: int = 20) -> Optional[float]:
    """OBV(On-Balance Volume) 추세 — 양수면 매집, 음수면 분산."""
    if len(closes) < period + 1:
        return None
    direction = closes.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (direction * volumes).cumsum()
    obv_tail = obv.tail(period)
    if len(obv_tail) < 2:
        return None
    # 선형 기울기 정규화
    x = np.arange(len(obv_tail))
    slope = np.polyfit(x, obv_tail.values, 1)[0]
    avg_vol = volumes.tail(period).mean()
    if avg_vol and avg_vol > 0:
        return _f(slope / avg_vol)
    return None


def _build_news_features(news_rows: list) -> dict:
    """
    최신성 감쇠 + 관련도 가중 감성 점수 계산.
    - 반감기 7일 지수 감쇠
    - relevance_score 가중치 적용
    - 감성 모멘텀: 최근 7일 vs 이전 7~14일 추세
    """
    now = datetime.utcnow()
    HALF_LIFE_DAYS = 7.0
    DECAY_LAMBDA = np.log(2) / HALF_LIFE_DAYS

    weighted_scores = []
    recent_scores = []   # 최근 7일
    older_scores = []    # 7~14일 전

    for row in news_rows:
        score = _f(row.sentiment_score)
        relevance = _f(row.relevance_score, 0.5)
        published_at = row.published_at

        if score is None or published_at is None:
            continue

        days_ago = max(0.0, (now - published_at).total_seconds() / 86400)
        decay = np.exp(-DECAY_LAMBDA * days_ago)
        weight = decay * max(0.1, relevance)

        weighted_scores.append((score, weight))

        if days_ago <= 7:
            recent_scores.append(score)
        elif days_ago <= 14:
            older_scores.append(score)

    if not weighted_scores:
        return {
            "sentiment_avg": 0.0,
            "sentiment_weighted": 0.0,
            "sentiment_momentum": 0.0,
            "negative_count": 0,
            "positive_count": 0,
            "news_frequency_spike": False,
            "news_count": 0,
        }

    total_weight = sum(w for _, w in weighted_scores)
    sentiment_weighted = sum(s * w for s, w in weighted_scores) / total_weight if total_weight > 0 else 0.0

    # 감성 모멘텀: 최근 7일 평균 - 이전 7일 평균
    sentiment_momentum = 0.0
    if recent_scores and older_scores:
        sentiment_momentum = float(np.mean(recent_scores)) - float(np.mean(older_scores))
    elif recent_scores:
        sentiment_momentum = float(np.mean(recent_scores))

    all_scores = [s for s, _ in weighted_scores]

    return {
        "sentiment_avg": float(np.mean(all_scores)),
        "sentiment_weighted": round(sentiment_weighted, 4),
        "sentiment_momentum": round(sentiment_momentum, 4),
        "negative_count": sum(1 for s in all_scores if s < -0.05),
        "positive_count": sum(1 for s in all_scores if s > 0.05),
        "news_frequency_spike": len(all_scores) > 10,
        "news_count": len(all_scores),
    }


async def build_features(db: AsyncSession, stock: Stock, market_code: str = "US") -> Optional[dict]:
    try:
        since = (datetime.utcnow() - timedelta(days=90)).date()

        price_result = await db.execute(
            select(PriceDaily)
            .where(PriceDaily.stock_id == stock.id, PriceDaily.date >= since)
            .order_by(PriceDaily.date)
        )
        prices = price_result.scalars().all()

        if len(prices) < 20:
            return None

        closes = pd.Series([float(p.close) for p in prices])
        volumes = pd.Series([float(p.volume) for p in prices])
        highs = pd.Series([float(p.high) for p in prices])
        lows = pd.Series([float(p.low) for p in prices])

        current_price = _f(closes.iloc[-1])
        if current_price is None or current_price <= 0:
            return None

        ma20 = _f(closes.tail(20).mean(), 0)
        ma60 = _f(closes.tail(60).mean() if len(closes) >= 60 else closes.mean(), 0)
        momentum_5d = _f((closes.iloc[-1] / closes.iloc[-6] - 1) if len(closes) >= 6 else 0, 0)
        momentum_20d = _f((closes.iloc[-1] / closes.iloc[-21] - 1) if len(closes) >= 21 else 0, 0)
        vol_avg_20d = _f(volumes.tail(20).mean(), 0)
        vol_latest_5d = _f(volumes.tail(5).mean(), 0)
        volume_growth_rate = _f((vol_latest_5d / vol_avg_20d - 1) if vol_avg_20d and vol_avg_20d > 0 else 0, 0)

        rsi = _rsi(closes)
        macd_line, macd_signal, macd_histogram = _macd(closes)
        bb_upper, bb_mid, bb_lower = _bollinger(closes)
        bb_position = None
        if bb_upper is not None and bb_lower is not None and (bb_upper - bb_lower) > 0:
            bb_position = _f((current_price - bb_lower) / (bb_upper - bb_lower))

        macd_histogram_prev = None
        if len(closes) >= 27:
            _, _, macd_histogram_prev = _macd(closes.iloc[:-1])

        atr_ratio = _atr(highs, lows, closes)
        obv_trend = _obv_trend(closes, volumes)

        technical = {
            "ma20_position": _f((current_price - ma20) / ma20 if ma20 and ma20 > 0 else 0, 0),
            "ma60_position": _f((current_price - ma60) / ma60 if ma60 and ma60 > 0 else 0, 0),
            "volume_growth_rate": volume_growth_rate,
            "momentum_5d": momentum_5d,
            "momentum_20d": momentum_20d,
            "rsi": rsi,
            "macd_histogram": macd_histogram,
            "macd_histogram_prev": macd_histogram_prev,
            "bb_position": bb_position,
            "atr_ratio": atr_ratio,        # 변동성 (낮을수록 안정)
            "obv_trend": obv_trend,        # 거래량 추세
        }

        fin_result = await db.execute(
            select(FinancialMetrics)
            .where(FinancialMetrics.stock_id == stock.id)
            .order_by(desc(FinancialMetrics.period_end))
            .limit(1)
        )
        fin = fin_result.scalar_one_or_none()

        fundamental = {
            "roe": _f(fin.roe) if fin else None,
            "operating_income_growth": None,
            "per_relative": _f(fin.per) if fin else None,
            "pbr_relative": _f(fin.pbr) if fin else None,
        }

        # 뉴스 — published_at과 relevance_score 포함해서 조회
        news_result = await db.execute(
            select(
                NewsArticle.sentiment_score,
                NewsArticle.published_at,
                NewsStockRelation.relevance_score,
            )
            .join(NewsArticle, NewsStockRelation.news_article_id == NewsArticle.id)
            .where(NewsStockRelation.stock_id == stock.id)
            .order_by(desc(NewsArticle.published_at))
            .limit(30)
        )
        news_rows = news_result.all()
        news = _build_news_features(news_rows)

        # 거시지표
        macro_result = await db.execute(
            select(MacroIndicator.indicator_type, MacroIndicator.value)
            .where(MacroIndicator.market_code == market_code)
            .order_by(desc(MacroIndicator.observed_at))
            .limit(20)
        )
        macro_rows = macro_result.all()
        macro_map: dict = {}
        for row in macro_rows:
            if row.indicator_type not in macro_map:
                macro_map[row.indicator_type] = _f(row.value)

        macro = {
            "vix": macro_map.get("VIX"),
            "interest_rate_sensitivity": 0.5,
            "fx_impact": 0.0,
        }

        flow = {
            "foreign_net_buy": None,
            "institutional_net_buy": None,
            "trading_value_growth": volume_growth_rate,
        }

        return {
            "technical": technical,
            "fundamental": fundamental,
            "news": news,
            "macro": macro,
            "flow": flow,
            "current_price": current_price,
        }

    except Exception as e:
        logger.error(f"Feature build failed for {stock.symbol}: {e}")
        return None
