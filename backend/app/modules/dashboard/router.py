from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session, platform_scope, utcnow
from app.core.deps import Principal, require_platform
from app.core.enums import (
    ApplicationStatus,
    IncidentStatus,
    OrganizationStatus,
    SubscriptionStatus,
    TenantStatus,
    UserStatus,
)
from app.modules.applications.models import Application
from app.modules.audit.models import AuditLog
from app.modules.billing.models import Invoice, Plan, Subscription
from app.modules.billing.service import monthly_recurring_cents
from app.modules.identity.models import User
from app.modules.infrastructure.models import Incident
from app.modules.organizations.models import Organization
from app.modules.tenants.models import Tenant

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def _month_starts(now: datetime, count: int = 6) -> list[datetime]:
    starts = []
    year, month = now.year, now.month
    for _ in range(count):
        starts.append(datetime(year, month, 1, tzinfo=UTC))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(starts))


async def _scalar(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one())


@router.get("/summary", response_model=dict, summary="Executive dashboard")
async def dashboard_summary(
    _: Principal = Depends(require_platform("dashboard.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    now = utcnow()
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    async with platform_scope(db):
        organizations = {
            "total": await _scalar(db, select(func.count()).select_from(Organization)),
            "active": await _scalar(
                db,
                select(func.count())
                .select_from(Organization)
                .where(Organization.status == OrganizationStatus.ACTIVE.value),
            ),
        }
        applications = {
            "total": await _scalar(db, select(func.count()).select_from(Application)),
            "active": await _scalar(
                db,
                select(func.count())
                .select_from(Application)
                .where(Application.status == ApplicationStatus.ACTIVE.value),
            ),
        }
        tenant_rows = (
            await db.execute(select(Tenant.status, func.count(Tenant.id)).group_by(Tenant.status))
        ).all()
        tenant_counts = {status: count for status, count in tenant_rows}
        tenants = {
            "total": sum(tenant_counts.values()),
            "active": tenant_counts.get(TenantStatus.ACTIVE.value, 0),
            "trial": tenant_counts.get(TenantStatus.TRIAL.value, 0),
            "suspended": tenant_counts.get(TenantStatus.SUSPENDED.value, 0),
            "provisioning": tenant_counts.get(TenantStatus.PROVISIONING.value, 0),
            "archived": tenant_counts.get(TenantStatus.ARCHIVED.value, 0),
            "new_this_month": await _scalar(
                db,
                select(func.count()).select_from(Tenant).where(Tenant.created_at >= month_start),
            ),
        }
        users = {
            "total": await _scalar(db, select(func.count()).select_from(User)),
            "internal": await _scalar(
                db,
                select(func.count()).select_from(User).where(User.user_type == "internal"),
            ),
            "external": await _scalar(
                db,
                select(func.count()).select_from(User).where(User.user_type == "external"),
            ),
            "active": await _scalar(
                db,
                select(func.count()).select_from(User).where(User.status == UserStatus.ACTIVE.value),
            ),
        }
        subscription_rows = (
            await db.execute(select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id))
        ).all()
        mrr = sum(monthly_recurring_cents(subscription, plan) for subscription, plan in subscription_rows)
        active_subs = sum(
            1
            for subscription, _ in subscription_rows
            if subscription.status in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value)
        )
        open_invoices = (
            await db.execute(
                select(func.count(), func.coalesce(func.sum(Invoice.amount_cents), 0)).where(
                    Invoice.status.in_(["open", "draft"])
                )
            )
        ).one()

        incidents = {
            "open": await _scalar(
                db,
                select(func.count())
                .select_from(Incident)
                .where(Incident.status != IncidentStatus.RESOLVED.value),
            ),
            "sev1": await _scalar(
                db,
                select(func.count())
                .select_from(Incident)
                .where(Incident.severity == "sev1", Incident.status != IncidentStatus.RESOLVED.value),
            ),
            "sev2": await _scalar(
                db,
                select(func.count())
                .select_from(Incident)
                .where(Incident.severity == "sev2", Incident.status != IncidentStatus.RESOLVED.value),
            ),
        }

        recent_audit = (
            (await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(12)))
            .scalars()
            .all()
        )
        recent_tenants = (
            await db.execute(
                select(Tenant, Application)
                .join(Application, Application.id == Tenant.application_id)
                .order_by(Tenant.created_at.desc())
                .limit(6)
            )
        ).all()

        month_starts = _month_starts(now)
        tenant_growth = []
        revenue_trend = []
        for index, start in enumerate(month_starts):
            end = month_starts[index + 1] if index + 1 < len(month_starts) else now
            tenant_growth.append(
                {
                    "month": start.strftime("%Y-%m"),
                    "tenants": await _scalar(
                        db,
                        select(func.count())
                        .select_from(Tenant)
                        .where(Tenant.created_at >= start, Tenant.created_at < end),
                    ),
                }
            )
            paid = (
                await db.execute(
                    select(func.coalesce(func.sum(Invoice.amount_cents), 0)).where(
                        Invoice.paid_at >= start, Invoice.paid_at < end
                    )
                )
            ).scalar_one()
            revenue_trend.append({"month": start.strftime("%Y-%m"), "revenue_cents": int(paid)})

        app_distribution = (
            await db.execute(
                select(Application.name, func.count(Tenant.id))
                .outerjoin(Tenant, Tenant.application_id == Application.id)
                .group_by(Application.id, Application.name)
                .order_by(func.count(Tenant.id).desc())
                .limit(6)
            )
        ).all()

        db_ok = (await db.execute(text("SELECT 1"))).scalar_one() == 1

    return {
        "generated_at": now,
        "organizations": organizations,
        "applications": applications,
        "tenants": tenants,
        "users": users,
        "billing": {
            "mrr_cents": mrr,
            "arr_cents": mrr * 12,
            "active_subscriptions": active_subs,
            "open_invoices": int(open_invoices[0]),
            "outstanding_cents": int(open_invoices[1]),
        },
        "incidents": incidents,
        "trends": {"tenant_growth": tenant_growth, "revenue": revenue_trend},
        "application_distribution": [{"name": name, "tenants": count} for name, count in app_distribution],
        "recent_audit": [
            {
                "id": entry.id,
                "action": entry.action,
                "actor_email": entry.actor_email,
                "resource_type": entry.resource_type,
                "resource_id": entry.resource_id,
                "scope_type": entry.scope_type,
                "scope_id": entry.scope_id,
                "outcome": entry.outcome,
                "created_at": entry.created_at,
            }
            for entry in recent_audit
        ],
        "recent_tenants": [
            {
                "id": tenant.id,
                "name": tenant.name,
                "slug": tenant.slug,
                "status": tenant.status,
                "application_name": application.name,
                "created_at": tenant.created_at,
            }
            for tenant, application in recent_tenants
        ],
        "system": {"database": "healthy" if db_ok else "degraded"},
    }


@router.get("/activity", response_model=list[dict], summary="Recent platform activity")
async def dashboard_activity(
    limit: int = 25,
    _: Principal = Depends(require_platform("dashboard.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    async with platform_scope(db):
        rows = (
            (
                await db.execute(
                    select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 100))
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": entry.id,
            "action": entry.action,
            "actor_email": entry.actor_email,
            "resource_type": entry.resource_type,
            "resource_id": entry.resource_id,
            "scope_type": entry.scope_type,
            "scope_id": entry.scope_id,
            "outcome": entry.outcome,
            "metadata": entry.metadata_json,
            "created_at": entry.created_at,
        }
        for entry in rows
    ]
