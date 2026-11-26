
"""Database engine, session factory and helpers."""

from collections.abc import Generator

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from models import Base

engine = create_engine(
    settings.database_url,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """One transaction per business event: commit on success, roll back on error.

    Posting functions (purchase, production run, invoice...) run inside this
    so a document and its ledger rows are written atomically.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request.

    The caller (service layer) is responsible for committing.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create all tables (dev only; switch to Alembic migrations later)."""
    Base.metadata.create_all(bind=engine)
