from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(default=None, max_length=80, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=2000)
    billing_email: EmailStr | None = None


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    billing_email: EmailStr | None = None
    status: str | None = Field(default=None, pattern=r"^(active|suspended|archived)$")


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    status: str
    billing_email: str | None
    logo_url: str | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime
