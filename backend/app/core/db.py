from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, String, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utcnow, nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    **({"connect_args": {"statement_cache_size": 0}} if settings.app_env == "test" else {}),
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def _runtime_setting(
    session: AsyncSession, name: str, active: str, cleared: str
) -> AsyncIterator[None]:
    """Apply a session setting for the block, then clear it durably.

    ``set_config(..., is_local=false)`` is transactional: if the block commits
    while the elevated value is active, a plain reset in ``finally`` would be
    rolled back when the pooled connection is returned. That would let the next
    borrower inherit a privileged RLS context. Instead, discard any leftover
    work, reset, and commit the reset before the connection can be reused.
    """
    await session.execute(text("SELECT set_config(:name, :value, false)"), {"name": name, "value": active})
    try:
        yield
        if session.in_transaction():
            # ``expire_on_commit`` is disabled for this session, so committing
            # here keeps the objects loaded inside the block usable afterwards.
            await session.commit()
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise
    finally:
        await session.execute(
            text("SELECT set_config(:name, :value, false)"), {"name": name, "value": cleared}
        )
        await session.commit()


@asynccontextmanager
async def tenant_scope(session: AsyncSession, tenant_id: uuid.UUID | None) -> AsyncIterator[None]:
    """Set the PostgreSQL session variable that backs row-level security.

    The runtime database role is not the table owner and has no BYPASSRLS, so a
    missing or incorrect value denies access instead of exposing rows.
    """
    async with _runtime_setting(session, "app.tenant_id", str(tenant_id) if tenant_id else "", ""):
        yield


@asynccontextmanager
async def platform_scope(session: AsyncSession) -> AsyncIterator[None]:
    """Explicitly bypass tenant RLS. Only call after a platform-level permission check."""
    async with _runtime_setting(session, "app.bypass_rls", "on", "off"):
        yield


def slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def new_token(prefix: str = "bsk") -> tuple[str, str]:
    """Return (plaintext, display prefix). Plaintext is never stored."""
    import secrets

    secret = secrets.token_urlsafe(32)
    return f"{prefix}_{secret}", f"{prefix}_{secret[:8]}"


class EnumColumn(String):
    """String column used for enums; constraints come from Pydantic + services."""
