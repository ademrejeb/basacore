from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import get_settings
from app.core.db import get_session, utcnow
from app.core.deps import Principal, get_principal, require_platform
from app.core.enums import ScopeType, UserStatus, UserType
from app.core.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from app.core.pagination import Page, PageParams, page_params, paginate
from app.core.permissions import exceeded_grants, permissions_for_role_key
from app.core.rate_limit import hit
from app.core.security import (
    hash_password,
    validate_password_strength,
)
from app.modules.access.models import Role, RolePermission, UserRole
from app.modules.access.policy import get_effective_policy
from app.modules.identity import service
from app.modules.identity.models import (
    AuthenticationEvent,
    User,
)
from app.modules.identity.schemas import (
    AuthEventOut,
    InviteAcceptRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    MfaConfirmRequest,
    MfaEnrollmentResponse,
    MfaEnrollRequest,
    MfaSetupConfirmRequest,
    MfaSetupStartRequest,
    MfaVerifyRequest,
    PasswordChangeRequest,
    PasswordConfirmRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    SessionOut,
    TokenBundle,
    UserCreateRequest,
    UserDetail,
    UserSummary,
    UserUpdateRequest,
)

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])
users_router = APIRouter(prefix="/users", tags=["Identity"])


def _client(request: Request) -> tuple[str | None, str | None]:
    return getattr(request.state, "client_ip", None), getattr(request.state, "user_agent", None)


def _set_auth_cookies(response: Response, tokens: TokenBundle) -> None:
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "domain": settings.cookie_domain or None,
    }
    response.set_cookie(
        "basa_access",
        tokens.access_token,
        max_age=tokens.expires_in,
        path="/",
        **common,
    )
    response.set_cookie(
        "bsa_refresh",
        tokens.refresh_token,
        max_age=max(tokens.refresh_expires_in, 0),
        # The admin console talks to the API through a same-origin proxy, so the
        # refresh cookie must be sent to the proxy path rather than /v1/auth.
        path="/",
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    # Deletion must repeat the attributes used when the cookie was written,
    # otherwise a COOKIE_DOMAIN deployment keeps sending the stale cookie.
    common = {
        "path": "/",
        "domain": settings.cookie_domain or None,
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }
    response.delete_cookie("basa_access", **common)
    response.delete_cookie("bsa_refresh", **common)


@router.post("/login", response_model=LoginResponse, summary="Authenticate with email and password")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> LoginResponse:
    ip, ua = _client(request)
    email = payload.email.lower()
    allowed_ip, _ = await hit(f"login:ip:{ip}", limit=40, window_seconds=300)
    allowed_pair, _ = await hit(f"login:pair:{ip}:{email}", limit=10, window_seconds=300)
    if not allowed_ip or not allowed_pair:
        raise AppError("rate_limited", "Too many sign-in attempts. Try again in a few minutes.", 429)

    user, tokens, challenge, challenge_mode = await service.authenticate(
        db, email=email, password=payload.password, ip=ip, user_agent=ua
    )
    if challenge:
        return LoginResponse(
            status=("mfa_enrollment_required" if challenge_mode == "enroll" else "mfa_required"),
            challenge_token=challenge,
            user=UserSummary.model_validate(user),
        )
    if tokens:
        _set_auth_cookies(response, tokens)
    return LoginResponse(status="authenticated", tokens=tokens, user=UserSummary.model_validate(user))


@router.post("/mfa/verify", response_model=LoginResponse, summary="Complete MFA sign-in")
async def mfa_verify(
    payload: MfaVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> LoginResponse:
    ip, ua = _client(request)
    allowed, _ = await hit(f"mfa:ip:{ip}", limit=20, window_seconds=300)
    if not allowed:
        raise AppError("rate_limited", "Too many verification attempts. Try again later.", 429)
    user, tokens = await service.verify_mfa(
        db, challenge_token=payload.challenge_token, code=payload.code, ip=ip, user_agent=ua
    )
    _set_auth_cookies(response, tokens)
    return LoginResponse(status="authenticated", tokens=tokens, user=UserSummary.model_validate(user))


@router.post("/refresh", response_model=TokenBundle, summary="Rotate a refresh token")
async def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    db: AsyncSession = Depends(get_session),
    refresh_cookie: str | None = Cookie(default=None, alias="bsa_refresh"),
) -> TokenBundle:
    ip, ua = _client(request)
    token = (payload.refresh_token if payload else None) or refresh_cookie
    if not token:
        raise Unauthorized("A refresh token is required.")
    _, tokens = await service.rotate_refresh_token(db, refresh_token=token, ip=ip, user_agent=ua)
    _set_auth_cookies(response, tokens)
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke the current session")
async def logout(
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
) -> Response:
    if principal.session_id:
        await service.revoke_session(db, user_id=principal.user.id, session_id=principal.session_id)
    _clear_auth_cookies(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/accept-invite", response_model=LoginResponse, summary="Accept an invitation and set a password"
)
async def accept_invite(
    payload: InviteAcceptRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> LoginResponse:
    ip, ua = _client(request)
    allowed, _ = await hit(f"invite:ip:{ip}", limit=20, window_seconds=300)
    if not allowed:
        raise AppError("rate_limited", "Too many attempts. Try again later.", 429)
    user, tokens = await service.accept_invite(
        db,
        invite_token=payload.token,
        new_password=payload.new_password,
        full_name=payload.full_name,
        ip=ip,
        user_agent=ua,
    )
    _set_auth_cookies(response, tokens)
    return LoginResponse(status="authenticated", tokens=tokens, user=UserSummary.model_validate(user))


@router.post("/password-reset/request", response_model=dict, summary="Request a password reset")
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ip, _ = _client(request)
    allowed, _ = await hit(f"pwreset:ip:{ip}", limit=10, window_seconds=900)
    allowed_email, _ = await hit(f"pwreset:email:{payload.email.lower()}", limit=5, window_seconds=900)
    if not allowed or not allowed_email:
        raise AppError("rate_limited", "Too many reset requests. Try again later.", 429)
    dev_token = await service.request_password_reset(db, email=str(payload.email))
    return {
        "message": "If the account exists, reset instructions have been sent.",
        "dev_token": dev_token,
    }


@router.post(
    "/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT, summary="Complete a password reset"
)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    db: AsyncSession = Depends(get_session),
) -> Response:
    await service.reset_password(db, reset_token=payload.token, new_password=payload.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse, summary="Current identity, scopes and permissions")
async def me(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
) -> MeResponse:
    return await service.build_me(db, principal.user)


@router.get("/sessions", response_model=list[SessionOut], summary="Active sessions for the current user")
async def sessions(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    return await service.list_sessions(db, principal.user.id, principal.session_id)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
) -> Response:
    revoked = await service.revoke_session(
        db, user_id=principal.user.id, session_id=session_id, reason="user_revoked"
    )
    if not revoked:
        raise NotFound("Session", str(session_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
) -> Response:
    await service.revoke_all_sessions(db, principal.user.id, except_session=principal.session_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mfa/setup/start", response_model=MfaEnrollmentResponse)
async def start_mfa_setup(
    payload: MfaSetupStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MfaEnrollmentResponse:
    ip, _ = _client(request)
    allowed, _ = await hit(f"mfa-setup:{ip}", limit=20, window_seconds=300)
    if not allowed:
        raise AppError("rate_limited", "Too many attempts. Try again later.", 429)
    secret, uri = await service.start_mfa_setup(db, challenge_token=payload.challenge_token)
    return MfaEnrollmentResponse(secret=secret, provisioning_uri=uri)


@router.post("/mfa/setup/confirm", response_model=LoginResponse)
async def complete_mfa_setup(
    payload: MfaSetupConfirmRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> LoginResponse:
    ip, ua = _client(request)
    allowed, _ = await hit(f"mfa-setup-confirm:{ip}", limit=20, window_seconds=300)
    if not allowed:
        raise AppError("rate_limited", "Too many attempts. Try again later.", 429)
    user, tokens, codes = await service.complete_mfa_setup(
        db, challenge_token=payload.challenge_token, code=payload.code, ip=ip, user_agent=ua
    )
    _set_auth_cookies(response, tokens)
    return LoginResponse(
        status="authenticated",
        tokens=tokens,
        recovery_codes=codes,
        user=UserSummary.model_validate(user),
    )


@router.post("/mfa/enroll", response_model=MfaEnrollmentResponse)
async def enroll_mfa(
    payload: MfaEnrollRequest | None = None,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
) -> MfaEnrollmentResponse:
    secret, uri = await service.enroll_mfa(db, principal.user, payload.current_password if payload else None)
    return MfaEnrollmentResponse(secret=secret, provisioning_uri=uri)


@router.post("/mfa/confirm", response_model=MfaEnrollmentResponse)
async def confirm_mfa(
    payload: MfaConfirmRequest,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
) -> MfaEnrollmentResponse:
    codes = await service.confirm_mfa(db, principal.user, payload.code)
    return MfaEnrollmentResponse(secret="", provisioning_uri="", recovery_codes=codes)


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_mfa(
    payload: PasswordConfirmRequest,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await service.disable_mfa(db, principal.user, payload.current_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChangeRequest,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await service.change_password(
        db,
        principal.user,
        payload.current_password,
        payload.new_password,
        current_session_id=principal.session_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/events", response_model=list[AuthEventOut], summary="Recent authentication events")
async def my_auth_events(
    limit: int = 25,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
) -> list[AuthenticationEvent]:
    rows = (
        (
            await db.execute(
                select(AuthenticationEvent)
                .where(AuthenticationEvent.user_id == principal.user.id)
                .order_by(AuthenticationEvent.created_at.desc())
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# ─────────────────────────── user administration ───────────────────────────


def _role_payload(assignment: UserRole, role: Role) -> dict[str, Any]:
    return {
        "role_id": role.id,
        "role_key": role.key,
        "role_name": role.name,
        "scope_type": assignment.scope_type,
        "scope_id": assignment.scope_id,
    }


async def _user_details(db: AsyncSession, users: list[User]) -> list[UserDetail]:
    if not users:
        return []
    rows = (
        await db.execute(
            select(UserRole, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_([user.id for user in users]))
        )
    ).all()
    grouped: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for assignment, role in rows:
        grouped.setdefault(assignment.user_id, []).append(_role_payload(assignment, role))
    return [
        UserDetail.model_validate(user).model_copy(update={"roles": grouped.get(user.id, [])})
        for user in users
    ]


async def _user_detail(db: AsyncSession, user: User) -> UserDetail:
    return (await _user_details(db, [user]))[0]


@users_router.get("", response_model=Page[UserDetail], summary="List users")
async def list_users(
    params: PageParams = Depends(page_params),
    user_status: UserStatus | None = None,
    user_type: UserType | None = None,
    _: Principal = Depends(require_platform("users.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(User).order_by(User.created_at.desc())
    if params.search:
        term = f"%{params.search.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(term), User.full_name.ilike(term)))
    if user_status:
        stmt = stmt.where(User.status == user_status.value)
    if user_type:
        stmt = stmt.where(User.user_type == user_type.value)
    users, total = await paginate(db, stmt, params)
    items = await _user_details(db, users)
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}


async def _assert_can_manage_user(db: AsyncSession, principal: Principal, user: User) -> None:
    rows = (
        await db.execute(
            select(UserRole, Role).join(Role, Role.id == UserRole.role_id).where(UserRole.user_id == user.id)
        )
    ).all()
    for assignment, role in rows:
        actor_permissions = await principal.permissions(
            db,
            organization_id=assignment.scope_id
            if assignment.scope_type == ScopeType.ORGANIZATION.value
            else None,
            application_id=assignment.scope_id
            if assignment.scope_type == ScopeType.APPLICATION.value
            else None,
            tenant_id=assignment.scope_id if assignment.scope_type == ScopeType.TENANT.value else None,
        )
        excess = exceeded_grants(permissions_for_role_key(role.key), actor_permissions)
        if excess:
            raise Forbidden(
                "You cannot manage a user with permissions you do not hold: " + ", ".join(sorted(excess))
            )


@users_router.post("", response_model=UserDetail, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    request: Request,
    principal: Principal = Depends(require_platform("users.invite")),
    db: AsyncSession = Depends(get_session),
) -> UserDetail:
    policy = await get_effective_policy(db, organization_id=payload.organization_id)
    problems = validate_password_strength(
        payload.password, min_length=int(policy.get("password_min_length", 12))
    )
    if problems:
        raise ValidationFailed(" ".join(problems))
    email = payload.email.lower()
    exists = (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
    if exists:
        raise Conflict("A user with that email already exists.")

    user = User(
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        user_type=payload.user_type.value,
        status=UserStatus.ACTIVE.value,
        job_title=payload.job_title,
        department=payload.department,
        email_verified_at=utcnow(),
        password_changed_at=utcnow(),
    )
    db.add(user)
    await db.flush()

    if payload.organization_id:
        from app.modules.organizations.models import Organization

        if await db.get(Organization, payload.organization_id) is None:
            raise NotFound("Organization", str(payload.organization_id))

    for role_key in payload.role_keys:
        role = (await db.execute(select(Role).where(Role.key == role_key))).scalar_one_or_none()
        if role is None:
            raise ValidationFailed(f"Unknown role: {role_key}")
        if role.scope_type == ScopeType.PLATFORM.value and not payload.user_type == UserType.INTERNAL:
            raise ValidationFailed("External users cannot hold platform roles.")
        scope_id = None
        if role.scope_type == ScopeType.ORGANIZATION.value:
            if not payload.organization_id:
                raise ValidationFailed("organization_id is required for organization roles.")
            scope_id = payload.organization_id
        elif role.scope_type != ScopeType.PLATFORM.value:
            raise ValidationFailed(f"Role {role_key} must be assigned in its resource context.")
        actor_permissions = await principal.permissions(
            db, organization_id=scope_id if role.scope_type == ScopeType.ORGANIZATION.value else None
        )
        excess = exceeded_grants(permissions_for_role_key(role.key), actor_permissions)
        if excess:
            raise Forbidden(
                "You cannot create a user with permissions you do not hold: " + ", ".join(sorted(excess))
            )
        db.add(
            UserRole(
                user_id=user.id,
                role_id=role.id,
                scope_type=role.scope_type,
                scope_id=scope_id,
                granted_by=principal.user.id,
                created_at=utcnow(),
            )
        )

    await record_audit(
        db,
        action="user.created",
        resource_type="user",
        resource_id=user.id,
        actor=principal,
        request=request,
        metadata={"email": email, "roles": payload.role_keys, "user_type": payload.user_type.value},
    )
    await db.commit()
    return await _user_detail(db, user)


@users_router.get("/{user_id}", response_model=UserDetail)
async def get_user(
    user_id: uuid.UUID,
    _: Principal = Depends(require_platform("users.read")),
    db: AsyncSession = Depends(get_session),
) -> UserDetail:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User", str(user_id))
    return await _user_detail(db, user)


@users_router.patch("/{user_id}", response_model=UserDetail)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    request: Request,
    principal: Principal = Depends(require_platform("users.update")),
    db: AsyncSession = Depends(get_session),
) -> UserDetail:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User", str(user_id))
    await _assert_can_manage_user(db, principal, user)
    changes = payload.model_dump(exclude_unset=True)
    previous_type = user.user_type
    for field, value in changes.items():
        if value is not None:
            setattr(user, field, value.value if hasattr(value, "value") else value)
    # Keep the role invariants enforced at assignment time: external users may
    # not hold platform roles, internal users do not use tenant roles.
    if user.user_type == UserType.EXTERNAL.value and previous_type != UserType.EXTERNAL.value:
        await db.execute(
            delete(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.scope_type == ScopeType.PLATFORM.value,
            )
        )
    elif user.user_type == UserType.INTERNAL.value and previous_type != UserType.INTERNAL.value:
        await db.execute(
            delete(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.scope_type == ScopeType.TENANT.value,
            )
        )
    if changes.get("status") in (UserStatus.SUSPENDED, UserStatus.DISABLED):
        await service.revoke_all_sessions(db, user.id)
    await record_audit(
        db,
        action="user.updated",
        resource_type="user",
        resource_id=user.id,
        actor=principal,
        request=request,
        metadata={"changes": {k: str(v) for k, v in changes.items()}},
    )
    await db.commit()
    return await _user_detail(db, user)


@users_router.post("/{user_id}/roles", response_model=UserDetail)
async def assign_role(
    user_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    principal: Principal = Depends(require_platform("roles.manage")),
    db: AsyncSession = Depends(get_session),
) -> UserDetail:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User", str(user_id))
    role_key = payload.get("role_key", "")
    role = (await db.execute(select(Role).where(Role.key == role_key))).scalar_one_or_none()
    if role is None:
        raise ValidationFailed("Unknown role.")
    scope_type = payload.get("scope_type", role.scope_type)
    scope_id_raw = payload.get("scope_id")
    if scope_type != role.scope_type:
        raise ValidationFailed("The role scope does not match the requested scope.")
    scope_id = uuid.UUID(scope_id_raw) if scope_id_raw else None
    if role.scope_type == ScopeType.PLATFORM.value:
        if user.user_type != UserType.INTERNAL.value:
            raise ValidationFailed("External users cannot hold platform roles.")
        scope_id = None
    elif scope_id is None:
        raise ValidationFailed("scope_id is required for this role.")
    if role.scope_type == ScopeType.TENANT.value and user.user_type == UserType.INTERNAL.value:
        raise ValidationFailed("Internal users should use application or platform roles.")

    role_permissions = set(
        (await db.execute(select(RolePermission.permission_key).where(RolePermission.role_id == role.id)))
        .scalars()
        .all()
    )
    actor_permissions = await principal.permissions(
        db,
        organization_id=scope_id if scope_type == ScopeType.ORGANIZATION.value else None,
        application_id=scope_id if scope_type == ScopeType.APPLICATION.value else None,
        tenant_id=scope_id if scope_type == ScopeType.TENANT.value else None,
    )
    excess = exceeded_grants(role_permissions, actor_permissions)
    if excess:
        raise Forbidden(
            "You cannot assign a role that grants permissions you do not hold: " + ", ".join(sorted(excess))
        )

    existing = (
        await db.execute(
            select(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.role_id == role.id,
                UserRole.scope_type == scope_type,
                UserRole.scope_id == scope_id if scope_id else UserRole.scope_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise Conflict("This role is already assigned in that scope.")
    db.add(
        UserRole(
            user_id=user.id,
            role_id=role.id,
            scope_type=scope_type,
            scope_id=scope_id,
            granted_by=principal.user.id,
            created_at=utcnow(),
        )
    )
    await record_audit(
        db,
        action="role.assigned",
        resource_type="user",
        resource_id=user.id,
        actor=principal,
        request=request,
        scope_type=scope_type,
        scope_id=scope_id,
        metadata={"role_key": role.key},
    )
    await db.commit()
    return await _user_detail(db, user)


@users_router.delete("/{user_id}/roles/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role(
    user_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_platform("roles.manage")),
    db: AsyncSession = Depends(get_session),
) -> Response:
    assignment = await db.get(UserRole, assignment_id)
    if assignment is None or assignment.user_id != user_id:
        raise NotFound("Role assignment", str(assignment_id))
    target = await db.get(User, user_id)
    if target is None:
        raise NotFound("User", str(user_id))
    await _assert_can_manage_user(db, principal, target)
    await db.delete(assignment)
    await record_audit(
        db,
        action="role.revoked",
        resource_type="user",
        resource_id=user_id,
        actor=principal,
        request=request,
        scope_type=assignment.scope_type,
        scope_id=assignment.scope_id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@users_router.post("/{user_id}/unlock", response_model=UserDetail)
async def unlock_user(
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_platform("users.update")),
    db: AsyncSession = Depends(get_session),
) -> UserDetail:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User", str(user_id))
    await _assert_can_manage_user(db, principal, user)
    user.failed_login_count = 0
    user.locked_until = None
    await record_audit(
        db,
        action="user.unlocked",
        resource_type="user",
        resource_id=user.id,
        actor=principal,
        request=request,
    )
    await db.commit()
    return await _user_detail(db, user)


@users_router.get("/{user_id}/sessions", response_model=list[SessionOut])
async def admin_user_sessions(
    user_id: uuid.UUID,
    _: Principal = Depends(require_platform("sessions.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await service.list_sessions(db, user_id, None)


@users_router.post("/{user_id}/sessions/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def admin_revoke_sessions(
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_platform("sessions.revoke")),
    db: AsyncSession = Depends(get_session),
) -> Response:
    target = await db.get(User, user_id)
    if target is None:
        raise NotFound("User", str(user_id))
    await _assert_can_manage_user(db, principal, target)
    count = await service.revoke_all_sessions(db, user_id)
    await record_audit(
        db,
        action="sessions.revoked",
        resource_type="user",
        resource_id=user_id,
        actor=principal,
        request=request,
        metadata={"revoked": count},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
