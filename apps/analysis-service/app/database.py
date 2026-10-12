from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

DATABASE_URL = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

# Neon 등 호스팅 PG 의 URL 에 붙는 sslmode/channel_binding 은 libpq 전용 파라미터라
# SQLAlchemy 가 asyncpg connect kwargs 로 그대로 넘기다 TypeError 를 낸다.
# 쿼리에서 떼어내고 ssl 연결 인자로 변환한다. 로컬 URL(파라미터 없음)은 무변화.
_parts = urlsplit(DATABASE_URL)
_query = dict(parse_qsl(_parts.query))
_connect_args = {}
if _query.pop("sslmode", None) not in (None, "disable"):
    _connect_args["ssl"] = True
_query.pop("channel_binding", None)
DATABASE_URL = urlunsplit(_parts._replace(query=urlencode(_query)))

engine = create_async_engine(DATABASE_URL, echo=False, connect_args=_connect_args)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
