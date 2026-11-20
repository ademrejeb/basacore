from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, require_platform
from app.core.errors import NotFound
from app.core.pagination import PageParams, page_params, paginate
from app.modules.audit.models import AuditLog

router = APIRouter(prefix="/audit-logs", tags=["Audit"])


class SecretCreate(BaseModel):
    scope_type: str = Field(pattern=r"^(platform|organization|application|tenant)$")
    scope_id: uuid.UUID | None = None
    name: str = Field(min_length=2, max_length=120)
    provider: str = Field(default="platform", max_length=40)
    path: str | None = Field(default=None, max_length=300)


@router.get("", response_model=dict, summary="Query the append-only audit trail")
async def list_audit_logs(
    params: PageParams = Depends(page_params),
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor_id: uuid.UUID | None = None,
    outcome: str | None = None,
    scope_type: str | None = None,
    scope_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("audit.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if params.search:
        term = f"%{params.search.strip()}%"
        stmt = stmt.where(
            or_(
                AuditLog.action.ilike(term),
                AuditLog.actor_email.ilike(term),
                AuditLog.resource_type.ilike(term),
                AuditLog.resource_id.ilike(term),
            )
        )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if outcome:
        stmt = stmt.where(AuditLog.outcome == outcome)
    if scope_type:
        stmt = stmt.where(AuditLog.scope_type == scope_type)
    if scope_id:
        stmt = stmt.where(AuditLog.scope_id == scope_id)
    rows, total = await paginate(db, stmt, params)
    return {
        "items": [
            {
                "id": log.id,
                "actor_id": log.actor_id,
                "actor_email": log.actor_email,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "scope_type": log.scope_type,
                "scope_id": log.scope_id,
                "outcome": log.outcome,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
                "request_id": log.request_id,
                "metadata": log.metadata_json,
                "created_at": log.created_at,
            }
            for log in rows
        ],
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@router.get("/{log_id}", response_model=dict)
async def get_audit_log(
    log_id: uuid.UUID,
    _: Principal = Depends(require_platform("audit.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    log = await db.get(AuditLog, log_id)
    if log is None:
        raise NotFound("Audit record", str(log_id))
    return {
        "id": log.id,
        "actor_id": log.actor_id,
        "actor_email": log.actor_email,
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "scope_type": log.scope_type,
        "scope_id": log.scope_id,
        "outcome": log.outcome,
        "ip_address": log.ip_address,
        "user_agent": log.user_agent,
        "request_id": log.request_id,
        "metadata": log.metadata_json,
        "created_at": log.created_at,
    }
