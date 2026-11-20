from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import get_session, utcnow
from app.core.deps import Principal, require_platform
from app.core.enums import (
    InvoiceStatus,
    PaymentStatus,
    SubscriptionStatus,
)
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.pagination import PageParams, page_params
from app.modules.applications.models import Application
from app.modules.billing import service
from app.modules.billing.models import Invoice, Payment, Plan, Subscription, UsageRecord
from app.modules.billing.schemas import (
    InvoiceOut,
    PlanCreate,
    PlanOut,
    PlanUpdate,
    SubscriptionChange,
)
from app.modules.tenants.models import Tenant

router = APIRouter(prefix="/billing", tags=["Billing"])


class PaymentRecord(BaseModel):
    invoice_id: uuid.UUID
    provider: str = Field(min_length=2, max_length=40)
    provider_payment_id: str | None = Field(default=None, max_length=120)
    amount_cents: int = Field(gt=0)
    status: PaymentStatus = PaymentStatus.SUCCEEDED
    failure_reason: str | None = Field(default=None, max_length=300)


@router.get("/summary", response_model=dict)
async def billing_summary(
    _: Principal = Depends(require_platform("billing.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = (await db.execute(select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id))).all()
    mrr = sum(service.monthly_recurring_cents(subscription, plan) for subscription, plan in rows)
    status_counts: dict[str, int] = {}
    plan_counts: dict[str, int] = {}
    for subscription, plan in rows:
        status_counts[subscription.status] = status_counts.get(subscription.status, 0) + 1
        plan_counts[plan.name] = plan_counts.get(plan.name, 0) + 1
    open_invoices = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Invoice.amount_cents), 0)).where(
                Invoice.status.in_([InvoiceStatus.OPEN.value, InvoiceStatus.DRAFT.value])
            )
        )
    ).one()
    return {
        "mrr_cents": mrr,
        "arr_cents": mrr * 12,
        "active_subscriptions": status_counts.get(SubscriptionStatus.ACTIVE.value, 0)
        + status_counts.get(SubscriptionStatus.TRIALING.value, 0),
        "status_distribution": status_counts,
        "plan_distribution": plan_counts,
        "open_invoices": open_invoices[0],
        "outstanding_cents": int(open_invoices[1]),
    }


def _limit_for(tenant: Tenant, plan: Plan | None, metric: str) -> int | None:
    quotas = tenant.quotas or {}
    if metric in quotas:
        try:
            return int(quotas[metric])
        except (TypeError, ValueError):
            return None
    if plan is not None:
        value = (plan.limits or {}).get(metric)
        if isinstance(value, int):
            return value
    return None


@router.get("/usage", response_model=dict)
async def usage_report(
    application_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("usage.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Current-period metered usage for every tenant, aggregated against quota limits."""
    start = service.period_start()
    tenant_stmt = (
        select(Tenant, Application.name)
        .outerjoin(Application, Application.id == Tenant.application_id)
        .where(Tenant.deleted_at.is_(None))
        .order_by(Tenant.name)
    )
    if application_id is not None:
        tenant_stmt = tenant_stmt.where(Tenant.application_id == application_id)
    tenant_rows = (await db.execute(tenant_stmt)).all()

    subscription_rows = (
        await db.execute(select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id))
    ).all()
    plans_by_tenant = {subscription.tenant_id: plan for subscription, plan in subscription_rows}

    usage_rows = (
        await db.execute(
            select(
                UsageRecord.tenant_id,
                UsageRecord.metric,
                func.sum(UsageRecord.quantity),
            )
            .where(UsageRecord.period_start == start)
            .group_by(UsageRecord.tenant_id, UsageRecord.metric)
        )
    ).all()
    used_by_tenant: dict[uuid.UUID, dict[str, float]] = {}
    aggregate: dict[str, float] = {}
    for tenant_id, metric, total in usage_rows:
        value = float(total)
        used_by_tenant.setdefault(tenant_id, {})[metric] = value
        aggregate[metric] = aggregate.get(metric, 0.0) + value

    metrics: set[str] = set(aggregate)
    for tenant, _ in tenant_rows:
        metrics.update((tenant.quotas or {}).keys())
        plan = plans_by_tenant.get(tenant.id)
        if plan is not None:
            metrics.update((plan.limits or {}).keys())
    ordered_metrics = sorted(metrics)

    tenants_payload: list[dict[str, Any]] = []
    over_limit_count = 0
    for tenant, application_name in tenant_rows:
        used = used_by_tenant.get(tenant.id, {})
        plan = plans_by_tenant.get(tenant.id)
        tenant_metrics = []
        tenant_over = False
        for metric in ordered_metrics:
            current = used.get(metric, 0.0)
            limit = _limit_for(tenant, plan, metric)
            over = bool(limit is not None and current > limit)
            tenant_over = tenant_over or over
            tenant_metrics.append(
                {
                    "metric": metric,
                    "used": round(current, 4),
                    "limit": limit,
                    "percent": round((current / limit * 100) if limit else 0, 1),
                    "over_limit": over,
                }
            )
        if tenant_over:
            over_limit_count += 1
        tenants_payload.append(
            {
                "id": tenant.id,
                "name": tenant.name,
                "slug": tenant.slug,
                "status": tenant.status,
                "application_id": tenant.application_id,
                "application_name": application_name,
                "metrics": tenant_metrics,
            }
        )

    return {
        "period_start": start.isoformat(),
        "generated_at": utcnow(),
        "tenant_count": len(tenant_rows),
        "over_limit_count": over_limit_count,
        "metrics": [
            {"metric": metric, "total": round(value, 4)} for metric, value in sorted(aggregate.items())
        ],
        "tenants": tenants_payload,
    }


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    application_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("plans.read")),
    db: AsyncSession = Depends(get_session),
) -> list[Plan]:
    stmt = select(Plan).order_by(Plan.sort_order, Plan.name)
    if application_id:
        stmt = stmt.where(Plan.application_id.in_([application_id, None]))
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


@router.post("/plans", response_model=PlanOut, status_code=201)
async def create_plan(
    payload: PlanCreate,
    request: Request,
    principal: Principal = Depends(require_platform("plans.manage")),
    db: AsyncSession = Depends(get_session),
) -> Plan:
    if payload.application_id:
        if await db.get(Application, payload.application_id) is None:
            raise NotFound("Application", str(payload.application_id))
    exists = (
        await db.execute(
            select(Plan.id).where(Plan.application_id == payload.application_id, Plan.key == payload.key)
        )
    ).scalar_one_or_none()
    if exists:
        raise Conflict("A plan with that key already exists for this application.")
    plan = Plan(
        application_id=payload.application_id,
        key=payload.key,
        name=payload.name,
        description=payload.description,
        price_monthly_cents=payload.price_monthly_cents,
        price_yearly_cents=payload.price_yearly_cents or payload.price_monthly_cents * 12,
        currency=payload.currency.upper(),
        limits=payload.limits,
        features=payload.features,
        is_public=payload.is_public,
        sort_order=payload.sort_order,
    )
    db.add(plan)
    await record_audit(
        db,
        action="plan.created",
        resource_type="plan",
        resource_id=plan.id,
        actor=principal,
        request=request,
        metadata={"key": plan.key, "monthly_cents": plan.price_monthly_cents},
    )
    await db.commit()
    return plan


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: uuid.UUID,
    payload: PlanUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("plans.manage")),
    db: AsyncSession = Depends(get_session),
) -> Plan:
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise NotFound("Plan", str(plan_id))
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is not None:
            setattr(plan, field, value)
    await record_audit(
        db,
        action="plan.updated",
        resource_type="plan",
        resource_id=plan.id,
        actor=principal,
        request=request,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return plan


@router.get("/subscriptions", response_model=dict)
async def list_subscriptions(
    params: PageParams = Depends(page_params),
    app_status: SubscriptionStatus | None = None,
    application_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("subscriptions.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = (
        select(Subscription, Tenant, Plan, Application)
        .join(Tenant, Tenant.id == Subscription.tenant_id)
        .join(Plan, Plan.id == Subscription.plan_id)
        .join(Application, Application.id == Tenant.application_id)
        .order_by(Subscription.created_at.desc())
    )
    if params.search:
        term = f"%{params.search.strip()}%"
        stmt = stmt.where(or_(Tenant.name.ilike(term), Tenant.slug.ilike(term)))
    if app_status:
        stmt = stmt.where(Subscription.status == app_status.value)
    if application_id:
        stmt = stmt.where(Tenant.application_id == application_id)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await db.execute(stmt.limit(params.limit).offset(params.offset))).all()
    items = [
        {
            "id": subscription.id,
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "tenant_slug": tenant.slug,
            "tenant_status": tenant.status,
            "application_id": application.id,
            "application_name": application.name,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "plan_key": plan.key,
            "status": subscription.status,
            "billing_interval": subscription.billing_interval,
            "mrr_cents": service.monthly_recurring_cents(subscription, plan),
            "current_period_start": subscription.current_period_start,
            "current_period_end": subscription.current_period_end,
            "trial_ends_at": subscription.trial_ends_at,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "discount_percent": subscription.discount_percent,
        }
        for subscription, tenant, plan, application in rows
    ]
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}


@router.patch("/subscriptions/{subscription_id}", response_model=dict)
async def change_subscription(
    subscription_id: uuid.UUID,
    payload: SubscriptionChange,
    request: Request,
    principal: Principal = Depends(require_platform("subscriptions.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    subscription = await db.get(Subscription, subscription_id)
    if subscription is None:
        raise NotFound("Subscription", str(subscription_id))
    tenant = await db.get(Tenant, subscription.tenant_id)
    if tenant is None:
        raise NotFound("Tenant", str(subscription.tenant_id))
    current_plan = await db.get(Plan, subscription.plan_id)
    changes: dict[str, Any] = {}

    if payload.plan_id and payload.plan_id != subscription.plan_id:
        new_plan = await db.get(Plan, payload.plan_id)
        if new_plan is None:
            raise NotFound("Plan", str(payload.plan_id))
        if new_plan.application_id not in (None, tenant.application_id):
            raise ValidationFailed("The plan is not available for this tenant's application.")
        subscription.plan_id = new_plan.id
        tenant.quotas = {k: v for k, v in (new_plan.limits or {}).items() if isinstance(v, int)}
        changes["plan"] = {"from": current_plan.key if current_plan else None, "to": new_plan.key}

    if payload.billing_interval:
        subscription.billing_interval = payload.billing_interval.value
        changes["billing_interval"] = payload.billing_interval.value

    if payload.discount_percent is not None:
        subscription.discount_percent = payload.discount_percent
        changes["discount_percent"] = payload.discount_percent

    if payload.cancel_at_period_end is not None:
        subscription.cancel_at_period_end = payload.cancel_at_period_end
        changes["cancel_at_period_end"] = payload.cancel_at_period_end

    if payload.extend_trial_days:
        if subscription.status not in (SubscriptionStatus.TRIALING.value,):
            raise ValidationFailed("Only trialing subscriptions can have their trial extended.")
        base = subscription.trial_ends_at or utcnow()
        subscription.trial_ends_at = base + timedelta(days=payload.extend_trial_days)
        subscription.current_period_end = max(subscription.current_period_end, subscription.trial_ends_at)
        changes["trial_extended_days"] = payload.extend_trial_days

    if payload.status:
        subscription.status = payload.status.value
        changes["status"] = payload.status.value
        if payload.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED):
            subscription.canceled_at = utcnow()
        await service.set_tenant_status_for_subscription(db, tenant, subscription)

    await record_audit(
        db,
        action="subscription.updated",
        resource_type="subscription",
        resource_id=subscription.id,
        actor=principal,
        request=request,
        scope_type="tenant",
        scope_id=tenant.id,
        metadata={**changes, "reason": payload.reason},
    )
    await db.commit()
    return {
        "id": subscription.id,
        "tenant_id": tenant.id,
        "plan_id": subscription.plan_id,
        "status": subscription.status,
        "current_period_end": subscription.current_period_end,
        "trial_ends_at": subscription.trial_ends_at,
        "changes": changes,
    }


@router.get("/invoices", response_model=dict)
async def list_invoices(
    params: PageParams = Depends(page_params),
    invoice_status: InvoiceStatus | None = None,
    _: Principal = Depends(require_platform("billing.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = (
        select(Invoice, Subscription, Tenant)
        .join(Subscription, Subscription.id == Invoice.subscription_id)
        .join(Tenant, Tenant.id == Subscription.tenant_id)
        .order_by(Invoice.issued_at.desc())
    )
    if invoice_status:
        stmt = stmt.where(Invoice.status == invoice_status.value)
    if params.search:
        term = f"%{params.search.strip()}%"
        stmt = stmt.where(or_(Invoice.number.ilike(term), Tenant.name.ilike(term)))
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await db.execute(stmt.limit(params.limit).offset(params.offset))).all()
    items = [
        {
            **InvoiceOut.model_validate(invoice).model_dump(),
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
        }
        for invoice, subscription, tenant in rows
    ]
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}


@router.post("/payments", response_model=dict, status_code=201)
async def record_payment(
    payload: PaymentRecord,
    request: Request,
    principal: Principal = Depends(require_platform("billing.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    invoice = await db.get(Invoice, payload.invoice_id)
    if invoice is None:
        raise NotFound("Invoice", str(payload.invoice_id))
    if payload.provider_payment_id:
        existing = (
            await db.execute(
                select(Payment).where(
                    Payment.provider == payload.provider,
                    Payment.provider_payment_id == payload.provider_payment_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return {"id": existing.id, "status": existing.status, "idempotent": True}
    payment = Payment(
        invoice_id=invoice.id,
        provider=payload.provider,
        provider_payment_id=payload.provider_payment_id,
        amount_cents=payload.amount_cents,
        currency=invoice.currency,
        status=payload.status.value,
        failure_reason=payload.failure_reason,
        processed_at=utcnow() if payload.status == PaymentStatus.SUCCEEDED else None,
        created_at=utcnow(),
    )
    db.add(payment)
    if payload.status == PaymentStatus.SUCCEEDED and payload.amount_cents >= invoice.amount_cents:
        invoice.status = InvoiceStatus.PAID.value
        invoice.paid_at = utcnow()
    await record_audit(
        db,
        action="payment.recorded",
        resource_type="invoice",
        resource_id=invoice.id,
        actor=principal,
        request=request,
        metadata={
            "provider": payload.provider,
            "amount_cents": payload.amount_cents,
            "status": payload.status.value,
        },
    )
    await db.commit()
    return {"id": payment.id, "status": payment.status, "invoice_status": invoice.status}
