"""
통합 뉴스 수집기 — 4개 소스를 모든 종목에 적용
  1. yfinance        : Yahoo Finance 집계 (Reuters, WSJ, Barron's, Motley Fool 등)
  2. Google News EN  : Reuters, CNBC, Seeking Alpha, MarketBeat 등 영어 매체
  3. Google News KO  : 연합뉴스, 매일경제, 조선비즈, 한국경제 등 국내 매체
  4. (KR 전용) Google News EN + KR name: 삼성전자 등 해외 영어 커버리지
URL 기준 중복 제거, 언어별 감성 분석 적용
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta

import feedparser
import requests
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from app.models.db_models import Market, NewsArticle, NewsStockRelation, Stock

logger = logging.getLogger(__name__)
vader = SentimentIntensityAnalyzer()

# ── 한국어 금융 감성 키워드 ──────────────────────────────────────
_POS_KO = {
    "상승", "급등", "강세", "호실적", "흑자", "최고가", "신고가", "급증", "성장",
    "개선", "회복", "돌파", "수익", "증가", "확대", "호조", "매수", "긍정",
    "어닝서프라이즈", "실적개선", "주가상승", "반등", "상향", "배당", "호재",
    "영업이익", "매출증가", "수주", "수출증가", "흑자전환",
}
_NEG_KO = {
    "하락", "급락", "약세", "부진", "적자", "최저가", "신저가", "감소", "둔화",
    "악화", "급감", "손실", "우려", "리스크", "매도", "부정", "경고", "침체",
    "어닝쇼크", "실적부진", "주가하락", "조정", "하향", "악재", "경영난", "파산",
    "적자전환", "매출감소", "소송", "제재", "규제",
}


def _ko_sentiment(text: str) -> float:
    pos = sum(1 for w in _POS_KO if w in text)
    neg = sum(1 for w in _NEG_KO if w in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 4)


def _en_sentiment(text: str) -> float:
    return round(vader.polarity_scores(text)["compound"], 4)


def _sentiment(text: str, language: str) -> float:
    return _ko_sentiment(text) if language == "ko" else _en_sentiment(text)


# ── Google News RSS ──────────────────────────────────────────────
def _fetch_google_news(query: str, lang: str, country: str, max_items: int = 5) -> list[dict]:
    url = (
        f"https://news.google.com/rss/search"
        f"?q={requests.utils.quote(query)}&hl={lang}&gl={country}&ceid={country}:{lang}"
    )
    try:
        feed = feedparser.parse(url)
        cutoff = datetime.utcnow() - timedelta(days=30)
        items = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            pub = entry.get("published_parsed")
            published_at = datetime(*pub[:6]) if pub else datetime.utcnow()
            if published_at < cutoff:
                continue
            source = entry.get("source", {}).get("title", "Google News")
            summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or "").strip()
            items.append({
                "title": title,
                "url": link,
                "published_at": published_at,
                "source": source,
                "summary": summary or None,
            })
        return items
    except Exception as e:
        logger.debug(f"Google News RSS error [{lang}] '{query}': {e}")
        return []


# ── yfinance ────────────────────────────────────────────────────
def _parse_yfinance_item(item: dict) -> dict | None:
    try:
        content = item.get("content", {})
        if not content:
            return None
        title = content.get("title", "").strip()
        if not title:
            return None
        canonical = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        url = canonical.get("url", "")
        if not url:
            return None
        pub_str = content.get("pubDate") or content.get("displayTime")
        try:
            published_at = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            published_at = datetime.utcnow()
        if published_at < datetime.utcnow() - timedelta(days=30):
            return None
        source = content.get("provider", {}).get("displayName", "Yahoo Finance")
        summary = content.get("summary") or content.get("description") or ""
        return {
            "title": title,
            "url": url,
            "published_at": published_at,
            "source": source,
            "summary": summary or None,
            "language": "en",
        }
    except Exception:
        return None


def _fetch_yfinance_news(symbol: str, limit: int) -> list[dict]:
    try:
        news_raw = yf.Ticker(symbol).news or []
        result = []
        for item in news_raw[:limit]:
            parsed = _parse_yfinance_item(item)
            if parsed:
                result.append(parsed)
        return result
    except Exception as e:
        logger.debug(f"yfinance news error [{symbol}]: {e}")
        return []


# ── DB 저장 ─────────────────────────────────────────────────────
async def _save_news(db: AsyncSession, stock: Stock, items: list[dict]) -> int:
    saved = 0
    for parsed in items:
        try:
            language = parsed.get("language", "en")
            text = parsed["title"] + " " + (parsed["summary"] or "")
            score = _sentiment(text, language)

            existing = await db.execute(
                select(NewsArticle).where(NewsArticle.url == parsed["url"])
            )
            article = existing.scalar_one_or_none()
            if not article:
                article = NewsArticle(
                    source=parsed["source"],
                    title=parsed["title"],
                    summary=parsed["summary"],
                    url=parsed["url"],
                    published_at=parsed["published_at"],
                    sentiment_score=score,
                    language=language,
                )
                db.add(article)
                await db.flush()
                saved += 1

            existing_rel = await db.execute(
                select(NewsStockRelation).where(
                    NewsStockRelation.news_article_id == article.id,
                    NewsStockRelation.stock_id == stock.id,
                )
            )
            if not existing_rel.scalar_one_or_none():
                db.add(NewsStockRelation(
                    news_article_id=article.id,
                    stock_id=stock.id,
                    relevance_score=0.9,
                ))
        except Exception as e:
            logger.debug(f"Error saving news: {e}")
    return saved


# ── 메인 수집 함수 ───────────────────────────────────────────────
async def collect_news(
    db: AsyncSession,
    market_code: str = "US",
    offset: int = 0,
    limit: int = 200,
    limit_per_stock: int = 10,
) -> dict:
    result = await db.execute(
        select(Stock)
        .join(Market)
        .where(Market.code == market_code, Stock.is_active == True)
        .offset(offset)
        .limit(limit)
    )
    stocks = result.scalars().all()

    total_collected = 0
    errors = 0

    for stock in stocks:
        try:
            seen_urls: set[str] = set()
            all_items: list[dict] = []

            def add_items(items: list[dict], language: str):
                for item in items:
                    if item["url"] not in seen_urls:
                        item["language"] = language
                        seen_urls.add(item["url"])
                        all_items.append(item)

            per_source = max(3, limit_per_stock // 3)
            symbol_clean = stock.symbol.replace(".KS", "").replace(".KQ", "")

            # ── 소스 1: yfinance ──────────────────────────────
            add_items(_fetch_yfinance_news(stock.symbol, per_source), "en")

            # ── 소스 2: Google News 영어 ─────────────────────
            if market_code == "US":
                en_query = f"{stock.name} stock"
            else:
                # KR 종목: 종목명(한국어) + stock — 대형주는 구글이 인식
                en_query = f"{stock.name} stock"
            add_items(_fetch_google_news(en_query, "en", "US", per_source), "en")
            await asyncio.sleep(0.3)

            # ── 소스 3: Google News 한국어 ───────────────────
            if market_code == "KR":
                ko_query = f"{stock.name} 주가"
            else:
                # US 종목: 티커 심볼로 국내 검색 (AAPL 주가, TSLA 주가 등)
                ko_query = f"{symbol_clean} 주가"
            add_items(_fetch_google_news(ko_query, "ko", "KR", per_source), "ko")
            await asyncio.sleep(0.3)

            # ── 소스 4: KR 종목 추가 영어 검색 (심볼 코드) ──
            if market_code == "KR":
                add_items(
                    _fetch_google_news(f"{symbol_clean} Korea stock", "en", "US", per_source),
                    "en",
                )
                await asyncio.sleep(0.3)

            collected = await _save_news(db, stock, all_items[:limit_per_stock])
            total_collected += collected

        except Exception as e:
            logger.error(f"Error collecting news for {stock.symbol}: {e}")
            errors += 1

    await db.commit()
    logger.info(
        f"News collection [{market_code}] offset={offset} limit={limit}: "
        f"{total_collected} articles saved, {errors} errors"
    )
    return {"collected": total_collected, "errors": errors, "total_in_batch": len(stocks)}
