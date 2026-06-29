from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def build_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    return create_engine(url, pool_pre_ping=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
