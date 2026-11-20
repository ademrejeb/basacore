from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import get_session, platform_scope, utcnow
from app.core.deps import ServicePrincipal, require_scope
from app.core.enums import AuditOutcome, DomainStatus, MembershipStatus
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.applications import service
from app.modules.applications.models import Application
from app.modules.billing.models import UsageRecord
from app.modules.billing.service import record_usage
from app.modules.identity.models import User
from app.modules.tenants.models import Tenant, TenantDomain, TenantMembership

router = APIRouter(prefix="/app", tags=["Integration API"])


class UsageReportIn(BaseModel):
    tenant_id: uuid.UUID
    metric: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9._-]+$")
    value: float = Field(gt=0, le=1_000_000_000)
    idempotency_key: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthorizationCheckIn(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    permission: str = Field(min_length=3, max_length=120)


class AuditWriteIn(BaseModel):
    action: str = Field(min_length=2, max_length=120)
    resource_type: str = Field(min_length=2, max_length=80)
    resource_id: str | None = Field(default=None, max_length=64)
    tenant_id: uuid.UUID | None = None
    outcome: AuditOutcome = AuditOutcome.SUCCESS
    metadata: dict[str, Any] = Field(default_factory=dict)


class TenantLookupOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    isolation_strategy: str
    branding: dict[str, Any]
    quotas: dict[str, Any]


async def _tenant_or_404(db: AsyncSession, application_id: uuid.UUID, tenant_id: uuid.UUID) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.application_id != application_id or tenant.deleted_at is not None:
        raise NotFound("Tenant", str(tenant_id))
    return tenant


def _tenant_payload(tenant: Tenant) -> dict[str, Any]:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "status": tenant.status,
        "isolation_strategy": tenant.isolation_strategy,
        "branding": tenant.branding,
        "quotas": tenant.quotas,
    }


@router.get("/me", summary="Service identity and granted scopes")
async def service_identity(
    principal: ServicePrincipal = Depends(require_scope("app:identity.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await db.get(Application, principal.application_id)
    return {
        "application": {
            "id": app.id,
            "name": app.name,
            "slug": app.slug,
            "status": app.status,
        }
        if app
        else None,
        "credential_id": principal.credential_id,
        "scopes": principal.scopes,
    }


@router.get("/config", summary="Effective tenant configuration, flags and plan limits")
async def tenant_config(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    principal: ServicePrincipal = Depends(require_scope("app:config.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with platform_scope(db):
        tenant = await _tenant_or_404(db, principal.application_id, tenant_id)
        return await service.build_context(db, tenant, environment_id)


@router.get("/tenants", summary="List tenants belonging to the calling application")
async def list_tenants(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
    principal: ServicePrincipal = Depends(require_scope("app:config.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with platform_scope(db):
        stmt = select(Tenant).where(
            Tenant.application_id == principal.application_id, Tenant.deleted_at.is_(None)
        )
        if status_filter:
            stmt = stmt.where(Tenant.status == status_filter)
        rows = (
            (await db.execute(stmt.order_by(Tenant.created_at.desc()).limit(limit).offset(offset)))
            .scalars()
            .all()
        )
    return {"items": [_tenant_payload(t) for t in rows], "limit": limit, "offset": offset}


@router.get("/tenants/resolve", response_model=dict, summary="Resolve a tenant by slug or domain")
async def resolve_tenant(
    slug: str | None = None,
    domain: str | None = None,
    principal: ServicePrincipal = Depends(require_scope("app:tenant.resolve")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if not slug and not domain:
        raise ValidationFailed("Provide either slug or domain.")
    async with platform_scope(db):
        if slug:
            tenant = (
                await db.execute(
                    select(Tenant).where(
                        Tenant.application_id == principal.application_id,
                        Tenant.slug == slug.strip().lower(),
                        Tenant.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
        else:
            tenant = (
                await db.execute(
                    select(Tenant)
                    .join(TenantDomain, TenantDomain.tenant_id == Tenant.id)
                    .where(
                        Tenant.application_id == principal.application_id,
                        TenantDomain.domain == (domain or "").strip().lower().rstrip("."),
                        # Unverified domains must never route traffic: anyone can
                        # add a hostname they do not own to their own tenant.
                        TenantDomain.status == DomainStatus.VERIFIED.value,
                        Tenant.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
    if tenant is None:
        raise NotFound("Tenant")
    return _tenant_payload(tenant)


@router.post("/authorization/check", summary="Check whether a user may act in a tenant")
async def authorization_check(
    payload: AuthorizationCheckIn,
    principal: ServicePrincipal = Depends(require_scope("app:authorization.check")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with platform_scope(db):
        tenant = await _tenant_or_404(db, principal.application_id, payload.tenant_id)
        user = await db.get(User, payload.user_id)
        if user is None or user.status != "active":
            return {
                "authorized": False,
                "reason": "user_not_active",
                "user_id": payload.user_id,
                "tenant_id": tenant.id,
                "permission": payload.permission,
            }
        if user.user_type == "external":
            membership = (
                await db.execute(
                    select(TenantMembership.id).where(
                        TenantMembership.tenant_id == tenant.id,
                        TenantMembership.user_id == user.id,
                        TenantMembership.status == MembershipStatus.ACTIVE.value,
                    )
                )
            ).scalar_one_or_none()
            if membership is None:
                return {
                    "authorized": False,
                    "reason": "not_a_member",
                    "user_id": user.id,
                    "tenant_id": tenant.id,
                    "permission": payload.permission,
                }
        scopes = (
            ("platform", None),
            ("organization", tenant.organization_id),
            ("application", tenant.application_id),
            ("tenant", tenant.id),
        )
        conditions = []
        params: dict[str, Any] = {"user_id": user.id}
        for index, (scope_type, scope_id) in enumerate(scopes):
            params[f"scope_{index}"] = scope_id
            conditions.append(
                f"(ur.scope_type = :scope_type_{index} AND "
                + (f"ur.scope_id = :scope_{index})" if scope_id else "ur.scope_id IS NULL)")
            )
            params[f"scope_type_{index}"] = scope_type
        from sqlalchemy import text

        rows = (
            (
                await db.execute(
                    text(
                        f"""
                    SELECT DISTINCT rp.permission_key
                    FROM user_roles ur
                    JOIN roles r ON r.id = ur.role_id
                    JOIN role_permissions rp ON rp.role_id = r.id
                    WHERE ur.user_id = :user_id
                      AND (ur.expires_at IS NULL OR ur.expires_at > now())
                      AND ({" OR ".join(conditions)})
                    """
                    ),
                    params,
                )
            )
            .scalars()
            .all()
        )
        permissions = set(rows)
    authorized = "*" in permissions or payload.permission in permissions
    return {
        "authorized": authorized,
        "reason": "allowed" if authorized else "missing_permission",
        "user_id": payload.user_id,
        "tenant_id": tenant.id,
        "permission": payload.permission,
        "effective_permissions": sorted(permissions) if authorized else [],
    }


@router.post("/usage", status_code=status.HTTP_201_CREATED, summary="Report metered usage")
async def report_usage(
    payload: UsageReportIn,
    request: Request,
    principal: ServicePrincipal = Depends(require_scope("app:usage.write")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with platform_scope(db):
        tenant = await _tenant_or_404(db, principal.application_id, payload.tenant_id)
        if payload.idempotency_key:
            existing = (
                await db.execute(
                    select(UsageRecord).where(
                        UsageRecord.application_id == principal.application_id,
                        UsageRecord.idempotency_key == payload.idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                if existing.tenant_id != tenant.id:
                    raise Conflict("That idempotency key belongs to a different tenant.")
                return {
                    "id": existing.id,
                    "metric": existing.metric,
                    "quantity": float(existing.quantity),
                    "period_start": existing.period_start,
                    "idempotent": True,
                }
        record = await record_usage(
            db,
            tenant=tenant,
            metric=payload.metric,
            quantity=Decimal(str(payload.value)),
            source="service",
            idempotency_key=payload.idempotency_key,
            metadata={**payload.metadata, "application_id": str(principal.application_id)},
        )
        await record_audit(
            db,
            action="usage.reported",
            resource_type="usage_record",
            resource_id=record.id,
            actor_email=f"service:{principal.credential_id}",
            scope_type="tenant",
            scope_id=tenant.id,
            request=request,
            metadata={"metric": payload.metric, "value": payload.value},
        )
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise Conflict("This usage report has already been recorded.") from exc
        return {
            "id": record.id,
            "metric": record.metric,
            "quantity": float(record.quantity),
            "period_start": record.period_start,
            "idempotent": False,
        }


@router.post("/audit", status_code=status.HTTP_201_CREATED, summary="Append an audit event")
async def write_audit(
    payload: AuditWriteIn,
    request: Request,
    principal: ServicePrincipal = Depends(require_scope("app:audit.write")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with platform_scope(db):
        tenant = (
            await _tenant_or_404(db, principal.application_id, payload.tenant_id)
            if payload.tenant_id
            else None
        )
        entry = await record_audit(
            db,
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            actor_email=f"service:{principal.credential_id}",
            scope_type="tenant" if tenant else "application",
            scope_id=tenant.id if tenant else principal.application_id,
            outcome=payload.outcome,
            request=request,
            metadata={**payload.metadata, "application_id": str(principal.application_id)},
        )
        await db.commit()
        return {"id": entry.id, "created_at": entry.created_at}


@router.get("/usage/summary", summary="Current-period usage for one tenant")
async def usage_summary(
    tenant_id: uuid.UUID,
    principal: ServicePrincipal = Depends(require_scope("app:config.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.modules.billing.service import usage_summary as summarize

    async with platform_scope(db):
        tenant = await _tenant_or_404(db, principal.application_id, tenant_id)
        return {
            "tenant_id": tenant.id,
            "period_start": date.today().replace(day=1).isoformat(),
            "generated_at": utcnow(),
            "metrics": await summarize(db, tenant),
        }
