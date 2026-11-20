from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.enums import BillingInterval, IsolationStrategy, TenantStatus


class TenantCreate(BaseModel):
    application_id: uuid.UUID
    organization_id: uuid.UUID
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(default=None, max_length=80, pattern=r"^[a-z0-9-]+$")
    owner_email: EmailStr | None = None
    owner_id: uuid.UUID | None = None
    plan_id: uuid.UUID | None = None
    billing_interval: BillingInterval = BillingInterval.MONTHLY
    trial_days: int = Field(default=14, ge=0, le=365)
    isolation_strategy: IsolationStrategy | None = None
    custom_domain: str | None = Field(default=None, max_length=255)
    quotas: dict[str, int] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)
    branding: dict[str, Any] = Field(default_factory=dict)

    @field_validator("custom_domain")
    @classmethod
    def normalize_domain(cls, value: str | None) -> str | None:
        if not value:
            return None
        domain = value.strip().lower().rstrip(".")
        if "://" in domain or "/" in domain or " " in domain:
            raise ValueError("custom_domain must be a bare hostname")
        return domain


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    isolation_strategy: IsolationStrategy | None = None
    quotas: dict[str, int] | None = None
    configuration: dict[str, Any] | None = None
    branding: dict[str, Any] | None = None


class TenantStatusChange(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class TenantTransfer(BaseModel):
    new_owner_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=300)


class TenantUserInvite(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=200)
    role_key: str = "tenant_member"
    send_email: bool = True


class TenantUserUpdate(BaseModel):
    role_key: str | None = None
    status: str | None = Field(default=None, pattern=r"^(active|suspended|invited)$")


class TenantDomainCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=255)
    is_primary: bool = False

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str) -> str:
        domain = value.strip().lower().rstrip(".")
        if "://" in domain or "/" in domain or " " in domain:
            raise ValueError("domain must be a bare hostname")
        return domain


class TenantConfigPublish(BaseModel):
    config: dict[str, Any]
    note: str | None = Field(default=None, max_length=300)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    status: TenantStatus
    owner_id: uuid.UUID | None
    isolation_strategy: str
    quotas: dict[str, Any]
    configuration: dict[str, Any]
    branding: dict[str, Any]
    trial_ends_at: datetime | None
    suspended_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
