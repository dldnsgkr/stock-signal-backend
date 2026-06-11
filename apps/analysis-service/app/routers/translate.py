import logging
import httpx
import trafilatura
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/translate", tags=["translate"])

SUPPORTED_LANGS = {"ko", "en", "ja", "zh-CN", "zh-TW"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class TranslateRequest(BaseModel):
    texts: list[str]
    target: str = "ko"


class TranslateResponse(BaseModel):
    translated: list[str]
    target: str


class ArticleRequest(BaseModel):
    url: str
    target: str = "original"  # "original" = 번역 안 함


class ArticleResponse(BaseModel):
    original: str
    translated: str | None
    target: str


@router.post("", response_model=TranslateResponse)
async def translate_texts(body: TranslateRequest):
    if body.target not in SUPPORTED_LANGS:
        raise HTTPException(status_code=400, detail=f"Unsupported target language: {body.target}")

    if not body.texts:
        return TranslateResponse(translated=[], target=body.target)

    try:
        translator = GoogleTranslator(source="auto", target=body.target)
        results = []
        for text in body.texts:
            if not text or not text.strip():
                results.append("")
                continue
            translated = translator.translate(text.strip())
            results.append(translated or text)
        return TranslateResponse(translated=results, target=body.target)

    except Exception as e:
        logger.error(f"Translation failed: {e}")
        raise HTTPException(status_code=500, detail="Translation failed")


@router.post("/article", response_model=ArticleResponse)
async def fetch_and_translate_article(body: ArticleRequest):
    """뉴스 URL에서 본문을 추출하고 선택적으로 번역합니다."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            resp = await client.get(body.url)
            html = resp.text
    except Exception as e:
        logger.error(f"Failed to fetch article {body.url}: {e}")
        raise HTTPException(status_code=502, detail="기사를 가져오지 못했습니다")

    # 본문 추출
    content = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )

    if not content or len(content.strip()) < 50:
        raise HTTPException(status_code=422, detail="본문을 추출할 수 없습니다 (페이월 또는 접근 제한)")

    original = content.strip()

    # 번역 요청이 없으면 원문만 반환
    if body.target == "original" or body.target == "en":
        return ArticleResponse(original=original, translated=None, target=body.target)

    if body.target not in SUPPORTED_LANGS:
        raise HTTPException(status_code=400, detail=f"Unsupported target language: {body.target}")

    # 긴 텍스트는 단락 단위로 나눠서 번역 (Google Translate 5000자 제한)
    try:
        translator = GoogleTranslator(source="auto", target=body.target)
        paragraphs = [p for p in original.split("\n") if p.strip()]
        translated_parts = []
        chunk = ""

        for para in paragraphs:
            if len(chunk) + len(para) > 4500:
                if chunk:
                    translated_parts.append(translator.translate(chunk.strip()) or chunk)
                chunk = para + "\n"
            else:
                chunk += para + "\n"

        if chunk.strip():
            translated_parts.append(translator.translate(chunk.strip()) or chunk)

        translated = "\n\n".join(translated_parts)
        return ArticleResponse(original=original, translated=translated, target=body.target)

    except Exception as e:
        logger.error(f"Article translation failed: {e}")
        # 번역 실패해도 원문은 반환
        return ArticleResponse(original=original, translated=None, target=body.target)
