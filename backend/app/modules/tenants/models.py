from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import (
    DomainStatus,
    IsolationStrategy,
    MembershipStatus,
    SslStatus,
    TenantStatus,
)


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tenants"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        String(20), nullable=False, default=TenantStatus.PROVISIONING.value, index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    isolation_strategy: Mapped[IsolationStrategy] = mapped_column(
        String(32), nullable=False, default=IsolationStrategy.SHARED.value
    )
    database_ref: Mapped[str | None] = mapped_column(String(200))
    quotas: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    branding: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("application_id", "slug", name="uq_tenant_app_slug"),
        Index("ix_tenants_app_status", "application_id", "status"),
    )


class TenantMembership(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "tenant_memberships"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        String(20), nullable=False, default=MembershipStatus.ACTIVE.value
    )
    is_billing_contact: Mapped[bool] = mapped_column(nullable=False, default=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership"),)


class TenantDomain(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "tenant_domains"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[DomainStatus] = mapped_column(
        String(20), nullable=False, default=DomainStatus.PENDING.value
    )
    verification_token: Mapped[str] = mapped_column(String(80), nullable=False)
    ssl_status: Mapped[SslStatus] = mapped_column(String(20), nullable=False, default=SslStatus.PENDING.value)
    ssl_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenantConfiguration(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "tenant_configurations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "version", name="uq_tenant_config_version"),)


class TenantResourceQuota(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-tenant hard quotas, overriding plan defaults when present."""

    __tablename__ = "tenant_resource_quotas"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    limit_value: Mapped[int] = mapped_column(Integer, nullable=False)
    enforcement: Mapped[str] = mapped_column(String(20), nullable=False, default="hard")

    __table_args__ = (UniqueConstraint("tenant_id", "metric", name="uq_tenant_quota_metric"),)
