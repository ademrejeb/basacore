"""OAuth 2.0 / OpenID Connect authorization gateway for external applications.

The gateway is first-party: every login client belongs to an application this
company operates. It implements authorization-code + PKCE, refresh rotation,
OIDC userinfo/ID tokens, and an embedded server-side login path. Tenant context
is always supplied by the application and re-validated against the user's
membership before any token is issued.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import platform_scope
from app.core.enums import MembershipStatus, ScopeType, TenantStatus, UserStatus
from app.core.errors import OAuthError, ValidationFailed
from app.core.security import (
    create_app_access_token,
    create_id_token,
    decode_app_token,
    generate_token,
    hash_token,
)
from app.modules.access.models import Role, RolePermission, UserRole
from app.modules.applications.models import (
    Application,
    ApplicationClient,
    ApplicationSession,
    AuthorizationCode,
)
from app.modules.identity.models import User
from app.modules.tenants.models import Tenant, TenantMembership

settings = get_settings()

DEFAULT_SCOPES = ["openid", "profile", "email"]
ALLOWED_SCOPES = {"openid", "profile", "email", "offline_access"}
ACTIVE_TENANT_STATUSES = {TenantStatus.ACTIVE.value, TenantStatus.TRIAL.value}


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- clients


async def load_client(db: AsyncSession, client_id: str) -> ApplicationClient:
    client = (
        await db.execute(select(ApplicationClient).where(ApplicationClient.client_id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise OAuthError("invalid_client", "Unknown client_id.", status_code=401)
    if client.status != "active":
        raise OAuthError("invalid_client", "This login client is disabled.", status_code=401)
    return client


async def load_application(db: AsyncSession, client: ApplicationClient) -> Application:
    app = await db.get(Application, client.application_id)
    if app is None or app.status in ("archived", "inactive"):
        raise OAuthError("invalid_client", "The application is not active.", status_code=401)
    return app


def validate_redirect_uri(client: ApplicationClient, redirect_uri: str) -> None:
    # Exact string match, including scheme, host, port and path. No wildcards:
    # an attacker-controlled prefix must never become a valid callback.
    if redirect_uri not in (client.redirect_uris or []):
        raise OAuthError("invalid_request", "redirect_uri is not registered for this client.")


def validate_post_logout_uri(client: ApplicationClient, uri: str) -> None:
    if uri not in (client.post_logout_redirect_uris or []):
        raise OAuthError("invalid_request", "post_logout_redirect_uri is not registered.")


def normalize_scopes(client: ApplicationClient, requested: str | None) -> list[str]:
    allowed = set(client.scopes or DEFAULT_SCOPES)
    if not requested:
        return [s for s in DEFAULT_SCOPES if s in allowed] + sorted(allowed - set(DEFAULT_SCOPES))
    scopes = [s for s in requested.split() if s]
    unknown = [s for s in scopes if s not in allowed]
    if unknown:
        raise OAuthError(
            "invalid_scope",
            f"Requested scopes are not granted to this client: {', '.join(sorted(unknown))}",
        )
    return scopes


def verify_client_secret(client: ApplicationClient, secret: str | None) -> bool:
    if client.client_type == "public":
        return True
    if not secret or not client.client_secret_hash:
        return False
    return secrets.compare_digest(client.client_secret_hash, hash_token(secret))


# --------------------------------------------------------------------------- tenants


@dataclass
class TenantContext:
    tenant: Tenant
    membership: TenantMembership | None
    roles: list[str]
    permissions: list[str]


async def load_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    """Read one tenant by id under a narrow RLS bypass.

    ``tenants`` is RLS-protected and the gateway has no tenant context yet on
    the token endpoint, so this single-row read is explicit and scoped by the
    caller's already-validated authorization code or session.
    """
    async with platform_scope(db):
        return await db.get(Tenant, tenant_id)


async def resolve_tenant(db: AsyncSession, application: Application, hint: str | None) -> Tenant:
    """Resolve the tenant hint supplied by the application.

    ``tenants`` and ``tenant_memberships`` are RLS-protected. The gateway only
    needs the single tenant named by the application, so it reads just that row
    under an explicit, narrow bypass and never exposes the surrounding set.
    """
    if not hint:
        raise ValidationFailed("A tenant hint is required (tenant slug or id).")
    hint = hint.strip()
    async with platform_scope(db):
        stmt = select(Tenant).where(
            Tenant.application_id == application.id,
            Tenant.deleted_at.is_(None),
        )
        try:
            tenant_id = uuid.UUID(hint)
        except ValueError:
            stmt = stmt.where(Tenant.slug == hint)
        else:
            stmt = stmt.where((Tenant.slug == hint) | (Tenant.id == tenant_id))
        tenant = (await db.execute(stmt)).scalar_one_or_none()
    if tenant is None:
        raise ValidationFailed("That tenant does not belong to this application.")
    if tenant.status not in ACTIVE_TENANT_STATUSES:
        raise OAuthError("access_denied", "This tenant is not available for sign-in.")
    return tenant


async def select_tenant_for_user(
    db: AsyncSession, application: Application, user: User, hint: str | None
) -> Tenant:
    if hint:
        tenant = await resolve_tenant(db, application, hint)
        await verify_membership(db, user, tenant)
        return tenant

    async with platform_scope(db):
        rows = (
            (
                await db.execute(
                    select(Tenant)
                    .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
                    .where(
                        Tenant.application_id == application.id,
                        Tenant.deleted_at.is_(None),
                        Tenant.status.in_(list(ACTIVE_TENANT_STATUSES)),
                        TenantMembership.user_id == user.id,
                        TenantMembership.status == MembershipStatus.ACTIVE.value,
                    )
                    .order_by(Tenant.created_at)
                )
            )
            .scalars()
            .all()
        )
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise OAuthError("access_denied", "You do not have access to any tenant of this application.")
    raise OAuthError(
        "tenant_required",
        "This account belongs to more than one tenant. The application must send a tenant hint.",
    )


async def verify_membership(db: AsyncSession, user: User, tenant: Tenant) -> TenantMembership | None:
    if user.user_type == "internal":
        # Staff of the operating company may enter any tenant of the app, but
        # still never a tenant that belongs to a different application.
        if tenant.application_id is None:
            raise OAuthError("access_denied", "Invalid tenant context.")
        return None
    async with platform_scope(db):
        membership = (
            await db.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.user_id == user.id,
                    TenantMembership.status == MembershipStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
    if membership is None:
        raise OAuthError("access_denied", "You are not a member of this tenant.")
    return membership


async def tenant_access(db: AsyncSession, user: User, tenant: Tenant) -> TenantContext:
    """Effective roles and permissions in one tenant, at token issue time."""
    conditions = [
        (UserRole.scope_type == ScopeType.TENANT.value) & (UserRole.scope_id == tenant.id),
        (UserRole.scope_type == ScopeType.APPLICATION.value) & (UserRole.scope_id == tenant.application_id),
        (UserRole.scope_type == ScopeType.ORGANIZATION.value) & (UserRole.scope_id == tenant.organization_id),
    ]
    if user.user_type == "internal":
        conditions.append((UserRole.scope_type == ScopeType.PLATFORM.value) & (UserRole.scope_id.is_(None)))

    rows = (
        await db.execute(
            select(Role.key, RolePermission.permission_key)
            .join(UserRole, UserRole.role_id == Role.id)
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .where(
                UserRole.user_id == user.id,
                (UserRole.expires_at.is_(None)) | (UserRole.expires_at > _now()),
                or_(*conditions),
            )
        )
    ).all()
    roles = sorted({key for key, _ in rows if key})
    permissions = sorted({permission for _, permission in rows if permission})
    membership = None
    if user.user_type != "internal":
        membership = await verify_membership(db, user, tenant)
    return TenantContext(tenant=tenant, membership=membership, roles=roles, permissions=permissions)


# ------------------------------------------------------------------- authorization codes


def verify_pkce(verifier: str, challenge: str, method: str | None) -> bool:
    if method != "S256":
        return False
    # RFC 7636 bounds the verifier. Rejecting non-ASCII here keeps malformed
    # input a clean invalid_grant instead of a UnicodeEncodeError/500.
    if not verifier or not verifier.isascii() or not 43 <= len(verifier) <= 128:
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, challenge)


async def create_authorization_code(
    db: AsyncSession,
    *,
    client: ApplicationClient,
    application: Application,
    user: User,
    tenant: Tenant,
    session_id: uuid.UUID | None,
    redirect_uri: str,
    scopes: list[str],
    nonce: str | None,
    code_challenge: str | None,
    code_challenge_method: str | None,
    auth_time: datetime,
    amr: list[str],
) -> str:
    raw = generate_token(48)
    db.add(
        AuthorizationCode(
            code_hash=hash_token(raw),
            application_client_id=client.id,
            application_id=application.id,
            user_id=user.id,
            tenant_id=tenant.id,
            session_id=session_id,
            redirect_uri=redirect_uri,
            scope=" ".join(scopes),
            nonce=nonce,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            expires_at=_now() + timedelta(seconds=settings.auth_code_ttl_seconds),
            auth_time=auth_time,
            amr=amr,
            created_at=_now(),
        )
    )
    return raw


async def consume_authorization_code(
    db: AsyncSession,
    *,
    client: ApplicationClient,
    code: str,
    redirect_uri: str,
    code_verifier: str | None,
) -> AuthorizationCode:
    row = (
        await db.execute(
            select(AuthorizationCode)
            .where(
                AuthorizationCode.code_hash == hash_token(code),
                AuthorizationCode.application_client_id == client.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise OAuthError("invalid_grant", "The authorization code is invalid.")
    if row.consumed_at is not None:
        # Replay of a code is treated as compromise, exactly like refresh reuse.
        await db.execute(
            ApplicationSession.__table__.update()
            .where(
                ApplicationSession.user_id == row.user_id,
                ApplicationSession.application_client_id == client.id,
                ApplicationSession.revoked_at.is_(None),
            )
            .values(revoked_at=_now(), revoked_reason="authorization_code_reuse")
        )
        await db.commit()
        raise OAuthError("invalid_grant", "The authorization code has already been used.")
    if row.expires_at.replace(tzinfo=UTC) <= _now():
        raise OAuthError("invalid_grant", "The authorization code has expired.")
    if not secrets.compare_digest(row.redirect_uri, redirect_uri):
        raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request.")
    if row.code_challenge:
        if not code_verifier:
            raise OAuthError("invalid_grant", "A PKCE code_verifier is required.")
        if not verify_pkce(code_verifier, row.code_challenge, row.code_challenge_method):
            raise OAuthError("invalid_grant", "The PKCE code_verifier is invalid.")
    elif client.pkce_required:
        raise OAuthError("invalid_grant", "This client requires PKCE.")
    row.consumed_at = _now()
    return row


# -------------------------------------------------------------------- app sessions


async def create_application_session(
    db: AsyncSession,
    *,
    client: ApplicationClient,
    application: Application,
    user: User,
    tenant: Tenant,
    scopes: list[str],
    nonce: str | None,
    auth_time: datetime,
    amr: list[str],
    ip: str | None,
    user_agent: str | None,
) -> tuple[ApplicationSession, str]:
    refresh_token = generate_token(48)
    now = _now()
    session = ApplicationSession(
        application_client_id=client.id,
        application_id=application.id,
        user_id=user.id,
        tenant_id=tenant.id,
        refresh_token_hash=hash_token(refresh_token),
        scope=" ".join(scopes),
        nonce=nonce,
        user_agent=(user_agent or "")[:500] or None,
        ip_address=ip,
        auth_time=auth_time,
        amr=amr,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=settings.app_refresh_token_ttl_days),
    )
    db.add(session)
    await db.flush()
    return session, refresh_token


async def issue_tokens(
    db: AsyncSession,
    *,
    client: ApplicationClient,
    application: Application,
    user: User,
    tenant: Tenant,
    session: ApplicationSession,
    refresh_token: str,
    scopes: list[str],
    nonce: str | None,
) -> dict[str, Any]:
    context = await tenant_access(db, user, tenant)
    access_token, expires_in = create_app_access_token(
        client_id=client.client_id,
        application_id=application.id,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        user_id=user.id,
        session_id=session.id,
        email=user.email,
        name=user.full_name,
        roles=context.roles,
        permissions=context.permissions,
        scopes=scopes,
        auth_time=session.auth_time,
        amr=session.amr,
    )
    payload: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "refresh_expires_in": max(int((session.expires_at.replace(tzinfo=UTC) - _now()).total_seconds()), 0),
        "scope": " ".join(scopes),
        "session_id": str(session.id),
    }
    if "openid" in scopes:
        id_token, _ = create_id_token(
            client_id=client.client_id,
            application_id=application.id,
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            user_id=user.id,
            session_id=session.id,
            email=user.email,
            name=user.full_name,
            roles=context.roles,
            permissions=context.permissions,
            scopes=scopes,
            auth_time=session.auth_time,
            amr=session.amr,
            nonce=nonce,
            email_verified=user.email_verified_at is not None,
        )
        payload["id_token"] = id_token
    return payload


async def rotate_refresh_token(
    db: AsyncSession,
    *,
    client: ApplicationClient,
    refresh_token: str,
    scope: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    # Row lock: a token may only be rotated by one request at a time. See the
    # console equivalent in identity.service.rotate_refresh_token.
    session = (
        await db.execute(
            select(ApplicationSession)
            .where(
                ApplicationSession.refresh_token_hash == hash_token(refresh_token),
                ApplicationSession.application_client_id == client.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise OAuthError("invalid_grant", "The refresh token is invalid.")
    now = _now()
    if session.revoked_at is not None:
        await db.execute(
            ApplicationSession.__table__.update()
            .where(
                ApplicationSession.user_id == session.user_id,
                ApplicationSession.application_client_id == client.id,
                ApplicationSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_reason="refresh_token_reuse")
        )
        await db.commit()
        raise OAuthError("invalid_grant", "Refresh token reuse detected; all sessions were revoked.")
    if session.expires_at.replace(tzinfo=UTC) <= now:
        raise OAuthError("invalid_grant", "The refresh token has expired.")

    user = await db.get(User, session.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise OAuthError("invalid_grant", "This account is not active.")
    application = await load_application(db, client)
    tenant = await load_tenant(db, session.tenant_id)
    if tenant is None or tenant.status not in ACTIVE_TENANT_STATUSES:
        raise OAuthError("invalid_grant", "This tenant is not available for sign-in.")
    await verify_membership(db, user, tenant)

    scopes = [s for s in session.scope.split() if s]
    if scope:
        requested = [s for s in scope.split() if s]
        if not set(requested).issubset(set(scopes)):
            raise OAuthError("invalid_scope", "A refresh cannot widen the granted scopes.")
        scopes = requested

    new_session, new_refresh = await create_application_session(
        db,
        client=client,
        application=application,
        user=user,
        tenant=tenant,
        scopes=scopes,
        nonce=session.nonce,
        auth_time=session.auth_time,
        amr=session.amr,
        ip=ip,
        user_agent=user_agent,
    )
    session.revoked_at = now
    session.revoked_reason = "rotated"
    session.replaced_by_id = new_session.id
    session.last_seen_at = now
    return await issue_tokens(
        db,
        client=client,
        application=application,
        user=user,
        tenant=tenant,
        session=new_session,
        refresh_token=new_refresh,
        scopes=scopes,
        nonce=session.nonce,
    )


async def revoke_refresh_token(db: AsyncSession, client: ApplicationClient, refresh_token: str) -> bool:
    session = (
        await db.execute(
            select(ApplicationSession).where(
                ApplicationSession.refresh_token_hash == hash_token(refresh_token),
                ApplicationSession.application_client_id == client.id,
            )
        )
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return False
    session.revoked_at = _now()
    session.revoked_reason = "logout"
    return True


# ------------------------------------------------------------------------ userinfo


async def userinfo(db: AsyncSession, access_token: str) -> dict[str, Any]:
    """Resolve an app token into an OIDC userinfo response, bound to its session."""
    try:
        claims = decode_app_token(access_token)
    except Exception as exc:  # noqa: BLE001 - all verification failures are 401
        raise OAuthError("invalid_token", "The access token is invalid or expired.", 401) from exc
    session_id = claims.session_id
    if not session_id:
        raise OAuthError("invalid_token", "The access token has no session.", 401)
    try:
        session = await db.get(ApplicationSession, uuid.UUID(session_id))
    except ValueError as exc:
        raise OAuthError("invalid_token", "The access token session is invalid.", 401) from exc
    if session is None or session.revoked_at is not None:
        raise OAuthError("invalid_token", "The application session is no longer active.", 401)
    if session.expires_at.replace(tzinfo=UTC) <= _now():
        raise OAuthError("invalid_token", "The application session has expired.", 401)
    client = await db.get(ApplicationClient, session.application_client_id)
    if client is None or client.status != "active":
        raise OAuthError("invalid_token", "The login client is no longer active.", 401)
    if claims.claims.get("aud") != client.client_id:
        raise OAuthError("invalid_token", "The access token audience is invalid.", 401)
    user = await db.get(User, session.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise OAuthError("invalid_token", "This account is not active.", 401)
    tenant = await load_tenant(db, session.tenant_id)
    if tenant is None or tenant.status not in ACTIVE_TENANT_STATUSES:
        raise OAuthError("invalid_token", "This tenant is not available for sign-in.", 401)
    try:
        context = await tenant_access(db, user, tenant)
    except OAuthError as exc:
        raise OAuthError("invalid_token", "This account no longer has access to the tenant.", 401) from exc

    return {
        "sub": str(user.id),
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "name": user.full_name,
        "preferred_username": user.email,
        "locale": user.locale,
        "picture": user.avatar_url,
        "app_id": str(client.application_id),
        "client_id": client.client_id,
        "tenant_id": str(session.tenant_id),
        "tenant_slug": tenant.slug if tenant else None,
        "roles": context.roles,
        "permissions": context.permissions,
        "sid": str(session.id),
        "auth_time": claims.claims.get("auth_time"),
        "amr": claims.claims.get("amr") or [],
    }


def authorize_error_redirect(redirect_uri: str, error: str, description: str, state: str | None) -> str:
    params = {"error": error, "error_description": description}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{urlencode(params)}"


def authorize_success_redirect(redirect_uri: str, code: str, state: str | None) -> str:
    params = {"code": code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{urlencode(params)}"
