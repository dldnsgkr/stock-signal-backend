import logging
from datetime import datetime, date, timedelta
import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.db_models import Stock, Market, FinancialMetrics, QuarterlyFinancials
from app.collectors.financial_math import (
    PBR_MAX, PER_MAX, derive_ratio, extract_equity, safe_amount, safe_float,
)

logger = logging.getLogger(__name__)


async def collect_financials(
    db: AsyncSession,
    market_code: str = "US",
    offset: int = 0,
    limit: int = 200,
) -> dict:
    result = await db.execute(
        select(Stock)
        .join(Market)
        .where(Market.code == market_code, Stock.is_active == True)
        .offset(offset)
        .limit(limit)
    )
    stocks = result.scalars().all()

    # 종목마다 쿼리하면 배치당 수백 번이 되므로 한 번에 읽는다.
    stale_before = date.today() - timedelta(days=QUARTERLY_REFRESH_DAYS)
    latest_q: dict[int, date] = {}
    if stocks:
        q_rows = await db.execute(
            select(QuarterlyFinancials.stock_id, func.max(QuarterlyFinancials.period_end))
            .where(QuarterlyFinancials.stock_id.in_([s.id for s in stocks]))
            .group_by(QuarterlyFinancials.stock_id)
        )
        latest_q = {sid: pe for sid, pe in q_rows.all()}

    collected = 0
    quarters_saved = 0
    skipped = 0
    errors = 0

    for stock in stocks:
        try:
            ticker = yf.Ticker(stock.symbol)
            info = ticker.info or {}

            # 섹터·업종은 종목 목록 수집기(SEC EDGAR / FinanceDataReader)가 주지 않아
            # `stocks.sector` 가 US 0.9% / KR 1.3% 만 채워져 있었다 — `/sectors` 섹터 분석과
            # 성과 리포트의 섹터별 집계가 사실상 1% 표본으로 돌고 있었다(2026-08-09 발견).
            # yfinance `info` 에는 들어 있고 여기서 이미 호출하므로 **추가 API 비용 없이** 채운다.
            # 이미 값이 있으면 덮어쓰지 않는다.
            if not stock.sector and info.get("sector"):
                stock.sector = str(info["sector"])[:100]
            if not stock.industry and info.get("industry"):
                stock.industry = str(info["industry"])[:100]

            # 분기 손익계산서 — 최신 분기가 오래됐을 때만 받는다(별도 API 호출이라 비싸다).
            # ⚠️ period_end 는 회계 분기 종료일이지 공시일이 아니다. 쓰는 쪽에서 공시 지연을 적용할 것.
            last_q = latest_q.get(stock.id)
            if last_q is None or last_q < stale_before:
                quarters_saved += await _upsert_quarterly(db, stock.id, ticker)

            roe = safe_float(info.get("returnOnEquity"))
            per = safe_float(info.get("trailingPE"))
            pbr = safe_float(info.get("priceToBook"))
            revenue = safe_amount(info.get("totalRevenue"))
            net_income = safe_amount(info.get("netIncomeToCommon"))
            operating_income = safe_amount(info.get("operatingIncome") or info.get("ebitda"))
            debt_ratio = safe_float(info.get("debtToEquity"))
            if debt_ratio is not None:
                debt_ratio = debt_ratio / 100

            # ── PER/PBR 폴백 (2026-08-03) ──
            # yfinance 는 `.KS` 티커에 trailingPE·priceToBook 을 주지 않는다(실측).
            # 그래서 KR 은 financial_metrics 5,011행 전체에 PER/PBR 이 0건이었고,
            # 가치 전략이 통째로 죽어 앙상블이 모멘텀 단일 모델로 붕괴했다
            # (KR 모멘텀 가중치 0.889, 모멘텀 100%로 채점되는 종목 40.7%).
            # 시총·순이익·자기자본은 정상 제공되므로 없을 때만 직접 계산해 채운다.
            #
            # 반사실 검증(scripts/counterfactual_value.py, KR 42런·채점 112,104건):
            # 임계값 65 기준 알파 PER+PBR +0.18~0.37%p, 적중률도 전 구성 양수.
            # BUY 신호는 약 25% 늘어난다.
            #
            # **있는 값은 절대 덮어쓰지 않는다.** US 는 trailingPE 가 없으면 대개
            # 적자라 이 폴백으로도 None 이 나와, 실제로 채워지는 건 5,584종목 중
            # 108개뿐이다(2026-08-03 측정). 즉 사실상 KR 전용이다.
            market_cap = safe_amount(info.get("marketCap"))

            if per is None:
                per = derive_ratio(market_cap, net_income, PER_MAX)
            if pbr is None and market_cap:
                # balance_sheet 는 info 와 별도 네트워크 호출이라, 결과를 쓸 수 있을
                # 때(시총이 있을 때)만 부른다. KR 배치 200종목 기준 약 +80초.
                pbr = derive_ratio(market_cap, _fetch_equity(ticker), PBR_MAX)

            # 데이터가 하나도 없으면 스킵
            if all(v is None for v in [roe, per, pbr, revenue, net_income]):
                logger.debug(f"{stock.symbol}: no financial data available")
                skipped += 1
                continue

            period_end = date.today().replace(day=1)
            period_type = "annual"

            existing = await db.execute(
                select(FinancialMetrics).where(
                    FinancialMetrics.stock_id == stock.id,
                    FinancialMetrics.period_type == period_type,
                    FinancialMetrics.period_end == period_end,
                )
            )
            fm = existing.scalar_one_or_none()

            if fm:
                fm.roe = roe
                fm.per = per
                fm.pbr = pbr
                fm.revenue = revenue
                fm.operating_income = operating_income
                fm.net_income = net_income
                fm.debt_ratio = debt_ratio
            else:
                fm = FinancialMetrics(
                    stock_id=stock.id,
                    period_type=period_type,
                    period_end=period_end,
                    roe=roe,
                    per=per,
                    pbr=pbr,
                    revenue=revenue,
                    operating_income=operating_income,
                    net_income=net_income,
                    debt_ratio=debt_ratio,
                )
                db.add(fm)

            await db.flush()
            collected += 1
            logger.info(f"{stock.symbol}: ROE={roe}, PER={per}, PBR={pbr}")

        except Exception as e:
            logger.error(f"Error collecting financials for {stock.symbol}: {e}")
            await db.rollback()
            errors += 1
            continue

    await db.commit()
    logger.info(f"Financial batch [offset={offset} limit={limit}]: {collected} collected, {skipped} skipped, {errors} errors")
    return {"collected": collected, "skipped": skipped, "errors": errors,
            "quarters_saved": quarters_saved, "total_in_batch": len(stocks)}


async def _upsert_quarterly(db: AsyncSession, stock_id: int, ticker) -> int:
    """분기 손익계산서를 quarterly_financials 에 upsert. 저장한 분기 수를 반환."""
    try:
        q = ticker.quarterly_income_stmt
    except Exception as e:
        logger.debug(f"quarterly_income_stmt 실패 (stock {stock_id}): {e}")
        return 0
    if q is None or getattr(q, "empty", True):
        return 0

    def row_map(keys):
        row = next((k for k in keys if k in q.index), None)
        if row is None:
            return {}
        out = {}
        for col in q.columns:
            v = safe_amount(q.loc[row][col])
            if v is not None:
                out[col] = v
        return out

    rev, opi, net = row_map(Q_REVENUE_KEYS), row_map(Q_OP_INCOME_KEYS), row_map(Q_NET_INCOME_KEYS)
    periods = set(rev) | set(opi) | set(net)
    saved = 0
    for col in periods:
        try:
            period_end = col.date() if hasattr(col, "date") else col
        except Exception:
            continue
        existing = await db.execute(
            select(QuarterlyFinancials).where(
                QuarterlyFinancials.stock_id == stock_id,
                QuarterlyFinancials.period_end == period_end,
            )
        )
        qf = existing.scalar_one_or_none()
        if qf:
            qf.revenue = rev.get(col)
            qf.operating_income = opi.get(col)
            qf.net_income = net.get(col)
        else:
            db.add(QuarterlyFinancials(
                stock_id=stock_id, period_end=period_end,
                revenue=rev.get(col), operating_income=opi.get(col), net_income=net.get(col),
            ))
        saved += 1
    return saved


def _fetch_equity(ticker) -> float | None:
    """yfinance balance_sheet 조회. 실패해도 수집 전체를 막지 않는다."""
    try:
        return extract_equity(ticker.balance_sheet)
    except Exception as e:
        logger.debug(f"balance_sheet fetch failed: {e}")
        return None
