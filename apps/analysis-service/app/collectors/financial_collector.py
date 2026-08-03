import logging
from datetime import datetime, date
import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.db_models import Stock, Market, FinancialMetrics

logger = logging.getLogger(__name__)

# 파생 PER/PBR 상한. 이 위는 분모가 0 에 가까워 생긴 노이즈로 보고 버린다.
PER_MAX = 1000.0
PBR_MAX = 100.0

# 자기자본 행 이름은 티커마다 다르다. 앞쪽을 우선한다.
EQUITY_KEYS = ("Stockholders Equity", "Common Stock Equity",
               "Total Equity Gross Minority Interest")


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

    collected = 0
    skipped = 0
    errors = 0

    for stock in stocks:
        try:
            ticker = yf.Ticker(stock.symbol)
            info = ticker.info or {}

            roe = _safe_float(info.get("returnOnEquity"))
            per = _safe_float(info.get("trailingPE"))
            pbr = _safe_float(info.get("priceToBook"))
            revenue = _safe_amount(info.get("totalRevenue"))
            net_income = _safe_amount(info.get("netIncomeToCommon"))
            operating_income = _safe_amount(info.get("operatingIncome") or info.get("ebitda"))
            debt_ratio = _safe_float(info.get("debtToEquity"))
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
            market_cap = _safe_amount(info.get("marketCap"))

            if per is None:
                per = _derive_ratio(market_cap, net_income, PER_MAX)
            if pbr is None and market_cap:
                # balance_sheet 는 info 와 별도 네트워크 호출이라, 결과를 쓸 수 있을
                # 때(시총이 있을 때)만 부른다. KR 배치 200종목 기준 약 +80초.
                pbr = _derive_ratio(market_cap, _fetch_equity(ticker), PBR_MAX)

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
    return {"collected": collected, "skipped": skipped, "errors": errors, "total_in_batch": len(stocks)}


def _derive_ratio(market_cap, denominator, cap: float) -> float | None:
    """
    시총 ÷ 분모 로 PER/PBR 을 만든다 (PER 분모=순이익, PBR 분모=자기자본).

    분모가 0 이하면 지표 자체가 의미 없으므로 None (적자 기업의 PER, 자본잠식의 PBR).
    상한을 넘는 값도 분모가 0 에 가까워 생긴 노이즈라 버린다.
    """
    if market_cap is None or denominator is None:
        return None
    if market_cap <= 0 or denominator <= 0:
        return None
    ratio = market_cap / denominator
    if ratio <= 0 or ratio > cap:
        return None
    return round(ratio, 4)


def _extract_equity(balance_sheet) -> float | None:
    """balance_sheet DataFrame 에서 가장 최근 연차 자기자본을 꺼낸다."""
    if balance_sheet is None or getattr(balance_sheet, "empty", True):
        return None
    row = next((k for k in EQUITY_KEYS if k in balance_sheet.index), None)
    if row is None:
        return None
    # 컬럼은 결산일이고 최신이 앞에 오지만, 순서를 가정하지 않고 직접 고른다.
    best_col = max(balance_sheet.columns)
    return _safe_amount(balance_sheet.loc[row][best_col])


def _fetch_equity(ticker) -> float | None:
    """yfinance balance_sheet 조회. 실패해도 수집 전체를 막지 않는다."""
    try:
        return _extract_equity(ticker.balance_sheet)
    except Exception as e:
        logger.debug(f"balance_sheet fetch failed: {e}")
        return None


def _safe_float(value) -> float | None:
    """비율 지표용 (ROE·PER·PBR·부채비율). 1e12 상한은 이상치 방어다."""
    try:
        if value is None or value != value:  # NaN check
            return None
        f = float(value)
        if f == 0.0 or abs(f) > 1e12:
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def _safe_amount(value) -> float | None:
    """
    통화 금액용 (매출·순이익·영업이익·시총).

    `_safe_float` 의 1e12 상한을 쓰면 **원화 대형주가 통째로 잘린다** — 삼성전자
    시총 약 1.7e15 KRW, 순이익 약 1.5e14 KRW 다. 실제로 이 상한 때문에 저장된
    net_income 최대값이 9,671억(1e12 바로 아래)에 걸려 있었고, KR 대형주는
    매출·순이익이 전부 NULL 이었다(2026-08-03 발견). 이 컬럼들이 스코어링에
    쓰이지 않아 드러나지 않았을 뿐이다.

    상한은 통화 단위와 무관하게 이상치만 걸러내도록 훨씬 크게 잡되,
    저장 컬럼이 Decimal(20,2)(정수부 18자리)라 그 안에 들어오도록 1e17 로 둔다.
    삼성전자 매출 약 4.9e14 KRW 보다 200배 여유가 있다.
    """
    try:
        if value is None or value != value:  # NaN check
            return None
        f = float(value)
        if f == 0.0 or abs(f) > 1e17:
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None
