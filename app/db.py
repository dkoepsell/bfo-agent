"""SQLite database setup (SQLAlchemy 2.x).

Holds accounts, BYOK keys, per-job ownership/billing, and token usage. The
heavy claim/ontology data continues to live as JSON/OWL on disk (see
app/jobs.py and the ontology library); this DB only carries the relational
metadata that the file store cannot express well.

Phase 1 bootstraps the schema with ``Base.metadata.create_all`` — no Alembic
until the schema starts churning (Phase 2 credits).
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


config.DATA_DIR.mkdir(parents=True, exist_ok=True)

# check_same_thread=False so the background worker thread can share the engine;
# all writes are short and serialized through SQLAlchemy sessions.
engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables if they do not exist. Idempotent; safe at every startup."""
    from . import models  # noqa: F401  (register mappers before create_all)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    """Transactional session: commit on success, rollback on error, always close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
