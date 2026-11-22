"""Database engine initialization, connection management, and session creation."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from sensorguard.db.schema import Base

DEFAULT_SQLITE_PATH = "data/sensorguard.db"


def get_database_url() -> str:
    """Retrieve database URL from environment or return default SQLite path.

    Falls back cleanly to local SQLite when DATABASE_URL is not set.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    path = Path(DEFAULT_SQLITE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.resolve().as_posix()}"


def create_db_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Create a configured SQLAlchemy engine."""
    db_url = url or get_database_url()
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(db_url, echo=echo, connect_args=connect_args)


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create a thread-safe session factory bound to the engine."""
    eng = engine or create_db_engine()
    return sessionmaker(bind=eng, autoflush=False, autocommit=False)


def init_db(engine: Engine | None = None) -> None:
    """Idempotently create all tables defined in Base metadata."""
    eng = engine or create_db_engine()
    Base.metadata.create_all(bind=eng)


def get_db_session(engine: Engine | None = None) -> Generator[Session, None, None]:
    """Provide a transactional database session scope."""
    factory = get_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
