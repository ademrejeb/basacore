from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import get_session, slugify
from app.core.deps import Principal, require_platform
from app.core.errors import Conflict, NotFound
from app.core.pagination import PageParams, page_params, paginate
from app.modules.organizations.models import Organization
from app.modules.organizations.schemas import (
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
)

router = APIRouter(prefix="/organizations", tags=["Organizations"])


async def _unique_slug(db: AsyncSession, base: str) -> str:
    slug = slugify(base)
    candidate, n = slug, 1
    while (
        await db.execute(select(Organization.id).where(Organization.slug == candidate))
    ).scalar_one_or_none():
        n += 1
        candidate = f"{slug}-{n}"
    return candidate


@router.get("", response_model=dict, summary="List organizations")
async def list_organizations(
    params: PageParams = Depends(page_params),
    _: Principal = Depends(require_platform("organizations.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Organization).order_by(Organization.created_at.desc())
    if params.search:
        term = f"%{params.search.strip()}%"
        stmt = stmt.where(or_(Organization.name.ilike(term), Organization.slug.ilike(term)))
    rows, total = await paginate(db, stmt, params)

    # Counts are included for the list view so the console does not need a
    # follow-up request per client. Aggregating the visible page keeps the
    # query cost constant regardless of registry size.
    org_ids = [org.id for org in rows]
    application_counts: dict[uuid.UUID, int] = {}
    workspace_counts: dict[uuid.UUID, int] = {}
    user_counts: dict[uuid.UUID, int] = {}
    if org_ids:
        from app.modules.access.models import UserRole
        from app.modules.applications.models import Application
        from app.modules.tenants.models import Tenant

        application_counts = dict(
            (
                await db.execute(
                    select(Application.organization_id, func.count())
                    .where(Application.organization_id.in_(org_ids))
                    .group_by(Application.organization_id)
                )
            ).all()
        )
        workspace_counts = dict(
            (
                await db.execute(
                    select(Tenant.organization_id, func.count())
                    .where(Tenant.organization_id.in_(org_ids))
                    .group_by(Tenant.organization_id)
                )
            ).all()
        )
        user_counts = dict(
            (
                await db.execute(
                    select(UserRole.scope_id, func.count(func.distinct(UserRole.user_id)))
                    .where(UserRole.scope_type == "organization", UserRole.scope_id.in_(org_ids))
                    .group_by(UserRole.scope_id)
                )
            ).all()
        )

    items = []
    for organization in rows:
        items.append(
            {
                **OrganizationOut.model_validate(organization).model_dump(),
                "counts": {
                    "applications": application_counts.get(organization.id, 0),
                    "tenants": workspace_counts.get(organization.id, 0),
                    "users": user_counts.get(organization.id, 0),
                },
            }
        )
    return {
        "items": items,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@router.post("", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: OrganizationCreate,
    request: Request,
    principal: Principal = Depends(require_platform("organizations.create")),
    db: AsyncSession = Depends(get_session),
) -> Organization:
    slug = payload.slug or await _unique_slug(db, payload.name)
    if (await db.execute(select(Organization.id).where(Organization.slug == slug))).scalar_one_or_none():
        raise Conflict("An organization with that slug already exists.")
    org = Organization(
        name=payload.name.strip(),
        slug=slug,
        description=payload.description,
        billing_email=str(payload.billing_email) if payload.billing_email else None,
    )
    db.add(org)
    await db.flush()
    await record_audit(
        db,
        action="organization.created",
        resource_type="organization",
        resource_id=org.id,
        actor=principal,
        request=request,
        scope_type="organization",
        scope_id=org.id,
        metadata={"name": org.name, "slug": org.slug},
    )
    await db.commit()
    return org


@router.get("/{organization_id}", response_model=dict)
async def get_organization(
    organization_id: uuid.UUID,
    _: Principal = Depends(require_platform("organizations.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    org = await db.get(Organization, organization_id)
    if org is None:
        raise NotFound("Organization", str(organization_id))

    from app.modules.access.models import UserRole
    from app.modules.applications.models import Application
    from app.modules.tenants.models import Tenant

    app_count = (
        await db.execute(
            select(func.count()).select_from(Application).where(Application.organization_id == org.id)
        )
    ).scalar_one()
    tenant_count = (
        await db.execute(select(func.count()).select_from(Tenant).where(Tenant.organization_id == org.id))
    ).scalar_one()
    member_count = (
        await db.execute(
            select(func.count(func.distinct(Application.owner_id))).where(
                Application.organization_id == org.id, Application.owner_id.is_not(None)
            )
        )
    ).scalar_one()
    org_user_count = (
        await db.execute(
            select(func.count(func.distinct(UserRole.user_id))).where(
                UserRole.scope_type == "organization", UserRole.scope_id == org.id
            )
        )
    ).scalar_one()
    return {
        **OrganizationOut.model_validate(org).model_dump(),
        "counts": {
            "applications": app_count,
            "tenants": tenant_count,
            "application_owners": member_count,
            "users": org_user_count,
        },
    }


@router.patch("/{organization_id}", response_model=OrganizationOut)
async def update_organization(
    organization_id: uuid.UUID,
    payload: OrganizationUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("organizations.update")),
    db: AsyncSession = Depends(get_session),
) -> Organization:
    org = await db.get(Organization, organization_id)
    if org is None:
        raise NotFound("Organization", str(organization_id))
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is not None:
            setattr(org, field, str(value) if field == "billing_email" else value)
    await record_audit(
        db,
        action="organization.updated",
        resource_type="organization",
        resource_id=org.id,
        actor=principal,
        request=request,
        scope_type="organization",
        scope_id=org.id,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return org


@router.get("/{organization_id}/applications", response_model=list[dict])
async def organization_applications(
    organization_id: uuid.UUID,
    _: Principal = Depends(require_platform("applications.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    from app.modules.applications.models import Application

    rows = (
        (
            await db.execute(
                select(Application)
                .where(Application.organization_id == organization_id)
                .order_by(Application.name)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": a.id,
            "name": a.name,
            "slug": a.slug,
            "status": a.status,
            "base_url": a.base_url,
        }
        for a in rows
    ]
