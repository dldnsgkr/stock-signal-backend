from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://localhost:5432/stock_signal"
    redis_url: str = "redis://localhost:6379"
    port: int = 8000
    env: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
