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


async def collect_macro_indicators(db: AsyncSession, market_code: str = "US") -> dict:
    tickers_map = US_MACRO_TICKERS if market_code == "US" else KR_MACRO_TICKERS
    collected = 0
    errors = 0
    start = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%d")
    end = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")

    for indicator_type, ticker_symbol in tickers_map.items():
        try:
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(start=start, end=end)

            if df is None or df.empty:
                logger.warning(f"No macro data for {indicator_type} ({ticker_symbol})")
                continue

            latest_row = df.iloc[-1]
            value = float(latest_row["Close"])
            observed_at = df.index[-1].to_pydatetime().replace(tzinfo=None)

            await db.execute(
                text("""
                    INSERT INTO macro_indicators (market_code, indicator_type, value, observed_at)
                    VALUES (:market_code, :indicator_type, :value, :observed_at)
                    ON CONFLICT (market_code, indicator_type, observed_at) DO UPDATE SET
                        value = EXCLUDED.value
                """),
                {
                    "market_code": market_code,
                    "indicator_type": indicator_type,
                    "value": value,
                    "observed_at": observed_at,
                },
            )
            collected += 1
            logger.info(f"{indicator_type}: {value:.4f} ({observed_at.date()})")

        except Exception as e:
            logger.error(f"Error collecting macro {indicator_type}: {e}")
            errors += 1

    await db.commit()
    logger.info(f"Macro collection done: {collected} indicators, {errors} errors")
    return {"collected": collected, "errors": errors}
