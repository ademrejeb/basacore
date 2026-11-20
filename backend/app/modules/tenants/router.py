from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import get_settings
from app.core.db import get_session, platform_scope, utcnow
from app.core.deps import Principal, require_platform
from app.core.enums import (
    AuditOutcome,
    DomainStatus,
    MembershipStatus,
    ScopeType,
    SubscriptionStatus,
    TenantStatus,
    UserStatus,
    UserType,
)
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.pagination import PageParams, page_params, paginate
from app.core.permissions import exceeded_grants, permissions_for_role_key
from app.core.security import generate_token, hash_token
from app.modules.access.models import Role, UserRole
from app.modules.applications.models import Application, FeatureFlag, FeatureFlagOverride
from app.modules.audit.models import AuditLog
from app.modules.billing.models import Invoice, Plan, Subscription
from app.modules.billing.service import monthly_recurring_cents, usage_summary
from app.modules.identity.models import AuthenticationEvent, User, VerificationToken
from app.modules.organizations.models import Organization
from app.modules.tenants import service
from app.modules.tenants.models import (
    Tenant,
    TenantConfiguration,
    TenantDomain,
    TenantMembership,
)
from app.modules.tenants.schemas import (
    TenantConfigPublish,
    TenantCreate,
    TenantDomainCreate,
    TenantOut,
    TenantStatusChange,
    TenantTransfer,
    TenantUpdate,
    TenantUserInvite,
    TenantUserUpdate,
)

settings = get_settings()
router = APIRouter(prefix="/tenants", tags=["Tenants"])


class TenantDelete(BaseModel):
    confirm_name: str
    reason: str = Field(min_length=5, max_length=300)


async def _detail(db: AsyncSession, tenant: Tenant, principal: Principal) -> dict[str, Any]:
    app = await db.get(Application, tenant.application_id)
    subscription = (
        await db.execute(select(Subscription).where(Subscription.tenant_id == tenant.id))
    ).scalar_one_or_none()
    plan = await db.get(Plan, subscription.plan_id) if subscription else None
    domains = (
        (
            await db.execute(
                select(TenantDomain)
                .where(TenantDomain.tenant_id == tenant.id)
                .order_by(TenantDomain.created_at)
            )
        )
        .scalars()
        .all()
    )

    can_users = await principal.has(
        db,
        "tenant.users.read",
        organization_id=tenant.organization_id,
        application_id=tenant.application_id,
        tenant_id=tenant.id,
    )
    can_billing = await principal.has(
        db,
        "tenant.billing.read",
        organization_id=tenant.organization_id,
        application_id=tenant.application_id,
        tenant_id=tenant.id,
    )
    can_config = await principal.has(
        db,
        "tenant.config.read",
        organization_id=tenant.organization_id,
        application_id=tenant.application_id,
        tenant_id=tenant.id,
    )
    can_audit = await principal.has(
        db,
        "tenant.audit.read",
        organization_id=tenant.organization_id,
        application_id=tenant.application_id,
        tenant_id=tenant.id,
    )

    payload: dict[str, Any] = {
        **TenantOut.model_validate(tenant).model_dump(),
        "application_name": app.name if app else None,
        "domains": [
            {
                "id": d.id,
                "domain": d.domain,
                "is_primary": d.is_primary,
                "status": d.status,
                "verification_token": d.verification_token,
                "ssl_status": d.ssl_status,
                "ssl_expires_at": d.ssl_expires_at,
                "verified_at": d.verified_at,
            }
            for d in domains
        ],
        "counts": {},
    }

    member_count = (
        await db.execute(
            select(func.count()).select_from(TenantMembership).where(TenantMembership.tenant_id == tenant.id)
        )
    ).scalar_one()
    payload["counts"]["users"] = member_count

    if can_users:
        memberships = (
            await db.execute(
                select(TenantMembership, User)
                .join(User, User.id == TenantMembership.user_id)
                .where(TenantMembership.tenant_id == tenant.id)
                .order_by(User.full_name)
            )
        ).all()
        member_ids = [user.id for _, user in memberships]
        role_rows = (
            await db.execute(
                select(UserRole.user_id, Role.key, Role.name)
                .join(Role, UserRole.role_id == Role.id)
                .where(
                    UserRole.user_id.in_(member_ids or [uuid.uuid4()]),
                    UserRole.scope_type == ScopeType.TENANT.value,
                    UserRole.scope_id == tenant.id,
                )
            )
        ).all()
        roles_by_user: dict[uuid.UUID, list[tuple[str, str]]] = {}
        for user_id, role_key, role_name in role_rows:
            roles_by_user.setdefault(user_id, []).append((role_key, role_name))
        users = []
        for membership, user in memberships:
            member_roles = roles_by_user.get(user.id, [])
            users.append(
                {
                    "id": user.id,
                    "email": user.email,
                    "full_name": user.full_name,
                    "status": user.status,
                    "membership_status": membership.status,
                    "is_billing_contact": membership.is_billing_contact,
                    "mfa_enabled": user.mfa_enabled,
                    "roles": [key for key, _ in member_roles],
                    "role_names": [name for _, name in member_roles],
                    "last_login_at": user.last_login_at,
                    "joined_at": membership.created_at,
                }
            )
        payload["users"] = users

    if can_billing:
        payload["subscription"] = (
            {
                "id": subscription.id,
                "status": subscription.status,
                "billing_interval": subscription.billing_interval,
                "plan_id": subscription.plan_id,
                "plan_name": plan.name if plan else None,
                "plan_key": plan.key if plan else None,
                "mrr_cents": (monthly_recurring_cents(subscription, plan) if plan else 0),
                "current_period_start": subscription.current_period_start,
                "current_period_end": subscription.current_period_end,
                "trial_ends_at": subscription.trial_ends_at,
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "discount_percent": subscription.discount_percent,
            }
            if subscription
            else None
        )
        payload["usage"] = await usage_summary(db, tenant)
        invoices = (
            (
                await db.execute(
                    select(Invoice)
                    .join(Subscription, Subscription.id == Invoice.subscription_id)
                    .where(Subscription.tenant_id == tenant.id)
                    .order_by(Invoice.issued_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        payload["invoices"] = [
            {
                "id": i.id,
                "number": i.number,
                "amount_cents": i.amount_cents,
                "currency": i.currency,
                "status": i.status,
                "issued_at": i.issued_at,
                "due_at": i.due_at,
                "paid_at": i.paid_at,
            }
            for i in invoices
        ]

    if can_config:
        configs = (
            (
                await db.execute(
                    select(TenantConfiguration)
                    .where(TenantConfiguration.tenant_id == tenant.id)
                    .order_by(TenantConfiguration.version.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        payload["config_history"] = [
            {
                "id": c.id,
                "version": c.version,
                "config": c.config,
                "note": c.note,
                "created_at": c.created_at,
            }
            for c in configs
        ]
        flags = (
            (await db.execute(select(FeatureFlag).where(FeatureFlag.application_id == tenant.application_id)))
            .scalars()
            .all()
        )
        overrides = (
            (
                await db.execute(
                    select(FeatureFlagOverride).where(
                        FeatureFlagOverride.tenant_id == tenant.id,
                        FeatureFlagOverride.flag_id.in_([f.id for f in flags] or [uuid.uuid4()]),
                    )
                )
            )
            .scalars()
            .all()
        )
        override_map = {o.flag_id: o.enabled for o in overrides}
        payload["features"] = [
            {
                "id": f.id,
                "key": f.key,
                "name": f.name,
                "enabled": f.enabled,
                "rollout_percentage": f.rollout_percentage,
                "override": override_map.get(f.id),
            }
            for f in flags
        ]

    if can_audit:
        logs = (
            (
                await db.execute(
                    select(AuditLog)
                    .where(
                        or_(
                            AuditLog.scope_id == tenant.id,
                            AuditLog.resource_id == str(tenant.id),
                        )
                    )
                    .order_by(AuditLog.created_at.desc())
                    .limit(25)
                )
            )
            .scalars()
            .all()
        )
        payload["activity"] = [
            {
                "id": log.id,
                "action": log.action,
                "actor_email": log.actor_email,
                "outcome": log.outcome,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "created_at": log.created_at,
                "metadata": log.metadata_json,
            }
            for log in logs
        ]

    return payload


@router.get("", response_model=dict, summary="List tenants across all applications")
async def list_tenants(
    params: PageParams = Depends(page_params),
    application_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    tenant_status: TenantStatus | None = None,
    _: Principal = Depends(require_platform("tenants.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with platform_scope(db):
        stmt = (
            select(Tenant, Application, Subscription, Plan, Organization)
            .join(Application, Application.id == Tenant.application_id)
            .join(Organization, Organization.id == Tenant.organization_id)
            .outerjoin(Subscription, Subscription.tenant_id == Tenant.id)
            .outerjoin(Plan, Plan.id == Subscription.plan_id)
            .order_by(Tenant.created_at.desc())
        )
        if params.search:
            term = f"%{params.search.strip()}%"
            stmt = stmt.where(or_(Tenant.name.ilike(term), Tenant.slug.ilike(term)))
        if application_id:
            stmt = stmt.where(Tenant.application_id == application_id)
        if organization_id:
            stmt = stmt.where(Tenant.organization_id == organization_id)
        if tenant_status:
            stmt = stmt.where(Tenant.status == tenant_status.value)
        total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
        rows = (await db.execute(stmt.limit(params.limit).offset(params.offset))).all()
        member_counts = dict(
            (
                await db.execute(
                    select(TenantMembership.tenant_id, func.count()).group_by(TenantMembership.tenant_id)
                )
            ).all()
        )
    items = []
    for tenant, app, subscription, plan, organization in rows:
        items.append(
            {
                **TenantOut.model_validate(tenant).model_dump(),
                "application_name": app.name,
                "organization_name": organization.name,
                "plan_name": plan.name if plan else None,
                "subscription_status": subscription.status if subscription else None,
                "mrr_cents": (monthly_recurring_cents(subscription, plan) if plan and subscription else 0),
                "users": member_counts.get(tenant.id, 0),
            }
        )
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreate,
    request: Request,
    principal: Principal = Depends(require_platform("tenants.create")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await db.get(Application, payload.application_id)
    if app is None:
        raise NotFound("Application", str(payload.application_id))
    from app.modules.organizations.models import Organization

    org = await db.get(Organization, payload.organization_id)
    if org is None:
        raise NotFound("Organization", str(payload.organization_id))

    plan = None
    if payload.plan_id:
        plan = await db.get(Plan, payload.plan_id)
        if plan is None:
            raise NotFound("Plan", str(payload.plan_id))
        if plan.application_id not in (None, app.id):
            raise ValidationFailed("The plan does not belong to this application.")

    owner: User | None = None
    if payload.owner_id:
        owner = await db.get(User, payload.owner_id)
        if owner is None:
            raise NotFound("User", str(payload.owner_id))
    elif payload.owner_email:
        owner = (
            await db.execute(select(User).where(User.email == payload.owner_email.lower()))
        ).scalar_one_or_none()

    invite_token: str | None = None
    invited_new_user = False
    if owner is None and payload.owner_email:
        invited_new_user = True
        owner = User(
            email=payload.owner_email.lower(),
            full_name=payload.owner_email.split("@")[0].replace(".", " ").title(),
            user_type=UserType.EXTERNAL.value,
            status=UserStatus.INVITED.value,
            password_hash=None,
        )
        db.add(owner)
        await db.flush()

    slug = payload.slug or await service.unique_tenant_slug(db, app.id, payload.name)
    exists = (
        await db.execute(select(Tenant.id).where(Tenant.application_id == app.id, Tenant.slug == slug))
    ).scalar_one_or_none()
    if exists:
        raise Conflict("A tenant with that slug already exists for this application.")

    isolation = payload.isolation_strategy.value if payload.isolation_strategy else app.isolation_strategy
    quotas = dict(payload.quotas)
    if not quotas and plan:
        quotas = {k: v for k, v in (plan.limits or {}).items() if isinstance(v, int)}

    async with platform_scope(db):
        tenant, subscription = await service.provision_tenant(
            db,
            application_id=app.id,
            organization_id=org.id,
            name=payload.name,
            slug=slug,
            owner=owner,
            plan=plan,
            billing_interval=payload.billing_interval.value,
            trial_days=payload.trial_days,
            isolation_strategy=isolation,
            quotas=quotas,
            configuration=payload.configuration,
            branding=payload.branding,
            created_by=principal.user.id,
        )
        if payload.custom_domain:
            existing_domain = (
                await db.execute(select(TenantDomain.id).where(TenantDomain.domain == payload.custom_domain))
            ).scalar_one_or_none()
            if existing_domain:
                raise Conflict("That domain is already attached to a tenant.")
            db.add(
                TenantDomain(
                    tenant_id=tenant.id,
                    domain=payload.custom_domain,
                    is_primary=True,
                    status=DomainStatus.PENDING.value,
                    verification_token=f"basa-verify={generate_token(18)}",
                    ssl_status="pending",
                    created_at=utcnow(),
                )
            )

        if owner and invited_new_user:
            invite_token = generate_token(32)
            db.add(
                VerificationToken(
                    user_id=owner.id,
                    purpose="invite",
                    token_hash=hash_token(invite_token),
                    expires_at=utcnow() + timedelta(days=7),
                    created_at=utcnow(),
                )
            )

        await record_audit(
            db,
            action="tenant.created",
            resource_type="tenant",
            resource_id=tenant.id,
            actor=principal,
            request=request,
            scope_type="tenant",
            scope_id=tenant.id,
            metadata={
                "application_id": str(app.id),
                "plan_id": str(plan.id) if plan else None,
                "isolation": isolation,
                "owner_id": str(owner.id) if owner else None,
            },
        )
        await db.commit()

    invite_email_sent = False
    if invite_token and owner is not None:
        invite_email_sent = await service.send_invite_email(recipient=owner.email, token=invite_token)

    async with platform_scope(db):
        detail = await _detail(db, tenant, principal)
    detail["invite_email_sent"] = invite_email_sent
    # Outside production the token is also returned so local workflows can be
    # exercised without a mail server. It never belongs in a production body.
    if invite_token and not settings.is_production:
        detail["invite_token"] = invite_token
        detail["invite_note"] = "Share this single-use invite token with the tenant administrator."
    return detail


@router.get("/{tenant_id}", response_model=dict, summary="Tenant detail (permission-filtered)")
async def get_tenant(
    access: service.TenantAccess = Depends(service.require_tenant_access("tenants.read")),
) -> dict[str, Any]:
    return await _detail(access.db, access.tenant, access.principal)


@router.patch("/{tenant_id}", response_model=dict)
async def update_tenant(
    payload: TenantUpdate,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenants.update")),
) -> dict[str, Any]:
    tenant = access.tenant
    changes = payload.model_dump(exclude_unset=True)
    if "isolation_strategy" in changes and changes["isolation_strategy"]:
        value = changes["isolation_strategy"].value
        if value not in service.ENABLED_ISOLATION_STRATEGIES:
            raise ValidationFailed("This isolation strategy is not enabled in this deployment.")
        tenant.isolation_strategy = value
    for field in ("name", "quotas", "configuration", "branding"):
        if field in changes and changes[field] is not None:
            setattr(tenant, field, changes[field])
    await record_audit(
        access.db,
        action="tenant.updated",
        resource_type="tenant",
        resource_id=tenant.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=tenant.id,
        metadata={"fields": sorted(changes)},
    )
    await access.db.commit()
    return await _detail(access.db, tenant, access.principal)


@router.post("/{tenant_id}/suspend", response_model=dict)
async def suspend_tenant(
    payload: TenantStatusChange,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenants.suspend")),
) -> dict[str, Any]:
    tenant = access.tenant
    if tenant.status == TenantStatus.SUSPENDED.value:
        return {"id": tenant.id, "status": tenant.status}
    tenant.status = TenantStatus.SUSPENDED.value
    tenant.suspended_at = utcnow()
    await record_audit(
        access.db,
        action="tenant.suspended",
        resource_type="tenant",
        resource_id=tenant.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=tenant.id,
        metadata={"reason": payload.reason},
    )
    await access.db.commit()
    return {"id": tenant.id, "status": tenant.status, "suspended_at": tenant.suspended_at}


@router.post("/{tenant_id}/reactivate", response_model=dict)
async def reactivate_tenant(
    payload: TenantStatusChange,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenants.suspend")),
) -> dict[str, Any]:
    tenant = access.tenant
    subscription = (
        await access.db.execute(select(Subscription).where(Subscription.tenant_id == tenant.id))
    ).scalar_one_or_none()
    if subscription and subscription.status == SubscriptionStatus.TRIALING.value:
        tenant.status = TenantStatus.TRIAL.value
    elif subscription and subscription.status in (
        SubscriptionStatus.PAST_DUE.value,
        SubscriptionStatus.PAUSED.value,
    ):
        raise ValidationFailed("The subscription must be brought current before reactivation.")
    else:
        tenant.status = TenantStatus.ACTIVE.value
    tenant.suspended_at = None
    await record_audit(
        access.db,
        action="tenant.reactivated",
        resource_type="tenant",
        resource_id=tenant.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=tenant.id,
        metadata={"reason": payload.reason},
    )
    await access.db.commit()
    return {"id": tenant.id, "status": tenant.status}


@router.post("/{tenant_id}/archive", response_model=dict)
async def archive_tenant(
    payload: TenantStatusChange,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenants.delete")),
) -> dict[str, Any]:
    tenant = access.tenant
    tenant.status = TenantStatus.ARCHIVED.value
    tenant.archived_at = utcnow()
    await record_audit(
        access.db,
        action="tenant.archived",
        resource_type="tenant",
        resource_id=tenant.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=tenant.id,
        metadata={"reason": payload.reason},
    )
    await access.db.commit()
    return {"id": tenant.id, "status": tenant.status}


@router.post("/{tenant_id}/delete", response_model=dict)
async def delete_tenant(
    payload: TenantDelete,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenants.delete")),
) -> dict[str, Any]:
    tenant = access.tenant
    if payload.confirm_name.strip() != tenant.name:
        raise ValidationFailed("confirm_name must exactly match the tenant name.")
    tenant.status = TenantStatus.DELETED.value
    tenant.deleted_at = utcnow()
    tenant.archived_at = tenant.archived_at or utcnow()
    await record_audit(
        access.db,
        action="tenant.deleted",
        resource_type="tenant",
        resource_id=tenant.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=tenant.id,
        metadata={"reason": payload.reason, "name": tenant.name},
    )
    await access.db.commit()
    return {"id": tenant.id, "status": tenant.status, "deleted_at": tenant.deleted_at}


@router.post("/{tenant_id}/transfer", response_model=dict)
async def transfer_tenant(
    payload: TenantTransfer,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenants.transfer")),
) -> dict[str, Any]:
    tenant = access.tenant
    new_owner = await access.db.get(User, payload.new_owner_id)
    if new_owner is None:
        raise NotFound("User", str(payload.new_owner_id))
    membership = (
        await access.db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == new_owner.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise ValidationFailed("The new owner must be a member of the tenant.")

    owner_role = (await access.db.execute(select(Role).where(Role.key == "tenant_owner"))).scalar_one()
    admin_role = (await access.db.execute(select(Role).where(Role.key == "tenant_admin"))).scalar_one()
    if tenant.owner_id and tenant.owner_id != new_owner.id:
        old_assignment = (
            await access.db.execute(
                select(UserRole).where(
                    UserRole.user_id == tenant.owner_id,
                    UserRole.role_id == owner_role.id,
                    UserRole.scope_type == ScopeType.TENANT.value,
                    UserRole.scope_id == tenant.id,
                )
            )
        ).scalar_one_or_none()
        if old_assignment:
            await access.db.delete(old_assignment)
            access.db.add(
                UserRole(
                    user_id=tenant.owner_id,
                    role_id=admin_role.id,
                    scope_type=ScopeType.TENANT.value,
                    scope_id=tenant.id,
                    granted_by=access.principal.user.id,
                    created_at=utcnow(),
                )
            )
    assigned = (
        await access.db.execute(
            select(UserRole).where(
                UserRole.user_id == new_owner.id,
                UserRole.role_id == owner_role.id,
                UserRole.scope_type == ScopeType.TENANT.value,
                UserRole.scope_id == tenant.id,
            )
        )
    ).scalar_one_or_none()
    if assigned is None:
        access.db.add(
            UserRole(
                user_id=new_owner.id,
                role_id=owner_role.id,
                scope_type=ScopeType.TENANT.value,
                scope_id=tenant.id,
                granted_by=access.principal.user.id,
                created_at=utcnow(),
            )
        )
    membership.status = MembershipStatus.ACTIVE.value
    tenant.owner_id = new_owner.id
    await record_audit(
        access.db,
        action="tenant.ownership_transferred",
        resource_type="tenant",
        resource_id=tenant.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=tenant.id,
        metadata={"new_owner_id": str(new_owner.id), "reason": payload.reason},
    )
    await access.db.commit()
    return {"id": tenant.id, "owner_id": tenant.owner_id}


@router.get("/{tenant_id}/users", response_model=list[dict])
async def list_tenant_users(
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.users.read")),
) -> list[dict[str, Any]]:
    rows = (
        await access.db.execute(
            select(TenantMembership, User)
            .join(User, User.id == TenantMembership.user_id)
            .where(TenantMembership.tenant_id == access.tenant.id)
            .order_by(User.full_name)
        )
    ).all()
    return [
        {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "status": user.status,
            "membership_status": membership.status,
            "is_billing_contact": membership.is_billing_contact,
            "mfa_enabled": user.mfa_enabled,
            "last_login_at": user.last_login_at,
            "joined_at": membership.created_at,
        }
        for membership, user in rows
    ]


@router.post("/{tenant_id}/users", response_model=dict, status_code=201)
async def add_tenant_user(
    payload: TenantUserInvite,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.users.invite")),
) -> dict[str, Any]:
    invited_role = (
        await access.db.execute(select(Role).where(Role.key == payload.role_key))
    ).scalar_one_or_none()
    if invited_role is None or invited_role.scope_type != ScopeType.TENANT.value:
        raise ValidationFailed("role_key must identify a tenant role.")
    actor_permissions = await access.principal.permissions(
        access.db,
        organization_id=access.tenant.organization_id,
        application_id=access.tenant.application_id,
        tenant_id=access.tenant.id,
    )
    excess = exceeded_grants(permissions_for_role_key(invited_role.key), actor_permissions)
    if excess:
        raise Forbidden("You cannot grant permissions you do not hold: " + ", ".join(sorted(excess)))

    user, invite_token = await service.create_initial_admin(
        access.db,
        tenant=access.tenant,
        email=str(payload.email),
        full_name=payload.full_name,
        role_key=payload.role_key,
        invited_by=access.principal.user.id,
    )
    await record_audit(
        access.db,
        action="tenant.user_invited",
        resource_type="user",
        resource_id=user.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=access.tenant.id,
        metadata={"email": user.email, "role_key": payload.role_key},
    )
    await access.db.commit()
    email_sent = False
    if invite_token and payload.send_email:
        email_sent = await service.send_invite_email(recipient=user.email, token=invite_token)
    return {
        "user_id": user.id,
        "email": user.email,
        "status": user.status,
        "invite_email_sent": email_sent,
        "invite_token": invite_token if not settings.is_production else None,
        "invite_note": "Single-use invite token; production delivers it by email." if invite_token else None,
    }


@router.post("/{tenant_id}/users/{user_id}/invite", response_model=dict)
async def resend_tenant_invite(
    user_id: uuid.UUID,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.users.invite")),
) -> dict[str, Any]:
    """Reissue an invite that expired or never arrived."""
    membership = (
        await access.db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == access.tenant.id,
                TenantMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFound("Tenant member", str(user_id))
    user = await access.db.get(User, user_id)
    if user is None:
        raise NotFound("User", str(user_id))
    if user.status != UserStatus.INVITED.value or user.password_hash:
        raise ValidationFailed("This user has already accepted the invite.")

    token = await service.issue_invite_token(access.db, user=user)
    await record_audit(
        access.db,
        action="tenant.invite_resent",
        resource_type="user",
        resource_id=user.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=access.tenant.id,
        metadata={"email": user.email},
    )
    await access.db.commit()
    email_sent = await service.send_invite_email(recipient=user.email, token=token)
    payload: dict[str, Any] = {
        "user_id": user.id,
        "email": user.email,
        "invite_email_sent": email_sent,
    }
    if not settings.is_production:
        payload["invite_token"] = token
    return payload


@router.patch("/{tenant_id}/users/{user_id}", response_model=dict)
async def update_tenant_user(
    user_id: uuid.UUID,
    payload: TenantUserUpdate,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.users.update")),
) -> dict[str, Any]:
    membership = (
        await access.db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == access.tenant.id,
                TenantMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFound("Tenant membership", str(user_id))
    if payload.status:
        membership.status = payload.status
    if payload.role_key:
        role = (
            await access.db.execute(select(Role).where(Role.key == payload.role_key))
        ).scalar_one_or_none()
        if role is None or role.scope_type != ScopeType.TENANT.value:
            raise ValidationFailed("Unknown tenant role.")
        actor_permissions = await access.principal.permissions(
            access.db,
            organization_id=access.tenant.organization_id,
            application_id=access.tenant.application_id,
            tenant_id=access.tenant.id,
        )
        excess = exceeded_grants(permissions_for_role_key(role.key), actor_permissions)
        if excess:
            raise Forbidden("You cannot grant permissions you do not hold: " + ", ".join(sorted(excess)))
        if role.key != "tenant_owner" and access.tenant.owner_id == user_id:
            raise ValidationFailed("Transfer ownership before removing the owner role.")
        current = (
            (
                await access.db.execute(
                    select(UserRole).where(
                        UserRole.user_id == user_id,
                        UserRole.scope_type == ScopeType.TENANT.value,
                        UserRole.scope_id == access.tenant.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for assignment in current:
            await access.db.delete(assignment)
        access.db.add(
            UserRole(
                user_id=user_id,
                role_id=role.id,
                scope_type=ScopeType.TENANT.value,
                scope_id=access.tenant.id,
                granted_by=access.principal.user.id,
                created_at=utcnow(),
            )
        )
    await record_audit(
        access.db,
        action="tenant.user_updated",
        resource_type="user",
        resource_id=user_id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=access.tenant.id,
        metadata={"status": payload.status, "role_key": payload.role_key},
    )
    await access.db.commit()
    return {"user_id": user_id, "status": membership.status, "role_key": payload.role_key}


@router.delete("/{tenant_id}/users/{user_id}", status_code=204)
async def remove_tenant_user(
    user_id: uuid.UUID,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.users.disable")),
) -> None:
    if access.tenant.owner_id == user_id:
        raise ValidationFailed("Transfer ownership before removing the tenant owner.")
    membership = (
        await access.db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == access.tenant.id,
                TenantMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFound("Tenant membership", str(user_id))
    assignments = (
        (
            await access.db.execute(
                select(UserRole).where(
                    UserRole.user_id == user_id,
                    UserRole.scope_type == ScopeType.TENANT.value,
                    UserRole.scope_id == access.tenant.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for assignment in assignments:
        await access.db.delete(assignment)
    await access.db.delete(membership)
    await record_audit(
        access.db,
        action="tenant.user_removed",
        resource_type="user",
        resource_id=user_id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=access.tenant.id,
    )
    await access.db.commit()


@router.get("/{tenant_id}/domains", response_model=list[dict])
async def list_domains(
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.domains.read")),
) -> list[dict[str, Any]]:
    rows = (
        (
            await access.db.execute(
                select(TenantDomain)
                .where(TenantDomain.tenant_id == access.tenant.id)
                .order_by(TenantDomain.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": d.id,
            "domain": d.domain,
            "is_primary": d.is_primary,
            "status": d.status,
            "verification_token": d.verification_token,
            "dns_record": {
                "type": "TXT",
                "name": f"_basa-verification.{d.domain}",
                "value": d.verification_token,
            },
            "ssl_status": d.ssl_status,
            "ssl_expires_at": d.ssl_expires_at,
            "verified_at": d.verified_at,
        }
        for d in rows
    ]


@router.post("/{tenant_id}/domains", response_model=dict, status_code=201)
async def add_domain(
    payload: TenantDomainCreate,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.domains.update")),
) -> dict[str, Any]:
    exists = (
        await access.db.execute(select(TenantDomain.id).where(TenantDomain.domain == payload.domain))
    ).scalar_one_or_none()
    if exists:
        raise Conflict("That domain is already attached to a tenant.")
    if payload.is_primary:
        await access.db.execute(
            TenantDomain.__table__.update()
            .where(TenantDomain.tenant_id == access.tenant.id)
            .values(is_primary=False)
        )
    token = f"basa-verify={generate_token(18)}"
    domain = TenantDomain(
        tenant_id=access.tenant.id,
        domain=payload.domain,
        is_primary=payload.is_primary,
        status=DomainStatus.PENDING.value,
        verification_token=token,
        ssl_status="pending",
        created_at=utcnow(),
    )
    access.db.add(domain)
    await record_audit(
        access.db,
        action="tenant.domain_added",
        resource_type="tenant_domain",
        resource_id=domain.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=access.tenant.id,
        metadata={"domain": domain.domain},
    )
    await access.db.commit()
    return {
        "id": domain.id,
        "domain": domain.domain,
        "status": domain.status,
        "verification_token": token,
        "dns_record": {"type": "TXT", "name": f"_basa-verification.{domain.domain}", "value": token},
    }


@router.post("/{tenant_id}/domains/{domain_id}/verify", response_model=dict)
async def verify_domain(
    domain_id: uuid.UUID,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.domains.update")),
) -> dict[str, Any]:
    domain = await access.db.get(TenantDomain, domain_id)
    if domain is None or domain.tenant_id != access.tenant.id:
        raise NotFound("Domain", str(domain_id))
    verified = await service.verify_domain_token(domain.domain, domain.verification_token)
    if verified:
        domain.status = DomainStatus.VERIFIED.value
        domain.verified_at = utcnow()
        expiry = await service.inspect_tls(domain.domain)
        if expiry:
            domain.ssl_status = "issued"
            domain.ssl_expires_at = expiry
    else:
        domain.status = DomainStatus.PENDING.value
    await record_audit(
        access.db,
        action="tenant.domain_verification",
        resource_type="tenant_domain",
        resource_id=domain.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=access.tenant.id,
        outcome=AuditOutcome.SUCCESS if verified else AuditOutcome.FAILURE,
        metadata={"domain": domain.domain, "verified": verified},
    )
    await access.db.commit()
    return {
        "id": domain.id,
        "domain": domain.domain,
        "status": domain.status,
        "verified": verified,
        "message": (
            "Domain verified."
            if verified
            else "No matching TXT record was found yet. DNS propagation can take time."
        ),
    }


@router.delete("/{tenant_id}/domains/{domain_id}", status_code=204)
async def delete_domain(
    domain_id: uuid.UUID,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.domains.update")),
) -> None:
    domain = await access.db.get(TenantDomain, domain_id)
    if domain is None or domain.tenant_id != access.tenant.id:
        raise NotFound("Domain", str(domain_id))
    await access.db.delete(domain)
    await record_audit(
        access.db,
        action="tenant.domain_removed",
        resource_type="tenant_domain",
        resource_id=domain_id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=access.tenant.id,
        metadata={"domain": domain.domain},
    )
    await access.db.commit()


@router.get("/{tenant_id}/config", response_model=dict)
async def get_tenant_config(
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.config.read")),
) -> dict[str, Any]:
    latest = (
        await access.db.execute(
            select(TenantConfiguration)
            .where(TenantConfiguration.tenant_id == access.tenant.id)
            .order_by(TenantConfiguration.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    history = (
        (
            await access.db.execute(
                select(TenantConfiguration)
                .where(TenantConfiguration.tenant_id == access.tenant.id)
                .order_by(TenantConfiguration.version.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {
        "current": (
            {"version": latest.version, "config": latest.config, "created_at": latest.created_at}
            if latest
            else None
        ),
        "history": [
            {"version": c.version, "config": c.config, "note": c.note, "created_at": c.created_at}
            for c in history
        ],
    }


@router.post("/{tenant_id}/config", response_model=dict, status_code=201)
async def publish_tenant_config(
    payload: TenantConfigPublish,
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.config.update")),
) -> dict[str, Any]:
    latest = (
        await access.db.execute(
            select(TenantConfiguration)
            .where(TenantConfiguration.tenant_id == access.tenant.id)
            .order_by(TenantConfiguration.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    version = (latest.version + 1) if latest else 1
    record = TenantConfiguration(
        tenant_id=access.tenant.id,
        version=version,
        config=payload.config,
        note=payload.note,
        created_by=access.principal.user.id,
        created_at=utcnow(),
    )
    access.db.add(record)
    access.tenant.configuration = payload.config
    await record_audit(
        access.db,
        action="tenant.config_published",
        resource_type="tenant_configuration",
        resource_id=record.id,
        actor=access.principal,
        request=access.request,
        scope_type="tenant",
        scope_id=access.tenant.id,
        metadata={"version": version},
    )
    await access.db.commit()
    return {"id": record.id, "version": version, "created_at": record.created_at}


@router.get("/{tenant_id}/usage", response_model=list[dict])
async def tenant_usage(
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.usage.read")),
) -> list[dict[str, Any]]:
    return await usage_summary(access.db, access.tenant)


@router.get("/{tenant_id}/audit", response_model=dict)
async def tenant_audit(
    params: PageParams = Depends(page_params),
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.audit.read")),
) -> dict[str, Any]:
    stmt = (
        select(AuditLog)
        .where(or_(AuditLog.scope_id == access.tenant.id, AuditLog.resource_id == str(access.tenant.id)))
        .order_by(AuditLog.created_at.desc())
    )
    rows, total = await paginate(access.db, stmt, params)
    return {
        "items": [
            {
                "id": log.id,
                "action": log.action,
                "actor_email": log.actor_email,
                "outcome": log.outcome,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "created_at": log.created_at,
                "metadata": log.metadata_json,
            }
            for log in rows
        ],
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@router.get("/{tenant_id}/security-events", response_model=list[dict])
async def tenant_security_events(
    limit: int = Query(default=50, ge=1, le=200),
    access: service.TenantAccess = Depends(service.require_tenant_access("tenant.security.read")),
) -> list[dict[str, Any]]:
    user_ids = select(TenantMembership.user_id).where(TenantMembership.tenant_id == access.tenant.id)
    rows = (
        (
            await access.db.execute(
                select(AuthenticationEvent)
                .where(AuthenticationEvent.user_id.in_(user_ids))
                .order_by(AuthenticationEvent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": e.id,
            "user_id": e.user_id,
            "email": e.email,
            "event_type": e.event_type,
            "outcome": e.outcome,
            "ip_address": e.ip_address,
            "user_agent": e.user_agent,
            "created_at": e.created_at,
            "metadata": e.event_metadata,
        }
        for e in rows
    ]
