"""제공업체 무관 LLM 텍스트 생성.

LLM_PROVIDER(gemini|groq) + LLM_API_KEY 로 동작. 나중에 Claude 등으로 교체 시
_call_* 만 추가하면 된다. 의존성 추가 없이 requests 로 REST 호출.
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

# gemini-2.0-flash 계열은 무료 할당량 소진(429) 사례가 있어 flash-latest 를 기본값으로.
GEMINI_MODEL = os.getenv("LLM_MODEL", "gemini-flash-latest")
GROQ_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
TIMEOUT = 30


class LLMError(Exception):
    pass


def llm_enabled() -> bool:
    return bool(os.getenv("LLM_API_KEY"))


def generate_text(prompt: str, *, max_tokens: int = 500, temperature: float = 0.4) -> str:
    provider = (os.getenv("LLM_PROVIDER") or "gemini").lower()
    key = os.getenv("LLM_API_KEY")
    if not key:
        raise LLMError("LLM_API_KEY not set")

    if provider == "gemini":
        return _call_gemini(prompt, key, max_tokens, temperature)
    if provider == "groq":
        return _call_groq(prompt, key, max_tokens, temperature)
    raise LLMError(f"unknown LLM_PROVIDER: {provider}")


def _call_gemini(prompt: str, key: str, max_tokens: int, temperature: float) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    resp = requests.post(
        url,
        params={"key": key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise LLMError(f"gemini {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        # 안전필터 차단 등
        raise LLMError(f"gemini empty response: {str(data)[:200]}") from e


def _call_groq(prompt: str, key: str, max_tokens: int, temperature: float) -> str:
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise LLMError(f"groq {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"].strip()
