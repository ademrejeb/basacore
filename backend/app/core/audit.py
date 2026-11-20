from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.deps import Principal
from app.core.enums import AuditOutcome
from app.modules.audit.models import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str | uuid.UUID | None = None,
    actor: Principal | None = None,
    actor_email: str | None = None,
    scope_type: str = "platform",
    scope_id: uuid.UUID | None = None,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    request: Request | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an audit record. The transaction owner commits it."""
    entry = AuditLog(
        id=uuid.uuid4(),
        actor_id=actor.user.id if actor else None,
        actor_email=actor.user.email if actor else actor_email,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        scope_type=scope_type,
        scope_id=scope_id,
        outcome=outcome.value,
        ip_address=getattr(request.state, "client_ip", None) if request else None,
        user_agent=getattr(request.state, "user_agent", None) if request else None,
        request_id=getattr(request.state, "request_id", None) if request else None,
        metadata_json=metadata or {},
        created_at=utcnow(),
    )
    db.add(entry)
    return entry
