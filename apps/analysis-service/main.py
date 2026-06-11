import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import collect, analysis, translate
from app.config import settings

app = FastAPI(
    title="Stock Signal Analysis Service",
    description="데이터 수집 및 추천 시그널 생성 서비스",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(collect.router)
app.include_router(analysis.router)
app.include_router(translate.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)
