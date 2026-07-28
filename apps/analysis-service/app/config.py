from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://localhost:5432/stock_signal"
    redis_url: str = "redis://localhost:6379"
    port: int = 8000
    env: str = "development"

    class Config:
        env_file = ".env"
        extra = "ignore"   # LLM_* 등 .env 의 추가 변수(os.getenv 로 읽는 것)를 거부하지 않도록


settings = Settings()
