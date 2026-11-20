from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import (
    DeploymentStatus,
    IncidentSeverity,
    IncidentStatus,
    ResourceStatus,
    ResourceType,
)


class InfrastructureResource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "infrastructure_resources"

    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("application_environments.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[ResourceType] = mapped_column(String(30), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    region: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[ResourceStatus] = mapped_column(
        String(20), nullable=False, default=ResourceStatus.PROVISIONING.value, index=True
    )
    monthly_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_infra_app_type", "application_id", "resource_type"),)


class Deployment(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "deployments"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_environments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[DeploymentStatus] = mapped_column(String(20), nullable=False, index=True)
    deployed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class Incident(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "incidents"

    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4000))
    severity: Mapped[IncidentSeverity] = mapped_column(String(10), nullable=False, index=True)
    status: Mapped[IncidentStatus] = mapped_column(
        String(20), nullable=False, default=IncidentStatus.OPEN.value, index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    impact: Mapped[str | None] = mapped_column(String(1000))
    postmortem: Mapped[str | None] = mapped_column(String(8000))


class MonitoringAlert(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "monitoring_alerts"

    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="firing", index=True)
    last_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
