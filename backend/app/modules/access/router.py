from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import get_session, slugify
from app.core.deps import Principal, require_platform
from app.core.enums import ScopeType
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.permissions import PERMISSIONS, ROLE_BY_KEY, ensure_known_permissions, exceeded_grants
from app.modules.access.models import Role, RolePermission, UserRole

router = APIRouter(prefix="/access", tags=["Access control"])


class RoleCreate(BaseModel):
    key: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    scope_type: ScopeType = ScopeType.ORGANIZATION
    organization_id: uuid.UUID | None = None
    permissions: list[str] = Field(default_factory=list)

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        ensure_known_permissions(set(value))
        if "*" in value:
            raise ValueError("Wildcard grants cannot be assigned to custom roles.")
        return sorted(set(value))


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] | None = None

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        ensure_known_permissions(set(value))
        if "*" in value:
            raise ValueError("Wildcard grants cannot be assigned to custom roles.")
        return sorted(set(value))


async def _role_payload(db: AsyncSession, role: Role) -> dict[str, Any]:
    permissions = (
        (
            await db.execute(
                select(RolePermission.permission_key)
                .where(RolePermission.role_id == role.id)
                .order_by(RolePermission.permission_key)
            )
        )
        .scalars()
        .all()
    )
    assignments = (
        await db.execute(select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id))
    ).scalar_one()
    return {
        "id": role.id,
        "key": role.key,
        "name": role.name,
        "description": role.description,
        "scope_type": role.scope_type,
        "organization_id": role.organization_id,
        "is_system": role.is_system,
        "permissions": list(permissions),
        "assignments": assignments,
        "created_at": role.created_at,
    }


@router.get("/permissions", response_model=list[dict], summary="Permission catalogue")
async def list_permissions(
    _: Principal = Depends(require_platform("roles.read")),
) -> list[dict[str, Any]]:
    return [{"key": p.key, "description": p.description, "category": p.category} for p in PERMISSIONS]


@router.get("/role-templates", response_model=list[dict], summary="System role templates")
async def role_templates(
    _: Principal = Depends(require_platform("roles.read")),
) -> list[dict[str, Any]]:
    return [
        {
            "key": spec.key,
            "name": spec.name,
            "description": spec.description,
            "scope_type": spec.scope_type.value,
            "permissions": sorted(spec.permissions),
        }
        for spec in ROLE_BY_KEY.values()
    ]


@router.get("/roles", response_model=list[dict], summary="List roles")
async def list_roles(
    scope_type: ScopeType | None = None,
    organization_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("roles.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = select(Role).order_by(Role.is_system.desc(), Role.name)
    if scope_type:
        stmt = stmt.where(Role.scope_type == scope_type.value)
    if organization_id:
        stmt = stmt.where(Role.organization_id == organization_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [await _role_payload(db, role) for role in rows]


@router.get("/roles/{role_id}", response_model=dict)
async def get_role(
    role_id: uuid.UUID,
    _: Principal = Depends(require_platform("roles.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    role = await db.get(Role, role_id)
    if role is None:
        raise NotFound("Role", str(role_id))
    return await _role_payload(db, role)


@router.post("/roles", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    request: Request,
    principal: Principal = Depends(require_platform("roles.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if payload.scope_type == ScopeType.PLATFORM and payload.organization_id:
        raise ValidationFailed("Platform roles cannot belong to an organization.")
    if payload.scope_type != ScopeType.PLATFORM and payload.organization_id is None:
        raise ValidationFailed("organization_id is required for organization-scoped roles.")
    if payload.scope_type not in (ScopeType.PLATFORM, ScopeType.ORGANIZATION):
        raise ValidationFailed(
            "Custom roles can be platform or organization scoped. Application and tenant "
            "roles are assigned through system templates."
        )
    if (await db.execute(select(Role.id).where(Role.key == payload.key))).scalar_one_or_none():
        raise Conflict("A role with that key already exists.")

    actor_permissions = await principal.permissions(db, organization_id=payload.organization_id)
    excess = exceeded_grants(set(payload.permissions), actor_permissions)
    if excess:
        raise Forbidden(
            "A role cannot grant permissions the actor does not hold: " + ", ".join(sorted(excess))
        )

    role = Role(
        key=slugify(payload.key),
        name=payload.name.strip(),
        description=payload.description,
        scope_type=payload.scope_type.value,
        organization_id=payload.organization_id,
        is_system=False,
    )
    db.add(role)
    await db.flush()
    for key in payload.permissions:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    await record_audit(
        db,
        action="role.created",
        resource_type="role",
        resource_id=role.id,
        actor=principal,
        request=request,
        scope_type=payload.scope_type.value,
        scope_id=payload.organization_id,
        metadata={"key": role.key, "permissions": payload.permissions},
    )
    await db.commit()
    return await _role_payload(db, role)


@router.patch("/roles/{role_id}", response_model=dict)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("roles.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    role = await db.get(Role, role_id)
    if role is None:
        raise NotFound("Role", str(role_id))
    if role.is_system:
        raise ValidationFailed("System role templates are immutable. Create a custom role instead.")
    changes = payload.model_dump(exclude_unset=True)
    if payload.name is not None:
        role.name = payload.name.strip()
    if payload.description is not None:
        role.description = payload.description
    if payload.permissions is not None:
        actor_permissions = await principal.permissions(db, organization_id=role.organization_id)
        excess = exceeded_grants(set(payload.permissions), actor_permissions)
        if excess:
            raise Forbidden(
                "A role cannot grant permissions the actor does not hold: " + ", ".join(sorted(excess))
            )
        await db.execute(RolePermission.__table__.delete().where(RolePermission.role_id == role.id))
        for key in payload.permissions:
            db.add(RolePermission(role_id=role.id, permission_key=key))
    await record_audit(
        db,
        action="role.updated",
        resource_type="role",
        resource_id=role.id,
        actor=principal,
        request=request,
        scope_type=role.scope_type,
        scope_id=role.organization_id,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return await _role_payload(db, role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_platform("roles.manage")),
    db: AsyncSession = Depends(get_session),
) -> None:
    role = await db.get(Role, role_id)
    if role is None:
        raise NotFound("Role", str(role_id))
    if role.is_system:
        raise ValidationFailed("System role templates cannot be deleted.")
    assigned = (
        await db.execute(select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id))
    ).scalar_one()
    if assigned:
        raise Conflict("Remove this role from all users before deleting it.")
    await db.delete(role)
    await record_audit(
        db,
        action="role.deleted",
        resource_type="role",
        resource_id=role_id,
        actor=principal,
        request=request,
        scope_type=role.scope_type,
        scope_id=role.organization_id,
        metadata={"key": role.key},
    )
    await db.commit()
