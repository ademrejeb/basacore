from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import BillingInterval, SubscriptionStatus


class PlanCreate(BaseModel):
    application_id: uuid.UUID | None = None
    key: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price_monthly_cents: int = Field(default=0, ge=0)
    price_yearly_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    limits: dict[str, Any] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list)
    is_public: bool = True
    sort_order: int = 0


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price_monthly_cents: int | None = Field(default=None, ge=0)
    price_yearly_cents: int | None = Field(default=None, ge=0)
    limits: dict[str, Any] | None = None
    features: list[str] | None = None
    is_public: bool | None = None
    sort_order: int | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID | None
    key: str
    name: str
    description: str | None
    price_monthly_cents: int
    price_yearly_cents: int
    currency: str
    limits: dict[str, Any]
    features: list[str]
    is_public: bool
    sort_order: int


class SubscriptionChange(BaseModel):
    plan_id: uuid.UUID | None = None
    billing_interval: BillingInterval | None = None
    status: SubscriptionStatus | None = None
    discount_percent: int | None = Field(default=None, ge=0, le=100)
    extend_trial_days: int | None = Field(default=None, ge=1, le=365)
    cancel_at_period_end: bool | None = None
    reason: str | None = Field(default=None, max_length=300)


class UsageReport(BaseModel):
    metric: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9._-]+$")
    value: float = Field(gt=0, le=1_000_000_000)
    idempotency_key: str | None = Field(default=None, max_length=120)


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subscription_id: uuid.UUID
    number: str
    amount_cents: int
    currency: str
    status: str
    period_start: datetime
    period_end: datetime
    issued_at: datetime
    due_at: datetime
    paid_at: datetime | None
    created_at: datetime
