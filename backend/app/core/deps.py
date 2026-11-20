from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import jwt
import structlog
from fastapi import Cookie, Depends, Header, Request
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.enums import ScopeType, UserStatus
from app.core.errors import Forbidden, Unauthorized
from app.core.security import TokenClaims, decode_app_token, decode_token
from app.modules.access.models import UserRole
from app.modules.identity.models import User, UserSession

logger = structlog.get_logger("auth")


@dataclass
class Principal:
    user: User
    session_id: uuid.UUID | None
    token_type: str
    claims: dict[str, Any] = field(default_factory=dict)
    _permission_cache: dict[tuple[str, ...], set[str]] = field(default_factory=dict, repr=False)

    @property
    def is_internal(self) -> bool:
        return self.user.user_type == "internal"

    async def permissions(
        self,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID | None = None,
        application_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> set[str]:
        key = (
            str(organization_id or ""),
            str(application_id or ""),
            str(tenant_id or ""),
        )
        if key in self._permission_cache:
            return self._permission_cache[key]

        scope_conditions = ["(ur.scope_type = 'platform' AND ur.scope_id IS NULL)"]
        params: dict[str, Any] = {"user_id": self.user.id}
        if organization_id:
            scope_conditions.append("(ur.scope_type = 'organization' AND ur.scope_id = :organization_id)")
            params["organization_id"] = organization_id
        if application_id:
            scope_conditions.append("(ur.scope_type = 'application' AND ur.scope_id = :application_id)")
            params["application_id"] = application_id
        if tenant_id:
            scope_conditions.append("(ur.scope_type = 'tenant' AND ur.scope_id = :tenant_id)")
            params["tenant_id"] = tenant_id

        stmt = text(
            f"""
            SELECT DISTINCT rp.permission_key
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            JOIN role_permissions rp ON rp.role_id = r.id
            WHERE ur.user_id = :user_id
              AND (ur.expires_at IS NULL OR ur.expires_at > now())
              AND ({" OR ".join(scope_conditions)})
            """
        )
        rows = (await db.execute(stmt, params)).scalars().all()
        granted = set(rows)
        self._permission_cache[key] = granted
        return granted

    async def has(
        self,
        db: AsyncSession,
        permission: str,
        *,
        organization_id: uuid.UUID | None = None,
        application_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
        platform_only: bool = False,
    ) -> bool:
        if platform_only:
            perms = await self.platform_permissions(db)
            return "*" in perms or permission in perms
        perms = await self.permissions(
            db,
            organization_id=organization_id,
            application_id=application_id,
            tenant_id=tenant_id,
        )
        return "*" in perms or permission in perms

    async def platform_permissions(self, db: AsyncSession) -> set[str]:
        return await self.permissions(db)

    async def has_platform_role(self, db: AsyncSession) -> bool:
        stmt = select(UserRole.id).where(
            UserRole.user_id == self.user.id,
            UserRole.scope_type == ScopeType.PLATFORM.value,
            UserRole.scope_id.is_(None),
        )
        return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None

    async def require(
        self,
        db: AsyncSession,
        permission: str,
        *,
        organization_id: uuid.UUID | None = None,
        application_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
        platform_only: bool = False,
    ) -> None:
        if not await self.has(
            db,
            permission,
            organization_id=organization_id,
            application_id=application_id,
            tenant_id=tenant_id,
            platform_only=platform_only,
        ):
            raise Forbidden(f"Missing permission: {permission}")


def _extract_token(request: Request, bearer: str | None, cookie_value: str | None) -> str | None:
    if bearer:
        scheme, _, token = bearer.partition(" ")
        if scheme.lower() == "bearer" and token:
            return token
    if cookie_value:
        return cookie_value
    return None


async def _principal_from_token(db: AsyncSession, token: str) -> Principal:
    try:
        claims: TokenClaims = decode_token(token, expected_type="user")
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("Invalid access token.") from exc

    try:
        user_id = uuid.UUID(claims.subject)
    except ValueError as exc:
        raise Unauthorized("Invalid access token.") from exc

    user = await db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise Unauthorized("This account is not active.")

    if claims.session_id:
        try:
            session_id = uuid.UUID(claims.session_id)
        except ValueError as exc:
            raise Unauthorized("Invalid access token.") from exc
        session = await db.get(UserSession, session_id)
        now = datetime.now(UTC)
        if session is None or session.revoked_at is not None or session.expires_at.replace(tzinfo=UTC) <= now:
            raise Unauthorized("This session has been revoked or expired.")

    return Principal(
        user=user,
        session_id=uuid.UUID(claims.session_id) if claims.session_id else None,
        token_type="user",
        claims=claims.claims,
    )


async def get_principal(
    request: Request,
    db: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
    basa_access: str | None = Cookie(default=None),
) -> Principal:
    token = _extract_token(request, authorization, basa_access)
    if not token:
        raise Unauthorized()
    return await _principal_from_token(db, token)


async def get_optional_principal(
    request: Request,
    db: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
    basa_access: str | None = Cookie(default=None),
) -> Principal | None:
    """Return the signed-in console user, or ``None`` for an anonymous request.

    Used by the hosted authorization endpoint, which redirects anonymous users
    to the login page instead of returning a 401.
    """
    token = _extract_token(request, authorization, basa_access)
    if not token:
        return None
    try:
        return await _principal_from_token(db, token)
    except Unauthorized:
        return None


@dataclass
class AppPrincipal:
    """A verified end-user token scoped to one application client and tenant."""

    user: User
    claims: dict[str, Any]
    client_id: str
    application_id: uuid.UUID
    tenant_id: uuid.UUID
    session_id: uuid.UUID

    @property
    def permissions(self) -> set[str]:
        return set(self.claims.get("permissions") or [])

    def has(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


async def get_app_principal(
    db: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> AppPrincipal:
    from app.modules.applications.models import Application, ApplicationClient, ApplicationSession

    token: str | None = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value
    if not token:
        raise Unauthorized("An application access token is required.")
    try:
        claims = decode_app_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("The application access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("Invalid application access token.") from exc

    client_id = claims.claims.get("client_id")
    session_raw = claims.session_id
    if not client_id or not session_raw:
        raise Unauthorized("Invalid application access token claims.")
    if claims.claims.get("aud") != client_id:
        raise Unauthorized("Invalid application access token audience.")

    client = (
        await db.execute(select(ApplicationClient).where(ApplicationClient.client_id == client_id))
    ).scalar_one_or_none()
    if client is None or client.status != "active":
        raise Unauthorized("This login client is not active.")
    app = await db.get(Application, client.application_id)
    if app is None or app.status in ("archived", "inactive"):
        raise Unauthorized("The application is not active.")

    try:
        session_id = uuid.UUID(session_raw)
        tenant_id = uuid.UUID(str(claims.claims.get("tenant_id")))
    except (ValueError, TypeError) as exc:
        raise Unauthorized("Invalid application access token claims.") from exc
    session = await db.get(ApplicationSession, session_id)
    now = datetime.now(UTC)
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at.replace(tzinfo=UTC) <= now
        or session.application_client_id != client.id
    ):
        raise Unauthorized("This application session has been revoked or expired.")

    user = await db.get(User, session.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise Unauthorized("This account is not active.")

    return AppPrincipal(
        user=user,
        claims=claims.claims,
        client_id=client.client_id,
        application_id=client.application_id,
        tenant_id=tenant_id,
        session_id=session_id,
    )


def require_platform(permission: str):
    """Authorize a platform-scoped permission and enable cross-tenant visibility.

    The PostgreSQL bypass is transaction-local, so it is dropped as soon as the
    request's transaction ends and can never leak through a pooled connection.
    It is only ever set after a successful platform-scope authorization check.
    """

    async def dependency(
        principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
    ) -> AsyncIterator[Principal]:
        await principal.require(db, permission, platform_only=True)
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        yield principal

    return dependency


@dataclass
class ServicePrincipal:
    application_id: uuid.UUID
    credential_id: uuid.UUID
    scopes: list[str]
    claims: dict[str, Any]

    def has(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes

    def require(self, scope: str) -> None:
        if not self.has(scope):
            raise Forbidden(f"Service credential is missing scope: {scope}")


CREDENTIAL_TOUCH_INTERVAL_SECONDS = 300


async def _touch_credential(credential_id: uuid.UUID) -> None:
    """Record credential use outside the request transaction.

    The auth dependency used to commit ``last_used_at`` on every SDK call, which
    turned read-only requests into writes and made one row a hotspot. The write
    now runs in its own short session at most once per interval, and failures are
    ignored: usage telemetry must never fail the request it observes.
    """
    from app.core.db import SessionLocal
    from app.modules.applications.models import ApiCredential

    try:
        async with SessionLocal() as touch:
            await touch.execute(
                update(ApiCredential)
                .where(ApiCredential.id == credential_id)
                .values(last_used_at=datetime.now(UTC))
            )
            await touch.commit()
    except Exception as exc:  # noqa: BLE001 - best effort telemetry
        logger.warning("credential_touch_failed", credential_id=str(credential_id), error=str(exc))


async def get_service_principal(
    db: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> ServicePrincipal:
    from app.modules.applications.models import ApiCredential, Application

    token: str | None = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value
    if not token:
        raise Unauthorized("A service access token is required.")
    try:
        claims = decode_token(token, expected_type="service")
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Service token expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("Invalid service token.") from exc

    credential_id = claims.claims.get("credential_id")
    application_id = claims.claims.get("app_id")
    if not credential_id or not application_id:
        raise Unauthorized("Invalid service token claims.")
    credential = await db.get(ApiCredential, uuid.UUID(credential_id))
    now = datetime.now(UTC)
    if credential is None or credential.status != "active" or credential.revoked_at is not None:
        raise Unauthorized("This service credential is no longer active.")
    if credential.expires_at and credential.expires_at.replace(tzinfo=UTC) <= now:
        raise Unauthorized("This service credential has expired.")
    if str(credential.application_id) != str(application_id):
        raise Unauthorized("Credential does not match the application.")
    app = await db.get(Application, credential.application_id)
    if app is None or app.status in ("archived", "inactive"):
        raise Unauthorized("The application is not active.")
    # The token carries the scopes that were granted at issue time.
    scopes = list(claims.claims.get("scopes") or [])
    if not set(scopes).issubset(set(credential.scopes or [])):
        raise Unauthorized("Service token scopes are no longer valid.")
    last_used = credential.last_used_at
    if last_used is not None and last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=UTC)
    if last_used is None or (now - last_used).total_seconds() >= CREDENTIAL_TOUCH_INTERVAL_SECONDS:
        await _touch_credential(credential.id)
    return ServicePrincipal(
        application_id=credential.application_id,
        credential_id=credential.id,
        scopes=scopes,
        claims=claims.claims,
    )


def require_scope(scope: str):
    async def dependency(
        principal: ServicePrincipal = Depends(get_service_principal),
    ) -> ServicePrincipal:
        principal.require(scope)
        return principal

    return dependency
