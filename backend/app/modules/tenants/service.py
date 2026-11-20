from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session, tenant_scope, utcnow
from app.core.deps import Principal, get_principal
from app.core.enums import (
    IsolationStrategy,
    MembershipStatus,
    ScopeType,
    TenantStatus,
    UserStatus,
    UserType,
)
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.mailer import send_email
from app.core.security import generate_token
from app.modules.access.models import Role, UserRole
from app.modules.identity.models import User, VerificationToken
from app.modules.tenants.models import (
    Tenant,
    TenantConfiguration,
    TenantMembership,
)

ENABLED_ISOLATION_STRATEGIES = {IsolationStrategy.SHARED.value}

logger = structlog.get_logger("tenants")
settings = get_settings()


@dataclass
class TenantAccess:
    tenant: Tenant
    principal: Principal
    db: AsyncSession
    request: Request


async def authorize_tenant(db: AsyncSession, principal: Principal, tenant: Tenant, permission: str) -> None:
    allowed = await principal.has(
        db,
        permission,
        organization_id=tenant.organization_id,
        application_id=tenant.application_id,
        tenant_id=tenant.id,
    )
    if not allowed:
        raise Forbidden(f"Missing permission: {permission}")

    if principal.user.user_type == UserType.EXTERNAL.value:
        membership = (
            await db.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.user_id == principal.user.id,
                    TenantMembership.status == MembershipStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise Forbidden("You are not a member of this tenant.")


def require_tenant_access(permission: str):
    """Resolve tenant from the path, authorize it, and activate RLS context."""

    async def dependency(
        tenant_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_session),
    ):
        # Setting the tenant context before the lookup is safe: RLS narrows the
        # query to the requested tenant. Authorization happens immediately after.
        async with tenant_scope(db, tenant_id):
            tenant = await db.get(Tenant, tenant_id)
            if tenant is None or tenant.deleted_at is not None:
                raise NotFound("Tenant", str(tenant_id))
            await authorize_tenant(db, principal, tenant, permission)
            yield TenantAccess(tenant=tenant, principal=principal, db=db, request=request)

    return dependency


async def unique_tenant_slug(db: AsyncSession, application_id: uuid.UUID, base: str) -> str:
    from app.core.db import slugify

    slug = slugify(base)
    candidate, n = slug, 1
    while (
        await db.execute(
            select(Tenant.id).where(Tenant.application_id == application_id, Tenant.slug == candidate)
        )
    ).scalar_one_or_none():
        n += 1
        candidate = f"{slug}-{n}"
    return candidate


async def provision_tenant(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    organization_id: uuid.UUID,
    name: str,
    slug: str,
    owner: User | None,
    plan,
    billing_interval: str,
    trial_days: int,
    isolation_strategy: str,
    quotas: dict[str, int],
    configuration: dict[str, Any],
    branding: dict[str, Any],
    created_by: uuid.UUID,
) -> tuple[Tenant, Any]:
    from app.modules.billing.service import create_subscription

    if isolation_strategy not in ENABLED_ISOLATION_STRATEGIES:
        raise ValidationFailed(
            "This deployment supports the shared_database isolation strategy only. "
            "Dedicated strategies require the provisioning adapter documented in docs/ARCHITECTURE.md."
        )
    if plan is not None and plan.application_id not in (None, application_id):
        raise ValidationFailed("The selected plan does not belong to this application.")

    tenant = Tenant(
        application_id=application_id,
        organization_id=organization_id,
        name=name.strip(),
        slug=slug,
        status=TenantStatus.PROVISIONING.value,
        owner_id=owner.id if owner else None,
        isolation_strategy=isolation_strategy,
        quotas=quotas,
        configuration=configuration,
        branding=branding,
        trial_ends_at=(utcnow() + timedelta(days=trial_days)) if trial_days else None,
    )
    db.add(tenant)
    await db.flush()

    db.add(
        TenantConfiguration(
            tenant_id=tenant.id,
            version=1,
            config=configuration,
            note="Initial configuration",
            created_by=created_by,
            created_at=utcnow(),
        )
    )

    if owner:
        db.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=owner.id,
                status=MembershipStatus.ACTIVE.value,
                is_billing_contact=True,
                created_at=utcnow(),
            )
        )
        owner_role = (await db.execute(select(Role).where(Role.key == "tenant_owner"))).scalar_one_or_none()
        if owner_role:
            db.add(
                UserRole(
                    user_id=owner.id,
                    role_id=owner_role.id,
                    scope_type=ScopeType.TENANT.value,
                    scope_id=tenant.id,
                    granted_by=created_by,
                    created_at=utcnow(),
                )
            )

    subscription = None
    if plan is not None:
        subscription = await create_subscription(
            db,
            tenant=tenant,
            plan=plan,
            billing_interval=billing_interval,
            trial_days=trial_days,
        )
        tenant.status = TenantStatus.TRIAL.value if trial_days > 0 else TenantStatus.ACTIVE.value
    else:
        tenant.status = TenantStatus.PROVISIONING.value
    return tenant, subscription


async def issue_invite_token(db: AsyncSession, *, user: User) -> str:
    """Mint a fresh invite token, replacing any previous unconsumed one.

    ``verification_tokens`` is unique on ``(user_id, purpose)``, so a resend must
    replace the row instead of inserting a second invite. Without this an expired
    invite could never be reissued and the user was permanently locked out.
    """
    await db.execute(
        delete(VerificationToken).where(
            VerificationToken.user_id == user.id,
            VerificationToken.purpose == "invite",
        )
    )
    token = generate_token(32)
    db.add(
        VerificationToken(
            user_id=user.id,
            purpose="invite",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(days=7),
            created_at=utcnow(),
        )
    )
    return token


async def send_invite_email(*, recipient: str, token: str) -> bool:
    link = f"{settings.web_base_url}/accept-invite?token={token}"
    return await send_email(
        recipient,
        "You are invited to Basacore",
        "You have been invited to a Basacore workspace.\n\n"
        f"Accept the invitation and choose your password within 7 days:\n\n{link}\n",
    )


async def create_initial_admin(
    db: AsyncSession,
    *,
    tenant: Tenant,
    email: str,
    full_name: str | None,
    role_key: str,
    invited_by: uuid.UUID,
) -> tuple[User, str | None]:
    email = email.strip().lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    invite_token = None
    if user is None:
        user = User(
            email=email,
            full_name=(full_name or email.split("@")[0]).strip(),
            password_hash=None,
            user_type=UserType.EXTERNAL.value,
            status=UserStatus.INVITED.value,
        )
        db.add(user)
        await db.flush()
        invite_token = await issue_invite_token(db, user=user)
    elif user.status == UserStatus.INVITED.value and not user.password_hash:
        # The invite may have expired or never arrived; reissuing replaces the
        # single stored token instead of leaving the account stuck.
        invite_token = await issue_invite_token(db, user=user)

    tenant_role = (await db.execute(select(Role).where(Role.key == role_key))).scalar_one_or_none()
    if tenant_role is None or tenant_role.scope_type != ScopeType.TENANT.value:
        raise ValidationFailed("role_key must identify a tenant role.")

    existing = (
        await db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user.id,
                status=MembershipStatus.INVITED.value
                if user.status == UserStatus.INVITED.value
                else MembershipStatus.ACTIVE.value,
                invited_by=invited_by,
                created_at=utcnow(),
            )
        )
    else:
        if existing.status == MembershipStatus.SUSPENDED.value or user.status == UserStatus.SUSPENDED.value:
            # Re-inviting must not silently lift a suspension.
            raise Conflict("This user is suspended. Reactivate the account before inviting them.")
        existing.status = MembershipStatus.ACTIVE.value

    assigned = (
        await db.execute(
            select(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.role_id == tenant_role.id,
                UserRole.scope_type == ScopeType.TENANT.value,
                UserRole.scope_id == tenant.id,
            )
        )
    ).scalar_one_or_none()
    if assigned is None:
        db.add(
            UserRole(
                user_id=user.id,
                role_id=tenant_role.id,
                scope_type=ScopeType.TENANT.value,
                scope_id=tenant.id,
                granted_by=invited_by,
                created_at=utcnow(),
            )
        )
    return user, invite_token


def _resolve_txt(domain: str) -> list[str]:
    import dns.resolver

    answers = dns.resolver.resolve(f"_basa-verification.{domain}", "TXT", lifetime=5)
    values: list[str] = []
    for answer in answers:
        values.append(
            b"".join(answer.strings).decode() if hasattr(answer, "strings") else answer.to_text().strip('"')
        )
    return values


async def verify_domain_token(domain: str, token: str) -> bool:
    """Verify ownership through a real DNS TXT lookup."""
    try:
        values = await asyncio.to_thread(_resolve_txt, domain)
    except Exception:  # noqa: BLE001 - DNS failures mean "not verified yet"
        return False
    return any(value.strip().strip('"') == token for value in values)


def _public_address(domain: str) -> str | None:
    """Resolve ``domain`` and return one globally routable address, else ``None``.

    Domain ownership is user-supplied, so the TLS probe must never be able to
    reach loopback, private, link-local or metadata endpoints. Resolving and
    connecting to the same address also closes the DNS-rebinding window between
    a separate validation step and the socket connect.
    """
    try:
        infos = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
    except OSError:
        return None
    for info in infos:
        candidate = info[4][0]
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_global:
            return candidate
    return None


def _tls_expiry(domain: str) -> datetime | None:
    target = _public_address(domain)
    if target is None:
        logger.warning("tls_inspection_blocked", domain=domain, reason="no_global_address")
        return None
    context = ssl.create_default_context()
    try:
        with socket.create_connection((target, 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as tls:
                cert = tls.getpeercert()
                if not cert or "notAfter" not in cert:
                    return None
                return datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except Exception:  # noqa: BLE001
        return None


async def inspect_tls(domain: str) -> datetime | None:
    return await asyncio.to_thread(_tls_expiry, domain)
