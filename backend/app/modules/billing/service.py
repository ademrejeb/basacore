from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.enums import BillingInterval, SubscriptionStatus, TenantStatus
from app.core.errors import QuotaExceeded, ValidationFailed
from app.modules.billing.models import Plan, Subscription, UsageRecord
from app.modules.tenants.models import Tenant


def _add_months(value: datetime, months: int) -> datetime:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    import calendar

    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def period_start(value: datetime | None = None) -> date:
    now = value or utcnow()
    return date(now.year, now.month, 1)


def price_for(plan: Plan, interval: str) -> int:
    if interval == BillingInterval.YEARLY.value:
        return plan.price_yearly_cents or plan.price_monthly_cents * 12
    return plan.price_monthly_cents


def monthly_recurring_cents(subscription: Subscription, plan: Plan) -> int:
    if subscription.status not in (
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.TRIALING.value,
        SubscriptionStatus.PAST_DUE.value,
    ):
        return 0
    if subscription.billing_interval == BillingInterval.YEARLY.value:
        annual = price_for(plan, BillingInterval.YEARLY.value)
        value = annual / 12
    else:
        value = price_for(plan, BillingInterval.MONTHLY.value)
    if subscription.discount_percent:
        value *= 1 - subscription.discount_percent / 100
    return int(round(value))


async def create_subscription(
    db: AsyncSession,
    *,
    tenant: Tenant,
    plan: Plan,
    billing_interval: str,
    trial_days: int,
) -> Subscription:
    now = utcnow()
    starts_trial = trial_days > 0
    if billing_interval == BillingInterval.YEARLY.value:
        period_end = now + timedelta(days=365)
    else:
        period_end = _add_months(now, 1)
    subscription = Subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        status=SubscriptionStatus.TRIALING.value if starts_trial else SubscriptionStatus.ACTIVE.value,
        billing_interval=billing_interval,
        current_period_start=now,
        current_period_end=period_end,
        trial_ends_at=now + timedelta(days=trial_days) if starts_trial else None,
    )
    db.add(subscription)
    if not tenant.quotas:
        tenant.quotas = {key: value for key, value in (plan.limits or {}).items() if isinstance(value, int)}
    return subscription


async def get_plan_for_tenant(
    db: AsyncSession, tenant: Tenant
) -> tuple[Subscription, Plan] | tuple[None, None]:
    subscription = (
        await db.execute(select(Subscription).where(Subscription.tenant_id == tenant.id))
    ).scalar_one_or_none()
    if subscription is None:
        return None, None
    plan = await db.get(Plan, subscription.plan_id)
    return subscription, plan


async def quota_limit(db: AsyncSession, tenant: Tenant, metric: str) -> int | None:
    if metric in (tenant.quotas or {}):
        return int(tenant.quotas[metric])
    _, plan = await get_plan_for_tenant(db, tenant)
    if plan and metric in (plan.limits or {}):
        value = plan.limits[metric]
        if isinstance(value, int):
            return value
        if value in (None, "unlimited"):
            return None
    return None


async def usage_for_period(
    db: AsyncSession, tenant_id: uuid.UUID, metric: str, start: date | None = None
) -> float:
    start = start or period_start()
    total = (
        await db.execute(
            select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
                UsageRecord.tenant_id == tenant_id,
                UsageRecord.metric == metric,
                UsageRecord.period_start == start,
            )
        )
    ).scalar_one()
    return float(total)


async def record_usage(
    db: AsyncSession,
    *,
    tenant: Tenant,
    metric: str,
    quantity: Decimal,
    source: str,
    idempotency_key: str | None,
    metadata: dict[str, Any] | None = None,
) -> UsageRecord:
    if quantity <= 0:
        raise ValidationFailed("Usage quantity must be positive.")
    # Quota enforcement is read-then-write. Lock the tenant row so concurrent
    # reports serialize instead of both reading the same "used" value and
    # overshooting the limit.
    await db.execute(select(Tenant.id).where(Tenant.id == tenant.id).with_for_update())

    start = period_start()
    limit = await quota_limit(db, tenant, metric)
    if limit is not None:
        used = await usage_for_period(db, tenant.id, metric, start)
        if used + float(quantity) > limit:
            raise QuotaExceeded(metric, limit, used)
    record = UsageRecord(
        tenant_id=tenant.id,
        application_id=tenant.application_id,
        metric=metric,
        quantity=quantity,
        period_start=start,
        recorded_at=utcnow(),
        source=source,
        idempotency_key=idempotency_key,
        metadata_json=metadata or {},
    )
    db.add(record)
    return record


async def usage_summary(db: AsyncSession, tenant: Tenant) -> list[dict[str, Any]]:
    subscription, plan = await get_plan_for_tenant(db, tenant)
    rows = (
        await db.execute(
            select(UsageRecord.metric, func.sum(UsageRecord.quantity))
            .where(UsageRecord.tenant_id == tenant.id, UsageRecord.period_start == period_start())
            .group_by(UsageRecord.metric)
        )
    ).all()
    used = {metric: float(total) for metric, total in rows}
    metrics = set(used) | set((plan.limits if plan else {}) or {}) | set(tenant.quotas or {})
    result = []
    for metric in sorted(metrics):
        limit = await quota_limit(db, tenant, metric)
        current = used.get(metric, 0.0)
        result.append(
            {
                "metric": metric,
                "used": round(current, 4),
                "limit": limit,
                "percent": round((current / limit * 100) if limit else 0, 1),
                "over_limit": bool(limit is not None and current > limit),
            }
        )
    return result


async def set_tenant_status_for_subscription(
    db: AsyncSession, tenant: Tenant, subscription: Subscription
) -> None:
    now = utcnow()
    if subscription.status in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value):
        if tenant.status in (TenantStatus.SUSPENDED.value, TenantStatus.PROVISIONING.value):
            tenant.status = (
                TenantStatus.TRIAL.value
                if subscription.status == SubscriptionStatus.TRIALING.value
                else TenantStatus.ACTIVE.value
            )
            tenant.suspended_at = None
    elif subscription.status in (SubscriptionStatus.PAST_DUE.value, SubscriptionStatus.PAUSED.value):
        tenant.status = TenantStatus.SUSPENDED.value
        tenant.suspended_at = tenant.suspended_at or now
