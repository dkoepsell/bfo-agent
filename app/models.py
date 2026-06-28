"""SQLAlchemy models: users, BYOK keys, job ownership, token usage.

Job *content* (claims, approval, per-claim status) stays in app/jobs.py JSON.
The ``Job`` row here is the ownership + billing mirror keyed by the same
job_id, so we can answer "whose job is this, what did it cost, is it free or
BYOK" without parsing the JSON store.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    free_jobs_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    byok: Mapped["ByokKey | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    # Flask-Login interface ------------------------------------------------
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)

    @property
    def has_byok(self) -> bool:
        return self.byok is not None


class ByokKey(Base):
    """A user's Anthropic key, Fernet-encrypted at rest. The plaintext key
    and ``enc_key`` are never returned by any endpoint; only ``key_hint``
    (last 4 chars) is surfaced to the UI."""
    __tablename__ = "byok_keys"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enc_key: Mapped[bytes] = mapped_column(LargeBinary)
    key_hint: Mapped[str] = mapped_column(String(8))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extractor_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="byok")


class Job(Base):
    """Ownership + billing mirror of an app/jobs.py job (same job_id)."""
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ontology_name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))  # 'free' | 'byok' (Ph2: 'credits')
    source_chars: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Usage(Base):
    """One row per Anthropic call, written via the EngineContext usage hook."""
    __tablename__ = "usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    phase: Mapped[str] = mapped_column(String(16))  # extract|propose|gate|query
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    billed_to: Mapped[str] = mapped_column(String(8))  # 'owner' | 'byok'
