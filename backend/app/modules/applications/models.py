from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import (
    ApplicationStatus,
    EnvironmentKind,
    EnvironmentStatus,
    IsolationStrategy,
)


class Application(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "applications"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ApplicationStatus] = mapped_column(
        String(20), nullable=False, default=ApplicationStatus.DRAFT.value, index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    base_url: Mapped[str | None] = mapped_column(String(500))
    auth_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    integration_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    isolation_strategy: Mapped[IsolationStrategy] = mapped_column(
        String(32), nullable=False, default=IsolationStrategy.SHARED.value
    )
    subscription_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    resource_allocation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationEnvironment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "application_environments"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[EnvironmentKind] = mapped_column(String(20), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    api_base_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EnvironmentStatus] = mapped_column(
        String(20), nullable=False, default=EnvironmentStatus.PENDING.value
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_latency_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("application_id", "slug", name="uq_environment_app_slug"),
        Index("ix_environments_kind", "application_id", "kind"),
    )


class ApiCredential(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "api_credentials"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApplicationClient(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    """OIDC/OAuth login client used by an external application's end users.

    This is deliberately separate from ``ApiCredential``: credentials authorize
    server-to-server management calls, while a login client authorizes an
    authorization-code flow for a person. Confidential clients hold a hashed
    secret; public clients (SPAs and native apps) must use PKCE.
    """

    __tablename__ = "application_clients"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    client_secret_hash: Mapped[str | None] = mapped_column(String(64))
    client_type: Mapped[str] = mapped_column(String(20), nullable=False, default="confidential")
    redirect_uris: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    post_logout_redirect_uris: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    allowed_origins: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=lambda: ["openid", "profile", "email"]
    )
    pkce_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthorizationCode(Base, UUIDPrimaryKeyMixin):
    """Short-lived, single-use authorization code. Only its hash is stored."""

    __tablename__ = "authorization_codes"

    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    application_client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_sessions.id", ondelete="SET NULL"))
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    scope: Mapped[str] = mapped_column(String(500), nullable=False)
    nonce: Mapped[str | None] = mapped_column(String(255))
    code_challenge: Mapped[str | None] = mapped_column(String(128))
    code_challenge_method: Mapped[str | None] = mapped_column(String(10))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auth_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amr: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_auth_codes_expiry", "expires_at"),)


class ApplicationSession(Base, UUIDPrimaryKeyMixin):
    """Refresh-token session for an external application's end-user login.

    Refresh tokens are opaque, rotated on every use, and stored only as a
    SHA-256 hash, matching the console session model.
    """

    __tablename__ = "application_sessions"

    application_client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(500), nullable=False)
    nonce: Mapped[str | None] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    auth_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amr: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(120))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("application_sessions.id"))

    __table_args__ = (Index("ix_app_sessions_user_active", "user_id", "revoked_at", "expires_at"),)


class ApplicationConfiguration(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "application_configurations"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("application_environments.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_app_config_version",
            "application_id",
            "environment_id",
            "version",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class FeatureFlag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "feature_flags"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollout_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (UniqueConstraint("application_id", "key", name="uq_flag_app_key"),)


class FeatureFlagOverride(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "feature_flag_overrides"

    flag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("flag_id", "tenant_id", name="uq_flag_tenant"),)


class Webhook(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "webhooks"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Integration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "integrations"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="disconnected")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("application_id", "provider", name="uq_integration_app_provider"),)
