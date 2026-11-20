from __future__ import annotations

import ipaddress
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import MembershipStatus, ScopeType, UserStatus
from app.core.errors import Forbidden, Unauthorized, ValidationFailed
from app.core.mailer import send_email
from app.core.security import (
    create_access_token,
    create_mfa_challenge,
    decode_token,
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    secret_box,
    validate_password_strength,
    verify_password,
)
from app.modules.access.models import Role, UserRole
from app.modules.access.policy import get_effective_policy, get_effective_policy_for_user
from app.modules.identity.models import (
    AuthenticationEvent,
    MfaRecoveryCode,
    PasswordHistory,
    User,
    UserSession,
    VerificationToken,
)
from app.modules.identity.schemas import (
    IdentityScope,
    MeResponse,
    TokenBundle,
    UserSummary,
)
from app.modules.organizations.models import Organization
from app.modules.tenants.models import TenantMembership

settings = get_settings()

_DUMMY_HASH = hash_password("not-a-real-password-9x!")


def _now() -> datetime:
    return datetime.now(UTC)


async def record_auth_event(
    db: AsyncSession,
    *,
    event_type: str,
    outcome: str,
    user: User | None = None,
    email: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuthenticationEvent(
            user_id=user.id if user else None,
            email=(user.email if user else email) or None,
            event_type=event_type,
            outcome=outcome,
            ip_address=ip,
            user_agent=(user_agent or "")[:500] or None,
            event_metadata={} if metadata is None else metadata,
            created_at=_now(),
        )
    )


async def _create_session(
    db: AsyncSession, user: User, *, ip: str | None, user_agent: str | None, policy: dict[str, Any]
) -> tuple[UserSession, str]:
    now = _now()
    ttl_days = int(policy.get("refresh_token_ttl_days", settings.refresh_token_ttl_days))
    max_sessions = int(policy.get("max_concurrent_sessions", 10))

    active = (
        (
            await db.execute(
                select(UserSession)
                .where(
                    UserSession.user_id == user.id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > now,
                )
                .order_by(UserSession.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for old in active[: max(0, len(active) - max_sessions + 1)]:
        old.revoked_at = now
        old.revoked_reason = "session_limit"
        await record_auth_event(
            db,
            event_type="session_revoked",
            outcome="success",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"reason": "session_limit", "session_id": str(old.id)},
        )

    refresh_token = generate_token(48)
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        user_agent=(user_agent or "")[:500] or None,
        ip_address=ip,
        mfa_verified=user.mfa_enabled,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=ttl_days),
    )
    db.add(session)
    await db.flush()
    return session, refresh_token


def _tokens(user: User, session: UserSession, refresh_token: str) -> TokenBundle:
    access_token, ttl = create_access_token(
        user_id=user.id,
        session_id=session.id,
        email=user.email,
        user_type=user.user_type,
    )
    refresh_ttl = int((session.expires_at - _now()).total_seconds())
    return TokenBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ttl,
        refresh_expires_in=max(refresh_ttl, 0),
        session_id=session.id,
    )


async def authenticate(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
    issue_session: bool = True,
) -> tuple[User, TokenBundle | None, str | None, str | None]:
    """Return (user, tokens, challenge, challenge_mode).

    ``challenge_mode`` is ``verify`` for an enrolled second factor or ``enroll``
    when policy requires MFA and the user has not enrolled yet. Exactly one of
    ``tokens`` and ``challenge`` is set.
    """
    platform_policy = await get_effective_policy(db)
    _enforce_ip_policy(platform_policy, ip)
    normalized = email.strip().lower()
    user = (await db.execute(select(User).where(User.email == normalized))).scalar_one_or_none()

    if user is None:
        verify_password(password, _DUMMY_HASH)
        await record_auth_event(
            db,
            event_type="login",
            outcome="failure",
            email=normalized,
            ip=ip,
            user_agent=user_agent,
            metadata={"reason": "unknown_account"},
        )
        await db.commit()
        raise Unauthorized("Email or password is incorrect.")

    policy = await get_effective_policy_for_user(db, user)
    _enforce_ip_policy(policy, ip)
    now = _now()
    if user.locked_until and user.locked_until.replace(tzinfo=UTC) > now:
        await record_auth_event(
            db,
            event_type="login",
            outcome="failure",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"reason": "account_locked"},
        )
        await db.commit()
        raise Forbidden("This account is temporarily locked. Try again later.")

    if user.status != UserStatus.ACTIVE.value:
        await record_auth_event(
            db,
            event_type="login",
            outcome="failure",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"reason": f"status_{user.status}"},
        )
        await db.commit()
        raise Forbidden("This account is not active.")

    if not user.password_hash or not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        max_attempts = int(policy.get("max_failed_attempts", 5))
        if user.failed_login_count >= max_attempts:
            user.locked_until = now + timedelta(minutes=int(policy.get("lockout_minutes", 15)))
        await record_auth_event(
            db,
            event_type="login",
            outcome="failure",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"reason": "bad_password", "failed_count": user.failed_login_count},
        )
        await db.commit()
        raise Unauthorized("Email or password is incorrect.")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    if bool(policy.get("require_email_verification")) and user.email_verified_at is None:
        await record_auth_event(
            db,
            event_type="login",
            outcome="failure",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"reason": "email_not_verified"},
        )
        await db.commit()
        raise Forbidden("Verify your email address before signing in.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    needs_enrollment = await _mfa_required_for(db, user, policy)

    if needs_enrollment and not user.mfa_enabled:
        await record_auth_event(
            db,
            event_type="mfa_enrollment_required",
            outcome="success",
            user=user,
            ip=ip,
            user_agent=user_agent,
        )
        await db.commit()
        return user, None, create_mfa_challenge(user.id, mode="enroll"), "enroll"

    if user.mfa_enabled:
        await record_auth_event(
            db, event_type="mfa_challenge", outcome="success", user=user, ip=ip, user_agent=user_agent
        )
        await db.commit()
        return user, None, create_mfa_challenge(user.id, mode="verify"), "verify"

    if not issue_session:
        await record_auth_event(
            db,
            event_type="login",
            outcome="success",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"embedded": True},
        )
        await db.commit()
        return user, None, None, None

    session, refresh_token = await _create_session(db, user, ip=ip, user_agent=user_agent, policy=policy)
    await record_auth_event(
        db,
        event_type="login",
        outcome="success",
        user=user,
        ip=ip,
        user_agent=user_agent,
        metadata={"session_id": str(session.id)},
    )
    await db.commit()
    return user, _tokens(user, session, refresh_token), None, None


async def _mfa_required_for(db: AsyncSession, user: User, policy: dict[str, Any]) -> bool:
    if bool(policy.get("mfa_required")):
        return True
    return bool(policy.get("mfa_required_for_admins")) and await _is_platform_admin(db, user)


async def _is_platform_admin(db: AsyncSession, user: User) -> bool:
    from app.modules.access.models import Role

    stmt = (
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            UserRole.user_id == user.id,
            Role.scope_type == ScopeType.PLATFORM.value,
            or_(UserRole.expires_at.is_(None), UserRole.expires_at > _now()),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


def _enforce_ip_policy(policy: dict[str, Any], ip: str | None) -> None:
    allowed = policy.get("allowed_ip_cidrs") or []
    if not allowed:
        return
    if not ip:
        raise Forbidden("This network is not permitted.")
    try:
        address = ipaddress.ip_address(ip)
        networks = [ipaddress.ip_network(cidr, strict=False) for cidr in allowed]
    except ValueError as exc:
        raise Forbidden("The configured network policy is invalid.") from exc
    if not any(address in network for network in networks):
        raise Forbidden("This network is not permitted.")


MFA_PENDING_TTL_MINUTES = 15
RECOVERY_CODE_COUNT = 10


def _decode_mfa_challenge(challenge_token: str, *, mode: str) -> uuid.UUID:
    try:
        claims = decode_token(challenge_token, expected_type="mfa")
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("The verification challenge has expired. Sign in again.") from exc
    if claims.claims.get("mode", "verify") != mode:
        raise Unauthorized("This sign-in challenge cannot be used here.")
    try:
        return uuid.UUID(claims.subject)
    except ValueError as exc:
        raise Unauthorized("The verification challenge is invalid.") from exc


async def _load_challenge_user(db: AsyncSession, challenge_token: str, *, mode: str) -> User:
    user = await db.get(User, _decode_mfa_challenge(challenge_token, mode=mode))
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise Unauthorized("This account is not active.")
    return user


async def verify_mfa(
    db: AsyncSession,
    *,
    challenge_token: str,
    code: str,
    ip: str | None,
    user_agent: str | None,
    issue_session: bool = True,
) -> tuple[User, TokenBundle | None]:
    user = await _load_challenge_user(db, challenge_token, mode="verify")
    if not user.mfa_secret_encrypted:
        raise Unauthorized("Multi-factor authentication is not available for this account.")

    if not _verify_totp(user, code):
        recovery = await _consume_recovery_code(db, user, code)
        if not recovery:
            await record_auth_event(
                db, event_type="mfa_verify", outcome="failure", user=user, ip=ip, user_agent=user_agent
            )
            await db.commit()
            raise Unauthorized("The verification code is invalid.")

    policy = await get_effective_policy_for_user(db, user)
    if not issue_session:
        user.last_login_at = _now()
        await record_auth_event(
            db,
            event_type="login",
            outcome="success",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"mfa": True, "embedded": True},
        )
        await db.commit()
        return user, None

    session, refresh_token = await _create_session(db, user, ip=ip, user_agent=user_agent, policy=policy)
    session.mfa_verified = True
    user.last_login_at = _now()
    await record_auth_event(
        db,
        event_type="login",
        outcome="success",
        user=user,
        ip=ip,
        user_agent=user_agent,
        metadata={"mfa": True, "session_id": str(session.id)},
    )
    await db.commit()
    return user, _tokens(user, session, refresh_token)


async def start_mfa_setup(db: AsyncSession, *, challenge_token: str) -> tuple[str, str]:
    """Start enrollment from a login challenge, before any session exists."""
    user = await _load_challenge_user(db, challenge_token, mode="enroll")
    if user.mfa_enabled:
        raise ValidationFailed("Multi-factor authentication is already enabled.")
    secret, uri = await _stage_mfa_secret(db, user)
    return secret, uri


async def complete_mfa_setup(
    db: AsyncSession,
    *,
    challenge_token: str,
    code: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, TokenBundle, list[str]]:
    user = await _load_challenge_user(db, challenge_token, mode="enroll")
    codes = await confirm_mfa(db, user, code)
    policy = await get_effective_policy_for_user(db, user)
    session, refresh_token = await _create_session(db, user, ip=ip, user_agent=user_agent, policy=policy)
    session.mfa_verified = True
    user.last_login_at = _now()
    await record_auth_event(
        db,
        event_type="login",
        outcome="success",
        user=user,
        ip=ip,
        user_agent=user_agent,
        metadata={"mfa": True, "enrolled": True, "session_id": str(session.id)},
    )
    await db.commit()
    return user, _tokens(user, session, refresh_token), codes


def _verify_totp_secret(secret: str, code: str) -> bool:
    normalized = code.strip().replace(" ", "")
    if not normalized.isdigit() or len(normalized) != 6:
        return False
    return pyotp.TOTP(secret).verify(normalized, valid_window=1)


def _verify_totp(user: User, code: str) -> bool:
    try:
        secret = secret_box().decrypt(user.mfa_secret_encrypted or "")
    except ValueError:
        return False
    return _verify_totp_secret(secret, code)


async def _consume_recovery_code(db: AsyncSession, user: User, code: str) -> bool:
    normalized = code.strip().upper()
    code_hash = hash_token(normalized)
    record = (
        await db.execute(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == code_hash,
                MfaRecoveryCode.used_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return False
    record.used_at = _now()
    return True


async def rotate_refresh_token(
    db: AsyncSession, *, refresh_token: str, ip: str | None, user_agent: str | None
) -> tuple[User, TokenBundle]:
    token_hash = hash_token(refresh_token)
    # Serialize concurrent rotations of the same token. Without the row lock two
    # parallel refreshes both pass the reuse check and one of them is later
    # mistaken for theft, revoking every session the user owns.
    session = (
        await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash).with_for_update()
        )
    ).scalar_one_or_none()
    now = _now()

    if session is None:
        raise Unauthorized("Refresh token is invalid.")

    if session.revoked_at is not None:
        # A rotated token being replayed is a strong signal of theft.
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == session.user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason="refresh_token_reuse")
        )
        await record_auth_event(
            db,
            event_type="refresh_reuse_detected",
            outcome="failure",
            user=None,
            ip=ip,
            user_agent=user_agent,
            metadata={"user_id": str(session.user_id), "session_id": str(session.id)},
        )
        await db.commit()
        raise Unauthorized("Session reuse detected. All sessions have been revoked.")

    if session.expires_at.replace(tzinfo=UTC) <= now:
        raise Unauthorized("Refresh token has expired.")

    user = await db.get(User, session.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise Unauthorized("This account is not active.")

    policy = await get_effective_policy_for_user(db, user)
    if bool(policy.get("require_email_verification")) and user.email_verified_at is None:
        await revoke_all_sessions(db, user.id)
        await record_auth_event(
            db,
            event_type="session_revoked",
            outcome="failure",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"reason": "email_not_verified"},
        )
        await db.commit()
        raise Unauthorized("Verify your email address before continuing.")
    if await _mfa_required_for(db, user, policy) and not user.mfa_enabled:
        await revoke_all_sessions(db, user.id)
        await record_auth_event(
            db,
            event_type="session_revoked",
            outcome="failure",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"reason": "mfa_enrollment_required"},
        )
        await db.commit()
        raise Unauthorized("Multi-factor authentication enrollment is required. Sign in again.")
    idle_minutes = int(policy.get("session_idle_minutes") or 0)
    if idle_minutes and session.last_seen_at.replace(tzinfo=UTC) + timedelta(minutes=idle_minutes) <= now:
        session.revoked_at = now
        session.revoked_reason = "idle_timeout"
        await record_auth_event(
            db,
            event_type="session_idle_timeout",
            outcome="failure",
            user=user,
            ip=ip,
            user_agent=user_agent,
            metadata={"session_id": str(session.id)},
        )
        await db.commit()
        raise Unauthorized("Your session expired after a period of inactivity. Please sign in again.")

    new_session, new_refresh = await _create_session(db, user, ip=ip, user_agent=user_agent, policy=policy)
    session.revoked_at = now
    session.revoked_reason = "rotated"
    session.replaced_by_id = new_session.id
    session.last_seen_at = now
    await record_auth_event(
        db,
        event_type="token_refresh",
        outcome="success",
        user=user,
        ip=ip,
        user_agent=user_agent,
        metadata={"session_id": str(new_session.id)},
    )
    await db.commit()
    return user, _tokens(user, new_session, new_refresh)


async def revoke_session(
    db: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID, reason: str = "logout"
) -> bool:
    session = await db.get(UserSession, session_id)
    if session is None or session.user_id != user_id:
        return False
    if session.revoked_at is None:
        session.revoked_at = _now()
        session.revoked_reason = reason
        await record_auth_event(
            db,
            event_type="logout" if reason == "logout" else "session_revoked",
            outcome="success",
            user=await db.get(User, user_id),
            metadata={"session_id": str(session_id), "reason": reason},
        )
        await db.commit()
    return True


async def revoke_all_sessions(
    db: AsyncSession, user_id: uuid.UUID, *, except_session: uuid.UUID | None = None
) -> int:
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason="revoked")
    )
    if except_session:
        stmt = stmt.where(UserSession.id != except_session)
    result = await db.execute(stmt)
    return result.rowcount or 0


async def list_sessions(
    db: AsyncSession, user_id: uuid.UUID, current_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    sessions = (
        (
            await db.execute(
                select(UserSession)
                .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
                .order_by(UserSession.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": s.id,
            "user_id": s.user_id,
            "ip_address": s.ip_address,
            "user_agent": s.user_agent,
            "location": s.location,
            "mfa_verified": s.mfa_verified,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "expires_at": s.expires_at,
            "revoked_at": s.revoked_at,
            "current": s.id == current_id,
        }
        for s in sessions
    ]


async def _stage_mfa_secret(db: AsyncSession, user: User) -> tuple[str, str]:
    secret = pyotp.random_base32()
    user.mfa_pending_secret_encrypted = secret_box().encrypt(secret)
    user.mfa_pending_started_at = _now()
    await db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Basacore")
    return secret, uri


async def enroll_mfa(db: AsyncSession, user: User, current_password: str | None = None) -> tuple[str, str]:
    """Stage a new factor. The active factor keeps working until confirmation."""
    if user.mfa_enabled:
        if (
            not current_password
            or not user.password_hash
            or not verify_password(current_password, user.password_hash)
        ):
            raise Unauthorized("Your current password is required to change MFA.")
    return await _stage_mfa_secret(db, user)


def _pending_mfa_secret(user: User) -> str | None:
    started = user.mfa_pending_started_at
    if not user.mfa_pending_secret_encrypted or started is None:
        return None
    if started.replace(tzinfo=UTC) + timedelta(minutes=MFA_PENDING_TTL_MINUTES) <= _now():
        return None
    try:
        return secret_box().decrypt(user.mfa_pending_secret_encrypted)
    except ValueError:
        return None


def _new_recovery_codes() -> list[str]:
    # 64 bits per code, grouped for transcription.
    return [
        f"{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}" for _ in range(RECOVERY_CODE_COUNT)
    ]


async def confirm_mfa(db: AsyncSession, user: User, code: str) -> list[str]:
    pending = _pending_mfa_secret(user)
    if pending is None:
        raise ValidationFailed("Start MFA enrollment again; the setup window expired.")
    if not _verify_totp_secret(pending, code):
        raise ValidationFailed("The verification code is invalid.")

    user.mfa_secret_encrypted = user.mfa_pending_secret_encrypted
    user.mfa_enabled = True
    user.mfa_confirmed_at = _now()
    user.mfa_pending_secret_encrypted = None
    user.mfa_pending_started_at = None
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    codes = _new_recovery_codes()
    for code_value in codes:
        db.add(
            MfaRecoveryCode(
                user_id=user.id,
                code_hash=hash_token(code_value),
                created_at=_now(),
            )
        )
    await record_auth_event(db, event_type="mfa_enrolled", outcome="success", user=user)
    await db.commit()
    return codes


async def disable_mfa(db: AsyncSession, user: User, password: str) -> None:
    if not user.password_hash or not verify_password(password, user.password_hash):
        raise Unauthorized("Password is incorrect.")
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_confirmed_at = None
    user.mfa_pending_secret_encrypted = None
    user.mfa_pending_started_at = None
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    await record_auth_event(db, event_type="mfa_disabled", outcome="success", user=user)
    await db.commit()


async def _recent_password_hashes(db: AsyncSession, user: User, limit: int) -> list[str]:
    if limit <= 0:
        return []
    rows = (
        (
            await db.execute(
                select(PasswordHistory.password_hash)
                .where(PasswordHistory.user_id == user.id)
                .order_by(PasswordHistory.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _check_password_history(
    db: AsyncSession, user: User, new_password: str, policy: dict[str, Any]
) -> None:
    limit = int(policy.get("password_history", 0))
    for previous in await _recent_password_hashes(db, user, limit):
        if verify_password(new_password, previous):
            raise ValidationFailed(f"The new password must differ from your last {limit} passwords.")


async def _record_password_history(db: AsyncSession, user: User, previous_hash: str | None) -> None:
    if not previous_hash:
        return
    db.add(
        PasswordHistory(
            user_id=user.id,
            password_hash=previous_hash,
            created_at=_now(),
        )
    )
    # Keep the table bounded; 24 is far beyond any configured policy.
    stale = (
        (
            await db.execute(
                select(PasswordHistory.id)
                .where(PasswordHistory.user_id == user.id)
                .order_by(PasswordHistory.created_at.desc())
                .offset(24)
            )
        )
        .scalars()
        .all()
    )
    if stale:
        await db.execute(delete(PasswordHistory).where(PasswordHistory.id.in_(stale)))


async def change_password(
    db: AsyncSession,
    user: User,
    current: str,
    new: str,
    *,
    current_session_id: uuid.UUID | None = None,
) -> None:
    if not user.password_hash or not verify_password(current, user.password_hash):
        raise Unauthorized("Current password is incorrect.")
    policy = await get_effective_policy_for_user(db, user)
    problems = validate_password_strength(new, min_length=int(policy.get("password_min_length", 12)))
    if problems:
        raise ValidationFailed(" ".join(problems))
    if verify_password(new, user.password_hash):
        raise ValidationFailed("The new password must be different from the current password.")
    await _check_password_history(db, user, new, policy)
    await _record_password_history(db, user, user.password_hash)
    user.password_hash = hash_password(new)
    user.password_changed_at = _now()
    await revoke_all_sessions(db, user.id, except_session=current_session_id)
    await record_auth_event(db, event_type="password_changed", outcome="success", user=user)
    await db.commit()


async def build_me(db: AsyncSession, user: User) -> MeResponse:
    rows = (
        await db.execute(
            select(UserRole, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user.id)
            .order_by(UserRole.created_at)
        )
    ).all()

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    role_ids_by_scope: dict[tuple[str, str], set[uuid.UUID]] = {}
    all_role_ids: set[uuid.UUID] = set()
    for assignment, role in rows:
        key = (assignment.scope_type, str(assignment.scope_id or ""))
        entry = grouped.setdefault(
            key,
            {
                "scope_type": assignment.scope_type,
                "scope_id": assignment.scope_id,
                "scope_name": "Platform",
                "role_keys": [],
                "permissions": set(),
            },
        )
        entry["role_keys"].append(role.key)
        role_ids_by_scope.setdefault(key, set()).add(role.id)
        all_role_ids.add(role.id)

    from app.modules.access.models import RolePermission
    from app.modules.applications.models import Application
    from app.modules.tenants.models import Tenant

    permissions_by_role: dict[uuid.UUID, set[str]] = {}
    if all_role_ids:
        permission_rows = (
            await db.execute(
                select(RolePermission.role_id, RolePermission.permission_key).where(
                    RolePermission.role_id.in_(all_role_ids)
                )
            )
        ).all()
        for role_id, permission_key in permission_rows:
            permissions_by_role.setdefault(role_id, set()).add(permission_key)

    organization_ids = [
        e["scope_id"]
        for e in grouped.values()
        if e["scope_type"] == ScopeType.ORGANIZATION.value and e["scope_id"]
    ]
    application_ids = [
        e["scope_id"]
        for e in grouped.values()
        if e["scope_type"] == ScopeType.APPLICATION.value and e["scope_id"]
    ]
    tenant_ids = [
        e["scope_id"] for e in grouped.values() if e["scope_type"] == ScopeType.TENANT.value and e["scope_id"]
    ]
    organization_names = (
        dict(
            (
                await db.execute(
                    select(Organization.id, Organization.name).where(
                        Organization.id.in_(organization_ids or [uuid.uuid4()])
                    )
                )
            ).all()
        )
        if organization_ids
        else {}
    )
    application_names = (
        dict(
            (
                await db.execute(
                    select(Application.id, Application.name).where(
                        Application.id.in_(application_ids or [uuid.uuid4()])
                    )
                )
            ).all()
        )
        if application_ids
        else {}
    )
    tenant_names = (
        dict(
            (
                await db.execute(
                    select(Tenant.id, Tenant.name).where(Tenant.id.in_(tenant_ids or [uuid.uuid4()]))
                )
            ).all()
        )
        if tenant_ids
        else {}
    )

    platform_permissions: set[str] = set()
    for key, entry in grouped.items():
        if entry["scope_type"] == ScopeType.ORGANIZATION.value:
            entry["scope_name"] = organization_names.get(entry["scope_id"], "Unknown organization")
        elif entry["scope_type"] == ScopeType.APPLICATION.value:
            entry["scope_name"] = application_names.get(entry["scope_id"], "Unknown application")
        elif entry["scope_type"] == ScopeType.TENANT.value:
            entry["scope_name"] = tenant_names.get(entry["scope_id"], "Unknown tenant")
        permissions: set[str] = set()
        for role_id in role_ids_by_scope.get(key, set()):
            permissions |= permissions_by_role.get(role_id, set())
        entry["permissions"] = permissions
        if entry["scope_type"] == ScopeType.PLATFORM.value:
            platform_permissions |= permissions

    scopes = [
        IdentityScope(
            scope_type=e["scope_type"],
            scope_id=e["scope_id"],
            scope_name=e["scope_name"],
            role_keys=e["role_keys"],
            permissions=sorted(e["permissions"]),
        )
        for e in grouped.values()
    ]

    membership_payload = [
        {"tenant_id": assignment.scope_id, "role": role.key, "role_name": role.name}
        for assignment, role in rows
        if assignment.scope_type == ScopeType.TENANT.value
    ]

    return MeResponse(
        user=UserSummary.model_validate(user),
        scopes=scopes,
        platform_permissions=sorted(platform_permissions),
        memberships=membership_payload,
    )


async def count_active_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(UserSession)
                .where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > _now(),
                )
            )
        ).scalar_one()
    )


async def _consume_token(db: AsyncSession, *, token: str, purpose: str) -> VerificationToken:
    record = (
        await db.execute(
            select(VerificationToken).where(
                VerificationToken.token_hash == hash_token(token),
                VerificationToken.purpose == purpose,
                VerificationToken.consumed_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise ValidationFailed("This link is invalid or has already been used.")
    if record.expires_at.replace(tzinfo=UTC) <= _now():
        raise ValidationFailed("This link has expired. Request a new one.")
    return record


async def accept_invite(
    db: AsyncSession,
    *,
    invite_token: str,
    new_password: str,
    full_name: str | None,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, TokenBundle]:
    token_row = await _consume_token(db, token=invite_token, purpose="invite")
    user = await db.get(User, token_row.user_id)
    if user is None:
        raise ValidationFailed("This invitation is no longer valid.")
    if user.status not in (UserStatus.INVITED.value, UserStatus.ACTIVE.value):
        raise Forbidden("This account cannot accept the invitation.")

    policy = await get_effective_policy_for_user(db, user)
    problems = validate_password_strength(new_password, min_length=int(policy.get("password_min_length", 12)))
    if problems:
        raise ValidationFailed(" ".join(problems))
    await _check_password_history(db, user, new_password, policy)
    await _record_password_history(db, user, user.password_hash)

    user.password_hash = hash_password(new_password)
    user.status = UserStatus.ACTIVE.value
    user.email_verified_at = user.email_verified_at or _now()
    user.password_changed_at = _now()
    user.failed_login_count = 0
    user.locked_until = None
    if full_name:
        user.full_name = " ".join(full_name.split())
    token_row.consumed_at = _now()

    memberships = (
        (
            await db.execute(
                select(TenantMembership).where(
                    TenantMembership.user_id == user.id,
                    TenantMembership.status == MembershipStatus.INVITED.value,
                )
            )
        )
        .scalars()
        .all()
    )
    for membership in memberships:
        membership.status = MembershipStatus.ACTIVE.value

    await revoke_all_sessions(db, user.id)
    session, refresh_token = await _create_session(db, user, ip=ip, user_agent=user_agent, policy=policy)
    await record_auth_event(
        db,
        event_type="invite_accepted",
        outcome="success",
        user=user,
        ip=ip,
        user_agent=user_agent,
        metadata={"session_id": str(session.id)},
    )
    await db.commit()
    return user, _tokens(user, session, refresh_token)


async def request_password_reset(db: AsyncSession, *, email: str) -> str | None:
    """Create a single-use reset token and deliver it. The response never reveals existence."""
    normalized = email.strip().lower()
    user = (
        await db.execute(select(User).where(User.email == normalized, User.status == UserStatus.ACTIVE.value))
    ).scalar_one_or_none()
    if user is None:
        return None

    await db.execute(
        delete(VerificationToken).where(
            VerificationToken.user_id == user.id, VerificationToken.purpose == "password_reset"
        )
    )
    token = generate_token(32)
    db.add(
        VerificationToken(
            user_id=user.id,
            purpose="password_reset",
            token_hash=hash_token(token),
            expires_at=_now() + timedelta(minutes=60),
            created_at=_now(),
        )
    )
    await record_auth_event(db, event_type="password_reset_requested", outcome="success", user=user)
    await db.commit()

    link = f"{settings.web_base_url}/reset-password?token={token}"
    await send_email(
        user.email,
        "Reset your Basacore password",
        f"Use this single-use link within 60 minutes to reset your password:\n\n{link}\n",
    )
    # In local development the mail sink cannot be read, so return the token to
    # make the workflow testable. Production never includes it in the response.
    return token if not settings.is_production else None


async def reset_password(db: AsyncSession, *, reset_token: str, new_password: str) -> None:
    token_row = await _consume_token(db, token=reset_token, purpose="password_reset")
    user = await db.get(User, token_row.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise ValidationFailed("This reset link is no longer valid.")
    policy = await get_effective_policy_for_user(db, user)
    problems = validate_password_strength(new_password, min_length=int(policy.get("password_min_length", 12)))
    if problems:
        raise ValidationFailed(" ".join(problems))
    await _check_password_history(db, user, new_password, policy)
    await _record_password_history(db, user, user.password_hash)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = _now()
    user.failed_login_count = 0
    user.locked_until = None
    token_row.consumed_at = _now()
    await revoke_all_sessions(db, user.id)
    await record_auth_event(db, event_type="password_reset", outcome="success", user=user)
    await db.commit()
