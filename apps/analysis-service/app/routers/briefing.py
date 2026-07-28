"""AI 종목 브리핑 — 시그널 근거 + 뉴스 + 수급을 LLM 으로 한국어 요약.

캐시: (symbol, 최신 추천 시각) 키. 새 추천이 없으면 재호출 없이 캐시 반환.
LLM 미설정/오류 시 503 로 안내(프론트는 조용히 숨김).
"""
import time
import logging
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db
from app.engine.llm import generate_text, llm_enabled, LLMError

router = APIRouter(prefix="/analysis", tags=["briefing"])
logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 6 * 3600  # 6시간 (새 추천 없으면 그대로)
_CACHE_MAX = 200


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: str, val: dict):
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time(), val)


@router.get("/briefing")
async def briefing(
    symbol: str = Query(...),
    market: str = Query("US"),
    db: AsyncSession = Depends(get_db),
):
    if not llm_enabled():
        return JSONResponse(status_code=503, content={"error": "LLM 미설정"})

    sym = symbol.upper()

    # 종목 + 최신 추천
    row = (await db.execute(text("""
        SELECT s.id, s.name, s.sector, m.code AS market,
               r.action, r.score, r.confidence, r.reasons_json, r.score_detail_json,
               r.recommended_at
        FROM stocks s
        JOIN markets m ON m.id = s.market_id
        LEFT JOIN LATERAL (
            SELECT * FROM recommendations rr
            WHERE rr.stock_id = s.id ORDER BY rr.recommended_at DESC LIMIT 1
        ) r ON true
        WHERE s.symbol = :sym
        LIMIT 1
    """), {"sym": sym})).mappings().first()

    if not row:
        return JSONResponse(status_code=404, content={"error": "종목 없음"})

    rec_at = row["recommended_at"]
    cache_key = f"{sym}:{rec_at.isoformat() if rec_at else 'none'}"
    cached = _cache_get(cache_key)
    if cached:
        return JSONResponse(content={**cached, "cached": True})

    # 최근 뉴스 5건
    news_rows = (await db.execute(text("""
        SELECT n.title, n.sentiment_score
        FROM news_articles n
        JOIN news_stock_relations nr ON nr.news_article_id = n.id
        WHERE nr.stock_id = :sid
        ORDER BY n.published_at DESC LIMIT 5
    """), {"sid": row["id"]})).mappings().all()

    # KR 수급 (최근 5거래일 외국인/기관 순매수 합)
    flow_line = ""
    if row["market"] == "KR":
        f = (await db.execute(text("""
            WITH d AS (SELECT DISTINCT trade_date FROM investor_flow_daily
                       WHERE stock_id = :sid ORDER BY trade_date DESC LIMIT 5)
            SELECT investor_type, SUM(net_buy_value)::bigint AS net
            FROM investor_flow_daily
            WHERE stock_id = :sid AND trade_date IN (SELECT trade_date FROM d)
            GROUP BY investor_type
        """), {"sid": row["id"]})).mappings().all()
        parts = []
        for fr in f:
            if fr["investor_type"] in ("foreign", "institution"):
                label = "외국인" if fr["investor_type"] == "foreign" else "기관"
                eok = round((fr["net"] or 0) / 1e8)
                parts.append(f"{label} {'+' if eok >= 0 else ''}{eok}억")
        if parts:
            flow_line = "최근 5거래일 수급: " + ", ".join(parts)

    prompt = _build_prompt(row, news_rows, flow_line)
    try:
        # gemini-flash-latest 는 사고(thinking) 토큰을 ~500 소비하므로 넉넉히 준다
        # (thinkingBudget=0 은 이 모델에서 400 거부됨). 부족하면 답이 잘린다.
        text_out = generate_text(prompt, max_tokens=2000, temperature=0.4)
    except LLMError as e:
        logger.error(f"briefing LLM 실패 {sym}: {e}")
        return JSONResponse(status_code=503, content={"error": "브리핑 생성 실패"})

    result = {
        "symbol": sym,
        "name": row["name"],
        "briefing": text_out,
        "basedOn": {
            "action": row["action"],
            "score": float(row["score"]) if row["score"] is not None else None,
            "recommendedAt": rec_at.isoformat() if rec_at else None,
        },
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _cache_put(cache_key, result)
    return JSONResponse(content={**result, "cached": False})


def _build_prompt(row, news_rows, flow_line: str) -> str:
    action_kr = {"BUY": "매수", "WATCH": "관심", "AVOID": "회피", "SELL": "청산"}.get(row["action"], "관심")
    sd = row["score_detail_json"] or {}
    reasons = row["reasons_json"] or []
    reasons_txt = "; ".join(reasons[:4]) if isinstance(reasons, list) else ""

    news_txt = ""
    if news_rows:
        lines = []
        for n in news_rows:
            s = n["sentiment_score"]
            tone = ""
            if s is not None:
                sv = float(s)
                tone = " (긍정)" if sv > 0.1 else " (부정)" if sv < -0.1 else " (중립)"
            lines.append(f"- {n['title']}{tone}")
        news_txt = "\n".join(lines)

    return f"""당신은 한국의 주식 데이터 분석 어시스턴트입니다. 아래 정량 데이터만 근거로,
{row['name']}({row['sector'] or '섹터 미상'})에 대한 한국어 브리핑을 작성하세요.

[시스템 시그널]
- 판정: {action_kr} (종합점수 {row['score']}, 신뢰도 {row['confidence']}%)
- 점수 구성: 모멘텀 {sd.get('momentum_score', sd.get('technical_score', '-'))}, 가치 {sd.get('value_score', sd.get('fundamental_score', '-'))}, 감성 {sd.get('sentiment_score', sd.get('news_score', '-'))}
- 근거: {reasons_txt or '특이 근거 없음'}

[최근 뉴스]
{news_txt or '수집된 뉴스 없음'}

{flow_line}

작성 지침:
- 3~4문장, 존댓말, 과장 없이 담백하게. 마크다운 기호(**, #, - 등) 없이 순수 텍스트로.
- 시그널 판정의 이유를 위 데이터로 설명하고, 뉴스·수급이 이를 뒷받침하거나 반대하는지 언급.
- 데이터에 없는 내용(목표가, 실적 전망 등)은 지어내지 마세요.
- 마지막에 한 문장으로 "본 요약은 데이터 기반 참고 자료이며 투자 권유가 아닙니다." 를 덧붙이세요."""
