import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base


def _resolve_database_url() -> str:
    raw_database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://pyrun:pyrun123@127.0.0.1:5433/pyrunai",
    )

    if raw_database_url.startswith("postgres://"):
        return raw_database_url.replace("postgres://", "postgresql://", 1)

    return raw_database_url


DATABASE_URL = _resolve_database_url()

connect_args = {}
if DATABASE_URL.startswith("postgresql"):
    connect_args["connect_timeout"] = 5

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()