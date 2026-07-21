import logging
from datetime import datetime, timedelta
import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)

US_MACRO_TICKERS = {
    "VIX": "^VIX",
    "SP500": "^GSPC",
    "US10Y": "^TNX",
    "DXY": "DX-Y.NYB",
    "GOLD": "GC=F",
    "OIL": "CL=F",
}

KR_MACRO_TICKERS = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "KRW_USD": "KRWUSD=X",
    "KR10Y": "^KS200",   # 대체: KOSPI 200
    "GOLD": "GC=F",
}


async def collect_macro_indicators(
    db: AsyncSession, market_code: str = "US", days: int = 10
) -> dict:
    tickers_map = US_MACRO_TICKERS if market_code == "US" else KR_MACRO_TICKERS
    collected = 0
    errors = 0
    start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    end = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")

    for indicator_type, ticker_symbol in tickers_map.items():
        try:
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(start=start, end=end)

            if df is None or df.empty:
                logger.warning(f"No macro data for {indicator_type} ({ticker_symbol})")
                continue

            df = df.dropna(subset=["Close"])
            if df.empty:
                continue

            # 조회 구간을 전부 저장한다. 예전에는 df.iloc[-1] 한 행만 넣어서,
            # 파이프라인이 하루라도 거르면 그날 지수가 영구히 비었다.
            rows = [
                {
                    "market_code": market_code,
                    "indicator_type": indicator_type,
                    "value": float(row["Close"]),
                    "observed_at": idx.to_pydatetime().replace(tzinfo=None),
                }
                for idx, row in df.iterrows()
            ]

            await db.execute(
                text("""
                    INSERT INTO macro_indicators (market_code, indicator_type, value, observed_at)
                    VALUES (:market_code, :indicator_type, :value, :observed_at)
                    ON CONFLICT (market_code, indicator_type, observed_at) DO UPDATE SET
                        value = EXCLUDED.value
                """),
                rows,
            )
            collected += len(rows)
            logger.info(
                f"{indicator_type}: {len(rows)} rows "
                f"({rows[0]['observed_at'].date()} ~ {rows[-1]['observed_at'].date()})"
            )

        except Exception as e:
            logger.error(f"Error collecting macro {indicator_type}: {e}")
            errors += 1

    await db.commit()
    logger.info(f"Macro collection done: {collected} indicators, {errors} errors")
    return {"collected": collected, "errors": errors}
