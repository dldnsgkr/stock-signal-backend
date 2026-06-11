from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Numeric,
    BigInteger, Text, ForeignKey, UniqueConstraint, Date, JSON
)
from sqlalchemy.orm import relationship
from app.database import Base
import datetime


class Market(Base):
    __tablename__ = "markets"
    id = Column(Integer, primary_key=True)
    code = Column(String(10), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    stocks = relationship("Stock", back_populates="market")


class Stock(Base):
    __tablename__ = "stocks"
    id = Column(Integer, primary_key=True)
    market_id = Column(Integer, ForeignKey("markets.id"), nullable=False)
    symbol = Column(String(20), nullable=False)
    name = Column(String(200), nullable=False)
    sector = Column(String(100))
    industry = Column(String(100))
    exchange = Column(String(50))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    market = relationship("Market", back_populates="stocks")
    prices = relationship("PriceDaily", back_populates="stock")
    financials = relationship("FinancialMetrics", back_populates="stock")
    news_relations = relationship("NewsStockRelation", back_populates="stock")

    __table_args__ = (UniqueConstraint("market_id", "symbol"),)


class PriceDaily(Base):
    __tablename__ = "price_daily"
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Numeric(15, 4), nullable=False)
    high = Column(Numeric(15, 4), nullable=False)
    low = Column(Numeric(15, 4), nullable=False)
    close = Column(Numeric(15, 4), nullable=False)
    volume = Column(BigInteger, nullable=False)
    adj_close = Column(Numeric(15, 4))

    stock = relationship("Stock", back_populates="prices")

    __table_args__ = (UniqueConstraint("stock_id", "date"),)


class FinancialMetrics(Base):
    __tablename__ = "financial_metrics"
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    period_type = Column(String(20), nullable=False)
    period_end = Column(Date, nullable=False)
    revenue = Column(Numeric(20, 2))
    operating_income = Column(Numeric(20, 2))
    net_income = Column(Numeric(20, 2))
    roe = Column(Numeric(10, 4))
    per = Column(Numeric(10, 4))
    pbr = Column(Numeric(10, 4))
    debt_ratio = Column(Numeric(10, 4))

    stock = relationship("Stock", back_populates="financials")

    __table_args__ = (UniqueConstraint("stock_id", "period_type", "period_end"),)


class NewsArticle(Base):
    __tablename__ = "news_articles"
    id = Column(Integer, primary_key=True)
    source = Column(String(100), nullable=False)
    title = Column(String(500), nullable=False)
    summary = Column(Text)
    url = Column(String(1000), unique=True, nullable=False)
    published_at = Column(DateTime, nullable=False)
    sentiment_score = Column(Numeric(5, 4))
    language = Column(String(10), default="en")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    stock_relations = relationship("NewsStockRelation", back_populates="article")


class NewsStockRelation(Base):
    __tablename__ = "news_stock_relations"
    id = Column(Integer, primary_key=True)
    news_article_id = Column(Integer, ForeignKey("news_articles.id"), nullable=False)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    relevance_score = Column(Numeric(5, 4), nullable=False)

    article = relationship("NewsArticle", back_populates="stock_relations")
    stock = relationship("Stock", back_populates="news_relations")

    __table_args__ = (UniqueConstraint("news_article_id", "stock_id"),)


class MacroIndicator(Base):
    __tablename__ = "macro_indicators"
    id = Column(Integer, primary_key=True)
    market_code = Column(String(10), nullable=False)
    indicator_type = Column(String(100), nullable=False)
    value = Column(Numeric(15, 6), nullable=False)
    observed_at = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("market_code", "indicator_type", "observed_at"),)
