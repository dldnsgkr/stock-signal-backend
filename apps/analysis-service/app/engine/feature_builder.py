import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from app.models.db_models import Stock, PriceDaily, FinancialMetrics, NewsArticle, NewsStockRelation, MacroIndicator

logger = logging.getLogger(__name__)


def _f(value, default=None):
    """NaN/inf/numpy float → Python float. JSON-safe."""
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

        # prev histogram for crossover detection
        macd_histogram_prev = None
        if len(closes) >= 27:
            _, _, macd_histogram_prev = _macd(closes.iloc[:-1])

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

        # 뉴스 감성 — article을 명시적으로 join해서 lazy load 방지
        news_result = await db.execute(
            select(NewsStockRelation.id, NewsArticle.sentiment_score)
            .join(NewsArticle, NewsStockRelation.news_article_id == NewsArticle.id)
            .where(NewsStockRelation.stock_id == stock.id)
            .order_by(desc(NewsArticle.published_at))
            .limit(20)
        )
        news_rows = news_result.all()

        sentiment_scores = [
            float(row.sentiment_score)
            for row in news_rows
            if row.sentiment_score is not None
        ]

        news = {
            "sentiment_avg": float(np.mean(sentiment_scores)) if sentiment_scores else 0.0,
            "negative_count": sum(1 for s in sentiment_scores if s < -0.05),
            "positive_count": sum(1 for s in sentiment_scores if s > 0.05),
            "news_frequency_spike": len(sentiment_scores) > 10,
        }

        # 거시지표 — 최신값 조회
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
