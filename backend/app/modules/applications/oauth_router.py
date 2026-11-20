from __future__ import annotations

import base64
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import get_settings
from app.core.db import get_session, tenant_scope, utcnow
from app.core.deps import Principal, get_optional_principal
from app.core.errors import AppError, Forbidden, OAuthError, Unauthorized, ValidationFailed
from app.core.rate_limit import hit
from app.core.security import create_service_token, decode_app_token, hash_token
from app.modules.access.policy import get_effective_policy
from app.modules.applications import auth_service
from app.modules.applications.models import (
    ApiCredential,
    Application,
    ApplicationClient,
    ApplicationSession,
)
from app.modules.applications.schemas import EmbeddedLoginRequest, EmbeddedMfaRequest
from app.modules.identity import service as identity_service
from app.modules.identity.models import User
from app.modules.tenants.models import Tenant

settings = get_settings()
router = APIRouter(prefix="/oauth", tags=["OAuth"])


def _basic_credentials(header: str | None) -> tuple[str | None, str | None]:
    if not header or not header.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
        client_id, _, client_secret = decoded.partition(":")
        return client_id or None, client_secret or None
    except Exception:  # noqa: BLE001 - malformed header is simply invalid
        return None, None


def _client(request: Request) -> tuple[str | None, str | None]:
    return getattr(request.state, "client_ip", None), getattr(request.state, "user_agent", None)


def _now() -> datetime:
    return datetime.now(UTC)


def _login_redirect(
    request: Request,
    application: Application,
    tenant: Tenant | None,
    login_hint: str | None,
    *,
    reauth: bool = False,
) -> RedirectResponse:
    # ``prompt`` is consumed exactly once: after the user signs in, the replay of
    # the authorization request must not force another login round-trip. A
    # separate ``reauth`` flag tells the login page not to reuse a live console
    # session when the application explicitly asked for re-authentication.
    pairs = [(key, value) for key, value in request.query_params.multi_items() if key != "prompt"]
    query = urlencode(pairs)
    return_to = request.url.path + (f"?{query}" if query else "")
    params: dict[str, str] = {"return_to": return_to, "app_name": application.name}
    if tenant is not None:
        params["tenant_name"] = tenant.name
    if login_hint:
        params["email"] = login_hint
    if reauth:
        params["reauth"] = "1"
    return RedirectResponse(f"{settings.web_base_url}/login?{urlencode(params)}", status_code=303)


def _authorize_error(redirect_uri: str, error: str, description: str, state: str | None):
    return RedirectResponse(
        auth_service.authorize_error_redirect(redirect_uri, error, description, state),
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/authorize", summary="OIDC authorization endpoint (authorization code + PKCE)")
async def authorize(
    request: Request,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str | None = None,
    state: str | None = None,
    nonce: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    tenant: str | None = None,
    login_hint: str | None = None,
    prompt: str | None = None,
    principal: Principal | None = Depends(get_optional_principal),
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    # Client and redirect_uri must be trusted before any error can be redirected.
    client = await auth_service.load_client(db, client_id)
    application = await auth_service.load_application(db, client)
    if redirect_uri not in (client.redirect_uris or []):
        raise OAuthError("invalid_request", "redirect_uri is not registered for this client.")

    if response_type != "code":
        return _authorize_error(redirect_uri, "unsupported_response_type", "Only code is supported.", state)
    if prompt not in (None, "login", "none"):
        return _authorize_error(redirect_uri, "invalid_request", "Unsupported prompt value.", state)
    if prompt == "none" and principal is None:
        # OIDC says a silent request that needs UI must fail, never render it.
        return _authorize_error(redirect_uri, "login_required", "The user must sign in interactively.", state)
    if client.pkce_required and not code_challenge:
        return _authorize_error(redirect_uri, "invalid_request", "PKCE code_challenge is required.", state)
    if code_challenge and code_challenge_method != "S256":
        return _authorize_error(redirect_uri, "invalid_request", "Only S256 PKCE is supported.", state)
    try:
        scopes = auth_service.normalize_scopes(client, scope)
    except OAuthError as exc:
        return _authorize_error(redirect_uri, exc.error, exc.description, state)

    tenant_obj: Tenant | None = None
    if tenant:
        try:
            tenant_obj = await auth_service.resolve_tenant(db, application, tenant)
        except AppError as exc:
            return _authorize_error(redirect_uri, "invalid_request", exc.message, state)
        except OAuthError as exc:
            return _authorize_error(redirect_uri, exc.error, exc.description, state)

    if principal is None or prompt == "login":
        return _login_redirect(request, application, tenant_obj, login_hint, reauth=prompt == "login")

    try:
        tenant_obj = await auth_service.select_tenant_for_user(db, application, principal.user, tenant)
    except OAuthError as exc:
        return _authorize_error(redirect_uri, exc.error, exc.description, state)
    except AppError as exc:
        return _authorize_error(redirect_uri, "invalid_request", exc.message, state)

    session = None
    if principal.session_id:
        from app.modules.identity.models import UserSession

        session = await db.get(UserSession, principal.session_id)
    amr = ["pwd"] + (["mfa"] if session is not None and session.mfa_verified else [])
    auth_time = session.created_at if session is not None else _now()

    code = await auth_service.create_authorization_code(
        db,
        client=client,
        application=application,
        user=principal.user,
        tenant=tenant_obj,
        session_id=principal.session_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        nonce=nonce,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        auth_time=auth_time,
        amr=amr,
    )
    async with tenant_scope(db, tenant_obj.id):
        await record_audit(
            db,
            action="oauth.authorize",
            resource_type="application_client",
            resource_id=client.id,
            actor=principal,
            scope_type="tenant",
            scope_id=tenant_obj.id,
            request=request,
            metadata={
                "application_id": str(application.id),
                "client_id": client.client_id,
                "tenant_slug": tenant_obj.slug,
                "scopes": scopes,
                "pkce": bool(code_challenge),
            },
        )
    await db.commit()
    return RedirectResponse(
        auth_service.authorize_success_redirect(redirect_uri, code, state),
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/token", response_model=dict, summary="OAuth 2.0 token endpoint")
async def token(
    request: Request,
    grant_type: str = Form(...),
    code: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
    code_verifier: str | None = Form(default=None),
    refresh_token: str | None = Form(default=None),
    scope: str | None = Form(default=None),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if grant_type == "client_credentials":
        return await _client_credentials_token(request, client_id, client_secret, authorization, scope, db)

    basic_id, basic_secret = _basic_credentials(authorization)
    client_id = basic_id or client_id
    client_secret = basic_secret or client_secret
    if not client_id:
        raise OAuthError("invalid_client", "client_id is required.", 401)

    client = await auth_service.load_client(db, client_id)
    if not auth_service.verify_client_secret(client, client_secret):
        raise OAuthError("invalid_client", "Client authentication failed.", 401)
    application = await auth_service.load_application(db, client)

    ip, ua = _client(request)

    if grant_type == "authorization_code":
        if not code or not redirect_uri:
            raise OAuthError("invalid_request", "code and redirect_uri are required.")
        row = await auth_service.consume_authorization_code(
            db, client=client, code=code, redirect_uri=redirect_uri, code_verifier=code_verifier
        )
        user = await db.get(User, row.user_id)
        tenant = await auth_service.load_tenant(db, row.tenant_id)
        if user is None or user.status != "active":
            raise OAuthError("invalid_grant", "This account is not active.")
        if tenant is None or tenant.status not in auth_service.ACTIVE_TENANT_STATUSES:
            raise OAuthError("invalid_grant", "This tenant is not available for sign-in.")
        await auth_service.verify_membership(db, user, tenant)

        scopes = [s for s in row.scope.split() if s]
        session, new_refresh = await auth_service.create_application_session(
            db,
            client=client,
            application=application,
            user=user,
            tenant=tenant,
            scopes=scopes,
            nonce=row.nonce,
            auth_time=row.auth_time,
            amr=row.amr,
            ip=ip,
            user_agent=ua,
        )
        payload = await auth_service.issue_tokens(
            db,
            client=client,
            application=application,
            user=user,
            tenant=tenant,
            session=session,
            refresh_token=new_refresh,
            scopes=scopes,
            nonce=row.nonce,
        )
        client.last_used_at = _now()
        async with tenant_scope(db, tenant.id):
            await record_audit(
                db,
                action="oauth.token_issued",
                resource_type="application_session",
                resource_id=session.id,
                actor_email=user.email,
                scope_type="tenant",
                scope_id=tenant.id,
                request=request,
                metadata={
                    "grant_type": grant_type,
                    "client_id": client.client_id,
                    "application_id": str(application.id),
                    "scopes": scopes,
                },
            )
        return payload

    if grant_type == "refresh_token":
        if not refresh_token:
            raise OAuthError("invalid_request", "refresh_token is required.")
        payload = await auth_service.rotate_refresh_token(
            db, client=client, refresh_token=refresh_token, scope=scope, ip=ip, user_agent=ua
        )
        client.last_used_at = _now()
        session_id = payload.get("session_id")
        tenant_id = None
        if session_id:
            session = await db.get(ApplicationSession, uuid.UUID(str(session_id)))
            tenant_id = session.tenant_id if session else None
        if tenant_id:
            async with tenant_scope(db, tenant_id):
                await record_audit(
                    db,
                    action="oauth.token_refreshed",
                    resource_type="application_session",
                    resource_id=session_id,
                    scope_type="tenant",
                    scope_id=tenant_id,
                    request=request,
                    metadata={"client_id": client.client_id},
                )
        else:
            await db.commit()
        return payload

    raise OAuthError("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")


async def _client_credentials_token(
    request: Request,
    client_id: str | None,
    client_secret: str | None,
    authorization: str | None,
    scope: str | None,
    db: AsyncSession,
) -> dict[str, Any]:
    basic_id, basic_secret = _basic_credentials(authorization)
    client_id = basic_id or client_id
    client_secret = basic_secret or client_secret
    if not client_id or not client_secret:
        raise Unauthorized("Client credentials are required.")

    ip = getattr(request.state, "client_ip", None)
    allowed, _ = await hit(f"oauth:{ip}:{client_id}", limit=60, window_seconds=60)
    if not allowed:
        raise Forbidden("Too many token requests. Try again shortly.")

    try:
        credential = await db.get(ApiCredential, uuid.UUID(client_id))
    except ValueError as exc:
        raise Unauthorized("Invalid client credentials.") from exc
    now = utcnow()
    if (
        credential is None
        or credential.status != "active"
        or credential.revoked_at is not None
        or not secrets.compare_digest(credential.secret_hash, hash_token(client_secret))
    ):
        raise Unauthorized("Invalid client credentials.")
    if credential.expires_at and credential.expires_at.replace(tzinfo=now.tzinfo) <= now:
        raise Unauthorized("This client credential has expired.")

    app = await db.get(Application, credential.application_id)
    if app is None or app.status in ("archived", "inactive"):
        raise Forbidden("The application is not active.")

    granted = list(credential.scopes or [])
    requested = [s for s in (scope or "").split() if s]
    if requested:
        unknown = set(requested) - set(granted)
        if unknown:
            raise ValidationFailed(f"Requested scopes were not granted: {sorted(unknown)}")
        granted = requested

    policy = await get_effective_policy(db)
    ttl_seconds = max(int(policy.get("api_token_ttl_minutes", 60)) * 60, 60)
    access_token, expires_in = create_service_token(
        application_id=app.id,
        credential_id=credential.id,
        scopes=granted,
        ttl_seconds=ttl_seconds,
    )
    credential.last_used_at = now
    await record_audit(
        db,
        action="oauth.token_issued",
        resource_type="api_credential",
        resource_id=credential.id,
        actor_email=f"service:{credential.id}",
        scope_type="application",
        scope_id=app.id,
        request=request,
        metadata={"scopes": granted, "grant_type": "client_credentials"},
    )
    await db.commit()
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "scope": " ".join(granted),
    }


@router.get("/userinfo", response_model=dict, summary="OIDC userinfo for an application access token")
async def userinfo(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    token = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value
    if not token:
        raise OAuthError("invalid_token", "A Bearer application access token is required.", 401)
    return await auth_service.userinfo(db, token)


@router.post("/revoke", status_code=200, summary="Revoke an application refresh token (RFC 7009)")
async def revoke(
    token: str = Form(...),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> Response:
    basic_id, basic_secret = _basic_credentials(authorization)
    client_id = basic_id or client_id
    client_secret = basic_secret or client_secret
    if not client_id:
        raise OAuthError("invalid_client", "client_id is required.", 401)
    client = await auth_service.load_client(db, client_id)
    if not auth_service.verify_client_secret(client, client_secret):
        raise OAuthError("invalid_client", "Client authentication failed.", 401)
    revoked = await auth_service.revoke_refresh_token(db, client, token)
    if not revoked:
        # Access tokens are stateless; revoke the session they point at instead.
        try:
            claims = decode_app_token(token, expected_audience=client.client_id)
        except Exception:  # noqa: BLE001 - unknown token is a no-op per RFC 7009
            claims = None
        if claims and claims.session_id:
            session = await db.get(ApplicationSession, uuid.UUID(claims.session_id))
            if session and session.application_client_id == client.id and session.revoked_at is None:
                session.revoked_at = _now()
                session.revoked_reason = "revoked"
    await db.commit()
    return Response(status_code=200, content="", media_type="text/plain")


@router.get("/logout", summary="RP-initiated logout and console session revocation")
async def logout(
    request: Request,
    client_id: str | None = None,
    post_logout_redirect_uri: str | None = None,
    state: str | None = None,
    id_token_hint: str | None = None,
    principal: Principal | None = Depends(get_optional_principal),
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if not client_id:
        raise OAuthError("invalid_request", "client_id is required.")
    client = await auth_service.load_client(db, client_id)
    if post_logout_redirect_uri:
        auth_service.validate_post_logout_uri(client, post_logout_redirect_uri)

    if principal is not None and principal.session_id:
        await identity_service.revoke_session(
            db, user_id=principal.user.id, session_id=principal.session_id, reason="logout"
        )

    response: RedirectResponse
    if post_logout_redirect_uri:
        params = {"state": state} if state else {}
        separator = "&" if "?" in post_logout_redirect_uri else "?"
        target = (
            f"{post_logout_redirect_uri}{separator}{urlencode(params)}"
            if params
            else post_logout_redirect_uri
        )
        response = RedirectResponse(target, status_code=303)
    else:
        response = RedirectResponse(f"{settings.web_base_url}/login", status_code=303)
    cookie_attributes = {
        "path": "/",
        "domain": settings.cookie_domain or None,
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }
    response.delete_cookie("basa_access", **cookie_attributes)
    response.delete_cookie("bsa_refresh", **cookie_attributes)
    return response


# --------------------------------------------------------------- embedded login


async def _embedded_client(
    db: AsyncSession, client_id: str, client_secret: str
) -> tuple[ApplicationClient, Application]:
    client = await auth_service.load_client(db, client_id)
    if client.client_type != "confidential":
        raise OAuthError(
            "invalid_client",
            "Embedded login is available only to confidential clients. "
            "Use the hosted flow for public clients.",
        )
    if not auth_service.verify_client_secret(client, client_secret):
        raise OAuthError("invalid_client", "Client authentication failed.", 401)
    return client, await auth_service.load_application(db, client)


async def _embedded_success(
    request: Request,
    db: AsyncSession,
    *,
    client: ApplicationClient,
    application: Application,
    user: User,
    tenant: Tenant,
    scopes: list[str],
    auth_time: datetime,
    amr: list[str],
) -> dict[str, Any]:
    ip, ua = _client(request)
    session, refresh_token = await auth_service.create_application_session(
        db,
        client=client,
        application=application,
        user=user,
        tenant=tenant,
        scopes=scopes,
        nonce=None,
        auth_time=auth_time,
        amr=amr,
        ip=ip,
        user_agent=ua,
    )
    payload = await auth_service.issue_tokens(
        db,
        client=client,
        application=application,
        user=user,
        tenant=tenant,
        session=session,
        refresh_token=refresh_token,
        scopes=scopes,
        nonce=None,
    )
    client.last_used_at = _now()
    async with tenant_scope(db, tenant.id):
        await record_audit(
            db,
            action="oauth.embedded_login",
            resource_type="application_session",
            resource_id=session.id,
            actor_email=user.email,
            scope_type="tenant",
            scope_id=tenant.id,
            request=request,
            metadata={
                "client_id": client.client_id,
                "application_id": str(application.id),
                "scopes": scopes,
            },
        )
    return {
        "status": "authenticated",
        **payload,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "avatar_url": user.avatar_url,
        },
        "tenant": {"id": str(tenant.id), "slug": tenant.slug, "name": tenant.name},
        "application": {"id": str(application.id), "name": application.name},
    }


async def _embedded_tenant(db: AsyncSession, application: Application, hint: str | None) -> Tenant:
    """Resolve the tenant hint, keeping embedded responses in the OAuth envelope."""
    try:
        return await auth_service.resolve_tenant(db, application, hint)
    except AppError as exc:
        raise OAuthError("invalid_request", exc.message) from exc


@router.post("/embedded/login", response_model=dict, summary="Embedded server-side end-user login")
async def embedded_login(
    payload: EmbeddedLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ip, _ = _client(request)
    allowed, _ = await hit(f"oauth:embedded:{ip}:{payload.client_id}", limit=20, window_seconds=300)
    if not allowed:
        raise OAuthError("temporarily_unavailable", "Too many sign-in attempts. Try again later.", 429)

    client, application = await _embedded_client(db, payload.client_id, payload.client_secret)
    tenant = await _embedded_tenant(db, application, payload.tenant)
    scopes = auth_service.normalize_scopes(client, payload.scope)

    user, tokens, challenge, challenge_mode = await identity_service.authenticate(
        db,
        email=str(payload.email),
        password=payload.password,
        ip=ip,
        user_agent=getattr(request.state, "user_agent", None),
        issue_session=False,
    )
    if challenge:
        if challenge_mode == "enroll":
            raise OAuthError("access_denied", "MFA enrollment is required before this account can sign in.")
        return {"status": "mfa_required", "challenge_token": challenge}
    # ``tokens`` is always None here because the gateway never creates a console
    # session for embedded clients.
    await auth_service.verify_membership(db, user, tenant)
    return await _embedded_success(
        request,
        db,
        client=client,
        application=application,
        user=user,
        tenant=tenant,
        scopes=scopes,
        auth_time=_now(),
        amr=["pwd"],
    )


@router.post("/embedded/mfa", response_model=dict, summary="Complete embedded login with MFA")
async def embedded_mfa(
    payload: EmbeddedMfaRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ip, ua = _client(request)
    allowed, _ = await hit(f"oauth:embedded-mfa:{ip}:{payload.client_id}", limit=20, window_seconds=300)
    if not allowed:
        raise OAuthError("temporarily_unavailable", "Too many verification attempts. Try again later.", 429)

    client, application = await _embedded_client(db, payload.client_id, payload.client_secret)
    tenant = await _embedded_tenant(db, application, payload.tenant)
    scopes = auth_service.normalize_scopes(client, payload.scope)

    user, _ = await identity_service.verify_mfa(
        db,
        challenge_token=payload.challenge_token,
        code=payload.code,
        ip=ip,
        user_agent=ua,
        issue_session=False,
    )
    await auth_service.verify_membership(db, user, tenant)
    return await _embedded_success(
        request,
        db,
        client=client,
        application=application,
        user=user,
        tenant=tenant,
        scopes=scopes,
        auth_time=_now(),
        amr=["pwd", "mfa"],
    )
