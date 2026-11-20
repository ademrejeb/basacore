"""Security policy administration.

``SecurityPolicy`` rows existed but nothing could write them, so every login,
refresh and password change ran against the seeded defaults only. These
endpoints make the configured policy real: one row per scope, merged from
broadest to narrowest by :func:`app.modules.access.policy.get_effective_policy`.
"""

from __future__ import annotations

import ipaddress
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import get_session
from app.core.deps import Principal, require_platform
from app.core.enums import ScopeType
from app.core.errors import ValidationFailed
from app.modules.access.models import SecurityPolicy
from app.modules.access.policy import POLICY_KEYS, get_effective_policy

router = APIRouter(prefix="/access", tags=["Access control"])

# Hard bounds keep a mistyped policy from locking every user out or disabling
# the controls that protect the platform.
_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "password_min_length": (8, 256),
    "max_failed_attempts": (1, 50),
    "lockout_minutes": (0, 10_080),
    "max_concurrent_sessions": (1, 1_000),
    "session_idle_minutes": (1, 43_200),
    "refresh_token_ttl_days": (1, 365),
    "password_history": (0, 24),
    "api_token_ttl_minutes": (1, 10_080),
}
_BOOL_KEYS = {"mfa_required", "mfa_required_for_admins", "require_email_verification"}


def _validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    unknown = set(settings) - POLICY_KEYS
    if unknown:
        raise ValidationFailed(
            "Unknown policy keys: " + ", ".join(sorted(unknown)),
        )
    for key, value in settings.items():
        if key in _INT_BOUNDS:
            low, high = _INT_BOUNDS[key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValidationFailed(f"{key} must be an integer between {low} and {high}.")
        elif key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise ValidationFailed(f"{key} must be a boolean.")
        elif key == "allowed_ip_cidrs":
            if not isinstance(value, list):
                raise ValidationFailed("allowed_ip_cidrs must be a list of CIDR strings.")
            for entry in value:
                try:
                    ipaddress.ip_network(str(entry), strict=False)
                except ValueError as exc:
                    raise ValidationFailed(f"Invalid CIDR in allowed_ip_cidrs: {entry}") from exc
    return settings


class PolicyUpdate(BaseModel):
    scope_type: ScopeType = ScopeType.PLATFORM
    scope_id: uuid.UUID | None = Field(default=None)
    name: str | None = Field(default=None, min_length=2, max_length=120)
    settings: dict[str, Any]

    @field_validator("settings")
    @classmethod
    def validate_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_settings(value)

    @field_validator("scope_id")
    @classmethod
    def validate_scope(cls, value: uuid.UUID | None, info) -> uuid.UUID | None:
        scope_type = info.data.get("scope_type", ScopeType.PLATFORM)
        if scope_type == ScopeType.PLATFORM and value is not None:
            raise ValueError("Platform policies cannot carry a scope_id.")
        if scope_type != ScopeType.PLATFORM and value is None:
            raise ValueError("scope_id is required for organization, application and tenant policies.")
        return value


@router.get("/security-policy", response_model=dict, summary="Effective security policy")
async def read_security_policy(
    organization_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("security.policies.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    merged = await get_effective_policy(
        db,
        organization_id=organization_id,
        application_id=application_id,
        tenant_id=tenant_id,
    )
    return {"settings": merged}


@router.put("/security-policy", response_model=dict, summary="Create or update a security policy")
async def upsert_security_policy(
    payload: PolicyUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("security.policies.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(SecurityPolicy).where(SecurityPolicy.scope_type == payload.scope_type.value)
    if payload.scope_id is None:
        stmt = stmt.where(SecurityPolicy.scope_id.is_(None))
    else:
        stmt = stmt.where(SecurityPolicy.scope_id == payload.scope_id)
    policy = (await db.execute(stmt.order_by(SecurityPolicy.created_at.asc()))).scalars().first()

    if policy is None:
        policy = SecurityPolicy(
            name=payload.name or f"{payload.scope_type.value} policy",
            scope_type=payload.scope_type.value,
            scope_id=payload.scope_id,
            settings=payload.settings,
            is_default=payload.scope_type == ScopeType.PLATFORM,
        )
        db.add(policy)
        action = "security.policy_created"
    else:
        # Merge so a caller can change one control without resending the rest.
        merged = dict(policy.settings or {})
        merged.update(payload.settings)
        policy.settings = merged
        if payload.name:
            policy.name = payload.name
        action = "security.policy_updated"

    await record_audit(
        db,
        action=action,
        resource_type="security_policy",
        resource_id=policy.id,
        actor=principal,
        request=request,
        scope_type=payload.scope_type.value,
        scope_id=payload.scope_id,
        metadata={"keys": sorted(payload.settings)},
    )
    await db.commit()
    return {
        "id": str(policy.id),
        "scope_type": policy.scope_type,
        "scope_id": str(policy.scope_id) if policy.scope_id else None,
        "settings": policy.settings,
    }


@router.get("/security-policy/scopes", response_model=list[dict], summary="Stored policy rows")
async def list_security_policies(
    _: Principal = Depends(require_platform("security.policies.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (await db.execute(select(SecurityPolicy).order_by(SecurityPolicy.scope_type))).scalars().all()
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "scope_type": row.scope_type,
            "scope_id": str(row.scope_id) if row.scope_id else None,
            "settings": row.settings,
            "is_default": row.is_default,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]
