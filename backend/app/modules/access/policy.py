from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ScopeType
from app.modules.access.models import SecurityPolicy, UserRole

DEFAULT_POLICY: dict[str, Any] = {
    "password_min_length": 12,
    "max_failed_attempts": 5,
    "lockout_minutes": 15,
    "max_concurrent_sessions": 10,
    "session_idle_minutes": 60 * 24,
    "refresh_token_ttl_days": 30,
    "mfa_required": False,
    "mfa_required_for_admins": False,
    "require_email_verification": False,
    "allowed_ip_cidrs": [],
    "password_history": 3,
    "api_token_ttl_minutes": 60,
}

POLICY_KEYS = set(DEFAULT_POLICY)


async def get_effective_policy_for_user(db: AsyncSession, user) -> dict[str, Any]:
    """Merge every policy row that applies to a user's actual grants.

    Sign-in happens before a tenant is chosen, so the platform row alone is not
    enough: a user with organization, application or tenant grants must also
    inherit those policies. Narrower scopes win; when several rows exist at the
    same level the most recently created one wins deterministically.
    """
    rows = (
        await db.execute(
            select(UserRole.scope_type, UserRole.scope_id).where(
                UserRole.user_id == user.id,
                or_(UserRole.expires_at.is_(None), UserRole.expires_at > datetime.now(UTC)),
            )
        )
    ).all()
    scope_ids: dict[str, set[uuid.UUID]] = {
        ScopeType.ORGANIZATION.value: set(),
        ScopeType.APPLICATION.value: set(),
        ScopeType.TENANT.value: set(),
    }
    for scope_type, scope_id in rows:
        if scope_type in scope_ids and scope_id is not None:
            scope_ids[scope_type].add(scope_id)

    conditions = [(SecurityPolicy.scope_type == ScopeType.PLATFORM.value) & SecurityPolicy.scope_id.is_(None)]
    for scope_type, ids in scope_ids.items():
        if ids:
            conditions.append((SecurityPolicy.scope_type == scope_type) & SecurityPolicy.scope_id.in_(ids))
    policies = (await db.execute(select(SecurityPolicy).where(or_(*conditions)))).scalars().all()
    return _merge_policies(policies)


async def get_effective_policy(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Merge policy rows from broadest to narrowest scope."""
    stmt = select(SecurityPolicy).where(
        (SecurityPolicy.scope_type == ScopeType.PLATFORM.value)
        | (
            (SecurityPolicy.scope_type == ScopeType.ORGANIZATION.value)
            & (SecurityPolicy.scope_id == organization_id)
        )
        | (
            (SecurityPolicy.scope_type == ScopeType.APPLICATION.value)
            & (SecurityPolicy.scope_id == application_id)
        )
        | ((SecurityPolicy.scope_type == ScopeType.TENANT.value) & (SecurityPolicy.scope_id == tenant_id))
    )
    policies = (await db.execute(stmt)).scalars().all()
    return _merge_policies(policies)


def _merge_policies(policies) -> dict[str, Any]:
    order = {
        ScopeType.PLATFORM.value: 0,
        ScopeType.ORGANIZATION.value: 1,
        ScopeType.APPLICATION.value: 2,
        ScopeType.TENANT.value: 3,
    }
    merged = dict(DEFAULT_POLICY)
    for policy in sorted(policies, key=lambda p: (order.get(p.scope_type, 9), p.created_at or p.updated_at)):
        for key, value in (policy.settings or {}).items():
            if key in POLICY_KEYS:
                merged[key] = value
    return merged
